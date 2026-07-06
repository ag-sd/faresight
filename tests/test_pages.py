"""Served-HTML assertions for the static pages (no JS harness in this project).

Income and Expenses are both rendered from the shared ``account_page.html``
template (see ``INCOME_CTX`` / ``EXPENSES_CTX`` in ``app/faresight.py``); the
navbar/head/scripts come from the shared ``base.html`` layout.
"""


def _income_html(client):
    r = client.get("/income")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    return r.text


def _expenses_html(client):
    r = client.get("/expenses")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    return r.text


# ── Income (bank accounts + transfers) ────────────────────────────────────────
def test_income_page_serves_html(client):
    _income_html(client)


def test_income_page_title(client):
    assert "Faresight — Income" in _income_html(client)


def test_income_page_has_activity_card(client):
    html = _income_html(client)
    assert 'id="activityTable"' in html
    assert "Account Activity" in html


def test_income_page_bank_creation_only(client):
    html = _income_html(client)
    assert "credit_card" not in html
    assert "openAddAccount('checking')" in html
    assert "openAddAccount('savings')" in html


def test_income_page_has_transfers_tab(client):
    html = _income_html(client)
    assert 'id="tab-transfers"' in html
    assert 'id="transfersTable"' in html


def test_income_page_scope_config(client):
    html = _income_html(client)
    assert 'accountScope: "bank"' in html
    assert "showTransfers: true" in html


def test_income_page_accordion_structure(client):
    html = _income_html(client)
    # Single-panel Bootstrap accordion wraps the Accounts/Transfers tabs,
    # styled like the standard cards (rounded + shadow-sm, not flush)
    assert 'class="accordion shadow-sm mb-4"' in html
    assert "accordion-flush" not in html
    assert "accordion-item" in html
    assert "accordion-button" in html
    assert 'data-bs-target="#collapseAcctCard"' in html
    # Panel starts collapsed (no "show"); button carries the collapsed state
    assert 'id="collapseAcctCard" class="accordion-collapse collapse"' in html
    assert "accordion-button collapsed" in html
    # Tabs live inside the collapsible panel, with nav-underline styling
    assert 'id="tab-accounts"' in html
    assert "nav-underline" in html
    assert "nav-tabs" not in html


# ── Expenses (credit cards only, no transfers) ────────────────────────────────
def test_expenses_page_serves_html(client):
    _expenses_html(client)


def test_expenses_page_title(client):
    assert "Faresight — Expenses" in _expenses_html(client)


def test_expenses_page_has_activity_card(client):
    html = _expenses_html(client)
    assert 'id="activityTable"' in html
    assert "Account Activity" in html


def test_expenses_page_credit_card_creation_only(client):
    html = _expenses_html(client)
    assert "openAddAccount('credit_card')" in html
    assert "openAddAccount('checking')" not in html
    assert "openAddAccount('savings')" not in html


def test_expenses_page_add_is_direct_button_not_dropdown(client):
    # Single account type → a plain "Add Credit Card" button, no dropdown.
    html = _expenses_html(client)
    assert 'onclick="openAddAccount(\'credit_card\')"' in html
    assert "dropdown-toggle" not in html
    assert "Add Credit Card" in html


def test_income_page_add_is_dropdown(client):
    # Multiple account types → the "Add Account" dropdown is kept.
    html = _income_html(client)
    assert "dropdown-toggle" in html


def test_expenses_page_no_transfers_tab(client):
    html = _expenses_html(client)
    assert 'id="tab-transfers"' not in html
    assert "transfersTable" not in html


def test_expenses_page_scope_config(client):
    html = _expenses_html(client)
    assert 'accountScope: "credit_card"' in html
    assert "showTransfers: false" in html


# ── Shared layout / nav ───────────────────────────────────────────────────────
def test_config_endpoint_exposes_top_card_page_limit(client):
    cfg = client.get("/api/config").json()
    assert cfg["top_card_page_limit"] == 5
    assert isinstance(cfg["top_card_page_limit"], int)


def test_nav_links_present_on_all_pages(client):
    for path in ("/", "/income", "/expenses", "/upload"):
        html = client.get(path).text
        assert 'href="/"' in html
        assert 'href="/income"' in html
        assert 'href="/expenses"' in html
        assert 'href="/upload"' in html


