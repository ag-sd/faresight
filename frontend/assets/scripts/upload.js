// Map of "name:size" -> { file, accountId } for deduplication.
// accountId is the per-file account choice; the importer is derived server-side
// from the account's default_importer.
const fileSet = new Map();

// Populated in init() and reused when rendering per-file selects.
let accountsList = [];
let _topCardPageLimit = 5;  // overwritten from /api/config at boot

// ── File list ─────────────────────────────────────────────────────────────────

function fileKey(f) { return f.name + ':' + f.size; }

function addFiles(fileList) {
  for (const f of fileList) {
    const key = fileKey(f);
    if (!fileSet.has(key)) {
      fileSet.set(key, { file: f, accountId: '' });
    }
  }
  renderFileList();
}

function removeFile(key) {
  fileSet.delete(key);
  renderFileList();
}

// Build a <select> from options, preselecting `selected`. Each option is
// { value, label }; a leading placeholder ('') is prepended.
function buildSelect(options, selected, placeholder, onChange) {
  const sel = document.createElement('select');
  sel.className = 'form-select form-select-sm';
  const ph = document.createElement('option');
  ph.value = '';
  ph.textContent = placeholder;
  sel.appendChild(ph);
  for (const o of options) {
    const opt = document.createElement('option');
    opt.value = o.value;
    opt.textContent = o.label;
    if (String(o.value) === String(selected)) opt.selected = true;
    sel.appendChild(opt);
  }
  sel.addEventListener('change', onChange);
  return sel;
}

function renderFileList() {
  const ul = document.getElementById('fileList');
  ul.innerHTML = '';
  for (const [key, entry] of fileSet) {
    const li = document.createElement('li');
    li.className = 'list-group-item py-2';

    const row = document.createElement('div');
    row.className = 'd-flex align-items-center gap-2 flex-wrap';

    const name = document.createElement('span');
    name.className = 'text-truncate flex-grow-1 me-1';
    name.style.minWidth = '8rem';
    name.innerHTML = '<i class="fa-regular fa-file me-2 text-muted"></i>';
    name.append(entry.file.name);  // text node — browser escapes special characters automatically

    const acctOpts = accountsList.map(a => ({ value: a.id, label: `${a.bank} — ${a.name}` }));
    const acctSel = buildSelect(acctOpts, entry.accountId, 'Account…', (e) => {
      entry.accountId = e.target.value;
      updateUploadBtn();
    });
    acctSel.style.maxWidth = '14rem';

    const btn = document.createElement('button');
    btn.className = 'btn btn-sm btn-outline-danger py-0 px-2 flex-shrink-0';
    btn.innerHTML = '<i class="fa-solid fa-xmark"></i>';  // static markup only
    btn.addEventListener('click', () => removeFile(key));   // key closed over, never serialized into HTML

    row.append(name, acctSel, btn);
    li.append(row);
    ul.appendChild(li);
  }
  updateUploadBtn();
}

// ── Dropzone ──────────────────────────────────────────────────────────────────

const zone  = document.getElementById('dropzone');
const input = document.getElementById('fileInput');

zone.addEventListener('click', () => input.click());

zone.addEventListener('dragover', e => {
  e.preventDefault();
  zone.classList.add('drag-over');
});
zone.addEventListener('dragleave', e => {
  if (!zone.contains(e.relatedTarget)) zone.classList.remove('drag-over');
});
zone.addEventListener('drop', e => {
  e.preventDefault();
  zone.classList.remove('drag-over');
  addFiles(e.dataTransfer.files);
});

input.addEventListener('change', () => {
  addFiles(input.files);
  input.value = '';
});

// ── Upload button state ───────────────────────────────────────────────────────

function updateUploadBtn() {
  const btn = document.getElementById('uploadBtn');
  // Enabled only once every file has an account assigned.
  const allAssigned = [...fileSet.values()].every(e => e.accountId);
  btn.disabled = !(fileSet.size > 0 && allAssigned);
}

// ── Upload ────────────────────────────────────────────────────────────────────

