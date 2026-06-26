// ── State ─────────────────────────────────────────────────────────────────────
let _editingAccountId = null;
let accountsTable, transfersTable;
let _allAccounts = [];
let _bankLogos = {};

const ACCOUNT_TYPE_LABELS = {
  checking: 'Checking',
  savings: 'Savings',
  credit_card: 'Credit Card',
};

// ── Tabulator: accounts ───────────────────────────────────────────────────────
function initAccountsTable() {
  accountsTable = new Tabulator('#accountsTable', {
    data: [],
    layout: 'fitColumns',
    movableColumns: true,
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
        title: 'Notes', field: 'notes',
        headerFilter: 'input', widthGrow: 3,
        formatter: (cell) => cell.getValue() ? esc(String(cell.getValue())) : '—',
      },
      {
        title: '', headerSort: false, hozAlign: 'center', width: 90,
        formatter: (cell) => {
          const active = cell.getRow().getData().is_active;
          const dis = active ? '' : ' disabled';
          return `<div class="d-flex gap-1 justify-content-center">` +
            `<button class="btn btn-primary btn-sm"${dis} title="Edit" aria-label="Edit"><i class="fa-regular fa-pen-to-square"></i></button>` +
            `<button class="btn btn-secondary btn-sm"${dis} title="Deactivate" aria-label="Deactivate"><i class="fa-regular fa-trash-can"></i></button>` +
            `</div>`;
        },
        cellClick: (_e, cell) => {
          const btn = _e.target.closest('button');
          if (!btn || btn.disabled) return;
          const data = cell.getRow().getData();
          if (btn.classList.contains('btn-primary')) {
            openEditAccount(data);
          } else if (btn.classList.contains('btn-secondary')) {
            deactivateAccount(data.id);
          }
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
  return esc(acct.nickname);
}

function initTransfersTable() {
  transfersTable = new Tabulator('#transfersTable', {
    data: [],
    layout: 'fitColumns',
    movableColumns: true,
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
  const accounts = await api('/api/accounts');
  _allAccounts = accounts;

  document.getElementById('statTotal').textContent  = accounts.length;
  document.getElementById('statActive').textContent = accounts.filter(a => a.is_active).length;
  document.getElementById('statCC').textContent     = accounts.filter(a => a.account_type === 'credit_card').length;
  document.getElementById('statBank').textContent   = accounts.filter(a => a.account_type === 'checking' || a.account_type === 'savings').length;

  refreshTransfers(accounts);
  accountsTable.setData(accounts);
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

// ── Modal open: edit ──────────────────────────────────────────────────────────
function openEditAccount(account) {
  _editingAccountId = account.id;
  document.getElementById('addAccountModalTitle').textContent = 'Edit Account';
  document.getElementById('acctId').value = account.id;
  document.getElementById('acctName').value = account.bank;
  document.getElementById('acctNickname').value = account.name;
  document.getElementById('acctNumber').value = account.account_number;
  document.getElementById('acctType').value = account.account_type;
  document.getElementById('acctNotes').value = account.notes ?? '';
  populateSourceSelect(account.id);
  document.getElementById('acctSourceId').value = account.source_account_id ?? '';
  document.getElementById('acctSourceAmount').value = account.source_amount ?? '';
  document.getElementById('acctSourceFreq').value = account.source_frequency ?? '';
  bootstrap.Modal.getOrCreateInstance(document.getElementById('addAccountModal')).show();
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

// ── Delete (soft) ─────────────────────────────────────────────────────────────
async function deactivateAccount(id) {
  if (!confirm('Deactivate this account?')) return;
  try {
    await api(`/api/accounts/${id}`, { method: 'PATCH', body: JSON.stringify({ is_active: false }) });
  } catch (err) {
    let msg = 'Could not deactivate account.';
    try { msg = JSON.parse(err.message).detail; } catch {}
    alert(msg);
    return;
  }
  await refreshAccounts();
}

// ── Boot ──────────────────────────────────────────────────────────────────────
initAccountsTable();
initTransfersTable();
api('/api/accounts/bank-logos').then(m => { _bankLogos = m; });
refreshAccounts();

document.getElementById('tab-transfers').addEventListener('shown.bs.tab', () => {
  transfersTable.redraw(true);
});
