// ── State ─────────────────────────────────────────────────────────────────────
let catChart, monthChart;
let categoryFilter = '';

// ── Account select (Import CSV) ───────────────────────────────────────────────
async function refreshAccountSelect() {
  const accounts = await api('/api/accounts');
  const select = document.getElementById('accountSelect');
  select.innerHTML = '<option value="">Select account…</option>';
  accounts.filter(a => a.is_active).forEach(a => {
    select.insertAdjacentHTML('beforeend',
      `<option value="${a.id}">${esc(a.nickname)} (${esc(a.name)})</option>`);
  });
}

// ── Import CSV form ───────────────────────────────────────────────────────────
document.getElementById('importForm').addEventListener('submit', async e => {
  e.preventDefault();
  const resultDiv = document.getElementById('importResult');
  resultDiv.className = 'mt-3 d-none';

  const fd = new FormData(e.target);
  let result;
  try {
    const res = await fetch('/api/transactions/import', { method: 'POST', body: fd });
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

// ── Boot ──────────────────────────────────────────────────────────────────────
async function refreshAll() {
  await Promise.all([refreshCharts(), refreshCategories(), refreshTable()]);
}

document.querySelector('input[name=date]').valueAsDate = new Date();
refreshAccountSelect();
refreshAll();
