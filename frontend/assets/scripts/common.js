// ── API helpers ───────────────────────────────────────────────────────────────
const api = async (path, opts = {}) => {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  if (!res.ok) throw new Error(await res.text());
  if (res.status === 204) return null;
  return res.json();
};

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Category data (loaded from /api/categories at page boot) ─────────────────
// {name: {color, bucket, description}} populated by loadCategories().
// Unknown/orphaned category names render in magenta so they stand out.
let _categoryMap = {};
let _categoriesLoaded = null;  // shared promise so concurrent callers share one fetch

async function loadCategories() {
  if (!_categoriesLoaded) {
    _categoriesLoaded = api('/api/categories')
      .then(cats => { _categoryMap = Object.fromEntries(cats.map(c => [c.name, c])); })
      .catch(() => { _categoriesLoaded = null; });  // reset on error so next call retries
  }
  return _categoriesLoaded;
}

function categoryColor(cat) {
  return _categoryMap[cat]?.color ?? '#ff2d78';
}

function categoryNames() {
  return Object.keys(_categoryMap).sort();
}

// ── Shared Tabulator formatters ───────────────────────────────────────────────
function modelCategoryFormatter(cell) {
  const { model_category, model_confidence } = cell.getRow().getData();
  if (model_confidence === -1) {
    return '<span class="text-secondary fst-italic small">Pending</span>';
  }
  if (!model_category) return `<span class="badge rounded-pill" style="background-color:${categoryColor('Other')}">Uncategorized</span>`;
  const color = categoryColor(model_category);
  // Confidence moves to a hover tooltip so the pill stays compact.
  const title = model_confidence != null ? ` title="Confidence: ${model_confidence}/10"` : '';
  return `<span class="badge rounded-pill" style="background-color:${color}"${title}>${esc(model_category)}</span>`;
}

function amountFormatter(cell) {
  const val = parseFloat(cell.getValue());
  const neg = val < 0;
  cell.getElement().style.color = neg ? 'var(--bs-danger)' : 'var(--bs-success)';
  return (neg ? '-' : '+') + '$' + Math.abs(val).toFixed(2);
}

// ── Shared transactions-table columns ─────────────────────────────────────────
// Column set for the dashboard / accounts activity / upload pending tables.
// `accounts` resolves the Source cell — an array, or a getter returning one so the
// lookup stays live if the page reassigns its accounts list. `categoryWidth` fixes
// the Category column width (null → grows); `withEdit` appends the pen column.
function txColumns({ accounts = [], categoryWidth = null, sourceGrow = 1, withEdit = false } = {}) {
  const accountsOf = () => (typeof accounts === 'function' ? accounts() : accounts);
  const category = categoryWidth == null
    ? { title: 'Category', field: 'model_category', widthGrow: 2, headerFilter: 'input', formatter: modelCategoryFormatter }
    : { title: 'Category', field: 'model_category', width: categoryWidth, headerFilter: 'input', formatter: modelCategoryFormatter };

  const cols = [
    { title: 'Date', field: 'date', sorter: 'date', headerFilter: 'input', width: 120 },
    {
      title: 'Description', field: 'description', headerFilter: 'input', widthGrow: 3,
      formatter: (cell) => esc(String(cell.getValue())),
    },
    category,
    {
      title: 'Source', field: 'account_id', widthGrow: sourceGrow,
      formatter: (cell) => {
        const id = cell.getValue();
        if (!id) return '—';
        const acct = accountsOf().find(a => a.id === id);
        return acct ? esc(acct.name) : String(id);
      },
    },
    {
      title: 'Amount', field: 'amount', sorter: 'number',
      headerFilter: 'input', hozAlign: 'right', cssClass: 'amount', width: 130,
      formatter: amountFormatter,
    },
  ];

  if (withEdit) {
    cols.push({
      title: '', headerSort: false, hozAlign: 'center', width: 48,
      formatter: () => '<button class="btn btn-link btn-sm p-0 text-secondary"><i class="fa-regular fa-pen-to-square"></i></button>',
      cellClick: (_e, cell) => openEditModal(cell.getRow().getData()),
    });
  }
  return cols;
}

// ── Shared edit-category modal ────────────────────────────────────────────────
// Pages assign `afterCategorySave` to refresh their own views once a save lands.
let _editTxId = null;
let afterCategorySave = () => {};

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
  sel.innerHTML = categoryNames()
    .map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
  sel.value = (tx.model_category && tx.model_category in _categoryMap)
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
  await afterCategorySave();
}

// ── NAS banners ───────────────────────────────────────────────────────────────
const NAS_BASE = 'alert d-flex align-items-center mb-0 rounded-0 border-0 border-bottom px-4 py-2 small fw-medium';

