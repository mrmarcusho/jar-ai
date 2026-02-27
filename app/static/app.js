// ── Navigation ────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById('page-' + tab.dataset.page).classList.add('active');

        if (tab.dataset.page === 'transactions') loadTransactions();
    });
});

// ── Helpers ───────────────────────────────────────────────────────
function $(id) { return document.getElementById(id); }

function fmt(amount) {
    const abs = Math.abs(amount);
    const str = abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return amount < 0 ? '-$' + str : '$' + str;
}

// ── Init: load summary info ──────────────────────────────────────
async function loadSummary() {
    try {
        const res = await fetch('/api/summary');
        const data = await res.json();
        const badge = $('backend-badge');

        if (data.statements && data.statements.length > 0) {
            badge.textContent = data.total_transactions + ' transactions loaded';
            // Populate type filter
            const sel = $('filter-type');
            if (data.types) {
                data.types.forEach(t => {
                    const opt = document.createElement('option');
                    opt.value = t;
                    opt.textContent = t.replace(/_/g, ' ');
                    sel.appendChild(opt);
                });
            }
        } else {
            badge.textContent = 'No data — import a statement';
        }
    } catch (e) {
        $('backend-badge').textContent = 'Error connecting';
    }
}
loadSummary();


// ══════════════════════════════════════════════════════════════════
// IMPORT PAGE
// ══════════════════════════════════════════════════════════════════

const dropzone = $('dropzone');
const fileInput = $('file-input');

dropzone.addEventListener('click', () => fileInput.click());
dropzone.addEventListener('dragover', e => { e.preventDefault(); dropzone.classList.add('dragover'); });
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
dropzone.addEventListener('drop', e => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
    if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', () => {
    if (fileInput.files.length) uploadFile(fileInput.files[0]);
});

async function uploadFile(file) {
    const progress = $('import-progress');
    const summaryCard = $('import-summary');

    progress.innerHTML = step('upload', 'running', 'Uploading ' + file.name + '...');
    summaryCard.style.display = 'none';

    const form = new FormData();
    form.append('file', file);

    try {
        progress.innerHTML = step('upload', 'ok', 'Uploaded ' + file.name) +
            step('pipeline', 'running', 'Running extraction pipeline... (this may take a minute)');

        const res = await fetch('/api/import', { method: 'POST', body: form });
        const data = await res.json();

        // Show steps
        let html = '';
        if (data.steps) {
            data.steps.forEach(s => {
                html += step(s.step, s.status, s.message);
            });
        }
        progress.innerHTML = html;

        // Show summary
        if (data.summary) {
            const s = data.summary;
            summaryCard.style.display = '';
            $('summary-grid').innerHTML =
                summaryItem('Period', (s.period_start || '?') + ' to ' + (s.period_end || '?')) +
                summaryItem('Opening Balance', fmt(s.opening_balance), 'green') +
                summaryItem('Closing Balance', fmt(s.closing_balance), 'green') +
                summaryItem('Deposits', s.num_deposits + ' totaling ' + fmt(s.total_deposits), 'green') +
                summaryItem('Withdrawals', s.num_withdrawals + ' totaling ' + fmt(s.total_withdrawals), 'red') +
                summaryItem('Validation', s.validation_passed ? 'Passed' : 'Failed', s.validation_passed ? 'green' : 'red');

            loadSummary();
        }
    } catch (e) {
        progress.innerHTML += step('error', 'error', 'Request failed: ' + e.message);
    }
}

function step(name, status, message) {
    const icons = { ok: '&#10003;', error: '&#10007;', warning: '!', running: '' };
    const iconContent = status === 'running'
        ? '<div class="spinner"></div>'
        : '<div class="step-icon ' + status + '">' + (icons[status] || '') + '</div>';
    return '<div class="step">' + iconContent + '<span>' + message + '</span></div>';
}

function summaryItem(label, value, color) {
    const cls = color ? ' ' + color : '';
    return '<div class="summary-item"><div class="label">' + label + '</div><div class="value' + cls + '">' + value + '</div></div>';
}


// ══════════════════════════════════════════════════════════════════
// TRANSACTIONS PAGE
// ══════════════════════════════════════════════════════════════════

let sortBy = 'date';
let sortDir = 'desc';

// Debounced search
let searchTimeout;
$('filter-search').addEventListener('input', () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(loadTransactions, 300);
});

$('filter-start').addEventListener('change', loadTransactions);
$('filter-end').addEventListener('change', loadTransactions);
$('filter-type').addEventListener('change', loadTransactions);
$('filter-direction').addEventListener('change', loadTransactions);

// Sortable headers
document.querySelectorAll('th[data-sort]').forEach(th => {
    th.addEventListener('click', () => {
        const col = th.dataset.sort;
        if (sortBy === col) {
            sortDir = sortDir === 'desc' ? 'asc' : 'desc';
        } else {
            sortBy = col;
            sortDir = col === 'date' ? 'desc' : 'asc';
        }
        loadTransactions();
    });
});

