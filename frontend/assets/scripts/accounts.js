// ── State ─────────────────────────────────────────────────────────────────────
let _addAccountType = '';

const ACCOUNT_TYPE_LABELS = {
  checking: 'Checking',
  savings: 'Savings',
  credit_card: 'Credit Card',
};

// ── Accounts table ────────────────────────────────────────────────────────────
async function refreshAccounts() {
  const accounts = await api('/api/accounts');

  document.getElementById('statTotal').textContent  = accounts.length;
  document.getElementById('statActive').textContent = accounts.filter(a => a.is_active).length;
  document.getElementById('statCC').textContent     = accounts.filter(a => a.account_type === 'credit_card').length;
  document.getElementById('statBank').textContent   = accounts.filter(a => a.account_type === 'checking' || a.account_type === 'savings').length;

  const tbody = document.getElementById('accountsBody');
  if (!accounts.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-5">No accounts yet. Use "Add Account" to get started.</td></tr>';
    return;
  }

  tbody.innerHTML = accounts.map(a => {
    const pill = a.is_active
      ? '<span class="badge rounded-pill bg-success">Active</span>'
      : '<span class="badge rounded-pill bg-secondary">Inactive</span>';
    const action = a.is_active
      ? `<button class="btn btn-outline-danger btn-sm" onclick="deactivateAccount(${a.id})">Deactivate</button>`
      : '<span class="text-muted small">—</span>';
    return `<tr>
      <td>${esc(a.name)}</td>
      <td>${esc(a.nickname)}</td>
      <td class="font-monospace small">${esc(a.account_number)}</td>
      <td>${ACCOUNT_TYPE_LABELS[a.account_type] ?? esc(a.account_type)}</td>
      <td>${pill}</td>
      <td class="text-muted small">${a.notes ? esc(a.notes) : '—'}</td>
      <td>${action}</td>
    </tr>`;
  }).join('');
}

// ── Add account modal ─────────────────────────────────────────────────────────
function openAddAccount(type) {
  _addAccountType = type;
  const titles = {
    checking: 'Add Checking Account',
    savings: 'Add Savings Account',
    credit_card: 'Add Credit Card',
  };
  document.getElementById('addAccountModalTitle').textContent = titles[type] ?? 'Add Account';
  document.getElementById('addAccountForm').reset();
  bootstrap.Modal.getOrCreateInstance(document.getElementById('addAccountModal')).show();
}

async function submitAddAccount() {
  const form = document.getElementById('addAccountForm');
  if (!form.checkValidity()) { form.reportValidity(); return; }
  const fd = new FormData(form);
  const body = {
    name: fd.get('name'),
    nickname: fd.get('nickname'),
    account_number: fd.get('account_number'),
    account_type: _addAccountType,
    notes: fd.get('notes') || null,
  };
  await api('/api/accounts', { method: 'POST', body: JSON.stringify(body) });
  bootstrap.Modal.getInstance(document.getElementById('addAccountModal')).hide();
  await refreshAccounts();
}

async function deactivateAccount(id) {
  if (!confirm('Deactivate this account?')) return;
  await api(`/api/accounts/${id}`, { method: 'PATCH', body: JSON.stringify({ is_active: false }) });
  await refreshAccounts();
}

// ── Boot ──────────────────────────────────────────────────────────────────────
refreshAccounts();
