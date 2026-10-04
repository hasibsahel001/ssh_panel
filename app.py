"""
MikroTik Control Panel - Backend
Runs commands on one or many MikroTik devices over SSH, in parallel, and
exports the results as CSV / Excel / TXT. Also includes a live ping
(connectivity test) endpoint using Server-Sent Events.
"""
from flask import Flask, request, jsonify, send_file, render_template, Response, stream_with_context
import paramiko
import ipaddress
import csv
import io
import os
import socket
import subprocess
import platform
import re
import time
import json
import uuid
import concurrent.futures
from datetime import datetime
from openpyxl import Workbook

app = Flask(__name__)

MAX_IPS = 500          # cap on IP count in range mode, to avoid accidental misuse
MAX_WORKERS = 40        # max concurrent SSH connections
DEFAULT_TIMEOUT = 8     # seconds

VPN_STORE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vpn_connections.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def generate_ip_list(mode, ip=None, start_ip=None, end_ip=None):
    if mode == "single":
        ip = (ip or "").strip()
        if not ip:
            raise ValueError("IP address is required")
        ipaddress.IPv4Address(ip)  # validate
        return [ip]

    start_ip = (start_ip or "").strip()
    end_ip = (end_ip or "").strip()
    if not start_ip or not end_ip:
        raise ValueError("Both Start IP and End IP are required")

    start = ipaddress.IPv4Address(start_ip)
    end = ipaddress.IPv4Address(end_ip)
    if int(end) < int(start):
        start, end = end, start

    count = int(end) - int(start) + 1
    if count > MAX_IPS:
        raise ValueError(f"Range contains {count} IPs; the maximum allowed is {MAX_IPS}")

    return [str(ipaddress.IPv4Address(i)) for i in range(int(start), int(end) + 1)]


def run_commands_on_device(ip, port, username, password, commands, timeout=DEFAULT_TIMEOUT):
    result = {"ip": ip, "success": False, "output": "", "error": ""}
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=ip,
            port=port,
            username=username,
            password=password,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
        )
        outputs = []
        for cmd in commands:
            cmd = cmd.strip()
            if not cmd:
                continue
            try:
                stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
                out = stdout.read().decode("utf-8", errors="replace")
                err = stderr.read().decode("utf-8", errors="replace")
                block = f"> {cmd}\n{out}"
                if err.strip():
                    block += f"{err}"
                outputs.append(block.rstrip())
            except Exception as ce:  # noqa: BLE001
                outputs.append(f"> {cmd}\n[Error running this command: {ce}]")
        result["output"] = "\n\n".join(outputs)
        result["success"] = True
    except paramiko.AuthenticationException:
        result["error"] = "Authentication failed: wrong username or password"
    except (socket.timeout, TimeoutError):
        result["error"] = "Connection timed out - device is not reachable"
    except socket.error as se:
        result["error"] = f"Network error: {se}"
    except Exception as e:  # noqa: BLE001
        result["error"] = f"Unknown error: {e}"
    finally:
        client.close()
    return result


def ping_once(ip, timeout=2):
    system = platform.system().lower()
    if system == "windows":
        cmd = ["ping", "-n", "1", "-w", str(timeout * 1000), ip]
    else:
        cmd = ["ping", "-c", "1", "-W", str(timeout), ip]

    try:
        start = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
        elapsed_ms = (time.time() - start) * 1000
        output = proc.stdout + proc.stderr
        success = proc.returncode == 0

        rtt = None
        match = re.search(r"time[=<]([\d.]+)\s*ms", output, re.IGNORECASE)
        if match:
            rtt = float(match.group(1))
        elif success:
            rtt = round(elapsed_ms, 1)

        return {"success": success, "rtt": rtt}
    except subprocess.TimeoutExpired:
        return {"success": False, "rtt": None}
    except Exception:  # noqa: BLE001
        return {"success": False, "rtt": None}