async function doUpload() {
  const btn = document.getElementById('uploadBtn');
  const wrap = document.getElementById('uploadProgressWrap');
  const bar = document.getElementById('uploadProgressBar');
  const text = document.getElementById('uploadProgressText');

  const entries = [...fileSet.values()];
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status"></span>Uploading…';
  wrap.classList.remove('d-none');

  // One request per file, so each carries its own account (the importer is
  // derived server-side). Failures are recorded per-file and do not abort the batch.
  const results = [];
  for (let i = 0; i < entries.length; i++) {
    const entry = entries[i];
    text.textContent = `Uploading file ${i + 1} of ${entries.length}…`;
    bar.style.width = Math.round(i / entries.length * 100) + '%';

    const fd = new FormData();
    fd.append('files', entry.file);
    fd.append('account_id', entry.accountId);

    try {
      const res = await fetch('/api/transactions/import-bulk', { method: 'POST', body: fd });
      if (!res.ok) throw new Error(await res.text());
      results.push(...await res.json());
    } catch (err) {
      results.push({ filename: entry.file.name, imported: 0, errors: ['Upload failed: ' + err.message] });
    }
  }
  bar.style.width = '100%';

  showResultModal(results);
  fileSet.clear();
  renderFileList();
  refreshImportTable();
  refreshPendingTable();

  wrap.classList.add('d-none');
  bar.style.width = '0%';
  btn.innerHTML = 'Upload';
  updateUploadBtn();
}

// ── Result modal ──────────────────────────────────────────────────────────────

function showResultModal(results) {
  let totalImported = 0, totalErrors = 0, totalSkipped = 0;

  const rows = results.map(r => {
    totalImported += r.imported;
    totalErrors += r.errors.length;
    totalSkipped += r.skipped ?? 0;

    const errBadge = r.errors.length > 0
      ? `<span class="badge bg-warning text-dark ms-1">${r.errors.length} error${r.errors.length !== 1 ? 's' : ''}</span>`
      : `<span class="badge bg-success ms-1">0 errors</span>`;

    const dupBadge = r.duplicate_file
      ? `<span class="badge bg-secondary ms-1">exact duplicate — file skipped</span>`
      : (r.skipped > 0
        ? `<span class="badge bg-secondary ms-1">${r.skipped} duplicate${r.skipped !== 1 ? 's' : ''} skipped</span>`
        : '');

    const errDetails = r.errors.length > 0
      ? `<details class="mt-2 ps-4">
           <summary class="small text-muted" style="cursor:pointer">Show errors</summary>
           <ul class="small text-danger mt-1 mb-0 ps-3">${r.errors.map(e => `<li>${esc(e)}</li>`).join('')}</ul>
         </details>`
      : '';

    return `<li class="list-group-item">
      <div class="d-flex align-items-center gap-2 flex-wrap">
        <i class="fa-regular fa-file text-muted flex-shrink-0"></i>
        <span class="flex-grow-1 text-truncate fw-medium">${esc(r.filename)}</span>
        <span class="badge bg-primary">${r.imported} imported</span>
        ${dupBadge}
        ${errBadge}
      </div>
      ${errDetails}
    </li>`;
  }).join('');

  document.getElementById('resultList').innerHTML = rows;
  document.getElementById('resultSummary').textContent =
    `${results.length} file${results.length !== 1 ? 's' : ''} processed — ` +
    `${totalImported} transaction${totalImported !== 1 ? 's' : ''} imported, ` +
    `${totalSkipped} duplicate${totalSkipped !== 1 ? 's' : ''} skipped, ` +
    `${totalErrors} error${totalErrors !== 1 ? 's' : ''}.`;

  new bootstrap.Modal(document.getElementById('resultModal')).show();
}

// ── Categorizer subprocess status ────────────────────────────────────────────

async function refreshCategorizerRunning() {
  const s = await api('/api/categorizer/running');
  const pill = document.getElementById('categorizerStatusPill');
  if (s.running) {
    pill.className = 'badge rounded-pill bg-success';
    pill.textContent = 'Running';
  } else {
    pill.className = 'badge rounded-pill bg-danger';
    pill.textContent = 'Stopped';
  }
}

// ── Categorization tracker ────────────────────────────────────────────────────

let _lastPending = null;

