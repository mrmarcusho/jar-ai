// ── Email Inbox Widget ──────────────────────────────────────────
// Separate card on the scan page. Connects a mailbox over IMAP (Gmail
// needs an App Password, not the account password), then scans it for
// financial emails. Settled money events land in the transactions
// ledger; appointments / notices / obligations land in documents.
// Cloud (Claude) only ever sees PII-redacted text.
//
//   initEmailWidget(containerId)

function initEmailWidget(containerId) {
    var container = document.getElementById(containerId);
    if (!container) return;

    var prefix = 'em-';
    function el(id) { return document.getElementById(prefix + id); }

    container.innerHTML =
        // Step 1: Connect / connected options
        '<div id="' + prefix + 'step-connect" class="card">' +
            '<h2>Scan Email Inbox</h2>' +
            '<div id="' + prefix + 'connect-body">' +
                '<div class="spinner" style="margin:12px 0"></div>' +
            '</div>' +
        '</div>' +

        // Step 2: Scanning
        '<div id="' + prefix + 'step-scanning" class="card" style="display:none">' +
            '<h2>Scanning Inbox...</h2>' +
            '<p id="' + prefix + 'scan-stage" style="color:var(--text-muted);margin-bottom:8px">Starting...</p>' +
            '<div class="scan-progress-bar"><div id="' + prefix + 'scan-bar" class="scan-progress-fill"></div></div>' +
            '<p id="' + prefix + 'scan-detail" style="color:var(--text-muted);font-size:13px;margin-top:8px"></p>' +
        '</div>' +

        // Step 3: Results
        '<div id="' + prefix + 'step-results" class="card" style="display:none">' +
            '<h2>Done</h2>' +
            '<div id="' + prefix + 'results-summary"></div>' +
            '<div style="margin-top:20px">' +
                '<a href="/dashboard" class="btn-primary" style="text-decoration:none">Go to Dashboard</a>' +
                '<button id="' + prefix + 'btn-scan-again" class="btn-secondary" style="margin-left:8px">Back</button>' +
            '</div>' +
        '</div>';

    // ── Connection state ────────────────────────────────────────
    function renderDisconnected(errorMsg) {
        el('connect-body').innerHTML =
            (errorMsg ? '<div style="color:var(--red);margin-bottom:12px">' + esc(errorMsg) + '</div>' : '') +
            '<p style="color:var(--text-muted);margin-bottom:16px">' +
                'Connect your inbox and Jar will scan it for financial emails — receipts, ' +
                'statements, bills, and appointments. For Gmail you must use a ' +
                '<a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener">16-character App Password</a> ' +
                '(requires 2-Step Verification), not your normal password.' +
            '</p>' +
            '<div style="margin-bottom:12px">' +
                '<label style="display:block;font-size:13px;color:var(--text-muted);margin-bottom:6px">Email address</label>' +
                '<input id="' + prefix + 'in-username" type="email" autocomplete="username" placeholder="you@gmail.com" ' +
                    'style="padding:8px 10px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-size:14px;width:320px;max-width:100%">' +
            '</div>' +
            '<div style="margin-bottom:12px">' +
                '<label style="display:block;font-size:13px;color:var(--text-muted);margin-bottom:6px">App Password</label>' +
                '<input id="' + prefix + 'in-password" type="password" autocomplete="current-password" placeholder="xxxx xxxx xxxx xxxx" ' +
                    'style="padding:8px 10px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-size:14px;width:320px;max-width:100%">' +
            '</div>' +
            '<div id="' + prefix + 'advanced" style="margin-bottom:14px">' +
                '<a href="#" id="' + prefix + 'link-advanced" style="font-size:13px;color:var(--text-muted)">Advanced (non-Gmail IMAP host)</a>' +
                '<div id="' + prefix + 'advanced-fields" style="display:none;margin-top:10px">' +
                    '<input id="' + prefix + 'in-host" type="text" placeholder="imap.example.com" ' +
                        'style="padding:8px 10px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-size:14px;width:220px;margin-right:8px">' +
                    '<input id="' + prefix + 'in-port" type="number" placeholder="993" ' +
                        'style="padding:8px 10px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-size:14px;width:90px">' +
                '</div>' +
            '</div>' +
            '<button id="' + prefix + 'btn-connect" class="btn-primary">Connect</button>';

        el('link-advanced').addEventListener('click', function(e) {
            e.preventDefault();
            var f = el('advanced-fields');
            f.style.display = f.style.display === 'none' ? '' : 'none';
        });
        el('btn-connect').addEventListener('click', connect);
        el('in-password').addEventListener('keydown', function(e) {
            if (e.key === 'Enter') connect();
        });
    }

    function renderConnected(data) {
        var last = data.last_sync ? new Date(data.last_sync).toLocaleString() : 'never';
        el('connect-body').innerHTML =
            '<p style="color:var(--text-muted);margin-bottom:16px">' +
                'Connected as <strong>' + esc(data.username || '') + '</strong>. ' +
                '<a href="#" id="' + prefix + 'link-disconnect" style="color:var(--text-muted)">Disconnect</a>' +
            '</p>' +
            '<div class="scan-stats" style="margin-bottom:16px">' +
                '<div class="scan-stat"><span>' + (data.total_events || 0) + '</span> financial events</div>' +
                '<div class="scan-stat"><span>' + (data.total_documents || 0) + '</span> documents</div>' +
                '<div class="scan-stat" style="font-size:13px;color:var(--text-muted)">Last sync: ' + esc(last) + '</div>' +
            '</div>' +
            '<div style="margin-bottom:14px">' +
                '<label style="display:block;font-size:13px;color:var(--text-muted);margin-bottom:6px">How far back to scan</label>' +
                '<select id="' + prefix + 'days-select" style="padding:6px 10px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-size:13px;min-width:200px">' +
                    '<option value="30">Last 30 days</option>' +
                    '<option value="90">Last 90 days</option>' +
                    '<option value="365">Last year</option>' +
                    '<option value="1095" selected>Last 3 years</option>' +
                '</select>' +
            '</div>' +
            '<button id="' + prefix + 'btn-start-scan" class="btn-primary">Start Full Scan</button>' +
            (data.last_sync
                ? '<button id="' + prefix + 'btn-sync" class="btn-secondary" style="margin-left:8px">Sync New</button>'
                : '');

        el('link-disconnect').addEventListener('click', function(e) {
            e.preventDefault();
            fetchJSON('/api/email/disconnect', { method: 'DELETE' })
                .then(function() { renderDisconnected(); })
                .catch(function() {});
        });
        el('btn-start-scan').addEventListener('click', startScan);
        if (el('btn-sync')) el('btn-sync').addEventListener('click', runSync);
    }

    function loadStatus() {
        fetchJSON('/api/email/status').then(function(data) {
            if (data.connected) renderConnected(data);
            else renderDisconnected();
        }).catch(function(e) {
            renderDisconnected('Error checking status: ' + e.message);
        });
    }

    // ── Connect ─────────────────────────────────────────────────
    function connect() {
        var username = el('in-username').value.trim();
        var password = el('in-password').value;
        if (!username || !password) {
            renderDisconnected('Email address and App Password are required.');
            return;
        }
        var payload = { username: username, password: password };
        if (el('in-host') && el('in-host').value.trim()) payload.host = el('in-host').value.trim();
        if (el('in-port') && el('in-port').value.trim()) payload.port = parseInt(el('in-port').value.trim());

        var btn = el('btn-connect');
        btn.disabled = true;
        btn.textContent = 'Connecting...';

        fetchJSON('/api/email/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        }).then(function() {
            loadStatus();
        }).catch(function(e) {
            renderDisconnected(e.message);
        });
    }

    loadStatus();

    // ── Start scan ──────────────────────────────────────────────
    function startScan() {
        el('step-connect').style.display = 'none';
        el('step-scanning').style.display = '';
        el('scan-stage').textContent = 'Starting...';
        el('scan-detail').textContent = '';
        el('scan-bar').style.width = '0%';

        var days = el('days-select') ? el('days-select').value : '';
        var url = '/api/email/scan';
        if (days) url += '?days_back=' + encodeURIComponent(days);
        var es = new EventSource(url);

        es.onmessage = function(event) {
            var msg = JSON.parse(event.data);
            if (msg.type === 'progress') {
                el('scan-stage').textContent = stageLabel(msg.stage);
                if (msg.total) {
                    var pct = Math.round((msg.current / msg.total) * 100);
                    el('scan-bar').style.width = pct + '%';
                    el('scan-detail').textContent = (msg.message || '') + ' (' + msg.current + '/' + msg.total + ')';
                } else {
                    el('scan-detail').textContent = msg.message || '';
                }
            } else if (msg.type === 'done') {
                es.close();
                showResults(msg.summary || {});
            } else if (msg.type === 'error') {
                es.close();
                showScanError(msg.error || 'Scan failed.');
            }
        };

        es.onerror = function() {
            es.close();
            showScanError('Connection lost during scan.');
        };
    }

    function stageLabel(stage) {
        var labels = {
            connect: 'Connecting to mailbox...',
            envelopes: 'Fetching message headers...',
            triage: 'Triaging messages...',
            bodies: 'Fetching message bodies...',
            extract: 'Extracting financial data...',
            write: 'Saving...',
        };
        return labels[stage] || (stage || 'Working...');
    }

    function showScanError(msg) {
        el('step-scanning').style.display = 'none';
        el('step-results').style.display = '';
        el('results-summary').innerHTML =
            '<div style="color:var(--red)">' + esc(msg) + '</div>';
    }

    // ── Sync (incremental) ──────────────────────────────────────
    function runSync() {
        el('step-connect').style.display = 'none';
        el('step-scanning').style.display = '';
        el('scan-stage').textContent = 'Syncing new messages...';
        el('scan-detail').textContent = '';
        el('scan-bar').style.width = '50%';
        el('scan-bar').style.opacity = '0.6';

        fetchJSON('/api/email/sync', { method: 'POST' }).then(function(data) {
            el('scan-bar').style.opacity = '1';
            showResults(data.summary || {
                events_written: data.new_events,
                documents_written: data.new_documents,
            });
        }).catch(function(e) {
            el('scan-bar').style.opacity = '1';
            showScanError(e.message);
        });
    }

    // ── Results ─────────────────────────────────────────────────
    function showResults(s) {
        el('step-scanning').style.display = 'none';
        el('step-results').style.display = '';

        var events = s.events_written || 0;
        var docs = s.documents_written || 0;
        var attachments = s.attachments_routed || 0;
        var dupes = s.duplicates || 0;
        var skipped = s.skipped || 0;
        var errors = s.errors || 0;

        var html = '<div class="results-stats">';
        html += step('events', events > 0 ? 'ok' : 'warning',
            events + ' financial event' + (events !== 1 ? 's' : '') + ' added to your ledger');
        html += step('docs', 'ok',
            docs + ' document' + (docs !== 1 ? 's' : '') + ' (bills, appointments, notices)');
        if (attachments > 0) html += step('att', 'ok', attachments + ' PDF attachment' + (attachments !== 1 ? 's' : '') + ' extracted');
        if (dupes > 0) html += step('dup', 'warning', dupes + ' already imported (skipped)');
        if (skipped > 0) html += step('skip', 'warning', skipped + ' non-financial email' + (skipped !== 1 ? 's' : '') + ' ignored');
        if (errors > 0) html += step('err', 'error', errors + ' error' + (errors !== 1 ? 's' : '') + ' during scan');
        html += '</div>';

        if (typeof s.envelopes_fetched === 'number') {
            html += '<p style="margin-top:14px;color:var(--text-muted);font-size:13px">' +
                'Scanned ' + s.envelopes_fetched + ' message' + (s.envelopes_fetched !== 1 ? 's' : '') +
                (s.duration_seconds ? ' in ' + s.duration_seconds + 's' : '') + '.' +
                '</p>';
        }

        el('results-summary').innerHTML = html;
    }

    // ── Back ────────────────────────────────────────────────────
    el('btn-scan-again').addEventListener('click', function() {
        el('step-results').style.display = 'none';
        el('step-connect').style.display = '';
        loadStatus();
    });
}
