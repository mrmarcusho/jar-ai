// ── Scan Page — thin wrapper around scanner widget ──────────────

initScanner('scanner-root', { inline: false });

fetchJSON('/api/config').then(function(c) {
    if (c.llm_available && document.getElementById('nav-chat')) {
        document.getElementById('nav-chat').style.display = '';
    }
}).catch(function() {});
