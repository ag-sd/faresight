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

// ── Category colour palette ───────────────────────────────────────────────────
const CATEGORY_COLORS = {
  'Groceries':                     '#30d158',
  'Dining & Takeout':              '#ff9f0a',
  'Transportation':                '#0071e3',
  'Housing & Utilities':           '#636366',
  'Shopping':                      '#bf5af2',
  'Health & Personal Care':        '#ff375f',
  'Entertainment & Subscriptions': '#5e5ce6',
  'Travel':                        '#64d2ff',
  'Income':                        '#34c759',
  'Payments':                      '#32ade6',
  'Transfers':                     '#8e8e93',
  'Fees':                          '#a2845e',
  'Interest Income':               '#63c88a',
  'Interest Paid':                 '#c9557e',
  'Other':                         '#aeaeb2',
};

function categoryColor(cat) {
  return CATEGORY_COLORS[cat] ?? '#6c757d';
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
async function openCreateRuleModal() {
  document.getElementById('ruleError').classList.add('d-none');
  document.getElementById('ruleDescription').value =
    document.getElementById('editTxDescription').textContent;

  const catSel = document.getElementById('ruleCategory');
  catSel.innerHTML = Object.keys(CATEGORY_COLORS)
    .map(c => `<option value="${esc(c)}">${esc(c)}</option>`)
    .join('');
  catSel.value = document.getElementById('editCategorySelect').value;

  const importers = await api('/api/importers');
  document.getElementById('ruleImporter').innerHTML =
    importers.map(i => `<option value="${esc(i)}">${esc(i)}</option>`).join('');

  new bootstrap.Modal(document.getElementById('createRuleModal')).show();
}

async function saveRule() {
  const errEl = document.getElementById('ruleError');
  errEl.classList.add('d-none');
  try {
    await api('/api/rules', {
      method: 'POST',
      body: JSON.stringify({
        description: document.getElementById('ruleDescription').value,
        category:    document.getElementById('ruleCategory').value,
        importer:    document.getElementById('ruleImporter').value,
      }),
    });
    bootstrap.Modal.getInstance(document.getElementById('createRuleModal')).hide();
  } catch (err) {
    const msg = err.message.includes('already exists')
      ? 'This exact rule already exists.'
      : 'Failed to save rule: ' + err.message;
    errEl.textContent = msg;
    errEl.classList.remove('d-none');
  }
}

// ── Boot ──────────────────────────────────────────────────────────────────────
refreshNasBanner();
