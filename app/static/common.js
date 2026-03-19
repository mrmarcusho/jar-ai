// ── Shared utilities for Jar pages ──────────────────────────────

function $(id) { return document.getElementById(id); }

function fmt(amount) {
    if (amount == null) return '$0.00';
    const abs = Math.abs(amount);
    const str = abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return amount < 0 ? '-$' + str : '$' + str;
}

function esc(s) {
    if (s == null) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

function fmtDate(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr + 'T00:00:00');
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function step(name, status, message) {
    const icons = { ok: '&#10003;', error: '&#10007;', warning: '!', running: '' };
    const iconContent = status === 'running'
        ? '<div class="spinner"></div>'
        : '<div class="step-icon ' + status + '">' + (icons[status] || '') + '</div>';
    return '<div class="step">' + iconContent + '<span>' + message + '</span></div>';
}

async function fetchJSON(url, opts) {
    const res = await fetch(url, opts);
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || body.detail || `HTTP ${res.status}`);
    }
    return res.json();
}
