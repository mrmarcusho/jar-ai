"""Phase I HSM (Holy Shit Moment) analysis prompt.

After extraction, this prompt asks the cloud LLM to scan transaction data
across all loaded statements and surface financial insights the family
might have missed.  Phase I = deterministic patterns only (no world
knowledge, no live data).
"""

from __future__ import annotations


# ── Phase I HSM definitions ──────────────────────────────────────

PHASE_1_HSMS = """\
### Domain A — Subscriptions & Recurring Charges

**A01 — Forgotten subscription**
Trigger: Recurring charge from a service with no related activity in 90+ days.
Output: "You have been charged $X/month for [Service] for [N] months. \
No related activity detected. Still using this?"

**A02 — Duplicate subscription**
Trigger: Two charges from same or similar merchant in the same billing cycle.
Output: "You appear to be charged twice for [Service] — $X on [date] and \
$X on [date]. This may be a duplicate."

**A03 — Subscription price increase**
Trigger: Recurring charge amount increased compared to prior months.
Output: "[Service] quietly increased from $X to $Y this month. \
That is $Z more per year."

**A04 — Free trial converted to paid**
Trigger: New recurring charge appears from a merchant with no prior history.
Output: "[Service] started charging you $X/month. This may be a free \
trial that converted to paid."

### Domain B — Promotional Traps & Contract Terms

**B01 — Promotional rate expiry**
Trigger: Charge increases significantly after 6 or 12 months from first charge.
Output: "[Merchant] charged you $X this month vs $Y last month. You may \
have signed up for a promotional rate that has now ended."

**B03 — Auto-renewal trap**
Trigger: Annual charge appears from a service or credit card with no recent \
activity. This includes credit card annual fees on cards with no spending \
in recent months. Look for merchant strings containing "ANNUAL FEE", \
"ANNUAL MEMBERSHIP", or similar. Check whether ANY other charges from that \
card issuer appear in recent statements — if not, the card is dormant.
Output: "[Service/Card] auto-renewed for $X. You have not used this \
in [N] months. Consider whether the benefits still justify the cost."

### Domain C — Banking & Cash Management

**C02 — Monthly maintenance fee**
Trigger: Recurring bank fee charge identified in transactions.
Output: "You paid $X in monthly bank fees this year. Many banks waive \
this fee with a minimum balance or direct deposit."

**C03 — Overdraft fee pattern**
Trigger: Repeated overdraft charges within 90 days (frequency pattern).
Output: "You have paid $X in overdraft fees in the last 90 days. \
Consider linking a backup account or enabling overdraft protection."

**C04 — ATM fee pattern**
Trigger: Repeated out-of-network ATM charges.
Output: "You paid $X in ATM fees last year. Switching to in-network ATMs \
could eliminate this cost entirely."

**C05 — Overdraft cascade**
Trigger: Multiple overdraft or NSF fees (3+) appearing within a short window \
(1–5 days). This is a cascade — one triggering event caused a chain reaction \
of fees. Different from C03 which tracks frequency over 90 days.
Output: "You were hit with [N] overdraft fees totaling $X between [date] \
and [date]. This looks like a cascade from a single event. Contact your \
bank — they may reverse some of these fees if you ask."

**C06 — Suspicious verification charge**
Trigger: A charge under $2.00 from an unrecognized merchant, especially with \
generic names containing VERIFY, TEST, AUTH, VALIDATION, PENDING, or similar. \
Also flag charges of exactly $1.00, $0.50, or $0.01 from merchants with no \
prior transaction history.
Output: "A $X charge appeared from [Merchant] on [date]. This looks like \
a card verification charge — someone may be testing your card number. \
Check your card activity and consider freezing the card if unrecognized."

**C07 — Idle cash in checking**
Trigger: Average checking balance exceeds $5,000 across 2+ consecutive \
statement periods with no outgoing transfer to a savings or investment \
account detected.
Output: "Your checking account has held an average balance of $X over the \
last [N] months with no savings transfers detected. This cash is earning \
nothing — even a basic savings account would generate $Y per year."

### Domain F — Insurance

**F01 — Insurance premium creep**
Trigger: Annual insurance renewal charge increases more than 5% YoY.
Output: "Your [insurance] premium increased $X at renewal — a [Y]% \
increase. It may be worth getting competing quotes."

### Domain G — Utilities & Telecom

**G01 — Utility rate increase**
Trigger: Monthly utility charge increases beyond seasonal norm.
Output: "Your [utility] bill increased $X this month beyond typical \
seasonal variation. This may reflect a rate change by your provider."

### Domain H — Debt & Loans

**H01 — High interest credit card balance**
Trigger: Credit card payment detected but balance not paid in full \
(varying payment amounts, not matching statement balance).
Output: "You are carrying a balance on [Card]. At a typical APR, you \
may be paying significant interest. Paying in full each month eliminates \
this cost."

**H04 — Minimum payment trap**
Trigger: Credit card minimum-like payment (small, round, consistent) \
for 3+ consecutive months.
Output: "You have made minimum payments on [Card] for [N] months. \
This significantly extends payoff time and total interest paid."

### Domain I — Housing

**I02 — Escrow adjustment**
Trigger: Mortgage payment amount changes unexpectedly.
Output: "Your mortgage payment changed by $X this month. This is likely \
an escrow adjustment — not a change to your principal or interest rate."

**I05 — HOA fee increase**
Trigger: HOA charge increases year over year.
Output: "Your HOA fees increased $X this year — a [Y]% increase. \
HOA reserve fund disclosures are typically available to homeowners annually."

### Domain L — Income & Cash Flow

**L01 — Income drop detected**
Trigger: A recurring deposit (paycheck, direct deposit) decreases by more \
than $100 or 5% compared to the prior cycle. Compare the same deposit source \
(same ACH company name or similar description) across consecutive statements.
Output: "Your [Source] deposit dropped from $X to $Y — a decrease of $Z \
per cycle. If this is a paycheck, verify your withholding, hours, or \
deductions have not changed unexpectedly."

**L02 — Income source disappeared**
Trigger: A recurring deposit source that appeared in 3+ prior statements \
is absent from the current statement.
Output: "No deposit from [Source] appeared this period. This source \
has been consistent for the last [N] months. Verify this is expected."

### Domain N — Spending Trends

**N01 — Category spending surge**
Trigger: Total spending at a merchant or merchant category increases by \
more than 100% or $200+ compared to the average of the prior 3 months. \
Compare the current statement period's total for that merchant/category \
against the historical average.
Output: "Your spending at [Merchant/Category] surged to $X this month — \
up from an average of $Y. That is a [Z]% increase."

**N02 — Merchant frequency spike**
Trigger: Transaction COUNT for a specific merchant doubles or more compared \
to the prior 3-month average. This catches behavioral changes (e.g. going \
from 5 Starbucks visits to 12) even when individual transaction amounts \
stay the same.
Output: "You had [N] transactions at [Merchant] this month — up from an \
average of [M]. Your total spend there was $X vs the usual $Y."
"""


