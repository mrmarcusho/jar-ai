// ── Dashboard Page Logic ────────────────────────────────────────

const PAGE_SIZE = 50;
let dashSortBy = 'date';
let dashSortDir = 'desc';
let dashPage = 0;
let dashTotalCount = 0;

// ── Tagging state ───────────────────────────────────────────────
let taxonomyCache = null;
let uncatTransactions = [];
let uncatReviewIndex = 0;
let reviewMode = false;

// ── Init ────────────────────────────────────────────────────────

async function initDashboard() {
    let config;
    try {
        config = await fetchJSON('/api/config');
    } catch (e) {
        // Fallback: try the old has-data endpoint
        try {
            const check = await fetchJSON('/api/dashboard/has-data');
            config = { has_data: check.has_data, llm_available: false };
        } catch (e2) {
            config = { has_data: false, llm_available: false };
        }
    }

    // Show/hide Chat tab
    if (config.llm_available && $('nav-chat')) {
        $('nav-chat').style.display = '';
    }

    if (config.has_data) {
        showDashboardContent();
    } else {
        // First-time user: show inline scanner
        $('inline-scanner-wrap').style.display = '';
        initScanner('inline-scanner', {
            inline: true,
            onComplete: transitionToDashboard,
        });
    }

    // Wire up chip clicks
    document.querySelectorAll('.chip-group').forEach(group => {
        group.addEventListener('click', e => {
            if (e.target.classList.contains('chip')) {
                e.target.classList.toggle('selected');
            }
        });
    });
}
initDashboard();

// ── Transition: scanner → dashboard ─────────────────────────────

function transitionToDashboard() {
    var wrap = $('inline-scanner-wrap');
    wrap.style.transition = 'opacity 0.4s ease';
    wrap.style.opacity = '0';

    setTimeout(function() {
        wrap.style.display = 'none';

        var content = $('dashboard-content');
        content.style.display = '';
        content.style.opacity = '0';
        content.style.transition = 'opacity 0.4s ease';

        // Force reflow before setting opacity
        content.offsetHeight;
        content.style.opacity = '1';

        showDashboardContent();
    }, 400);
}

// ── Load & render dashboard data ────────────────────────────────

async function showDashboardContent() {
    $('dashboard-content').style.display = '';

    // Parallel fetch all dashboard data
    const [summary, balanceHistory, categories, monthly, merchants, uncatData] = await Promise.all([
        fetchJSON('/api/dashboard/summary').catch(() => null),
        fetchJSON('/api/dashboard/balance-history').catch(() => null),
        fetchJSON('/api/dashboard/spending-by-category').catch(() => null),
        fetchJSON('/api/dashboard/monthly-trends').catch(() => null),
        fetchJSON('/api/dashboard/top-merchants').catch(() => null),
        fetchJSON('/api/transactions/uncategorized?limit=1').catch(() => null),
    ]);

    if (summary) renderSummaryCards(summary);
    if (balanceHistory) renderBalanceChart(balanceHistory);
    if (categories) renderCategoryChart(categories);
    if (monthly) renderMonthlyChart(monthly);
    if (merchants) renderMerchants(merchants);

    if (uncatData) renderUncatBanner(uncatData.count);

    loadInsights();
    loadDashTypes();
    loadDashTransactions();
}

// ── Uncategorized Banner ────────────────────────────────────────

function renderUncatBanner(count) {
    var banner = $('uncat-banner');
    if (count > 0) {
        banner.style.display = '';
        $('uncat-count').textContent = count;
    } else {
        banner.style.display = 'none';
    }
}

async function refreshUncatBanner() {
    try {
        var data = await fetchJSON('/api/transactions/uncategorized?limit=1');
        renderUncatBanner(data.count);
    } catch (e) { /* ignore */ }
}

// ── Review Flow ─────────────────────────────────────────────────

async function startReviewFlow() {
    try {
        var data = await fetchJSON('/api/transactions/uncategorized?limit=200');
        uncatTransactions = data.transactions || [];
        if (uncatTransactions.length === 0) {
            showToast('No uncategorized transactions');
            return;
        }
        uncatReviewIndex = 0;
        reviewMode = true;
        await loadTaxonomy();
        openTagModal(uncatTransactions[0]);
    } catch (e) {
        showToast('Error loading transactions');
    }
}

