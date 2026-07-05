"""Served-HTML assertions for the static pages (no JS harness in this project)."""


def _accounts_html(client):
    r = client.get("/accounts")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    return r.text


def test_accounts_page_serves_html(client):
    _accounts_html(client)


def test_config_endpoint_exposes_top_card_page_limit(client):
    cfg = client.get("/api/config").json()
    assert cfg["top_card_page_limit"] == 5
    assert isinstance(cfg["top_card_page_limit"], int)


def test_accounts_page_has_activity_card(client):
    html = _accounts_html(client)
    assert 'id="activityTable"' in html
    assert "Account Activity" in html


def test_accounts_page_summary_widget_removed(client):
    html = _accounts_html(client)
    for stat_id in ("statTotal", "statActive", "statCC", "statBank"):
        assert stat_id not in html
    assert "Account Summary" not in html


def test_accounts_page_no_credit_card_creation(client):
    html = _accounts_html(client)
    assert "credit_card" not in html
    assert "openAddAccount('checking')" in html
    assert "openAddAccount('savings')" in html


def test_dashboard_has_create_rule_modal(client):
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert 'id="createRuleModal"' in html
    assert 'id="ruleDescription"' in html
    assert 'id="ruleCategory"' in html
    assert 'id="ruleImporter"' in html
    assert 'fa-bookmark' in html


def test_accounts_page_has_create_rule_modal(client):
    html = _accounts_html(client)
    assert 'id="createRuleModal"' in html
    assert 'id="ruleDescription"' in html
    assert 'id="ruleCategory"' in html
    assert 'id="ruleImporter"' in html
    assert 'fa-bookmark' in html


def test_accounts_page_accordion_structure(client):
    html = _accounts_html(client)
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
    assert 'id="tab-transfers"' in html
    assert "nav-underline" in html
    assert "nav-tabs" not in html
    # The activity card is not collapsible; the old standalone tab nav is gone
    assert "collapseActivity" not in html
    assert '<ul class="nav nav-tabs mb-0"' not in html