def build_hsm_analysis_prompt() -> str:
    """Return the system prompt for HSM analysis."""
    return f"""\
You are a financial analyst for a family finance app called JAR.

Your job is to review bank transaction data and identify "Holy Shit Moments" \
(HSMs) — financial insights the family likely missed. These are patterns like \
forgotten subscriptions, sneaky price increases, fee traps, and other \
money-saving opportunities hiding in plain sight.

## Phase I HSMs — Transaction Pattern Detection

Only report findings from this list. Do NOT invent new categories.

{PHASE_1_HSMS}

## Rules

1. Only report findings you are CONFIDENT about based on the data provided.
2. Each finding MUST reference specific dollar amounts and dates/periods.
3. Use the output templates above — adapt them with real data.
4. If a merchant name is in ACH format (e.g. "NFLX DIGITAL"), use the \
   human-readable name (e.g. "Netflix") in your output.
5. Calculate amount_impact as the estimated ANNUAL cost or savings.
6. If no HSMs are found, return an empty JSON array: []
7. Do NOT flag normal expected charges (rent, mortgage, utilities at stable \
   rates, regular groceries, etc.) unless they match an HSM trigger.
8. For price increases (A03, B01, F01, G01, I02, I05), only flag if you \
   can see the PREVIOUS lower amount in the historical data.
9. Severity: "critical" for fee cascades (C05), fraud signals (C06), \
   income drops (L01/L02), or >$200/year impact. \
   "warning" for price increases, forgotten services, spending surges. \
   "info" for minor observations or first-time patterns.
10. For C05 (overdraft cascade), look at the FEE TRANSACTIONS section \
    for clusters of fees within a few days — do not confuse with C03.
11. For C06 (verification charges), only flag charges from merchants \
    with NO prior history AND suspicious amounts ($0.01–$2.00).
12. For N01/N02 (spending trends), use the CATEGORY SPENDING TRENDS \
    and MERCHANT FREQUENCY sections. Only flag significant deviations \
    (100%+ increase or $200+ absolute change for N01, 2x count for N02).
13. For L01/L02 (income changes), use the INCOME HISTORY section. \
    Only flag if the same source shows a clear drop or disappearance.

## Output Format

Return ONLY a JSON array. No markdown fences, no explanation, no other text.

Each element:
{{
  "hsm_id": "A03",
  "title": "Subscription price increase detected",
  "description": "Netflix quietly increased from $15.49 to $22.99 this month. That is $90.00 more per year.",
  "amount_impact": 90.00,
  "severity": "warning",
  "merchant": "Netflix"
}}
"""