// ── Taxonomy ────────────────────────────────────────────────────

async function loadTaxonomy() {
    if (taxonomyCache) return;
    taxonomyCache = await fetchJSON('/api/categories');
}

function populateCategorySelect() {
    var sel = $('tag-category');
    sel.innerHTML = '<option value="">Select category...</option>';
    var cats = taxonomyCache.categories;
    for (var key in cats) {
        var opt = document.createElement('option');
        opt.value = key;
        opt.textContent = cats[key].label;
        sel.appendChild(opt);
    }
}

function onCategoryChange() {
    var catKey = $('tag-category').value;
    var subSel = $('tag-subcategory');
    subSel.innerHTML = '<option value="">Select subcategory...</option>';
    if (!catKey || !taxonomyCache) return;
    var subs = taxonomyCache.categories[catKey].subcategories;
    for (var key in subs) {
        var opt = document.createElement('option');
        opt.value = key;
        opt.textContent = subs[key];
        subSel.appendChild(opt);
    }
}

// ── Tag Modal ───────────────────────────────────────────────────

async function openTagModal(txn) {
    await loadTaxonomy();
    populateCategorySelect();

    $('tag-txn-date').textContent = fmtDate(txn.date);
    $('tag-txn-desc').textContent = txn.description_raw || '(no description)';
    var isDeposit = txn.is_deposit === 1;
    $('tag-txn-amount').textContent = fmt(txn.amount);
    $('tag-txn-amount').style.color = isDeposit ? 'var(--green)' : 'var(--red)';

    // Pre-fill keyword
    var keyword = txn.suggested_keyword || '';
    $('tag-keyword').value = keyword;
    $('tag-keyword-hint').textContent = keyword ? 'Suggested keyword: "' + keyword + '"' : '';

    // Reset selections
    $('tag-category').value = txn.category && txn.category !== 'uncategorized' ? txn.category : '';
    onCategoryChange();
    $('tag-subcategory').value = txn.subcategory || '';
    $('tag-custom').value = '';
    $('tag-create-rule').checked = true;

    // Reset chips
    document.querySelectorAll('.chip').forEach(c => c.classList.remove('selected'));

    // Store current txn on the modal
    $('tag-modal-backdrop').dataset.txnId = txn.transaction_id;

    // Review progress
    if (reviewMode) {
        $('tag-skip-btn').style.display = '';
        $('tag-review-progress').style.display = '';
        var total = uncatTransactions.length;
        var pct = Math.round((uncatReviewIndex / total) * 100);
        $('tag-progress-bar').style.setProperty('--progress', pct + '%');
        $('tag-progress-text').textContent = (uncatReviewIndex + 1) + ' of ' + total;
    } else {
        $('tag-skip-btn').style.display = 'none';
        $('tag-review-progress').style.display = 'none';
    }

    $('tag-modal-backdrop').style.display = '';
}

function closeTagModal() {
    $('tag-modal-backdrop').style.display = 'none';
    reviewMode = false;
}

function skipTag() {
    uncatReviewIndex++;
    if (uncatReviewIndex < uncatTransactions.length) {
        openTagModal(uncatTransactions[uncatReviewIndex]);
    } else {
        closeTagModal();
        showToast('Review complete!');
        refreshUncatBanner();
        loadDashTransactions();
    }
}

async function saveTag() {
    var txnId = $('tag-modal-backdrop').dataset.txnId;
    var category = $('tag-category').value;
    var subcategory = $('tag-subcategory').value;
    if (!category) {
        showToast('Please select a category');
        return;
    }

    // Gather chips
    var household = null;
    var hChip = document.querySelector('#tag-household .chip.selected');
    if (hChip) household = hChip.dataset.value;

    var budgetType = null;
    var bChip = document.querySelector('#tag-budget .chip.selected');
    if (bChip) budgetType = bChip.dataset.value;

    var customTags = $('tag-custom').value.trim() || null;
    var createRule = $('tag-create-rule').checked;
    var keyword = $('tag-keyword').value.trim();

    try {
        var result = await fetchJSON('/api/transactions/' + encodeURIComponent(txnId) + '/tag', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                category: category,
                subcategory: subcategory || null,
                household_tag: household,
                budget_type: budgetType,
                custom_tags: customTags,
                create_rule: createRule,
                keyword: keyword || null,
            }),
        });

        var msg = 'Tagged!';
        if (result.rule_created) {
            msg += ' Rule created';
            if (result.similar_updated > 0) {
                msg += ', ' + result.similar_updated + ' similar updated';
            }
        }
        showToast(msg);

        if (reviewMode) {
            // Remove tagged txn from list
            uncatTransactions.splice(uncatReviewIndex, 1);
            if (uncatReviewIndex < uncatTransactions.length) {
                openTagModal(uncatTransactions[uncatReviewIndex]);
            } else {
                closeTagModal();
                showToast('Review complete!');
            }
        } else {
            closeTagModal();
        }

        refreshUncatBanner();
        loadDashTransactions();
    } catch (e) {
        showToast('Error: ' + e.message);
    }
}

