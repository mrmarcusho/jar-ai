// ── Scan Page — thin wrapper around scanner widget ──────────────

initScanner('scanner-root', { inline: false });
initDriveWidget('drive-root');
initEmailWidget('email-root');
// Text-message scan hidden for demo — flip to true to restore.
var SHOW_MESSAGES_SCAN = false;
if (SHOW_MESSAGES_SCAN) initMessagesWidget('messages-root');

fetchJSON('/api/config').then(function(c) {
    if (c.llm_available && document.getElementById('nav-chat')) {
        document.getElementById('nav-chat').style.display = '';
    }
}).catch(function() {});