def is_windows():
    return platform.system().lower() == "windows"

def is_admin():
    """New-NetRoute/Remove-NetRoute need the process to be elevated."""
    ok, out, _err = run_powershell(
        "([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent())"
        ".IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)"
    )
    return ok and out.strip().lower() == "true"


# ---------------------------------------------------------------------------
# VPN tunnels (Windows built-in PPTP / L2TP client, via PowerShell + rasdial)
#
# These connect FROM the machine running this panel to a remote network,
# using Windows' own VPN stack - same as "Settings > VPN > Add a VPN
# connection", just automated. Split tunneling is always enabled when the
# tunnel is created, so only traffic meant for the remote network goes
# through the tunnel; the laptop's normal internet/network path is left
# untouched.
# ---------------------------------------------------------------------------
def load_vpn_connections():
    if not os.path.exists(VPN_STORE_PATH):
        return []
    try:
        with open(VPN_STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return []


def save_vpn_connections(data):
    with open(VPN_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def run_powershell(command, timeout=20):
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode == 0, proc.stdout.strip(), proc.stderr.strip()
    except Exception as e:  # noqa: BLE001
        return False, "", str(e)


def create_windows_vpn(name, protocol, server, psk=None):
    tunnel_type = "Pptp" if protocol == "pptp" else "L2tp"
    cmd = (
        f'Add-VpnConnection -Name "{name}" -ServerAddress "{server}" '
        f"-TunnelType {tunnel_type} -EncryptionLevel Optional "
        f"-AuthenticationMethod MSChapv2 -SplitTunneling -RememberCredential -Force"
    )
    if protocol == "l2tp" and psk:
        cmd += f' -L2tpPsk "{psk}"'
    return run_powershell(cmd)


def remove_windows_vpn(name):
    return run_powershell(f'Remove-VpnConnection -Name "{name}" -Force -ErrorAction SilentlyContinue')


def get_windows_vpn_status(name):
    ok, out, _err = run_powershell(
        f'(Get-VpnConnection -Name "{name}" -ErrorAction SilentlyContinue).ConnectionStatus'
    )
    if ok and out:
        return out.strip()
    return "Unknown"


def rasdial_connect(name, username, password):
    try:
        proc = subprocess.run(
            ["rasdial", name, username, password],
            capture_output=True, text=True, timeout=25,
        )
        output = (proc.stdout + proc.stderr).strip()
        return proc.returncode == 0, output
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def rasdial_disconnect(name):
    try:
        proc = subprocess.run(
            ["rasdial", name, "/DISCONNECT"],
            capture_output=True, text=True, timeout=15,
        )
        output = (proc.stdout + proc.stderr).strip()
        return proc.returncode == 0, output
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def parse_networks(raw):
    """'192.168.50.0/24, 192.168.60.0/24' -> ['192.168.50.0/24', '192.168.60.0/24']"""
    if not raw:
        return []
    return [n.strip() for n in raw.replace(";", ",").split(",") if n.strip()]


def add_vpn_routes(name, networks):
    """
    Adds a persistent static route for each remote subnet via the VPN's own
    interface, so traffic to that subnet actually goes through the tunnel.
    PPTP/L2TP on Windows never pushes these routes automatically - this is
    what makes pinging devices behind the remote router possible.

    Uses try/catch inside PowerShell (instead of -ErrorAction SilentlyContinue)
    so the real failure reason - most commonly "Access is denied" when this
    panel isn't running elevated - comes back instead of a bare "failed".
    """
    added = []
    for net in networks:
        ok, out, err = run_powershell(
            f'try {{ '
            f'New-NetRoute -DestinationPrefix "{net}" -InterfaceAlias "{name}" '
            f'-ErrorAction Stop | Out-Null; Write-Output "OK" '
            f'}} catch {{ Write-Output ("ERR:" + $_.Exception.Message) }}'
        )
        text = out.strip()
        success = ok and text == "OK"
        if success:
            error_msg = ""
        elif text.startswith("ERR:"):
            error_msg = text[4:].strip()
        else:
            error_msg = err or text or "Unknown error"
        added.append({"network": net, "ok": success, "error": error_msg})
    return added


def remove_vpn_routes(name, networks):
    for net in networks:
        run_powershell(
            f'Remove-NetRoute -DestinationPrefix "{net}" -InterfaceAlias "{name}" '
            f'-Confirm:$false -ErrorAction SilentlyContinue'
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/run", methods=["POST"])
def api_run():
    data = request.get_json(force=True, silent=True) or {}

    mode = data.get("mode", "single")
    try:
        port = int(data.get("port", 2288))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid port"}), 400

    username = data.get("username", "")
    password = data.get("password", "")
    commands_raw = data.get("commands", "")
    commands = commands_raw.split("\n") if commands_raw else []

    if not username:
        return jsonify({"error": "Username is required"}), 400
    if not any(c.strip() for c in commands):
        return jsonify({"error": "Enter at least one command"}), 400

    try:
        if mode == "single":
            ips = generate_ip_list("single", ip=data.get("ip", ""))
        else:
            ips = generate_ip_list(
                "range", start_ip=data.get("start_ip", ""), end_ip=data.get("end_ip", "")
            )
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 400

    results = []
    workers = min(MAX_WORKERS, max(1, len(ips)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_ip = {
            executor.submit(
                run_commands_on_device, ip, port, username, password, commands
            ): ip
            for ip in ips
        }
        for future in concurrent.futures.as_completed(future_to_ip):
            results.append(future.result())

    order = {ip: idx for idx, ip in enumerate(ips)}
    results.sort(key=lambda r: order.get(r["ip"], 0))

    ok = sum(1 for r in results if r["success"])
    return jsonify(
        {
            "results": results,
            "summary": {"total": len(results), "success": ok, "failed": len(results) - ok},
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )


@app.route("/api/export/csv", methods=["POST"])
def export_csv():
    data = request.get_json(force=True, silent=True) or {}
    results = data.get("results", [])

    si = io.StringIO()
    writer = csv.writer(si)
    writer.writerow(["IP", "Status", "Output", "Error"])
    for r in results:
        writer.writerow(
            [r.get("ip", ""), "Success" if r.get("success") else "Failed",
             r.get("output", ""), r.get("error", "")]
        )
    mem = io.BytesIO(si.getvalue().encode("utf-8-sig"))
    mem.seek(0)
    filename = f"mikrotik_output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name=filename)


@app.route("/api/export/xlsx", methods=["POST"])
def export_xlsx():
    data = request.get_json(force=True, silent=True) or {}
    results = data.get("results", [])

    wb = Workbook()
    ws = wb.active
    ws.title = "Output"
    ws.append(["IP", "Status", "Output", "Error"])
    for r in results:
        ws.append(
            [r.get("ip", ""), "Success" if r.get("success") else "Failed",
             r.get("output", ""), r.get("error", "")]
        )
    for col in ws.columns:
        max_len = max((len(str(c.value)) if c.value is not None else 0) for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 12), 90)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = cell.alignment.copy(wrap_text=True, vertical="top")

    mem = io.BytesIO()
    wb.save(mem)
    mem.seek(0)
    filename = f"mikrotik_output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        mem,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/export/txt", methods=["POST"])
def export_txt():
    data = request.get_json(force=True, silent=True) or {}
    results = data.get("results", [])

    lines = []
    for r in results:
        status = "SUCCESS" if r.get("success") else "FAILED"
        lines.append(f"===== {r.get('ip', '')}  [{status}] =====")
        if r.get("success"):
            lines.append(r.get("output", "") or "(no output)")
        else:
            lines.append(f"Error: {r.get('error', '')}")
        lines.append("")
    mem = io.BytesIO("\n".join(lines).encode("utf-8"))
    mem.seek(0)
    filename = f"mikrotik_output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    return send_file(mem, mimetype="text/plain", as_attachment=True, download_name=filename)


@app.route("/api/ping/stream")
def ping_stream():
    ip = request.args.get("ip", "").strip()
    try:
        count = int(request.args.get("count", 10))
    except (TypeError, ValueError):
        count = 10
    count = max(1, min(count, 100))

    try:
        ipaddress.ip_address(ip)
    except ValueError:
        def err_gen():
            yield f"data: {json.dumps({'error': 'Invalid IP address'})}\n\n"
        return Response(err_gen(), mimetype="text/event-stream")

    def generate():
        sent = 0
        received = 0
        rtts = []
        for seq in range(1, count + 1):
            result = ping_once(ip)
            sent += 1
            if result["success"]:
                received += 1
                if result["rtt"] is not None:
                    rtts.append(result["rtt"])

            payload = {
                "seq": seq, "ip": ip,
                "success": result["success"], "rtt": result["rtt"],
                "done": False,
            }
            yield f"data: {json.dumps(payload)}\n\n"
            time.sleep(0.15)

        loss_pct = round(((sent - received) / sent) * 100, 1) if sent else 0
        summary = {
            "done": True, "sent": sent, "received": received, "loss_pct": loss_pct,
            "avg_rtt": round(sum(rtts) / len(rtts), 1) if rtts else None,
            "min_rtt": min(rtts) if rtts else None,
            "max_rtt": max(rtts) if rtts else None,
        }
        yield f"data: {json.dumps(summary)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/vpn/list", methods=["GET"])
def vpn_list():
    conns = load_vpn_connections()
    result = []
    for c in conns:
        status = get_windows_vpn_status(c["name"]) if is_windows() else "Unsupported OS"
        result.append({
            "id": c["id"],
            "name": c["name"],
            "protocol": c["protocol"],
            "server": c["server"],
            "username": c["username"],
            "has_psk": bool(c.get("psk")),
            "networks": c.get("networks", ""),
            "status": status,
        })
    return jsonify({"connections": result, "windows": is_windows()})


@app.route("/api/vpn/add", methods=["POST"])
def vpn_add():
    if not is_windows():
        return jsonify({"error": "VPN tunnels require this panel to run on Windows"}), 400

    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    protocol = data.get("protocol", "pptp")
    server = (data.get("server") or "").strip()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    psk = (data.get("psk") or "").strip() or None
    networks = (data.get("networks") or "").strip()

    if not name or not server or not username:
        return jsonify({"error": "Name, server address and username are required"}), 400
    if protocol not in ("pptp", "l2tp"):
        return jsonify({"error": "Invalid protocol"}), 400

    conns = load_vpn_connections()
    if any(c["name"] == name for c in conns):
        return jsonify({"error": "A tunnel with this name already exists"}), 400

    ok, out, err = create_windows_vpn(name, protocol, server, psk)
    if not ok:
        return jsonify({"error": f"Failed to create VPN connection: {err or out}"}), 500

    entry = {
        "id": str(uuid.uuid4()),
        "name": name, "protocol": protocol, "server": server,
        "username": username, "password": password, "psk": psk,
        "networks": networks,
    }
    conns.append(entry)
    save_vpn_connections(conns)
    return jsonify({"success": True, "id": entry["id"]})


@app.route("/api/vpn/edit", methods=["POST"])
def vpn_edit():
    if not is_windows():
        return jsonify({"error": "VPN tunnels require this panel to run on Windows"}), 400

    data = request.get_json(force=True, silent=True) or {}
    conn_id = data.get("id")
    conns = load_vpn_connections()
    entry = next((c for c in conns if c["id"] == conn_id), None)
    if not entry:
        return jsonify({"error": "Tunnel not found"}), 404

    new_name = (data.get("name") or entry["name"]).strip()
    new_protocol = data.get("protocol", entry["protocol"])
    new_server = (data.get("server") or entry["server"]).strip()
    new_username = (data.get("username") or entry["username"]).strip()
    new_password = data.get("password", entry["password"])
    new_psk = (data.get("psk") or "").strip() or None
    new_networks = data.get("networks", entry.get("networks", "")).strip()

    needs_recreate = (
        new_name != entry["name"] or new_protocol != entry["protocol"]
        or new_server != entry["server"] or new_psk != entry.get("psk")
    )

    if needs_recreate:
        remove_vpn_routes(entry["name"], parse_networks(entry.get("networks", "")))
        remove_windows_vpn(entry["name"])
        ok, out, err = create_windows_vpn(new_name, new_protocol, new_server, new_psk)
        if not ok:
            return jsonify({"error": f"Failed to update VPN connection: {err or out}"}), 500

    entry.update({
        "name": new_name, "protocol": new_protocol, "server": new_server,
        "username": new_username, "password": new_password, "psk": new_psk,
        "networks": new_networks,
    })
    save_vpn_connections(conns)
    return jsonify({"success": True})


@app.route("/api/vpn/delete", methods=["POST"])
def vpn_delete():
    data = request.get_json(force=True, silent=True) or {}
    conn_id = data.get("id")
    conns = load_vpn_connections()
    entry = next((c for c in conns if c["id"] == conn_id), None)
    if not entry:
        return jsonify({"error": "Tunnel not found"}), 404

    if is_windows():
        remove_vpn_routes(entry["name"], parse_networks(entry.get("networks", "")))
        remove_windows_vpn(entry["name"])

    conns = [c for c in conns if c["id"] != conn_id]
    save_vpn_connections(conns)
    return jsonify({"success": True})


@app.route("/api/vpn/connect", methods=["POST"])
def vpn_connect():
    if not is_windows():
        return jsonify({"error": "VPN tunnels require this panel to run on Windows"}), 400

    data = request.get_json(force=True, silent=True) or {}
    conn_id = data.get("id")
    conns = load_vpn_connections()
    entry = next((c for c in conns if c["id"] == conn_id), None)
    if not entry:
        return jsonify({"error": "Tunnel not found"}), 404

    success, output = rasdial_connect(entry["name"], entry["username"], entry["password"])
    if not success:
        status = get_windows_vpn_status(entry["name"])
        return jsonify({"error": f"Connection failed: {output}", "status": status}), 500

        # Give the virtual adapter a moment to come up before routing through it
    time.sleep(1.5)

    networks = parse_networks(entry.get("networks", ""))
    route_results = []
    route_warning = None

    if networks:
        if not is_admin():
            route_warning = (
                "Connected, but routes for the remote network(s) were NOT added: "
                "this panel isn't running as Administrator, and changing the "
                "routing table (New-NetRoute) requires elevation. Close this, "
                "re-open your terminal with \"Run as Administrator\", and run "
                "python app.py again, then reconnect this tunnel."
            )
        else:
            route_results = add_vpn_routes(entry["name"], networks)

    status = get_windows_vpn_status(entry["name"])
    return jsonify({
        "success": True, "status": status,
        "routes": route_results, "warning": route_warning,
    })


@app.route("/api/vpn/disconnect", methods=["POST"])
def vpn_disconnect():
    if not is_windows():
        return jsonify({"error": "VPN tunnels require this panel to run on Windows"}), 400

    data = request.get_json(force=True, silent=True) or {}
    conn_id = data.get("id")
    conns = load_vpn_connections()
    entry = next((c for c in conns if c["id"] == conn_id), None)
    if not entry:
        return jsonify({"error": "Tunnel not found"}), 404

    remove_vpn_routes(entry["name"], parse_networks(entry.get("networks", "")))
    success, output = rasdial_disconnect(entry["name"])
    status = get_windows_vpn_status(entry["name"])
    return jsonify({"success": success, "status": status, "message": output})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)