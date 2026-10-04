# MikroTik Control Panel (Local)

A web panel that runs on your own machine and connects over **SSH** to one or
many MikroTik devices, runs the RouterOS commands you type, shows the output
per device, and lets you export the results as **CSV**, **Excel (xlsx)**, and
**TXT**.

## Features

- **Single IP**: connect to one device with IP, port, username and password.
- **Multiple IPs (range)**: enter a Start IP and End IP — commands run **in
  parallel** (many connections at once) across every IP in the range, so
  execution stays fast.
- Connection fields (username/password) stay filled in after a run, so you
  can re-run quickly without retyping them. A page reload always starts from
  a clean slate — nothing is saved to disk or browser storage.
- Dark / light theme toggle in the header.
- Results shown as status cards (Success / Failed) with full output in a
  popup for each device.
- One-click export of all results to CSV, Excel and TXT.
- Up to 500 IPs per range and up to 40 concurrent connections (configurable
  in `app.py`).

## Requirements

- Python 3.9 or newer
- MikroTik devices must have the **SSH** service enabled. Default port used
  by this panel is **2288** — set it under `IP > Services` in Winbox (or
  keep it on 22 and just type 22 in the Port field).

## Install & run

```bash
cd mikrotik_panel
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
python app.py
```

Then open your browser at:

```
http://127.0.0.1:5000
```

## Security notes

- This app is built to run **locally only** (`host="127.0.0.1"`). Do not
  expose it on a public network — device passwords are sent from the browser
  to this local server in plain JSON over the POST request, which is only
  safe on localhost.
- Passwords are never written to disk; they're only used in memory while a
  run is in progress.
- Paramiko's `AutoAddPolicy` is used to accept host keys automatically, so
  you don't need a pre-populated `known_hosts` file. Tighten this in
  `app.py` if you need stricter host-key verification.

## Entering commands

Type each command on its own line, exactly as you would in a MikroTik
terminal, e.g.:

```
/system identity print
/ip address print
/interface print
```

## Project structure

```
mikrotik_panel/
├── app.py                 # Flask backend + SSH connection + export
├── requirements.txt
├── templates/
│   └── index.html
└── static/
    ├── style.css
    └── script.js
```