def build_data_context(
    current_stmt: dict,
    current_txns: list[dict],
    recurring_history: list[dict],
    fee_txns: list[dict],
    income_history: list[dict] | None = None,
    category_trends: list[dict] | None = None,
    merchant_frequency: list[dict] | None = None,
    balance_history: list[dict] | None = None,
) -> str:
    """Build the data context string sent as the user message.

    Args:
        current_stmt: Current statement row (dict with period_start, etc.).
        current_txns: Transactions from the just-imported statement.
        recurring_history: Recurring merchant charge history across all statements.
        fee_txns: All fee-type transactions across all statements.
        income_history: Recurring deposit sources with amounts by period.
        category_trends: Spending totals by merchant per statement period.
        merchant_frequency: Transaction counts by merchant per statement period.
        balance_history: Statement balances over time (for idle cash detection).

    Returns:
        Formatted text block the LLM can analyze.
    """
    lines = []

    # ── Current statement summary ──
    lines.append("## Current Statement (just imported)")
    lines.append(f"Period: {current_stmt['period_start']} to {current_stmt['period_end']}")
    lines.append(f"Account: {current_stmt.get('account_id', 'unknown')}")
    lines.append(f"Opening Balance: ${current_stmt['opening_balance']:,.2f}")
    lines.append(f"Closing Balance: ${current_stmt['closing_balance']:,.2f}")
    lines.append("")

    # ── Current transactions ──
    lines.append("## Current Statement Transactions")
    lines.append("DATE | DESCRIPTION | AMOUNT | TYPE | ACH_COMPANY")
    for t in current_txns:
        desc = (t.get("description_raw") or "")[:80]
        ach = t.get("ach_company_name") or ""
        lines.append(
            f"{t['date']} | {desc} | ${t['amount']:,.2f} | "
            f"{t.get('type', '')} | {ach}"
        )
    lines.append("")

    # ── Balance history (for idle cash detection — C07) ──
    if balance_history:
        lines.append("## Balance History (all statements)")
        lines.append("PERIOD_END | OPENING_BALANCE | CLOSING_BALANCE | AVG_BALANCE")
        for b in balance_history:
            avg = (b["opening_balance"] + b["closing_balance"]) / 2
            lines.append(
                f"{b['period_end']} | ${b['opening_balance']:,.2f} | "
                f"${b['closing_balance']:,.2f} | ${avg:,.2f}"
            )
        lines.append("")

    # ── Income history (for L01/L02 — income drops) ──
    if income_history:
        lines.append("## Income History (recurring deposits by source)")
        lines.append("SOURCE | MONTHS_SEEN | AMOUNTS (oldest to newest)")
        for inc in income_history:
            amounts_str = ", ".join(f"${a:,.2f}" for a in inc["amounts"])
            lines.append(f"{inc['source']} | {inc['months_seen']} | {amounts_str}")
        lines.append("")

    # ── Recurring charge history ──
    if recurring_history:
        lines.append("## Recurring Charge History (merchants seen in 2+ months)")
        lines.append("MERCHANT | MONTHS_SEEN | AMOUNTS (oldest to newest)")
        for r in recurring_history:
            amounts_str = ", ".join(f"${a:,.2f}" for a in r["amounts"])
            lines.append(f"{r['merchant']} | {r['months_seen']} | {amounts_str}")
        lines.append("")

    # ── Category spending trends (for N01 — spending surge) ──
    if category_trends:
        lines.append("## Category Spending Trends (merchant totals by period)")
        lines.append("MERCHANT | PERIOD | TOTAL_SPENT | TXN_COUNT")
        for ct in category_trends:
            lines.append(
                f"{ct['merchant']} | {ct['period']} | "
                f"${ct['total']:,.2f} | {ct['count']}"
            )
        lines.append("")

    # ── Merchant frequency (for N02 — frequency spike) ──
    if merchant_frequency:
        lines.append("## Merchant Frequency (transaction counts by period)")
        lines.append("MERCHANT | PERIOD | TXN_COUNT | TOTAL_SPENT")
        for mf in merchant_frequency:
            lines.append(
                f"{mf['merchant']} | {mf['period']} | "
                f"{mf['count']} | ${mf['total']:,.2f}"
            )
        lines.append("")

    # ── Fee transactions ──
    if fee_txns:
        lines.append("## Fee Transactions (all statements, sorted by date)")
        lines.append("DATE | DESCRIPTION | AMOUNT | STATEMENT_PERIOD")
        for f in fee_txns:
            desc = (f.get("description_raw") or "")[:60]
            lines.append(
                f"{f['date']} | {desc} | ${f['amount']:,.2f} | "
                f"{f.get('period_end', '')}"
            )
        lines.append("")

    if not recurring_history and not fee_txns and not income_history:
        lines.append("## Historical Data")
        lines.append("This is the first statement imported. No historical comparison available.")
        lines.append("")

    return "\n".join(lines)
