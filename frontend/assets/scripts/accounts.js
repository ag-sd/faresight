// ── Page config ───────────────────────────────────────────────────────────────
// Injected by the template (see account_page.html). `accountScope` is the
// transactions `account_type` filter ('bank' or 'credit_card'); `showTransfers`
// toggles the Transfers tab (Income only).
const { accountScope, showTransfers } = window.PAGE_CONFIG;

// ── State ─────────────────────────────────────────────────────────────────────
let _editingAccountId = null;
let _detailsAccount = null;
let accountsTable, transfersTable, activityTable;
let _allAccounts = [];
let _bankLogos = {};
let _topCardPageLimit = 5;  // overwritten from /api/config at boot
let _editTxId = null;

const ACCOUNT_TYPE_LABELS = {
  checking: 'Checking',
  savings: 'Savings',
  credit_card: 'Credit Card',
};

// Which account types the visible accounts list shows for this page.
function inScope(a) {
  return accountScope === 'credit_card'
    ? a.account_type === 'credit_card'
    : a.account_type === 'checking' || a.account_type === 'savings';
}

// ── Tabulator: accounts ───────────────────────────────────────────────────────
function initAccountsTable() {
  accountsTable = new Tabulator('#accountsTable', {
    data: [],
    layout: 'fitColumns',
    movableColumns: true,
    pagination: true,
    paginationSize: _topCardPageLimit,
    columns: [
      {
        title: '', field: 'bank', headerSort: false, width: 56, hozAlign: 'center',
        formatter: (cell) => {
          const val = cell.getValue() || '';
          const svg = _bankLogos[val.toLowerCase().trim()];
          if (svg) {
            return `<img src="/static/assets/images/banks/${esc(svg)}" alt="${esc(val)}" title="${esc(val)}" class="bank-logo">`;
          }
          return `<span title="${esc(val)}">${esc(val)}</span>`;
        },
      },
      { title: 'Name', field: 'name', headerFilter: 'input', widthGrow: 2 },
      {
        title: 'Account #', field: 'account_number',
        headerFilter: 'input', width: 140, cssClass: 'font-monospace',
      },
      {
        title: 'Type', field: 'account_type',
        headerFilter: 'input', width: 130,
        formatter: (cell) => ACCOUNT_TYPE_LABELS[cell.getValue()] ?? esc(String(cell.getValue())),
      },
      {
        title: 'Balance', field: 'current_balance', hozAlign: 'right', width: 140,
        formatter: (cell) => {
          const val = cell.getValue();
          if (val == null) return '<span class="text-muted">—</span>';
          return '$' + parseFloat(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        },
      },
      {
        title: 'Status', field: 'is_active', width: 110, sorter: 'boolean',
        formatter: (cell) => cell.getValue()
          ? '<span class="badge rounded-pill bg-success">Active</span>'
          : '<span class="badge rounded-pill bg-secondary">Inactive</span>',
      },
      {
        title: 'Source', headerSort: false, widthGrow: 1,
        formatter: (cell) => {
          const data = cell.getRow().getData();
          if (!data.source_account_id) return '—';
          const src = _allAccounts.find(a => a.id === data.source_account_id);
          const name = src ? esc(src.name) : `#${data.source_account_id}`;
          const parts = [name];
          if (data.source_amount != null) parts.push('$' + parseFloat(data.source_amount).toFixed(2));
          if (data.source_frequency) parts.push(data.source_frequency);
          return parts.join(' · ');
        },
      },
      {
        title: '', headerSort: false, hozAlign: 'center', width: 60,
        formatter: () =>
          `<button class="btn btn-outline-secondary btn-sm" title="Details" aria-label="Details">` +
          `<i class="fa-regular fa-eye"></i></button>`,
        cellClick: (_e, cell) => {
          if (!_e.target.closest('button')) return;
          openDetailsAccount(cell.getRow().getData());
        },
      },
    ],
  });
}

// ── Tabulator: transfers ──────────────────────────────────────────────────────
function buildAccountTooltip(acct) {
  const type = ACCOUNT_TYPE_LABELS[acct.account_type] ?? esc(String(acct.account_type));
  const badge = acct.is_active
    ? '<span class="badge bg-success">Active</span>'
    : '<span class="badge bg-secondary">Inactive</span>';
  return `<strong>${esc(acct.bank)}</strong><br>#${esc(acct.account_number)} · ${type}<br>${badge}`;
}

function accountCellFormatter(cell, _p, onRendered) {
  const id = cell.getValue();
  const acct = _allAccounts.find(a => a.id === id);
  if (!acct) return `#${id}`;
  const el = cell.getElement();
  el.setAttribute('data-bs-toggle', 'tooltip');
  el.setAttribute('data-bs-html', 'true');
  el.setAttribute('title', buildAccountTooltip(acct));
  onRendered(() => bootstrap.Tooltip.getOrCreateInstance(el));
  return esc(acct.name);
}

function initTransfersTable() {
  transfersTable = new Tabulator('#transfersTable', {
    data: [],
    layout: 'fitColumns',
    movableColumns: true,
    pagination: true,
    paginationSize: _topCardPageLimit,
    placeholder: 'No transfers configured.',
    columns: [
      { title: 'From', field: 'fromId', widthGrow: 2, formatter: accountCellFormatter },
      { title: 'To',   field: 'toId',   widthGrow: 2, formatter: accountCellFormatter },
      {
        title: 'Amount', field: 'amount', sorter: 'number',
        hozAlign: 'right', cssClass: 'amount', width: 130,
        formatter: (cell) => {
          const v = cell.getValue();
          return v != null ? '$' + parseFloat(v).toFixed(2) : '—';
        },
      },
      {
        title: 'Frequency', field: 'frequency', width: 130,
        formatter: (cell) => {
          const v = cell.getValue();
          return v ? v.charAt(0).toUpperCase() + v.slice(1) : '—';
        },
      },
    ],
  });
}

function refreshTransfers(accounts) {
  const rows = accounts
    .filter(a => a.source_account_id != null)
    .map(a => ({
      fromId:    a.source_account_id,
      toId:      a.id,
      amount:    a.source_amount,
      frequency: a.source_frequency,
    }));
  transfersTable.setData(rows);
}

// ── Accounts table ────────────────────────────────────────────────────────────
async function refreshAccounts() {
  // Fetch ALL accounts: transfers rows, source dropdowns, and tooltips may
  // reference accounts of any type (e.g. CC autopay from checking). Only the
  // visible accounts list is restricted to this page's scope.
  const accounts = await api('/api/accounts');
  _allAccounts = accounts;

  if (showTransfers) refreshTransfers(accounts);
  accountsTable.setData(accounts.filter(inScope));
}

// ── Tabulator: account activity (transactions in this page's scope) ──────────
function initActivityTable() {
  activityTable = new Tabulator('#activityTable', {
    ajaxURL: '/api/transactions',
    ajaxParams: () => ({ account_type: accountScope }),
    pagination: true,
    paginationMode: 'remote',
    paginationSize: 25,
    layout: 'fitColumns',
    movableColumns: true,
    initialSort: [{ column: 'date', dir: 'desc' }],
    dataSendParams: { size: 'limit' },
    ajaxResponse: (_url, _p, response) => ({
      data: response.data,
      last_page: Math.ceil(response.total / response.limit),
    }),
    columns: [
      {
        title: 'Date', field: 'date', sorter: 'date',
        headerFilter: 'input', width: 120,
      },
      {
        title: 'Description', field: 'description',
        headerFilter: 'input', widthGrow: 3,
        formatter: (cell) => esc(String(cell.getValue())),
      },
      {
        // Fixed width sized to the widest pill ("Entertainment & Subscriptions");
        // the freed grow space goes to Source below.
        title: 'Category', field: 'model_category', width: 210,
        headerFilter: 'input',
        formatter: modelCategoryFormatter,
      },
      {
        title: 'Source', field: 'account_id', widthGrow: 2,
        formatter: (cell) => {
          const id = cell.getValue();
          if (!id) return '—';
          const acct = _allAccounts.find(a => a.id === id);
          return acct ? esc(acct.name) : String(id);
        },
      },
      {
        title: 'Amount', field: 'amount', sorter: 'number',
        headerFilter: 'input', hozAlign: 'right', cssClass: 'amount', width: 130,
        formatter: amountFormatter,
      },
      {
        title: '', headerSort: false, hozAlign: 'center', width: 48,
        formatter: () => '<button class="btn btn-link btn-sm p-0 text-secondary"><i class="fa-regular fa-pen-to-square"></i></button>',
        cellClick: (_e, cell) => openEditModal(cell.getRow().getData()),
      },
    ],
  });
}

// ── Edit category modal ───────────────────────────────────────────────────────
function openEditModal(tx) {
  _editTxId = tx.id;
  document.getElementById('editTxDate').textContent = tx.date;
  document.getElementById('editTxDescription').textContent = tx.description;
  const amount = parseFloat(tx.amount);
  const neg = amount < 0;
  const el = document.getElementById('editTxAmount');
  el.textContent = (neg ? '-' : '+') + '$' + Math.abs(amount).toFixed(2);
  el.className = neg ? 'text-danger' : 'text-success';
  const sel = document.getElementById('editCategorySelect');
  sel.innerHTML = Object.keys(CATEGORY_COLORS).sort()
    .map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
  sel.value = (tx.model_category && tx.model_category in CATEGORY_COLORS)
    ? tx.model_category : 'Other';
  new bootstrap.Modal(document.getElementById('editCategoryModal')).show();
}

async function saveCategory() {
  const category = document.getElementById('editCategorySelect').value;
  await api(`/api/transactions/${_editTxId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model_category: category, model_confidence: 10, user_modified_category: true }),
  });
  bootstrap.Modal.getInstance(document.getElementById('editCategoryModal')).hide();
  await activityTable.replaceData();
}

// ── Source account dropdown ───────────────────────────────────────────────────
function populateSourceSelect(excludeId) {
  const sel = document.getElementById('acctSourceId');
  sel.innerHTML = '<option value="">None</option>';
  _allAccounts
    .filter(a => a.is_active && a.id !== excludeId)
    .forEach(a => {
      sel.insertAdjacentHTML('beforeend',
        `<option value="${a.id}">${esc(a.name)} (${esc(a.bank)})</option>`);
    });
}

// ── Modal open: create ────────────────────────────────────────────────────────
function openAddAccount(type) {
  _editingAccountId = null;
  document.getElementById('addAccountModalTitle').textContent = {
    checking: 'Add Checking Account',
    savings: 'Add Savings Account',
    credit_card: 'Add Credit Card',
  }[type] ?? 'Add Account';
  document.getElementById('addAccountForm').reset();
  document.getElementById('acctId').value = '';
  document.getElementById('acctType').value = type;
  populateSourceSelect(null);
  bootstrap.Modal.getOrCreateInstance(document.getElementById('addAccountModal')).show();
}

// ── Details modal helpers ─────────────────────────────────────────────────────
function _setDetailsFormDisabled(disabled) {
  document.querySelectorAll('#detailsAccountForm input, #detailsAccountForm select, #detailsAccountForm textarea')
    .forEach(el => { el.disabled = disabled; });
}

// ── Modal open: details ───────────────────────────────────────────────────────
function openDetailsAccount(account) {
  _detailsAccount = account;
  document.getElementById('detailsAccountModalTitle').textContent = `${account.name} — Details`;
  document.getElementById('detailName').value = account.bank;
  document.getElementById('detailNickname').value = account.name;
  document.getElementById('detailNumber').value = account.account_number;
  document.getElementById('detailType').value = account.account_type;
  document.getElementById('detailNotes').value = account.notes ?? '';

  // Populate source select with all accounts (incl. inactive) for display
  const sel = document.getElementById('detailSourceId');
  sel.innerHTML = '<option value="">None</option>';
  _allAccounts
    .filter(a => a.id !== account.id)
    .forEach(a => sel.insertAdjacentHTML('beforeend',
      `<option value="${a.id}">${esc(a.name)} (${esc(a.bank)})</option>`));
  sel.value = account.source_account_id ?? '';

  document.getElementById('detailSourceAmount').value = account.source_amount ?? '';
  document.getElementById('detailSourceFreq').value = account.source_frequency ?? '';

  // Badge: show ACTIVE pill when a source transfer is configured
  document.getElementById('detailSourceBadge').classList.toggle('d-none', !account.source_account_id);
  // Accordion: always reset to collapsed on each open
  const _srcCollapse = document.getElementById('collapseDetailSource');
  _srcCollapse.classList.remove('show');
  const _srcBtn = document.querySelector('[data-bs-target="#collapseDetailSource"]');
  _srcBtn.classList.add('collapsed');
  _srcBtn.setAttribute('aria-expanded', 'false');

  _setDetailsFormDisabled(true);
  document.getElementById('detailsFooter').classList.remove('d-none');
  document.getElementById('detailsEditFooter').classList.add('d-none');
  document.getElementById('detailsDeactivateBtn').disabled = !account.is_active;
  bootstrap.Modal.getOrCreateInstance(document.getElementById('detailsAccountModal')).show();
}

// ── Details modal: enter / exit edit mode ─────────────────────────────────────
function enterEditMode() {
  // Re-populate source select with active accounts only for editing
  const currentSourceVal = document.getElementById('detailSourceId').value;
  const sel = document.getElementById('detailSourceId');
  sel.innerHTML = '<option value="">None</option>';
  _allAccounts
    .filter(a => a.is_active && a.id !== _detailsAccount.id)
    .forEach(a => sel.insertAdjacentHTML('beforeend',
      `<option value="${a.id}">${esc(a.name)} (${esc(a.bank)})</option>`));
  sel.value = currentSourceVal;

  _setDetailsFormDisabled(false);
  document.getElementById('detailsFooter').classList.add('d-none');
  document.getElementById('detailsEditFooter').classList.remove('d-none');
}

function exitEditMode() {
  openDetailsAccount(_detailsAccount);
}

// ── Details modal: save ───────────────────────────────────────────────────────
async function saveDetailsForm() {
  const fd = new FormData(document.getElementById('detailsAccountForm'));
  const sourceIdRaw = fd.get('source_account_id');
  const sourceAmtRaw = fd.get('source_amount');
  const sourceId = sourceIdRaw ? parseInt(sourceIdRaw, 10) : null;
  const body = {
    bank: fd.get('bank'),
    name: fd.get('name'),
    account_number: fd.get('account_number'),
    account_type: fd.get('account_type'),
    notes: fd.get('notes') || null,
    source_account_id: sourceId,
    source_amount: sourceId && sourceAmtRaw ? parseFloat(sourceAmtRaw) : null,
    source_frequency: sourceId ? (fd.get('source_frequency') || null) : null,
  };
  try {
    await api(`/api/accounts/${_detailsAccount.id}`, { method: 'PATCH', body: JSON.stringify(body) });
  } catch (err) {
    let msg = 'Could not save account.';
    try { msg = JSON.parse(err.message).detail; } catch {}
    alert(msg);
    return;
  }
  bootstrap.Modal.getInstance(document.getElementById('detailsAccountModal')).hide();
  await refreshAccounts();
}

// ── Details modal: deactivate ─────────────────────────────────────────────────
async function deactivateFromDetails() {
  if (!confirm('Deactivate this account?')) return;
  try {
    await api(`/api/accounts/${_detailsAccount.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: false }) });
  } catch (err) {
    let msg = 'Could not deactivate account.';
    try { msg = JSON.parse(err.message).detail; } catch {}
    alert(msg);
    return;
  }
  bootstrap.Modal.getInstance(document.getElementById('detailsAccountModal')).hide();
  await refreshAccounts();
}