async function refreshCategorizerStatus() {
  const s = await api('/api/categorizer/status');
  const total = s.pending + s.categorized;
  const tracker = document.getElementById('categorizerTracker');

  // Keep the pending table in step, but only reload when the count actually
  // changed so we don't reset the user's page/scroll on every poll.
  if (s.pending !== _lastPending) { _lastPending = s.pending; refreshPendingTable(); }

  if (total === 0) { tracker.classList.add('d-none'); return; }
  tracker.classList.remove('d-none');

  const pct = Math.round(s.categorized / total * 100);
  const bar = document.getElementById('categorizerBar');
  bar.style.width = pct + '%';

  if (s.pending === 0) {
    bar.classList.remove('progress-bar-striped', 'progress-bar-animated', 'bg-primary');
    bar.classList.add('bg-success');
    document.getElementById('categorizerText').textContent = 'All transactions categorized';
    document.getElementById('categorizerCount').textContent = `${s.categorized} total`;
  } else {
    bar.classList.add('progress-bar-striped', 'progress-bar-animated', 'bg-primary');
    bar.classList.remove('bg-success');
    const text = `${s.pending} transaction${s.pending !== 1 ? 's' : ''} pending…`;
    document.getElementById('categorizerText').textContent = text;
    document.getElementById('categorizerCount').textContent = `${s.categorized} / ${total}`;
  }
}

// ── Recent uploads table ──────────────────────────────────────────────────────

let importTable = null;

// Reload the Recent Uploads table so freshly imported files appear.
function refreshImportTable() {
  if (importTable) importTable.setData();
}

function initImportTable(accountMap) {
  importTable = new Tabulator('#importTable', {
    ajaxURL: '/api/file-imports',
    pagination: true,
    paginationMode: 'remote',
    paginationSize: 25,
    layout: 'fitColumns',
    dataSendParams: { size: 'limit' },
    ajaxResponse: (_url, _p, r) => ({
      data: r.data,
      last_page: Math.ceil(r.total / r.limit),
    }),
    columns: [
      { title: 'File',          field: 'filename',       widthGrow: 3 },
      {
        title: 'Account', field: 'account_id', widthGrow: 2,
        formatter: (cell) => accountMap[cell.getValue()] ?? '—',
      },
      { title: 'Rows Seen',     field: 'rows_seen',      hozAlign: 'right', width: 120 },
      { title: 'Rows Imported', field: 'rows_persisted', hozAlign: 'right', width: 140 },
      { title: 'Duplicates',    field: 'rows_skipped',   hozAlign: 'right', width: 120 },
      {
        title: 'Loaded At', field: 'loaded_at', width: 200,
        formatter: (cell) => new Date(cell.getValue()).toLocaleString(),
      },
    ],
  });
}

// ── Pending categorization table ──────────────────────────────────────────────

let pendingTable = null;

function refreshPendingTable() {
  if (pendingTable) pendingTable.setData();
}

function initPendingTable() {
  pendingTable = new Tabulator('#pendingTxTable', {
    ajaxURL: '/api/transactions',
    ajaxParams: () => ({ pending_only: true }),
    pagination: true,
    paginationMode: 'remote',
    paginationSize: _topCardPageLimit,
    layout: 'fitColumns',
    movableColumns: true,
    initialSort: [{ column: 'date', dir: 'desc' }],
    dataSendParams: { size: 'limit' },
    ajaxResponse: (_url, _p, response) => ({
      data: response.data,
      last_page: Math.ceil(response.total / response.limit),
    }),
    columns: txColumns({ accounts: () => accountsList, withEdit: true }),
  });
}

// Post-save refresh for the shared edit-category modal (common.js): the saved row
// leaves the pending set, so reload the table and update the progress bar.
afterCategorySave = async () => {
  refreshPendingTable();
  await refreshCategorizerStatus();
};

// Refresh the rules table whenever a rule is created or edited via the shared modal.
afterRuleSave = loadRules;

// ── Classification rules ──────────────────────────────────────────────────────

let _rulesById = {};

