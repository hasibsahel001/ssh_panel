(() => {
  let currentMode = 'single';
  let lastResults = [];

  // ---------------- theme toggle ----------------
  // Note: theme is intentionally not persisted (no localStorage) so a reload
  // always starts from the default dark theme, same as the rest of the panel's state.
  const themeToggle = document.getElementById('theme-toggle');
  const themeIcon = document.getElementById('theme-icon');

  themeToggle.addEventListener('click', () => {
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    if (isLight) {
      document.documentElement.removeAttribute('data-theme');
      themeIcon.textContent = '☾';
      themeToggle.title = 'Switch to light theme';
    } else {
      document.documentElement.setAttribute('data-theme', 'light');
      themeIcon.textContent = '☀';
      themeToggle.title = 'Switch to dark theme';
    }
  });

  // ---------------- clock ----------------
  const clockEl = document.getElementById('clock');
  function tickClock(){
    const now = new Date();
    clockEl.textContent = now.toLocaleTimeString('en-US', {
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit',
      hour12: true
    });
  }
  tickClock();
  setInterval(tickClock, 1000);

  // ---------------- mode switch (single / range) ----------------
  const modeBtns = document.querySelectorAll('.layout > .config-panel .mode-switch .mode-btn');
  const modeSingle = document.getElementById('mode-single');
  const modeRange = document.getElementById('mode-range');

  modeBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      modeBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentMode = btn.dataset.mode;
      if (currentMode === 'single') {
        modeSingle.classList.remove('hidden');
        modeRange.classList.add('hidden');
      } else {
        modeRange.classList.remove('hidden');
        modeSingle.classList.add('hidden');
      }
    });
  });

  // ---------------- password toggle ----------------
  const pwInput = document.getElementById('password');
  document.getElementById('toggle-pw').addEventListener('click', () => {
    pwInput.type = pwInput.type === 'password' ? 'text' : 'password';
  });

  // ---------------- elements ----------------
  const runBtn = document.getElementById('run-btn');
  const statusMsg = document.getElementById('status-msg');
  const deviceGrid = document.getElementById('device-grid');
  const emptyState = document.getElementById('empty-state');
  const summaryBar = document.getElementById('summary-bar');
  const exportActions = document.getElementById('export-actions');
  const sumTotal = document.getElementById('sum-total');
  const sumOk = document.getElementById('sum-ok');
  const sumFail = document.getElementById('sum-fail');
  const sumTime = document.getElementById('sum-time');

  function setStatus(msg, type){
    statusMsg.textContent = msg || '';
    statusMsg.className = 'status-msg' + (type ? ' ' + type : '');
  }

  function snippetOf(text){
    if(!text) return '';
    const firstLine = text.split('\n').find(l => l.trim() && !l.startsWith('>'));
    return firstLine ? firstLine.trim() : text.trim().split('\n')[0];
  }

  function renderPending(ips){
    deviceGrid.innerHTML = '';
    emptyState.classList.add('hidden');
    summaryBar.classList.add('hidden');
    exportActions.classList.add('hidden');
    ips.forEach(ip => {
      const tile = document.createElement('div');
      tile.className = 'device-tile pending';
      tile.dataset.ip = ip;
      tile.innerHTML = `
        <span class="tile-ip">${ip}</span>
        <span class="tile-status">Connecting…</span>
      `;
      deviceGrid.appendChild(tile);
    });
  }

  function renderResults(results, summary, timestamp){
    lastResults = results;
    deviceGrid.innerHTML = '';
    results.forEach(r => {
      const tile = document.createElement('div');
      tile.className = 'device-tile ' + (r.success ? 'success' : 'failed');
      tile.dataset.ip = r.ip;
      const statusText = r.success ? 'Success' : 'Failed';
      const snippet = r.success ? snippetOf(r.output) : r.error;
      tile.innerHTML = `
        <span class="tile-ip">${r.ip}</span>
        <span class="tile-status">${statusText}</span>
        <div class="tile-snippet">${escapeHtml(snippet || '')}</div>
      `;
      tile.addEventListener('click', () => openModal(r));
      deviceGrid.appendChild(tile);
    });

    sumTotal.textContent = summary.total;
    sumOk.textContent = summary.success;
    sumFail.textContent = summary.failed;
    sumTime.textContent = timestamp;
    summaryBar.classList.remove('hidden');
    exportActions.classList.remove('hidden');
  }

  function escapeHtml(str){
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ---------------- modal ----------------
  const modal = document.getElementById('output-modal');
  const modalIp = document.getElementById('modal-ip');
  const modalOutput = document.getElementById('modal-output');

  function openModal(r){
    modalIp.textContent = r.ip + (r.success ? '  —  Success' : '  —  Failed');
    modalOutput.textContent = r.success ? (r.output || '(no output)') : ('Error: ' + r.error);
    modal.classList.remove('hidden');
  }
  document.getElementById('modal-close').addEventListener('click', () => modal.classList.add('hidden'));
  modal.addEventListener('click', (e) => { if (e.target === modal) modal.classList.add('hidden'); });

  // ---------------- run ----------------
  runBtn.addEventListener('click', async () => {
    const port = document.getElementById('port').value.trim() || '2288';
    const username = document.getElementById('username').value.trim();
    const password = document.getElementById('password').value;
    const commands = document.getElementById('commands').value;

    if (!username) { setStatus('Enter a username', 'error'); return; }
    if (!commands.trim()) { setStatus('Enter at least one command', 'error'); return; }

    const payload = { mode: currentMode, port, username, password, commands };

    if (currentMode === 'single') {
      const ip = document.getElementById('single-ip').value.trim();
      if (!ip) { setStatus('Enter an IP address', 'error'); return; }
      payload.ip = ip;
      renderPending([ip]);
    } else {
      const startIp = document.getElementById('start-ip').value.trim();
      const endIp = document.getElementById('end-ip').value.trim();
      if (!startIp || !endIp) { setStatus('Enter both Start IP and End IP', 'error'); return; }
      payload.start_ip = startIp;
      payload.end_ip = endIp;
      renderPending([`${startIp} → ${endIp}`]);
    }

    runBtn.disabled = true;
    setStatus('Connecting and running commands…', '');

    try {
      const res = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();

      if (!res.ok) {
        setStatus(data.error || 'Request failed', 'error');
        deviceGrid.innerHTML = '';
        emptyState.classList.remove('hidden');
        return;
      }

      renderResults(data.results, data.summary, data.timestamp);
      setStatus(`Done — ${data.summary.success} succeeded, ${data.summary.failed} failed`, 'ok');
    } catch (err) {
      setStatus('Could not reach the local server: ' + err.message, 'error');
    } finally {
      runBtn.disabled = false;
    }
  });

  // ---------------- exports ----------------
  document.querySelectorAll('.export-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!lastResults.length) return;
      const type = btn.dataset.type;
      try {
        const res = await fetch(`/api/export/${type}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ results: lastResults })
        });
        if (!res.ok) { setStatus('Could not generate the export file', 'error'); return; }
        const blob = await res.blob();
        const disposition = res.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename="?([^"]+)"?/);
        const filename = match ? match[1] : `mikrotik_output.${type}`;
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
      } catch (err) {
        setStatus('Download failed: ' + err.message, 'error');
      }
    });
  });

  // =========================================================================
  // PING MODAL
  // =========================================================================
  const pingModal = document.getElementById('ping-modal');
  const pingTriggerBtn = document.getElementById('ping-trigger-btn');
  const pingModalClose = document.getElementById('ping-modal-close');
  const pingClearBtn = document.getElementById('ping-clear-btn');
  const pingResultsEl = document.getElementById('ping-results');
  const pingSummaryEl = document.getElementById('ping-summary');
  const pingStartBtn = document.getElementById('ping-start-btn');
  const pingIpInput = document.getElementById('ping-ip');
  const pingCountInput = document.getElementById('ping-count');

  let pingSource = null;

  function resetPingBtn(){
    pingStartBtn.disabled = false;
    pingStartBtn.querySelector('.run-btn-text').textContent = 'Start Ping';
  }

  function clearPingResults(){
    if (pingSource) { pingSource.close(); pingSource = null; }
    pingResultsEl.innerHTML = '';
    pingSummaryEl.classList.add('hidden');
    pingSummaryEl.innerHTML = '';
    pingIpInput.value = '';
    pingCountInput.value = '10';
    resetPingBtn();
  }

  pingTriggerBtn.addEventListener('click', () => {
    pingModal.classList.remove('hidden');
  });

  function closePingModal(){
    pingModal.classList.add('hidden');
    clearPingResults();
  }
  pingModalClose.addEventListener('click', closePingModal);
  pingClearBtn.addEventListener('click', closePingModal);
  pingModal.addEventListener('click', (e) => { if (e.target === pingModal) closePingModal(); });

  pingStartBtn.addEventListener('click', () => {
    const ip = pingIpInput.value.trim();
    let count = parseInt(pingCountInput.value, 10);
    if (!ip) { setStatus('Enter a target IP to ping', 'error'); return; }
    if (!count || count < 1) count = 10;
    if (count > 100) count = 100;

    pingResultsEl.innerHTML = '';
    pingSummaryEl.classList.add('hidden');
    pingSummaryEl.innerHTML = '';

    if (pingSource) pingSource.close();

    pingStartBtn.disabled = true;
    pingStartBtn.querySelector('.run-btn-text').textContent = 'Pinging…';

    pingSource = new EventSource(`/api/ping/stream?ip=${encodeURIComponent(ip)}&count=${count}`);

    pingSource.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.error) {
        setStatus(data.error, 'error');
        pingSource.close();
        resetPingBtn();
        return;
      }

      if (data.done) {
        pingSummaryEl.classList.remove('hidden');
        pingSummaryEl.innerHTML = `
          <span>Sent: <b>${data.sent}</b></span>
          <span>Received: <b>${data.received}</b></span>
          <span>Loss: <b>${data.loss_pct}%</b></span>
          <span>Avg: <b>${data.avg_rtt ?? '-'} ms</b></span>
          <span>Min/Max: <b>${data.min_rtt ?? '-'} / ${data.max_rtt ?? '-'} ms</b></span>
        `;
        pingSource.close();
        resetPingBtn();
        return;
      }

      const row = document.createElement('div');
      row.className = 'ping-row ' + (data.success ? 'ok' : 'fail');
      row.innerHTML = `
        <span class="seq">#${data.seq} — ${data.ip}</span>
        <span class="rtt">${data.success ? data.rtt + ' ms' : 'timeout'}</span>
      `;
      pingResultsEl.appendChild(row);
      pingResultsEl.scrollTop = pingResultsEl.scrollHeight;
    };

    pingSource.onerror = () => {
      setStatus('Ping stream connection lost', 'error');
      pingSource.close();
      resetPingBtn();
    };
  });

  // =========================================================================
  // VPN TUNNEL MODAL
  // =========================================================================
  const vpnModal = document.getElementById('vpn-modal');
  const vpnTriggerBtn = document.getElementById('vpn-trigger-btn');
  const vpnModalClose = document.getElementById('vpn-modal-close');
  const vpnListEl = document.getElementById('vpn-list');
  const vpnEmptyHint = document.getElementById('vpn-empty-hint');
  const vpnOsWarning = document.getElementById('vpn-os-warning');
  const vpnNewBtn = document.getElementById('vpn-new-btn');
  const vpnForm = document.getElementById('vpn-form');
  const vpnFormTitle = document.getElementById('vpn-form-title');
  const vpnIdInput = document.getElementById('vpn-id');
  const vpnNameInput = document.getElementById('vpn-name');
  const vpnServerInput = document.getElementById('vpn-server');
  const vpnUsernameInput = document.getElementById('vpn-username');
  const vpnPasswordInput = document.getElementById('vpn-password');
  const vpnPskInput = document.getElementById('vpn-psk');
  const vpnPskField = document.getElementById('vpn-psk-field');
  const vpnNetworksInput = document.getElementById('vpn-networks');
  const vpnSaveBtn = document.getElementById('vpn-save-btn');
  const vpnCancelBtn = document.getElementById('vpn-cancel-btn');
  const vpnStatusMsg = document.getElementById('vpn-status-msg');

  let vpnProtocol = 'pptp';

  function setVpnStatus(msg, type){
    vpnStatusMsg.textContent = msg || '';
    vpnStatusMsg.className = 'status-msg' + (type ? ' ' + type : '');
  }

  const vpnProtocolBtns = document.querySelectorAll('#vpn-protocol-switch .mode-btn');
  vpnProtocolBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      vpnProtocolBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      vpnProtocol = btn.dataset.protocol;
      vpnPskField.classList.toggle('hidden', vpnProtocol !== 'l2tp');
    });
  });

  function openVpnModal(){
    vpnModal.classList.remove('hidden');
    hideVpnForm();
    loadVpnList();
  }
  function closeVpnModal(){
    vpnModal.classList.add('hidden');
    hideVpnForm();
    setVpnStatus('', '');
  }
  vpnTriggerBtn.addEventListener('click', openVpnModal);
  vpnModalClose.addEventListener('click', closeVpnModal);
  vpnModal.addEventListener('click', (e) => { if (e.target === vpnModal) closeVpnModal(); });

  function showVpnForm(entry){
    vpnForm.classList.remove('hidden');
    if (entry) {
      vpnFormTitle.textContent = 'Edit Tunnel';
      vpnIdInput.value = entry.id;
      vpnNameInput.value = entry.name;
      vpnServerInput.value = entry.server;
      vpnUsernameInput.value = entry.username;
      vpnPasswordInput.value = '';
      vpnPasswordInput.placeholder = 'Leave blank to keep current password';
      vpnPskInput.value = '';
      vpnNetworksInput.value = entry.networks || '';
      vpnProtocol = entry.protocol;
    } else {
      vpnFormTitle.textContent = 'New Tunnel';
      vpnIdInput.value = '';
      vpnNameInput.value = '';
      vpnServerInput.value = '';
      vpnUsernameInput.value = '';
      vpnPasswordInput.value = '';
      vpnPasswordInput.placeholder = '••••••••';
      vpnPskInput.value = '';
      vpnNetworksInput.value = '';
      vpnProtocol = 'pptp';
    }
    vpnProtocolBtns.forEach(b => b.classList.toggle('active', b.dataset.protocol === vpnProtocol));
    vpnPskField.classList.toggle('hidden', vpnProtocol !== 'l2tp');
  }
  function hideVpnForm(){
    vpnForm.classList.add('hidden');
  }

  vpnNewBtn.addEventListener('click', () => showVpnForm(null));
  vpnCancelBtn.addEventListener('click', hideVpnForm);

  async function loadVpnList(){
    try {
      const res = await fetch('/api/vpn/list');
      const data = await res.json();

      vpnOsWarning.classList.toggle('hidden', !!data.windows);

      const conns = data.connections || [];
      vpnEmptyHint.classList.toggle('hidden', conns.length > 0);
      vpnListEl.innerHTML = '';

      conns.forEach(c => {
        const isConnected = (c.status || '').toLowerCase() === 'connected';
        const row = document.createElement('div');
        row.className = 'vpn-row';
        row.innerHTML = `
          <div class="vpn-row-info">
            <div class="vpn-row-name">
              ${escapeHtml(c.name)}
              <span class="vpn-badge">${c.protocol.toUpperCase()}</span>
            </div>
            <div class="vpn-row-meta">${escapeHtml(c.server)} — ${escapeHtml(c.username)}</div>
            ${c.networks ? `<div class="vpn-row-meta">→ ${escapeHtml(c.networks)}</div>` : ''}
            <div class="vpn-status ${isConnected ? 'connected' : 'disconnected'}">
              <span class="vpn-status-dot"></span>${c.status}
            </div>
          </div>
          <div class="vpn-row-actions">
            <button class="vpn-icon-btn vpn-toggle-btn" type="button">${isConnected ? 'Disconnect' : 'Connect'}</button>
            <button class="vpn-icon-btn vpn-edit-btn" type="button">Edit</button>
            <button class="vpn-icon-btn danger vpn-delete-btn" type="button">Delete</button>
          </div>
        `;

        row.querySelector('.vpn-toggle-btn').addEventListener('click', () => toggleVpnConnection(c, isConnected));
        row.querySelector('.vpn-edit-btn').addEventListener('click', () => showVpnForm(c));
        row.querySelector('.vpn-delete-btn').addEventListener('click', () => deleteVpnConnection(c));

        vpnListEl.appendChild(row);
      });
    } catch (err) {
      setVpnStatus('Could not load saved tunnels: ' + err.message, 'error');
    }
  }

  async function toggleVpnConnection(entry, isConnected){
    setVpnStatus(isConnected ? `Disconnecting "${entry.name}"…` : `Connecting to "${entry.name}"…`, '');
    try {
      const res = await fetch(`/api/vpn/${isConnected ? 'disconnect' : 'connect'}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: entry.id })
      });
      const data = await res.json();
      if (!res.ok) {
        setVpnStatus(data.error || 'VPN action failed', 'error');
      } else if (!isConnected && data.warning) {
        setVpnStatus(data.warning, 'error');
      } else if (!isConnected && Array.isArray(data.routes) && data.routes.length) {
        const failed = data.routes.filter(r => !r.ok);
        if (failed.length) {
          const details = failed.map(r => `${r.network} (${r.error || 'failed'})`).join('; ');
          setVpnStatus(`Connected, but ${failed.length} route(s) failed — ${details}`, 'error');
        } else {
          setVpnStatus(`Connected to "${entry.name}" — routes added for ${data.routes.map(r => r.network).join(', ')}`, 'ok');
        }
      } else {
        setVpnStatus(isConnected ? `Disconnected from "${entry.name}"` : `Connected to "${entry.name}"`, 'ok');
      }
    } catch (err) {
      setVpnStatus('Could not reach the local server: ' + err.message, 'error');
    }
    loadVpnList();
  }

  async function deleteVpnConnection(entry){
    if (!confirm(`Delete the tunnel "${entry.name}"? This also removes it from Windows.`)) return;
    try {
      const res = await fetch('/api/vpn/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: entry.id })
      });
      const data = await res.json();
      if (!res.ok) {
        setVpnStatus(data.error || 'Could not delete tunnel', 'error');
      } else {
        setVpnStatus(`Deleted "${entry.name}"`, 'ok');
      }
    } catch (err) {
      setVpnStatus('Could not reach the local server: ' + err.message, 'error');
    }
    loadVpnList();
  }

  vpnSaveBtn.addEventListener('click', async () => {
    const id = vpnIdInput.value;
    const payload = {
      name: vpnNameInput.value.trim(),
      protocol: vpnProtocol,
      server: vpnServerInput.value.trim(),
      username: vpnUsernameInput.value.trim(),
      password: vpnPasswordInput.value,
      psk: vpnPskInput.value.trim(),
      networks: vpnNetworksInput.value.trim(),
    };

    if (!payload.name || !payload.server || !payload.username) {
      setVpnStatus('Name, server address and username are required', 'error');
      return;
    }

    const isEdit = !!id;
    if (isEdit) payload.id = id;

    vpnSaveBtn.disabled = true;
    setVpnStatus(isEdit ? 'Updating tunnel…' : 'Creating tunnel…', '');

    try {
      const res = await fetch(`/api/vpn/${isEdit ? 'edit' : 'add'}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) {
        setVpnStatus(data.error || 'Could not save tunnel', 'error');
      } else {
        setVpnStatus(isEdit ? 'Tunnel updated' : 'Tunnel added', 'ok');
        hideVpnForm();
        loadVpnList();
      }
    } catch (err) {
      setVpnStatus('Could not reach the local server: ' + err.message, 'error');
    } finally {
      vpnSaveBtn.disabled = false;
    }
  });
})();