// ── Toast ───────────────────────────────────────────────────────

function showToast(msg) {
    var toast = $('toast');
    toast.textContent = msg;
    toast.style.display = '';
    clearTimeout(toast._timer);
    toast._timer = setTimeout(function() {
        toast.style.display = 'none';
    }, 3000);
}

// ── Summary Cards ───────────────────────────────────────────────

function renderSummaryCards(data) {
    const stmts = data.statements_in_range || [];
    const numStmts = stmts.length;

    $('card-balance').textContent = data.closing_balance != null ? fmt(data.closing_balance) : '-';
    $('card-balance-sub').textContent = numStmts + ' statement' + (numStmts !== 1 ? 's' : '') + ' loaded';

    const avgIncome = numStmts > 0 ? data.total_income / numStmts : 0;
    const avgSpending = numStmts > 0 ? Math.abs(data.total_spending) / numStmts : 0;

    $('card-income').textContent = fmt(avgIncome);
    $('card-income-sub').textContent = 'Total: ' + fmt(data.total_income);

    $('card-spending').textContent = fmt(avgSpending);
    $('card-spending-sub').textContent = 'Total: ' + fmt(Math.abs(data.total_spending));

    const net = data.net_change || 0;
    $('card-net').textContent = fmt(net);
    $('card-net').style.color = net >= 0 ? 'var(--green)' : 'var(--red)';
    $('card-net-sub').textContent = net >= 0 ? 'Positive cash flow' : 'Negative cash flow';
}

// ── Charts ──────────────────────────────────────────────────────

const CHART_COLORS = [
    '#2563eb', '#dc2626', '#16a34a', '#ca8a04', '#9333ea',
    '#0891b2', '#e11d48', '#65a30d', '#c2410c', '#6366f1',
];

function renderBalanceChart(data) {
    const history = data.history || [];
    if (history.length === 0) return;

    new Chart($('chart-balance'), {
        type: 'line',
        data: {
            labels: history.map(h => h.period_end),
            datasets: [{
                label: 'Closing Balance',
                data: history.map(h => h.closing_balance),
                borderColor: '#2563eb',
                backgroundColor: 'rgba(37,99,235,0.1)',
                fill: true,
                tension: 0.3,
                pointRadius: 4,
            }],
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
                y: {
                    ticks: { callback: v => '$' + v.toLocaleString() },
                },
            },
        },
    });
}

function renderCategoryChart(data) {
    const cats = data.categories || [];
    if (cats.length === 0) return;

    new Chart($('chart-categories'), {
        type: 'doughnut',
        data: {
            labels: cats.map(c => (c.category || 'other').replace(/_/g, ' ')),
            datasets: [{
                data: cats.map(c => c.total),
                backgroundColor: CHART_COLORS.slice(0, cats.length),
            }],
        },
        options: {
            responsive: true,
            plugins: {
                legend: { position: 'right', labels: { boxWidth: 12, font: { size: 11 } } },
            },
        },
    });
}

function renderMonthlyChart(data) {
    const months = data.months || [];
    if (months.length === 0) return;

    new Chart($('chart-monthly'), {
        type: 'bar',
        data: {
            labels: months.map(m => m.month),
            datasets: [
                {
                    label: 'Income',
                    data: months.map(m => m.income),
                    backgroundColor: 'rgba(22,163,74,0.7)',
                },
                {
                    label: 'Spending',
                    data: months.map(m => m.spending),
                    backgroundColor: 'rgba(220,38,38,0.7)',
                },
            ],
        },
        options: {
            responsive: true,
            plugins: { legend: { labels: { boxWidth: 12 } } },
            scales: {
                y: {
                    ticks: { callback: v => '$' + v.toLocaleString() },
                },
            },
        },
    });
}

