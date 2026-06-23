// ── API helpers ───────────────────────────────────────────────────────────────
const api = async (path, opts = {}) => {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  if (!res.ok) throw new Error(await res.text());
  if (res.status === 204) return null;
  return res.json();
};

// ── State ─────────────────────────────────────────────────────────────────────
let catChart, monthChart;
let categoryFilter = '';

const NAS_BASE = 'alert d-flex align-items-center mb-0 rounded-0 border-0 border-bottom px-4 py-2 small fw-medium';

// ── Banks ─────────────────────────────────────────────────────────────────────
async function refreshBanks() {
  const banks = await api('/api/banks');
  const select = document.getElementById('bankSelect');
  select.innerHTML = '<option value="">Select bank…</option>';
  banks.forEach(b => {
    select.insertAdjacentHTML('beforeend', `<option value="${esc(b)}">${esc(b)}</option>`);
  });
}

// ── Import CSV form ───────────────────────────────────────────────────────────
document.getElementById('importForm').addEventListener('submit', async e => {
  e.preventDefault();
  const resultDiv = document.getElementById('importResult');
  resultDiv.className = 'mt-3 d-none';

  const fd = new FormData(e.target);
  let res, result;
  try {
    res = await fetch('/api/transactions/import', { method: 'POST', body: fd });
    result = await res.json();
  } catch (err) {
    resultDiv.className = 'mt-3 alert alert-danger';
    resultDiv.textContent = `Upload failed: ${err.message}`;
    return;
  }

  if (result.errors.length) {
    resultDiv.className = 'mt-3 alert alert-warning';
    resultDiv.innerHTML =
      `Imported ${result.imported} transaction(s). ${result.errors.length} row(s) skipped.` +
      `<ul class="mb-0 mt-1">${result.errors.map(err => `<li>${esc(err)}</li>`).join('')}</ul>`;
  } else {
    resultDiv.className = 'mt-3 alert alert-success';
    resultDiv.textContent = `Successfully imported ${result.imported} transaction(s).`;
  }
  resultDiv.classList.remove('d-none');
  e.target.reset();
  await refreshAll();
});

// ── Charts ────────────────────────────────────────────────────────────────────
async function refreshCharts() {
  const [byCat, byMonth] = await Promise.all([
    api('/api/summary/by-category'),
    api('/api/summary/by-month'),
  ]);

  const palette = ['#0071e3','#30d158','#ff9f0a','#ff375f','#bf5af2','#5e5ce6','#64d2ff','#ff6961'];
  if (catChart) catChart.destroy();
  catChart = new Chart(document.getElementById('catChart'), {
    type: 'doughnut',
    data: {
      labels: byCat.map(r => r.category),
      datasets: [{ data: byCat.map(r => Math.abs(r.total)), backgroundColor: palette }],
    },
    options: { plugins: { legend: { position: 'right' } }, maintainAspectRatio: false },
  });

  const labels = byMonth.map(r => `${r.year}-${String(r.month).padStart(2,'0')}`);
  if (monthChart) monthChart.destroy();
  monthChart = new Chart(document.getElementById('monthChart'), {
    type: 'bar',
    data: {
      labels,
      datasets: [{ label: 'Total', data: byMonth.map(r => Math.abs(r.total)), backgroundColor: '#0071e3' }],
    },
    options: { plugins: { legend: { display: false } }, maintainAspectRatio: false },
  });
}

// ── Category filter / datalist ────────────────────────────────────────────────
async function refreshCategories() {
  const cats = await api('/api/categories');
  const filter = document.getElementById('catFilter');
  const datalist = document.getElementById('catList');

  const prev = filter.value;
  filter.innerHTML = '<option value="">All categories</option>';
  datalist.innerHTML = '';
  cats.forEach(c => {
    filter.insertAdjacentHTML('beforeend', `<option value="${c}">${c}</option>`);
    datalist.insertAdjacentHTML('beforeend', `<option value="${c}">`);
  });
  filter.value = prev;
}

// ── Transaction list ──────────────────────────────────────────────────────────
async function refreshTable() {
  const url = categoryFilter
    ? `/api/transactions?category=${encodeURIComponent(categoryFilter)}`
    : '/api/transactions';
  const txs = await api(url);
  const tbody = document.getElementById('txBody');

  if (!txs.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-5">No transactions yet.</td></tr>';
    return;
  }

  tbody.innerHTML = txs.map(t => {
    const neg = t.amount < 0;
    const amtStr = (neg ? '-' : '+') + '$' + Math.abs(t.amount).toFixed(2);
    return `<tr>
      <td>${t.date}</td>
      <td>${esc(t.description)}${t.note ? `<br><small class="text-muted">${esc(t.note)}</small>` : ''}</td>
      <td><span class="badge bg-secondary text-dark">${esc(t.category)}</span></td>
      <td>${t.source ? esc(t.source) : '—'}</td>
      <td class="amount text-end ${neg ? 'text-danger' : 'text-success'}">${amtStr}</td>
      <td class="d-flex gap-1">
        <button class="btn btn-danger btn-sm" onclick="deleteTx(${t.id})">Del</button>
      </td>
    </tr>`;
  }).join('');
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Delete ────────────────────────────────────────────────────────────────────
async function deleteTx(id) {
  if (!confirm('Delete this transaction?')) return;
  await api(`/api/transactions/${id}`, { method: 'DELETE' });
  await refreshAll();
}

// ── Add manually form ─────────────────────────────────────────────────────────
document.getElementById('addForm').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = {
    date: fd.get('date'),
    description: fd.get('description'),
    amount: parseFloat(fd.get('amount')),
    category: fd.get('category'),
    source: fd.get('source') || null,
    note: fd.get('note') || null,
  };
  await api('/api/transactions', { method: 'POST', body: JSON.stringify(body) });
  e.target.reset();
  await refreshAll();
});

// ── Category filter ───────────────────────────────────────────────────────────
document.getElementById('catFilter').addEventListener('change', async e => {
  categoryFilter = e.target.value;
  await refreshTable();
});

// ── NAS status banners ────────────────────────────────────────────────────────
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

// ── Sync actions ──────────────────────────────────────────────────────────────
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

// ── Boot ──────────────────────────────────────────────────────────────────────
async function refreshAll() {
  await Promise.all([refreshCharts(), refreshCategories(), refreshTable()]);
}

document.querySelector('input[name=date]').valueAsDate = new Date();

refreshBanks();
refreshNasBanner();
refreshAll();
