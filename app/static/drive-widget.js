// ── Google Drive Widget ─────────────────────────────────────────
// Separate card on the scan page. Connects a Google Drive, scans the
// whole Drive or a chosen folder for bank-statement PDFs, then runs the
// selected files through the same cloud-extraction pipeline as the local
// scanner (server downloads them first).
//
//   initDriveWidget(containerId)

function initDriveWidget(containerId) {
    var container = document.getElementById(containerId);
    if (!container) return;

    var prefix = 'dr-';
    function el(id) { return document.getElementById(prefix + id); }

    var candidates = [];

    container.innerHTML =
        // Step 1: Connect / connected options
        '<div id="' + prefix + 'step-connect" class="card">' +
            '<h2>Scan Google Drive</h2>' +
            '<div id="' + prefix + 'connect-body">' +
                '<div class="spinner" style="margin:12px 0"></div>' +
            '</div>' +
        '</div>' +

        // Step 2: Scanning
        '<div id="' + prefix + 'step-scanning" class="card" style="display:none">' +
            '<h2>Scanning Drive...</h2>' +
            '<div class="scan-stats">' +
                '<div class="scan-stat"><span id="' + prefix + 'stat-pdfs">0</span> PDFs found</div>' +
            '</div>' +
            '<div class="scan-progress-bar"><div id="' + prefix + 'scan-bar" class="scan-progress-fill"></div></div>' +
        '</div>' +

        // Step 3: Review
        '<div id="' + prefix + 'step-review" class="card" style="display:none">' +
            '<h2>Review Candidates</h2>' +
            '<p id="' + prefix + 'review-summary" style="color:var(--text-muted);margin-bottom:16px"></p>' +
            '<div id="' + prefix + 'no-candidates" style="display:none;text-align:center;padding:30px;color:var(--text-muted)">' +
                'No bank statement candidates found in your Drive.' +
            '</div>' +
            '<div class="table-wrap">' +
                '<table id="' + prefix + 'candidates-table" style="display:none">' +
                    '<thead><tr>' +
                        '<th style="width:36px"><input type="checkbox" id="' + prefix + 'select-all"></th>' +
                        '<th>File</th><th>Confidence</th><th>Bank Hint</th>' +
                    '</tr></thead>' +
                    '<tbody id="' + prefix + 'candidates-body"></tbody>' +
                '</table>' +
            '</div>' +
            '<div id="' + prefix + 'review-actions" style="margin-top:16px;display:none">' +
                '<button id="' + prefix + 'btn-process" class="btn-primary">Process Selected</button>' +
                '<span id="' + prefix + 'selected-count" style="margin-left:12px;font-size:13px;color:var(--text-muted)">0 selected</span>' +
            '</div>' +
        '</div>' +

        // Step 4: Processing
        '<div id="' + prefix + 'step-processing" class="card" style="display:none">' +
            '<h2>Processing Statements</h2>' +
            '<div id="' + prefix + 'process-progress"></div>' +
        '</div>' +

        // Step 5: Results
        '<div id="' + prefix + 'step-results" class="card" style="display:none">' +
            '<h2>Done</h2>' +
            '<div id="' + prefix + 'results-summary"></div>' +
            '<div style="margin-top:20px">' +
                '<a href="/dashboard" class="btn-primary" style="text-decoration:none">Go to Dashboard</a>' +
                '<button id="' + prefix + 'btn-scan-again" class="btn-secondary" style="margin-left:8px">Scan Again</button>' +
            '</div>' +
        '</div>';

    // ── Connection state ────────────────────────────────────────
    function showError(msg) {
        el('connect-body').innerHTML =
            '<div style="color:var(--red);margin-bottom:12px">' + esc(msg) + '</div>' +
            '<button id="' + prefix + 'btn-connect" class="btn-primary">Connect Google Drive</button>';
        bindConnect();
    }

    function renderDisconnected() {
        el('connect-body').innerHTML =
            '<p style="color:var(--text-muted);margin-bottom:16px">' +
                'Connect your Google Drive and Jar will scan it for PDF bank statements. ' +
                'Selected files are downloaded locally, then extracted the same way as local files.' +
            '</p>' +
            '<button id="' + prefix + 'btn-connect" class="btn-primary">Connect Google Drive</button>';
        bindConnect();
    }

    function renderConnected(email) {
        el('connect-body').innerHTML =
            '<p style="color:var(--text-muted);margin-bottom:16px">' +
                'Connected' + (email ? ' as <strong>' + esc(email) + '</strong>' : '') + '. ' +
                '<a href="#" id="' + prefix + 'link-disconnect" style="color:var(--text-muted)">Disconnect</a>' +
            '</p>' +
            '<div style="margin-bottom:14px">' +
                '<label style="display:block;font-size:13px;color:var(--text-muted);margin-bottom:6px">What to scan</label>' +
                '<select id="' + prefix + 'folder-select" style="padding:6px 10px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-size:13px;min-width:260px">' +
                    '<option value="">Entire Drive</option>' +
                '</select>' +
            '</div>' +
            '<button id="' + prefix + 'btn-start-scan" class="btn-primary">Start Scan</button>';

        el('link-disconnect').addEventListener('click', function(e) {
            e.preventDefault();
            fetchJSON('/api/drive/disconnect', { method: 'POST' }).then(renderDisconnected).catch(function() {});
        });
        el('btn-start-scan').addEventListener('click', startScan);
        loadFolders();
    }

    function bindConnect() {
        el('btn-connect').addEventListener('click', function() {
            window.location = '/api/drive/auth';
        });
    }

    function loadFolders() {
        fetchJSON('/api/drive/folders').then(function(data) {
            var sel = el('folder-select');
            if (!sel) return;
            (data.folders || []).forEach(function(f) {
                var opt = document.createElement('option');
                opt.value = f.id;
                opt.textContent = f.name;
                sel.appendChild(opt);
            });
        }).catch(function() {});
    }

    function loadStatus() {
        fetchJSON('/api/drive/status').then(function(data) {
            if (data.connected) renderConnected(data.email);
            else renderDisconnected();
        }).catch(function(e) {
            showError('Error checking Drive status: ' + e.message);
        });
    }

    // Surface OAuth callback outcome from the redirect query string.
    var params = new URLSearchParams(window.location.search);
    if (params.get('drive') === 'error') {
        showError('Google Drive connection failed. Check your OAuth setup and try again.');
    } else {
        loadStatus();
    }

    // ── Start scan ──────────────────────────────────────────────
    function startScan() {
        el('step-connect').style.display = 'none';
        el('step-scanning').style.display = '';

        var folderId = el('folder-select') ? el('folder-select').value : '';
        var url = '/api/drive/scan';
        if (folderId) url += '?folder_id=' + encodeURIComponent(folderId);
        var es = new EventSource(url);

        es.onmessage = function(event) {
            var msg = JSON.parse(event.data);
            if (msg.type === 'progress') {
                el('stat-pdfs').textContent = msg.pdfs_found;
                var bar = el('scan-bar');
                bar.style.width = '100%';
                bar.style.opacity = '0.6';
            }
            if (msg.type === 'done') {
                es.close();
                candidates = msg.candidates || [];
                showReview();
            }
        };

        es.onerror = function() {
            es.close();
            if (candidates.length === 0) {
                el('step-scanning').style.display = 'none';
                el('step-review').style.display = '';
                el('no-candidates').style.display = '';
            }
        };
    }

    // ── Review ──────────────────────────────────────────────────
    function showReview() {
        el('step-scanning').style.display = 'none';
        el('step-review').style.display = '';

        if (candidates.length === 0) {
            el('no-candidates').style.display = '';
            return;
        }

        el('review-summary').textContent = 'Found ' + candidates.length + ' potential bank statement' + (candidates.length !== 1 ? 's' : '') + '.';
        el('candidates-table').style.display = '';
        el('review-actions').style.display = '';

        var tbody = el('candidates-body');
        tbody.innerHTML = candidates.map(function(c, i) {
            var score = c.score;
            var badgeClass = 'badge-red';
            if (score >= 0.7) badgeClass = 'badge-green';
            else if (score >= 0.5) badgeClass = 'badge-yellow';

            var banks = (c.detected_banks || []).join(', ') || '-';

            return '<tr>' +
                '<td><input type="checkbox" class="' + prefix + 'candidate-cb" data-idx="' + i + '"' + (score >= 0.5 ? ' checked' : '') + '></td>' +
                '<td>' + esc(c.filename) + '</td>' +
                '<td><span class="confidence-badge ' + badgeClass + '">' + score.toFixed(2) + '</span></td>' +
                '<td>' + esc(banks) + '</td>' +
                '</tr>';
        }).join('');

        updateSelectedCount();

        el('select-all').addEventListener('change', function() {
            container.querySelectorAll('.' + prefix + 'candidate-cb').forEach(function(cb) { cb.checked = this.checked; }.bind(this));
            updateSelectedCount();
        });
        tbody.addEventListener('change', updateSelectedCount);
    }

    function updateSelectedCount() {
        var checked = container.querySelectorAll('.' + prefix + 'candidate-cb:checked').length;
        el('selected-count').textContent = checked + ' selected';
        el('btn-process').disabled = checked === 0;
    }

    // ── Process ─────────────────────────────────────────────────
    el('btn-process').addEventListener('click', processSelected);

    async function processSelected() {
        var selected = [];
        container.querySelectorAll('.' + prefix + 'candidate-cb:checked').forEach(function(cb) {
            var c = candidates[parseInt(cb.dataset.idx)];
            selected.push({ file_id: c.file_id, filename: c.filename });
        });
        if (selected.length === 0) return;

        el('step-review').style.display = 'none';
        el('step-processing').style.display = '';

        var progress = el('process-progress');
        progress.innerHTML = '<div class="extraction-logs" id="' + prefix + 'extraction-logs"></div>';
        var logs = el('extraction-logs');

        try {
            var response = await fetch('/api/drive/process', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ files: selected }),
            });

            var reader = response.body.getReader();
            var decoder = new TextDecoder();
            var buffer = '';

            while (true) {
                var chunk = await reader.read();
                if (chunk.done) break;
                buffer += decoder.decode(chunk.value, { stream: true });
                var lines = buffer.split('\n');
                buffer = lines.pop();

                for (var k = 0; k < lines.length; k++) {
                    var line = lines[k];
                    if (line.indexOf('data: ') !== 0) continue;
                    var msg = JSON.parse(line.substring(6));

                    if (msg.type === 'file_start') {
                        var header = document.createElement('div');
                        header.className = 'log-header';
                        header.textContent = 'Processing ' + msg.file + ' (' + (msg.index + 1) + '/' + msg.total + ')';
                        logs.appendChild(header);
                    } else if (msg.type === 'log') {
                        var entry = document.createElement('div');
                        entry.className = 'log-line';
                        entry.textContent = '[' + msg.time.toFixed(2) + 's]  ' + msg.step + ' — ' + msg.details;
                        logs.appendChild(entry);
                    } else if (msg.type === 'done') {
                        showResults(msg.results || []);
                    }
                    logs.scrollTop = logs.scrollHeight;
                }
            }
        } catch (e) {
            progress.innerHTML += step('error', 'error', 'Error: ' + e.message);
        }
    }

    // ── Results ─────────────────────────────────────────────────
    function showResults(results) {
        el('step-processing').style.display = 'none';
        el('step-results').style.display = '';

        var success = results.filter(function(r) { return r.status === 'success'; }).length;
        var skipped = results.filter(function(r) { return r.status === 'skipped'; }).length;
        var failed = results.filter(function(r) { return r.status === 'error' || r.status === 'unknown_bank'; }).length;

        var html = '<div class="results-stats">';
        if (success > 0) html += step('ok', 'ok', success + ' statement' + (success !== 1 ? 's' : '') + ' processed successfully');
        if (skipped > 0) html += step('skip', 'warning', skipped + ' already in database (skipped)');
        if (failed > 0) html += step('fail', 'error', failed + ' failed');
        html += '</div>';

        html += '<div style="margin-top:16px">';
        results.forEach(function(r) {
            var name = r.stem || r.source;
            var icon = r.status === 'success' ? 'ok' : r.status === 'skipped' ? 'warning' : 'error';
            var timeStr = r.total_time ? ' (' + r.total_time + 's)' : '';
            var msg = r.status === 'success' ? name + ' — loaded' + timeStr
                : r.status === 'skipped' ? name + ' — already imported'
                : name + ' — ' + (r.error || r.status);
            html += step(name, icon, msg);
        });
        html += '</div>';

        el('results-summary').innerHTML = html;
    }

    // ── Scan again ──────────────────────────────────────────────
    el('btn-scan-again').addEventListener('click', function() {
        candidates = [];
        el('step-results').style.display = 'none';
        el('step-connect').style.display = '';
        loadStatus();
    });
}
