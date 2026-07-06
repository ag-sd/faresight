// Map of "name:size" -> File for deduplication
const fileSet = new Map();

// ── File list ─────────────────────────────────────────────────────────────────

function fileKey(f) { return f.name + ':' + f.size; }

function addFiles(fileList) {
  for (const f of fileList) fileSet.set(fileKey(f), f);
  renderFileList();
}

function removeFile(key) {
  fileSet.delete(key);
  renderFileList();
}

function renderFileList() {
  const ul = document.getElementById('fileList');
  ul.innerHTML = '';
  for (const [key, file] of fileSet) {
    const li = document.createElement('li');
    li.className = 'list-group-item d-flex justify-content-between align-items-center py-2';

    const span = document.createElement('span');
    span.className = 'text-truncate me-2';
    span.innerHTML = '<i class="fa-regular fa-file me-2 text-muted"></i>';
    span.append(file.name);  // text node — browser escapes special characters automatically

    const btn = document.createElement('button');
    btn.className = 'btn btn-sm btn-outline-danger py-0 px-2 flex-shrink-0';
    btn.innerHTML = '<i class="fa-regular fa-xmark"></i>';  // static markup only
    btn.addEventListener('click', () => removeFile(key));   // key closed over, never serialized into HTML

    li.append(span, btn);
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
  btn.disabled = !(
    fileSet.size > 0 &&
    document.getElementById('accountSelect').value !== '' &&
    document.getElementById('importerSelect').value !== ''
  );
}

// ── Upload ────────────────────────────────────────────────────────────────────

async function doUpload() {
  const btn = document.getElementById('uploadBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status"></span>Uploading…';

  const fd = new FormData();
  for (const file of fileSet.values()) fd.append('files', file);
  fd.append('account_id', document.getElementById('accountSelect').value);
  fd.append('importer', document.getElementById('importerSelect').value);

  try {
    const res = await fetch('/api/transactions/import-bulk', { method: 'POST', body: fd });
    if (!res.ok) throw new Error(await res.text());
    const results = await res.json();
    showResultModal(results);
    fileSet.clear();
    renderFileList();
  } catch (err) {
    alert('Upload failed: ' + err.message);
  } finally {
    btn.innerHTML = 'Upload';
    updateUploadBtn();
  }
}

// ── Result modal ──────────────────────────────────────────────────────────────

function showResultModal(results) {
  let totalImported = 0, totalErrors = 0;

  const rows = results.map(r => {
    totalImported += r.imported;
    totalErrors += r.errors.length;

    const errBadge = r.errors.length > 0
      ? `<span class="badge bg-warning text-dark ms-1">${r.errors.length} error${r.errors.length !== 1 ? 's' : ''}</span>`
      : `<span class="badge bg-success ms-1">0 errors</span>`;

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
        ${errBadge}
      </div>
      ${errDetails}
    </li>`;
  }).join('');

  document.getElementById('resultList').innerHTML = rows;
  document.getElementById('resultSummary').textContent =
    `${results.length} file${results.length !== 1 ? 's' : ''} processed — ` +
    `${totalImported} transaction${totalImported !== 1 ? 's' : ''} imported, ` +
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

async function refreshCategorizerStatus() {
  const s = await api('/api/categorizer/status');
  const total = s.pending + s.categorized;
  const tracker = document.getElementById('categorizerTracker');

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

function initImportTable(accountMap) {
  new Tabulator('#importTable', {
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
      {
        title: 'Loaded At', field: 'loaded_at', width: 200,
        formatter: (cell) => new Date(cell.getValue()).toLocaleString(),
      },
    ],
  });
}

// ── Classification rules ──────────────────────────────────────────────────────

async function loadRules() {
  const rules = await api('/api/rules');
  const wrap = document.getElementById('rulesTableWrap');

  if (rules.length === 0) {
    wrap.innerHTML = '<p class="text-muted small p-4 mb-0">No rules yet. Open any transaction, pick a category, then click <i class="fa-regular fa-bookmark"></i> to save it as a rule.</p>';
    return;
  }

  const rows = rules.map(r => `
    <tr>
      <td class="text-truncate" style="max-width:300px" title="${esc(r.description)}">${esc(r.description)}</td>
      <td>${esc(r.category)}</td>
      <td>${esc(r.importer)}</td>
      <td class="text-end">
        <div class="btn-group btn-group-sm">
          <button class="btn btn-outline-primary" onclick="applyRule(${r.id})">
            <i class="fa-solid fa-play me-1"></i>Run Now
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
            <th>Description</th>
            <th>Category</th>
            <th>Importer</th>
            <th></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
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

// ── Init ──────────────────────────────────────────────────────────────────────

async function init() {
  const [accounts, importers] = await Promise.all([
    api('/api/accounts'),
    api('/api/importers'),
  ]);

  const accountMap = {};
  const acctSel = document.getElementById('accountSelect');
  accounts.forEach(a => {
    accountMap[a.id] = `${a.bank} — ${a.name}`;
    const opt = document.createElement('option');
    opt.value = a.id;
    opt.textContent = `${a.bank} — ${a.name} (${a.account_number})`;
    acctSel.appendChild(opt);
  });

  const impSel = document.getElementById('importerSelect');
  importers.forEach(name => {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    impSel.appendChild(opt);
  });

  return accountMap;
}

init().then(accountMap => initImportTable(accountMap));
loadRules();
refreshCategorizerRunning();
setInterval(refreshCategorizerRunning, 10000);
refreshCategorizerStatus();
setInterval(refreshCategorizerStatus, 10000);
