// ── Text Messages Widget ────────────────────────────────────────
// Separate card on the scan page. Reads the local macOS Messages
// database (~/Library/Messages/chat.db), embeds each message on-device
// with bge-m3, and stores it in the local vector database for semantic
// search. Raw text never leaves the machine; only redacted excerpts are
// sent to the cloud at answer time.
//
// Requires Full Disk Access (System Settings > Privacy & Security).
//
//   initMessagesWidget(containerId)

function initMessagesWidget(containerId) {
    var container = document.getElementById(containerId);
    if (!container) return;

    var prefix = 'msg-';
    function el(id) { return document.getElementById(prefix + id); }

    container.innerHTML =
        '<div id="' + prefix + 'step-main" class="card">' +
            '<h2>Scan Text Messages</h2>' +
            '<div id="' + prefix + 'main-body">' +
                '<div class="spinner" style="margin:12px 0"></div>' +
            '</div>' +
        '</div>' +

        '<div id="' + prefix + 'step-scanning" class="card" style="display:none">' +
            '<h2>Scanning Messages...</h2>' +
            '<p id="' + prefix + 'scan-stage" style="color:var(--text-muted);margin-bottom:8px">Starting...</p>' +
            '<div class="scan-progress-bar"><div id="' + prefix + 'scan-bar" class="scan-progress-fill"></div></div>' +
            '<p id="' + prefix + 'scan-detail" style="color:var(--text-muted);font-size:13px;margin-top:8px"></p>' +
        '</div>' +

        '<div id="' + prefix + 'step-results" class="card" style="display:none">' +
            '<h2>Done</h2>' +
            '<div id="' + prefix + 'results-summary"></div>' +
            '<div style="margin-top:20px">' +
                '<a href="/chat" class="btn-primary" style="text-decoration:none">Ask a question</a>' +
                '<button id="' + prefix + 'btn-back" class="btn-secondary" style="margin-left:8px">Back</button>' +
            '</div>' +
        '</div>';

    // ── Main view ───────────────────────────────────────────────
    function renderMain(status, errorMsg) {
        var last = status && status.last_sync
            ? new Date(status.last_sync).toLocaleString() : 'never';
        var total = (status && status.total_messages) || 0;
        var hasAccess = !status || status.full_disk_access;  // assume ok if unknown

        var intro =
            (errorMsg ? '<div style="color:var(--red);margin-bottom:12px">' + esc(errorMsg) + '</div>' : '') +
            '<p style="color:var(--text-muted);margin-bottom:16px">' +
                'Jar reads your Mac\'s Messages history (iMessage &amp; SMS) so you can ask ' +
                'about appointments, orders, and plans buried in your texts. Everything is ' +
                'embedded on-device; only redacted excerpts ever reach the cloud.' +
            '</p>';

        el('main-body').innerHTML = intro +
            (hasAccess ? accessGrantedHtml(status, last, total) : accessNeededHtml(status));

        if (hasAccess) {
            el('btn-scan').addEventListener('click', startScan);
            if (el('btn-sync')) el('btn-sync').addEventListener('click', runSync);
        } else {
            el('btn-open-settings').addEventListener('click', openSettings);
            el('btn-recheck').addEventListener('click', loadStatus);
        }
    }

    // Full Disk Access NOT granted — guide the user to enable it.
    function accessNeededHtml(status) {
        var exe = (status && status.runtime_executable) || '';
        return '' +
            '<div style="background:rgba(255,90,90,0.08);border:1px solid var(--red);border-radius:6px;padding:14px;margin-bottom:16px">' +
                '<div style="font-weight:600;margin-bottom:8px">Full Disk Access needed</div>' +
                '<p style="color:var(--text-muted);font-size:13px;margin-bottom:10px">' +
                    'macOS protects your messages, so Jar can\'t read them until you grant ' +
                    '<strong>Full Disk Access</strong>. Jar can\'t flip this switch for you ' +
                    '(macOS requires you to do it), but the button below opens the right settings pane.' +
                '</p>' +
                '<ol style="color:var(--text-muted);font-size:13px;margin:0 0 12px 18px;padding:0">' +
                    '<li>Click <strong>Open Full Disk Access Settings</strong> below.</li>' +
                    '<li>Turn on the toggle for the app running Jar' +
                        (exe ? ' (this build runs as <code style="font-size:12px">' + esc(exe) + '</code> — for a terminal, enable <strong>Terminal</strong> or <strong>iTerm</strong>)' : '') +
                        '. Use the <strong>+</strong> button to add it if it isn\'t listed.</li>' +
                    '<li><strong>Quit and reopen</strong> the app — macOS only applies the change on the next launch.</li>' +
                    '<li>Come back and click <strong>Re-check</strong>.</li>' +
                '</ol>' +
                '<button id="' + prefix + 'btn-open-settings" class="btn-primary">Open Full Disk Access Settings</button>' +
                '<button id="' + prefix + 'btn-recheck" class="btn-secondary" style="margin-left:8px">Re-check</button>' +
            '</div>';
    }

    // Full Disk Access granted — show the scan controls.
    function accessGrantedHtml(status, last, total) {
        return '' +
            '<div class="scan-stats" style="margin-bottom:16px">' +
                '<div class="scan-stat"><span>' + total + '</span> messages embedded</div>' +
                '<div class="scan-stat" style="font-size:13px;color:var(--text-muted)">Last sync: ' + esc(last) + '</div>' +
            '</div>' +
            '<div style="margin-bottom:14px">' +
                '<label style="display:block;font-size:13px;color:var(--text-muted);margin-bottom:6px">How far back to scan</label>' +
                '<select id="' + prefix + 'days-select" style="padding:6px 10px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-size:13px;min-width:220px">' +
                    '<option value="30" selected>Last 30 days (fastest to try)</option>' +
                    '<option value="90">Last 90 days</option>' +
                    '<option value="365">Last year</option>' +
                    '<option value="1095">Last 3 years</option>' +
                    '<option value="">All messages (slowest)</option>' +
                '</select>' +
            '</div>' +
            '<button id="' + prefix + 'btn-scan" class="btn-primary">Start Scan</button>' +
            (status && status.last_sync
                ? '<button id="' + prefix + 'btn-sync" class="btn-secondary" style="margin-left:8px">Sync New</button>'
                : '');
    }

    function openSettings() {
        var btn = el('btn-open-settings');
        btn.disabled = true;
        var orig = btn.textContent;
        btn.textContent = 'Opening...';
        fetchJSON('/api/messages/open-settings', { method: 'POST' })
            .catch(function(e) {
                renderMain(null, 'Could not open System Settings: ' + e.message +
                    '. Open it manually: System Settings > Privacy & Security > Full Disk Access.');
            })
            .then(function() {
                if (btn) { btn.disabled = false; btn.textContent = orig; }
            });
    }

    function loadStatus() {
        fetchJSON('/api/messages/status').then(function(status) {
            renderMain(status);
        }).catch(function(e) {
            renderMain(null, 'Error checking status: ' + e.message);
        });
    }

    loadStatus();

    // ── Start scan (full, bounded by days_back) ─────────────────
    function startScan() {
        var days = el('days-select') ? el('days-select').value : '';
        var url = '/api/messages/scan';
        if (days) url += '?days_back=' + encodeURIComponent(days);
        runJob(url, 'Reading messages and embedding on-device...');
    }

    function runSync() {
        runJob('/api/messages/sync', 'Syncing new messages...');
    }

    // Both endpoints are plain JSON POSTs (no streaming). Show an
    // indeterminate bar while the request runs.
    function runJob(url, stageText) {
        el('step-main').style.display = 'none';
        el('step-scanning').style.display = '';
        el('scan-stage').textContent = stageText;
        el('scan-detail').textContent = 'This can take a while on the first run.';
        el('scan-bar').style.width = '60%';
        el('scan-bar').style.opacity = '0.6';

        fetchJSON(url, { method: 'POST' }).then(function(summary) {
            el('scan-bar').style.opacity = '1';
            el('scan-bar').style.width = '100%';
            showResults(summary || {});
        }).catch(function(e) {
            el('scan-bar').style.opacity = '1';
            showScanError(e.message);
        });
    }

    function showScanError(msg) {
        el('step-scanning').style.display = 'none';
        el('step-results').style.display = '';
        var hint = /Full Disk Access/i.test(msg)
            ? '<p style="color:var(--text-muted);font-size:13px;margin-top:10px">' +
              'Open System Settings &rarr; Privacy &amp; Security &rarr; Full Disk Access, ' +
              'enable this app, then restart it and try again.</p>'
            : '';
        el('results-summary').innerHTML =
            '<div style="color:var(--red)">' + esc(msg) + '</div>' + hint;
    }

    function showResults(s) {
        el('step-scanning').style.display = 'none';
        el('step-results').style.display = '';

        var stored = s.stored || 0;
        var html = '<div class="results-stats">';
        html += step('stored', stored > 0 ? 'ok' : 'warning',
            stored + ' message' + (stored !== 1 ? 's' : '') + ' embedded and stored');
        if (s.full_rescan) html += step('mode', 'ok', 'Full rescan (cursor reset)');
        html += '</div>';
        html += '<p style="margin-top:14px;color:var(--text-muted);font-size:13px">' +
            'You can now ask Jar about your texts in the chat.' +
            '</p>';
        el('results-summary').innerHTML = html;
    }

    el('btn-back').addEventListener('click', function() {
        el('step-results').style.display = 'none';
        el('step-main').style.display = '';
        loadStatus();
    });
}