async function loadRules() {
  const rules = await api('/api/rules');
  _rulesById = Object.fromEntries(rules.map(r => [r.id, r]));
  const wrap = document.getElementById('rulesTableWrap');

  if (rules.length === 0) {
    wrap.innerHTML = '<p class="text-muted small p-4 mb-0">No rules yet. Open any transaction, pick a category, then click <i class="fa-solid fa-bookmark"></i> to save it as a rule.</p>';
    return;
  }

  const rows = rules.map(r => `
    <tr>
      <td class="text-truncate" style="max-width:300px" title="${esc(r.description)}">${esc(r.description)}</td>
      <td>${esc(r.category)}</td>
      <td class="text-end">
        <div class="btn-group btn-group-sm">
          <button class="btn btn-outline-primary" onclick="applyRule(${r.id})">
            <i class="fa-solid fa-play me-1"></i>Run Now
          </button>
          <button class="btn btn-outline-secondary" onclick="editRule(${r.id})" title="Edit rule">
            <i class="fa-regular fa-pen-to-square"></i>
          </button>
          <button class="btn btn-outline-danger" onclick="deleteRule(${r.id})">
            <i class="fa-regular fa-trash-can"></i>
          </button>
        </div>
      </td>
    </tr>`).join('');

  wrap.innerHTML = `
    <div class="table-responsive">
      <table class="table table-hover mb-0 align-middle">
        <thead class="table-light">
          <tr>
            <th>Pattern</th>
            <th>Category</th>
            <th></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function editRule(id) {
  openEditRuleModal(_rulesById[id]);
}

async function applyRule(id) {
  const result = await api(`/api/rules/${id}/apply`, { method: 'POST' });
  const n = result.updated;
  alert(`Rule applied — ${n} transaction${n !== 1 ? 's' : ''} updated.`);
}

async function deleteRule(id) {
  if (!confirm('Delete this rule?')) return;
  await api(`/api/rules/${id}`, { method: 'DELETE' });
  loadRules();
}

// ── Categories table ──────────────────────────────────────────────────────────

let _categoriesTable = null;

async function initCategoriesTable() {
  const cats = await api('/api/categories');
  if (_categoriesTable) {
    _categoriesTable.setData(cats);
    return;
  }
  _categoriesTable = new Tabulator('#categoriesTable', {
    data: cats,
    layout: 'fitColumns',
    columns: [
      {
        title: 'Color', field: 'color', width: 72, hozAlign: 'center', headerSort: false,
        formatter: cell => {
          const c = cell.getValue();
          return `<span class="d-inline-block rounded" style="width:24px;height:24px;background:${esc(c)};border:1px solid #dee2e6" title="${esc(c)}"></span>`;
        },
      },
      { title: 'Name', field: 'name', widthGrow: 2 },
      {
        title: 'Bucket', field: 'bucket', widthGrow: 1,
        editor: 'list', editorParams: { values: ['spend', 'income', 'internal'] },
        cellEdited: async cell => {
          try {
            await api(`/api/categories/${encodeURIComponent(cell.getRow().getData().name)}`, {
              method: 'PATCH',
              body: JSON.stringify({ bucket: cell.getValue() }),
            });
            await loadCategories();
          } catch (e) {
            alert('Failed to update bucket: ' + e.message);
            cell.restoreOldValue();
          }
        },
      },
      {
        title: 'Description', field: 'description', widthGrow: 3,
        editor: 'input',
        cellEdited: async cell => {
          try {
            await api(`/api/categories/${encodeURIComponent(cell.getRow().getData().name)}`, {
              method: 'PATCH',
              body: JSON.stringify({ description: cell.getValue() }),
            });
          } catch (e) {
            alert('Failed to update description: ' + e.message);
            cell.restoreOldValue();
          }
        },
      },
      {
        title: '', headerSort: false, hozAlign: 'center', width: 80,
        formatter: () =>
          '<div class="d-flex gap-1 justify-content-center">' +
          '<button class="btn btn-sm btn-outline-primary cat-edit-btn" title="Edit color"><i class="fa-regular fa-pen-to-square"></i></button>' +
          '<button class="btn btn-sm btn-outline-danger cat-del-btn" title="Delete"><i class="fa-regular fa-trash-can"></i></button>' +
          '</div>',
        cellClick: (_e, cell) => {
          const tgt = _e.target.closest('button');
          if (!tgt) return;
          const name = cell.getRow().getData().name;
          if (tgt.classList.contains('cat-del-btn')) deleteCategory(name);
          if (tgt.classList.contains('cat-edit-btn')) openEditCategoryModal(cell.getRow().getData());
        },
      },
    ],
  });
}

function openCreateCategoryModal() {
  document.getElementById('categoryModalTitle').textContent = 'Add Category';
  document.getElementById('catName').value = '';
  document.getElementById('catName').disabled = false;
  document.getElementById('catColor').value = '#6c757d';
  document.getElementById('catColorHex').textContent = '#6c757d';
  document.getElementById('catBucket').value = 'spend';
  document.getElementById('catDescription').value = '';
  document.getElementById('categoryModalError').classList.add('d-none');
  document.getElementById('catSaveBtn').onclick = saveCategoryCreate;
  new bootstrap.Modal(document.getElementById('categoryModal')).show();
}

function openEditCategoryModal(cat) {
  document.getElementById('categoryModalTitle').textContent = 'Edit Category';
  document.getElementById('catName').value = cat.name;
  document.getElementById('catName').disabled = true;
  document.getElementById('catColor').value = cat.color;
  document.getElementById('catColorHex').textContent = cat.color;
  document.getElementById('catBucket').value = cat.bucket;
  document.getElementById('catDescription').value = cat.description ?? '';
  document.getElementById('categoryModalError').classList.add('d-none');
  document.getElementById('catSaveBtn').onclick = () => saveCategoryEdit(cat.name);
  new bootstrap.Modal(document.getElementById('categoryModal')).show();
}

// Keep the hex label in sync with the color picker.
document.getElementById('catColor')?.addEventListener('input', e => {
  document.getElementById('catColorHex').textContent = e.target.value;
});

async function saveCategoryCreate() {
  const errEl = document.getElementById('categoryModalError');
  errEl.classList.add('d-none');
  try {
    await api('/api/categories', {
      method: 'POST',
      body: JSON.stringify({
        name:        document.getElementById('catName').value.trim(),
        color:       document.getElementById('catColor').value,
        bucket:      document.getElementById('catBucket').value,
        description: document.getElementById('catDescription').value.trim() || null,
      }),
    });
    bootstrap.Modal.getInstance(document.getElementById('categoryModal')).hide();
    await loadCategories();
    await initCategoriesTable();
  } catch (e) {
    errEl.textContent = e.message.includes('already exists') || e.message.includes('409')
      ? 'A category with this name already exists.'
      : 'Failed to save: ' + e.message;
    errEl.classList.remove('d-none');
  }
}

async function saveCategoryEdit(name) {
  const errEl = document.getElementById('categoryModalError');
  errEl.classList.add('d-none');
  try {
    await api(`/api/categories/${encodeURIComponent(name)}`, {
      method: 'PATCH',
      body: JSON.stringify({
        color:       document.getElementById('catColor').value,
        bucket:      document.getElementById('catBucket').value,
        description: document.getElementById('catDescription').value.trim() || null,
      }),
    });
    bootstrap.Modal.getInstance(document.getElementById('categoryModal')).hide();
    await loadCategories();
    await initCategoriesTable();
  } catch (e) {
    errEl.textContent = 'Failed to save: ' + e.message;
    errEl.classList.remove('d-none');
  }
}

async function deleteCategory(name) {
  if (!confirm(`Delete category "${name}"?\n\nExisting transactions keep the category name as a label — they will appear magenta until re-categorized.`)) return;
  await api(`/api/categories/${encodeURIComponent(name)}`, { method: 'DELETE' });
  await loadCategories();
  await initCategoriesTable();
}

// ── Init ──────────────────────────────────────────────────────────────────────

async function init() {
  const accounts = await api('/api/accounts');

  // Cache for the per-file selects rendered in renderFileList().
  accountsList = accounts;

  const accountMap = {};
  accounts.forEach(a => { accountMap[a.id] = `${a.bank} — ${a.name}`; });
  return accountMap;
}

(async () => {
  try {
    const cfg = await api('/api/config');
    _topCardPageLimit = cfg.top_card_page_limit ?? _topCardPageLimit;
  } catch (_) { /* keep default */ }
  // loadCategories() is called by common.js boot — await the same promise chain
  // so _categoryMap is populated before any table that needs category colors.
  await loadCategories();
  const accountMap = await init();
  initImportTable(accountMap);
  initPendingTable();  // after init() so accountsList is populated
  initCategoriesTable();
})();

loadRules();
refreshCategorizerRunning();
setInterval(refreshCategorizerRunning, 10000);
refreshCategorizerStatus();
setInterval(refreshCategorizerStatus, 10000);