function renderMerchants(data) {
    const merchants = data.merchants || [];
    const list = $('merchant-list');

    if (merchants.length === 0) {
        list.innerHTML = '<li style="color:var(--text-muted)">No merchant data</li>';
        return;
    }

    list.innerHTML = merchants.map(m =>
        '<li><span class="merchant-name">' + esc(m.merchant) + '</span>' +
        '<span class="merchant-amount">' + fmt(m.total_spent) + '</span></li>'
    ).join('');
}

// ── Insights ────────────────────────────────────────────────────

async function loadInsights() {
    // Biggest expense
    try {
        const data = await fetchJSON('/api/transactions?is_deposit=0&sort_by=amount&sort_dir=asc&limit=1');
        const t = data.transactions[0];
        if (t) {
            $('insight-biggest-result').innerHTML =
                '<strong>' + fmt(t.amount) + '</strong> — ' + esc(t.description_raw) +
                '<br><span style="color:var(--text-muted)">' + fmtDate(t.date) + '</span>';
        }
    } catch (e) { /* ignore */ }

    // Recurring bills
    try {
        const data = await fetchJSON('/api/recurring');
        const bills = data.recurring_bills || [];
        if (bills.length > 0) {
            $('insight-recurring-result').innerHTML =
                '<strong>' + bills.length + '</strong> recurring bill' + (bills.length !== 1 ? 's' : '') +
                '<br>' + bills.slice(0, 3).map(b => esc(b.payee) + ' (' + fmt(b.avg_amount) + ')').join('<br>');
        } else {
            $('insight-recurring-result').textContent = 'None detected';
        }
    } catch (e) { /* ignore */ }

    // Large transactions (over $500)
    try {
        const data = await fetchJSON('/api/transactions?is_deposit=0&max_amount=-500&sort_by=amount&sort_dir=asc&limit=5');
        const count = data.count;
        $('insight-large-result').innerHTML = '<strong>' + count + '</strong> transaction' + (count !== 1 ? 's' : '') + ' over $500';
    } catch (e) { /* ignore */ }

    // Export CSV
    $('insight-export').addEventListener('click', () => {
        window.location.href = '/api/export/csv';
    });
    $('insight-export-result').innerHTML = '<span style="color:var(--blue);cursor:pointer">Click to download</span>';
}

// ── Transactions Table ──────────────────────────────────────────

async function loadDashTypes() {
    try {
        const data = await fetchJSON('/api/summary');
        const sel = $('dash-filter-type');
        (data.types || []).forEach(t => {
            const opt = document.createElement('option');
            opt.value = t;
            opt.textContent = t.replace(/_/g, ' ');
            sel.appendChild(opt);
        });
    } catch (e) { /* ignore */ }
}

let dashSearchTimeout;
$('dash-filter-search').addEventListener('input', () => {
    clearTimeout(dashSearchTimeout);
    dashSearchTimeout = setTimeout(() => { dashPage = 0; loadDashTransactions(); }, 300);
});
$('dash-filter-start').addEventListener('change', () => { dashPage = 0; loadDashTransactions(); });
$('dash-filter-end').addEventListener('change', () => { dashPage = 0; loadDashTransactions(); });
$('dash-filter-type').addEventListener('change', () => { dashPage = 0; loadDashTransactions(); });
$('dash-filter-direction').addEventListener('change', () => { dashPage = 0; loadDashTransactions(); });

// Sortable headers
document.querySelectorAll('th[data-sort]').forEach(th => {
    th.addEventListener('click', () => {
        const col = th.dataset.sort;
        if (dashSortBy === col) {
            dashSortDir = dashSortDir === 'desc' ? 'asc' : 'desc';
        } else {
            dashSortBy = col;
            dashSortDir = col === 'date' ? 'desc' : 'asc';
        }
        loadDashTransactions();
    });
});

// Pagination
$('dash-prev').addEventListener('click', () => { if (dashPage > 0) { dashPage--; loadDashTransactions(); } });
$('dash-next').addEventListener('click', () => {
    if ((dashPage + 1) * PAGE_SIZE < dashTotalCount) { dashPage++; loadDashTransactions(); }
});

