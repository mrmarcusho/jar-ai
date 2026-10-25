// ── Scanner Widget ──────────────────────────────────────────────
// Reusable scanner module.
//   initScanner(containerId, options)
//     options.inline  — true = embedded mode (no "Go to Dashboard" link, calls onComplete)
//     options.onComplete — callback when processing finishes (inline mode only)

function initScanner(containerId, options) {
    options = options || {};
    var inline = !!options.inline;
    var onComplete = options.onComplete || function() {};

    var prefix = inline ? 'is-' : 'sc-';
    var container = document.getElementById(containerId);
    if (!container) return;

    // Closure-scoped element lookup
    function el(id) { return document.getElementById(prefix + id); }

    var candidates = [];
    var scanPaths = [];

    // ── Generate HTML ───────────────────────────────────────────
    container.innerHTML =
        // Step 1: Permission prompt
        '<div id="' + prefix + 'step-permission" class="card">' +
            '<h2>Scan for Bank Statements</h2>' +
            '<p style="color:var(--text-muted);margin-bottom:16px">' +
                'Jar will scan your local folders for PDF bank statements. ' +
                'Everything stays on your machine — no data leaves this computer.' +
            '</p>' +
            '<div id="' + prefix + 'scan-paths" style="margin-bottom:20px">' +
                '<div class="spinner" style="margin:12px 0"></div>' +
            '</div>' +
            '<div id="' + prefix + 'add-path-row" style="display:none;margin-bottom:16px">' +
                '<div style="display:flex;gap:8px">' +
                    '<input id="' + prefix + 'add-path-input" type="text" placeholder="/path/to/folder" ' +
                        'style="flex:1;padding:6px 10px;border:1px solid var(--border);border-radius:6px;' +
                        'background:var(--bg);color:var(--text);font-size:13px">' +
                    '<button id="' + prefix + 'btn-add-path" class="btn-secondary" style="padding:6px 14px;font-size:13px">Add</button>' +
                '</div>' +
            '</div>' +
            '<button id="' + prefix + 'btn-start-scan" class="btn-primary" disabled>Start Scan</button>' +
            '<div style="margin-top:12px">' +
                '<a href="/chat?page=import" style="font-size:13px;color:var(--text-muted)">or import a single file manually</a>' +
            '</div>' +
        '</div>' +

        // Step 2: Scan progress
        '<div id="' + prefix + 'step-scanning" class="card" style="display:none">' +
            '<h2>Scanning...</h2>' +
            '<div class="scan-stats">' +
                '<div class="scan-stat"><span id="' + prefix + 'stat-dirs">0</span> folders scanned</div>' +
                '<div class="scan-stat"><span id="' + prefix + 'stat-pdfs">0</span> PDFs found</div>' +
                '<div class="scan-stat"><span id="' + prefix + 'stat-candidates">0</span> candidates</div>' +
                '<div class="scan-stat"><span id="' + prefix + 'stat-time">0s</span> elapsed</div>' +
            '</div>' +
            '<div id="' + prefix + 'scan-current" style="font-size:12px;color:var(--text-muted);margin-top:8px;word-break:break-all"></div>' +
            '<div class="scan-progress-bar"><div id="' + prefix + 'scan-bar" class="scan-progress-fill"></div></div>' +
        '</div>' +

        // Step 3: Candidate review
        '<div id="' + prefix + 'step-review" class="card" style="display:none">' +
            '<h2>Review Candidates</h2>' +
            '<p id="' + prefix + 'review-summary" style="color:var(--text-muted);margin-bottom:16px"></p>' +
            '<div id="' + prefix + 'no-candidates" style="display:none;text-align:center;padding:30px;color:var(--text-muted)">' +
                'No bank statement candidates found. Try importing a file manually.' +
            '</div>' +
            '<div class="table-wrap">' +
                '<table id="' + prefix + 'candidates-table" style="display:none">' +
                    '<thead><tr>' +
                        '<th style="width:36px"><input type="checkbox" id="' + prefix + 'select-all"></th>' +
                        '<th>File</th><th>Location</th><th>Confidence</th><th>Bank Hint</th>' +
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
            (inline ? '' :
                '<div style="margin-top:20px">' +
                    '<a href="/dashboard" class="btn-primary" style="text-decoration:none">Go to Dashboard</a>' +
                    '<button id="' + prefix + 'btn-scan-again" class="btn-secondary" style="margin-left:8px">Scan Again</button>' +
                '</div>') +
        '</div>';

    // ── Load scan paths ─────────────────────────────────────────
    function renderPaths() {
        var pathsDiv = el('scan-paths');
        if (scanPaths.length > 0) {
            pathsDiv.innerHTML =
                '<div style="font-size:13px;color:var(--text-muted);margin-bottom:8px">Folders to scan:</div>' +
                scanPaths.map(function(p, i) {
                    return '<div class="scan-path-item" style="display:flex;align-items:center;justify-content:space-between">' +
                        '<span>' + esc(p) + '</span>' +
                        '<button class="' + prefix + 'remove-path" data-idx="' + i + '" ' +
                            'style="background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:16px;padding:0 4px;line-height:1" ' +
                            'title="Remove">&times;</button>' +
                    '</div>';
                }).join('');
            el('btn-start-scan').disabled = false;
        } else {
            pathsDiv.innerHTML = '<div style="color:var(--text-muted)">No scan directories. Add a folder below.</div>';
            el('btn-start-scan').disabled = true;
        }
        // Bind remove buttons
        container.querySelectorAll('.' + prefix + 'remove-path').forEach(function(btn) {
            btn.addEventListener('click', function() {
                scanPaths.splice(parseInt(this.dataset.idx), 1);
                renderPaths();
            });
        });
    }

    function loadPaths() {
        fetchJSON('/api/scan/paths').then(function(data) {
            scanPaths = (data.paths && data.paths.length > 0) ? data.paths.slice() : [];
            renderPaths();
            el('add-path-row').style.display = '';
        }).catch(function(e) {
            el('scan-paths').innerHTML = '<div style="color:var(--red)">Error loading scan paths: ' + esc(e.message) + '</div>';
        });
    }
    loadPaths();

    // ── Add custom path ─────────────────────────────────────────
    function addPath() {
        var input = el('add-path-input');
        var val = input.value.trim();
        if (val && scanPaths.indexOf(val) === -1) {
            scanPaths.push(val);
            renderPaths();
            input.value = '';
        }
    }
    el('btn-add-path').addEventListener('click', addPath);
    el('add-path-input').addEventListener('keydown', function(e) {
        if (e.key === 'Enter') { e.preventDefault(); addPath(); }
    });

    // ── Start scan ──────────────────────────────────────────────
    el('btn-start-scan').addEventListener('click', startScan);

    function startScan() {
        el('step-permission').style.display = 'none';
        el('step-scanning').style.display = '';

        var scanUrl = '/api/scan/start';
        if (scanPaths.length > 0) {
            scanUrl += '?paths=' + encodeURIComponent(scanPaths.join(','));
        }
        var es = new EventSource(scanUrl);

        es.onmessage = function(event) {
            var msg = JSON.parse(event.data);

            if (msg.type === 'progress') {
                el('stat-dirs').textContent = msg.dirs_scanned;
                el('stat-pdfs').textContent = msg.pdfs_found;
                el('stat-candidates').textContent = msg.candidates_found;
                el('stat-time').textContent = msg.elapsed + 's';
                el('scan-current').textContent = msg.current_dir;
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

    // ── Review candidates ───────────────────────────────────────
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
            var dir = c.filepath.substring(0, c.filepath.lastIndexOf('/'));

            return '<tr>' +
                '<td><input type="checkbox" class="' + prefix + 'candidate-cb" data-idx="' + i + '"' + (i === 0 ? ' checked' : '') + '></td>' +
                '<td>' + esc(c.filename) + '</td>' +
                '<td class="scan-location">' + esc(dir) + '</td>' +
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

    // ── Process selected ────────────────────────────────────────
    el('btn-process').addEventListener('click', processSelected);

    async function processSelected() {
        var selected = [];
        container.querySelectorAll('.' + prefix + 'candidate-cb:checked').forEach(function(cb) {
            selected.push(candidates[parseInt(cb.dataset.idx)].filepath);
        });

        if (selected.length === 0) return;

        el('step-review').style.display = 'none';
        el('step-processing').style.display = '';

        var progress = el('process-progress');
        progress.innerHTML =
            '<div class="extraction-logs" id="' + prefix + 'extraction-logs"></div>';

        var logs = el('extraction-logs');

        try {
            var response = await fetch('/api/scan/process', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ paths: selected }),
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
                        entry.textContent = '[' + msg.time.toFixed(2) + 's]  ' + msg.step + ' \u2014 ' + msg.details;
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

    // ── Show results ────────────────────────────────────────────
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

        if (inline) {
            onComplete();
        }
    }

    // ── Scan again (standalone only) ────────────────────────────
    if (!inline) {
        var btnScanAgain = el('btn-scan-again');
        if (btnScanAgain) {
            btnScanAgain.addEventListener('click', function() {
                candidates = [];
                scanPaths = [];
                el('step-results').style.display = 'none';
                el('step-permission').style.display = '';
                loadPaths();
            });
        }
    }
}