def test_nav_marks_active_page(client):
    cases = {
        "/": "Dashboard",
        "/income": "Income",
        "/expenses": "Expenses",
        "/upload": "Upload",
    }
    for path, label in cases.items():
        html = client.get(path).text
        assert f'aria-current="page" href="{path}">{label}</a>' in html


def test_old_accounts_route_gone(client):
    assert client.get("/accounts").status_code == 404


def test_dashboard_has_no_transactions_table(client):
    """The dashboard is a pure overview — transaction browsing/editing lives on
    the Income/Expenses pages. The table and the modals its pen column opened
    are gone."""
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert 'id="txTable"' not in html
    assert "Export CSV" not in html
    assert 'id="editCategoryModal"' not in html
    assert 'id="createRuleModal"' not in html


def test_income_page_has_create_rule_modal(client):
    html = _income_html(client)
    assert 'id="createRuleModal"' in html
    assert 'id="ruleDescription"' in html
    assert 'id="ruleCategory"' in html
    assert 'id="ruleImporter"' in html
    assert 'fa-bookmark' in html


def test_dashboard_has_badges_row(client):
    html = client.get("/").text
    for el_id in ("badgeNetWorth", "badgeSpend", "badgeIncome", "badgeSavingsRate",
                  "badgeSpendDelta", "badgeIncomeDelta"):
        assert f'id="{el_id}"' in html
    assert "Net Worth" in html
    assert "Savings Rate" in html


def test_dashboard_has_cashflow_chart(client):
    html = client.get("/").text
    assert 'id="cashflowChart"' in html
    assert 'id="cashflowYear"' in html
    assert "Cash Flow" in html


def test_dashboard_has_insights_cards(client):
    html = client.get("/").text
    for el_id in ("recurringTotal", "recurringList", "topMoversList", "topMerchantsList"):
        assert f'id="{el_id}"' in html
    assert "Recurring &amp; Subscriptions" in html
    assert "Spending Insights" in html


# ── Upload page: tabbed import card ───────────────────────────────────────────
def test_upload_page_import_card_is_tabbed(client):
    html = client.get("/upload").text
    assert 'nav-underline' in html
    for anchor in ('id="tab-import"', 'id="pane-import"',
                   'id="tab-recent"', 'id="pane-recent"'):
        assert anchor in html


def test_upload_page_heading_renamed_to_import(client):
    html = client.get("/upload").text
    assert 'Import Transactions' not in html
    assert '>Import</button>' in html


def test_upload_page_recent_uploads_table_in_pane(client):
    html = client.get("/upload").text
    # Table moved into the Recent Uploads tab pane rather than a standalone card.
    assert 'id="pane-recent"' in html
    assert 'id="importTable"' in html


def test_upload_page_has_progress_and_controls(client):
    html = client.get("/upload").text
    assert 'id="uploadProgressWrap"' in html
    assert 'id="uploadProgressText"' in html
    assert 'id="uploadProgressBar"' in html
    assert 'id="accountSelect"' in html
    assert 'id="importerSelect"' in html
    assert 'id="uploadBtn"' in html
    assert 'id="resultModal"' in html


# ── Upload page: CLASSIFICATION card ──────────────────────────────────────────
def test_upload_page_classification_card_is_tabbed(client):
    html = client.get("/upload").text
    assert '>Classification<' in html
    for anchor in ('id="tab-aicat"', 'id="pane-aicat"',
                   'id="tab-rules"', 'id="pane-rules"'):
        assert anchor in html


def test_upload_page_classification_tab_contents(client):
    html = client.get("/upload").text
    # Pending table + the two AI elements moved into the AI Categorization tab.
    assert 'id="pendingTxTable"' in html
    assert 'id="categorizerTracker"' in html
    assert 'id="categorizerStatusPill"' in html
    # Rules table lives in the second tab.
    assert 'id="rulesTableWrap"' in html


def test_upload_page_includes_edit_and_rule_modals(client):
    html = client.get("/upload").text
    assert 'id="editCategoryModal"' in html
    assert 'id="createRuleModal"' in html