function categoryBadgeHtml(category, txnId) {
    var cat = (category || 'uncategorized').replace(/_/g, ' ');
    var cls = 'cat-' + (category || 'uncategorized');
    return '<span class="category-badge ' + cls + '" onclick="onCategoryBadgeClick(\'' + esc(txnId) + '\')">' + esc(cat) + '</span>';
}

async function onCategoryBadgeClick(txnId) {
    // Fetch the transaction, then open tag modal
    try {
        var data = await fetchJSON('/api/transactions?search=&limit=500');
        var txn = (data.transactions || []).find(t => t.transaction_id === txnId);
        if (!txn) {
            showToast('Transaction not found');
            return;
        }
        // Add suggested keyword
        var kw = '';
        if (txn.ach_company_name) {
            kw = txn.ach_company_name;
        } else if (txn.description_raw) {
            // Simple extraction — server has better version, but this works for click-to-tag
            kw = txn.description_raw.split(/\s+/).slice(0, 3).join(' ');
        }
        txn.suggested_keyword = kw;
        reviewMode = false;
        await loadTaxonomy();
        openTagModal(txn);
    } catch (e) {
        showToast('Error: ' + e.message);
    }
}

async function loadDashTransactions() {
    const params = new URLSearchParams();
    const search = $('dash-filter-search').value.trim();
    const start = $('dash-filter-start').value;
    const end = $('dash-filter-end').value;
    const type = $('dash-filter-type').value;
    const dir = $('dash-filter-direction').value;

    if (search) params.set('search', search);
    if (start) params.set('start_date', start);
    if (end) params.set('end_date', end);
    if (type) params.set('type', type);
    if (dir !== '') params.set('is_deposit', dir);
    params.set('sort_by', dashSortBy);
    params.set('sort_dir', dashSortDir);
    params.set('limit', PAGE_SIZE);

    try {
        const data = await fetchJSON('/api/transactions?' + params);
        dashTotalCount = data.count;

        $('dash-txn-count').textContent = data.count + ' transaction' + (data.count !== 1 ? 's' : '');

        // Update sort arrows
        document.querySelectorAll('th[data-sort]').forEach(th => {
            const arrow = th.querySelector('.sort-arrow');
            if (th.dataset.sort === dashSortBy) {
                arrow.textContent = dashSortDir === 'asc' ? ' \u25B2' : ' \u25BC';
            } else {
                arrow.textContent = '';
            }
        });

        const tbody = $('dash-txn-body');
        if (!data.transactions || data.transactions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:40px">No transactions found</td></tr>';
            updatePagination();
            return;
        }

        tbody.innerHTML = data.transactions.map(t => {
            const isDeposit = t.is_deposit === 1;
            const amtClass = isDeposit ? 'deposit' : 'withdrawal';

            let achHtml = '';
            if (t.ach_company_name) {
                const parts = [];
                if (t.ach_company_name) parts.push('<span>' + esc(t.ach_company_name) + '</span>');
                if (t.ach_individual_name) parts.push('<span>' + esc(t.ach_individual_name) + '</span>');
                achHtml = '<div class="ach-fields">' + parts.join('') + '</div>';
            }

            return '<tr>' +
                '<td>' + esc(t.date) + '</td>' +
                '<td><div class="desc-raw">' + esc(t.description_raw || '') + '</div>' + achHtml + '</td>' +
                '<td class="amount-cell ' + amtClass + '">' + fmt(t.amount) + '</td>' +
                '<td>' + categoryBadgeHtml(t.category, t.transaction_id) + '</td>' +
                '<td><span class="type-badge">' + esc((t.type || '').replace(/_/g, ' ')) + '</span></td>' +
                '</tr>';
        }).join('');

        updatePagination();
    } catch (e) {
        $('dash-txn-body').innerHTML = '<tr><td colspan="5" style="color:var(--red)">Error loading transactions</td></tr>';
    }
}

function updatePagination() {
    const totalPages = Math.max(1, Math.ceil(dashTotalCount / PAGE_SIZE));
    $('dash-page-info').textContent = 'Page ' + (dashPage + 1) + ' of ' + totalPages;
    $('dash-prev').disabled = dashPage === 0;
    $('dash-next').disabled = (dashPage + 1) * PAGE_SIZE >= dashTotalCount;
}