async function refreshNasBanner() {
  try {
    const s = await api('/api/sync/status');
    const nasBanner  = document.getElementById('nasBanner');
    const lockBanner = document.getElementById('lockBanner');

    if (s.lock_warning) {
      document.getElementById('lockMsg').textContent =
        `Database may be in use on "${s.lock_warning}". Proceeding will sync your local copy and may overwrite their recent changes.`;
      lockBanner.classList.remove('d-none');
      nasBanner.classList.add('d-none');
      return;
    }
    lockBanner.classList.add('d-none');

    if (s.reachable === false) {
      nasBanner.className = NAS_BASE + ' alert-warning';
      nasBanner.textContent = 'NAS share unreachable — running on local copy. Changes will not be synced.';
    } else if (s.last_action === 'pulled_update') {
      nasBanner.className = NAS_BASE + ' alert-success';
      nasBanner.textContent = `NAS sync: pulled latest data from NAS (updated ${s.detail}).`;
    } else if (s.last_action === 'pushed_update' && s.last_push) {
      nasBanner.className = NAS_BASE + ' alert-success';
      nasBanner.textContent = `NAS sync: pushed to NAS at ${s.last_push}.`;
    } else {
      nasBanner.className = NAS_BASE + ' d-none';
    }
  } catch (_) {
    // status endpoint unavailable — silently skip banners
  }
}

async function syncNow() {
  const btn = document.getElementById('syncNowBtn');
  btn.disabled = true;
  btn.textContent = 'Syncing…';
  try {
    await api('/api/sync', { method: 'POST' });
    await refreshNasBanner();
  } finally {
    btn.disabled = false;
    btn.textContent = 'Sync now';
  }
}

async function proceedAnyway() {
  await api('/api/sync', { method: 'POST' });
  await refreshNasBanner();
}

async function goOffline() {
  await api('/api/sync/go-offline', { method: 'POST' });
  document.getElementById('lockBanner').classList.add('d-none');
}

// ── Rules ─────────────────────────────────────────────────────────────────────
// Rule descriptions are regex patterns (case-insensitive, match anywhere).
// Pages assign `afterRuleSave` to refresh their own views once a save lands.
let _editingRuleId = null;
let afterRuleSave = () => {};

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function _populateRuleSelects() {
  document.getElementById('ruleCategory').innerHTML = categoryNames()
    .map(c => `<option value="${esc(c)}">${esc(c)}</option>`)
    .join('');
}

function _showRuleModal(title, saveLabel) {
  document.getElementById('ruleError').classList.add('d-none');
  document.getElementById('createRuleModalLabel').textContent = title;
  document.getElementById('ruleSaveBtn').textContent = saveLabel;
  new bootstrap.Modal(document.getElementById('createRuleModal')).show();
}

function openCreateRuleModal() {
  _editingRuleId = null;
  // Escape the transaction description so the prefilled pattern matches it
  // literally (e.g. AMZN*MKTP); the user can edit it into a real regex.
  document.getElementById('ruleDescription').value =
    escapeRegExp(document.getElementById('editTxDescription').textContent);

  _populateRuleSelects();
  document.getElementById('ruleCategory').value =
    document.getElementById('editCategorySelect').value;

  _showRuleModal('Create Classification Rule', 'Save Rule');
}

function openEditRuleModal(rule) {
  _editingRuleId = rule.id;
  document.getElementById('ruleDescription').value = rule.description;

  _populateRuleSelects();
  document.getElementById('ruleCategory').value = rule.category;

  _showRuleModal('Edit Classification Rule', 'Save Changes');
}

async function saveRule() {
  const errEl = document.getElementById('ruleError');
  errEl.classList.add('d-none');
  const description = document.getElementById('ruleDescription').value;

  // Fast local feedback; the backend's re.compile is authoritative (JS and
  // Python regex dialects differ on edge syntax).
  try {
    new RegExp(description);
  } catch (err) {
    errEl.textContent = 'Invalid regular expression: ' + err.message;
    errEl.classList.remove('d-none');
    return;
  }

  const body = JSON.stringify({
    description,
    category: document.getElementById('ruleCategory').value,
  });
  try {
    if (_editingRuleId !== null) {
      await api(`/api/rules/${_editingRuleId}`, { method: 'PATCH', body });
    } else {
      await api('/api/rules', { method: 'POST', body });
    }
    bootstrap.Modal.getInstance(document.getElementById('createRuleModal')).hide();
    _editingRuleId = null;
    await afterRuleSave();
  } catch (err) {
    let msg;
    try { msg = JSON.parse(err.message).detail; } catch (_) { msg = err.message; }
    errEl.textContent = msg.includes('already exists')
      ? 'This exact rule already exists.'
      : 'Failed to save rule: ' + msg;
    errEl.classList.remove('d-none');
  }
}

// ── Boot ──────────────────────────────────────────────────────────────────────
loadCategories();
refreshNasBanner();