// ── Modal submit (create + edit) ──────────────────────────────────────────────
async function submitAccountForm() {
  const form = document.getElementById('addAccountForm');
  if (!form.checkValidity()) { form.reportValidity(); return; }
  const fd = new FormData(form);

  const sourceIdRaw = fd.get('source_account_id');
  const sourceAmtRaw = fd.get('source_amount');
  const sourceId = sourceIdRaw ? parseInt(sourceIdRaw, 10) : null;

  const body = {
    bank: fd.get('bank'),
    name: fd.get('name'),
    account_number: fd.get('account_number'),
    account_type: fd.get('account_type'),
    notes: fd.get('notes') || null,
    source_account_id: sourceId,
    source_amount: sourceId && sourceAmtRaw ? parseFloat(sourceAmtRaw) : null,
    source_frequency: sourceId ? (fd.get('source_frequency') || null) : null,
  };

  try {
    if (_editingAccountId) {
      await api(`/api/accounts/${_editingAccountId}`, { method: 'PATCH', body: JSON.stringify(body) });
    } else {
      await api('/api/accounts', { method: 'POST', body: JSON.stringify(body) });
    }
  } catch (err) {
    let msg = 'Could not save account.';
    try { msg = JSON.parse(err.message).detail; } catch {}
    alert(msg);
    return;
  }

  bootstrap.Modal.getInstance(document.getElementById('addAccountModal')).hide();
  await refreshAccounts();
}

// ── Boot ──────────────────────────────────────────────────────────────────────
api('/api/accounts/bank-logos').then(m => { _bankLogos = m; });
(async () => {
  // Page limit must be known before the tables are constructed.
  try {
    const cfg = await api('/api/config');
    _topCardPageLimit = cfg.top_card_page_limit ?? _topCardPageLimit;
  } catch (_) { /* keep default */ }
  initAccountsTable();
  if (showTransfers) initTransfersTable();
  // Activity table's Source column needs _allAccounts before its first render.
  await refreshAccounts();
  initActivityTable();
})();

// Tabulator renders 0-height inside hidden containers — redraw once visible.
if (showTransfers) {
  document.getElementById('tab-transfers').addEventListener('shown.bs.tab', () => {
    transfersTable.redraw(true);
  });
}
document.getElementById('collapseAcctCard').addEventListener('shown.bs.collapse', () => {
  accountsTable.redraw(true);
  if (showTransfers) transfersTable.redraw(true);
});