async function loadTransactions() {
    const params = new URLSearchParams();
    const search = $('filter-search').value.trim();
    const start = $('filter-start').value;
    const end = $('filter-end').value;
    const type = $('filter-type').value;
    const dir = $('filter-direction').value;

    if (search) params.set('search', search);
    if (start) params.set('start_date', start);
    if (end) params.set('end_date', end);
    if (type) params.set('type', type);
    if (dir !== '') params.set('is_deposit', dir);
    params.set('sort_by', sortBy);
    params.set('sort_dir', sortDir);

    try {
        const res = await fetch('/api/transactions?' + params);
        const data = await res.json();

        $('txn-count').textContent = data.count + ' transaction' + (data.count !== 1 ? 's' : '');

        // Update sort arrows
        document.querySelectorAll('th[data-sort]').forEach(th => {
            const arrow = th.querySelector('.sort-arrow');
            if (th.dataset.sort === sortBy) {
                arrow.textContent = sortDir === 'asc' ? ' \u25B2' : ' \u25BC';
            } else {
                arrow.textContent = '';
            }
        });

        const tbody = $('txn-body');
        if (!data.transactions || data.transactions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted);padding:40px">No transactions found</td></tr>';
            return;
        }

        tbody.innerHTML = data.transactions.map(t => {
            const isDeposit = t.is_deposit === 1;
            const amtClass = isDeposit ? 'deposit' : 'withdrawal';

            // ACH fields
            let achHtml = '';
            if (t.ach_company_name) {
                const parts = [];
                if (t.ach_company_name) parts.push('<span>' + esc(t.ach_company_name) + '</span>');
                if (t.ach_individual_name) parts.push('<span>' + esc(t.ach_individual_name) + '</span>');
                if (t.ach_entry_class) parts.push('<span>' + esc(t.ach_entry_class) + '</span>');
                achHtml = '<div class="ach-fields">' + parts.join('') + '</div>';
            }

            return '<tr>' +
                '<td>' + esc(t.date) + '</td>' +
                '<td><div class="desc-raw">' + esc(t.description_raw || '') + '</div>' + achHtml + '</td>' +
                '<td class="amount-cell ' + amtClass + '">' + fmt(t.amount) + '</td>' +
                '<td><span class="type-badge">' + esc((t.type || '').replace(/_/g, ' ')) + '</span></td>' +
                '</tr>';
        }).join('');
    } catch (e) {
        $('txn-body').innerHTML = '<tr><td colspan="4" style="color:var(--red)">Error loading transactions</td></tr>';
    }
}

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}


// ══════════════════════════════════════════════════════════════════
// CHAT PAGE
// ══════════════════════════════════════════════════════════════════

const chatInput = $('chat-input');
const chatSend = $('chat-send');
const chatMessages = $('chat-messages');
const debugToggle = $('debug-toggle');

chatSend.addEventListener('click', sendChat);
chatInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
});

$('chat-reset').addEventListener('click', async () => {
    await fetch('/api/chat/reset', { method: 'POST' });
    chatMessages.innerHTML = '<div class="message assistant">Conversation reset. How can I help?</div>';
});

async function sendChat() {
    const text = chatInput.value.trim();
    if (!text) return;

    // Add user message
    chatMessages.innerHTML += '<div class="message user">' + esc(text) + '</div>';
    chatInput.value = '';
    chatInput.disabled = true;
    chatSend.disabled = true;

    // Add thinking indicator
    const thinkingId = 'thinking-' + Date.now();
    chatMessages.innerHTML += '<div class="message assistant" id="' + thinkingId + '"><div class="spinner"></div> Thinking...</div>';
    chatMessages.scrollTop = chatMessages.scrollHeight;

    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text }),
        });
        const data = await res.json();

        // Remove thinking indicator
        const thinking = document.getElementById(thinkingId);
        if (thinking) thinking.remove();

        if (data.error) {
            chatMessages.innerHTML += '<div class="message assistant" style="color:var(--red)">Error: ' + esc(data.error) + '</div>';
        } else {
            let html = formatResponse(data.response || '');

            // Debug info
            if (debugToggle.checked && data.debug && data.debug.length > 0) {
                html += '<div class="debug-info">';
                data.debug.forEach(d => {
                    html += '<div><strong>' + esc(d.function) + '</strong>(' + esc(JSON.stringify(d.params)) + ')</div>';
                });
                html += '</div>';
            }

            chatMessages.innerHTML += '<div class="message assistant">' + html + '</div>';
        }
    } catch (e) {
        const thinking = document.getElementById(thinkingId);
        if (thinking) thinking.remove();
        chatMessages.innerHTML += '<div class="message assistant" style="color:var(--red)">Connection error: ' + esc(e.message) + '</div>';
    }

    chatInput.disabled = false;
    chatSend.disabled = false;
    chatInput.focus();
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function formatResponse(text) {
    // Simple markdown-ish formatting
    // Code blocks
    text = text.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
    // Inline code
    text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Bold
    text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Line breaks
    text = text.replace(/\n/g, '<br>');
    return text;
}
