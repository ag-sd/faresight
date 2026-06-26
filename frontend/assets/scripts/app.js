// ── State ─────────────────────────────────────────────────────────────────────
let catChart, monthChart, txTable;

// ── Tabulator: transactions ───────────────────────────────────────────────────
function initTxTable() {
  txTable = new Tabulator('#txTable', {
    data: [],
    layout: 'fitColumns',
    movableColumns: true,
    initialSort: [{ column: 'date', dir: 'desc' }],
    columns: [
      {
        title: 'Date', field: 'date', sorter: 'date',
        headerFilter: 'input', width: 120,
      },
      {
        title: 'Description', field: 'description',
        headerFilter: 'input', widthGrow: 3,
        formatter: (cell) => {
          const { description, note } = cell.getRow().getData();
          return note
            ? `${esc(String(description))}<br><small class="text-muted">${esc(String(note))}</small>`
            : esc(String(description));
        },
      },
      {
        title: 'Category', field: 'category',
        headerFilter: 'input', width: 160,
        formatter: (cell) => {
          const v = cell.getValue();
          return v ? `<span class="badge bg-secondary text-dark">${esc(String(v))}</span>` : '';
        },
      },
      {
        title: 'Source', field: 'source',
        headerFilter: 'input', widthGrow: 1,
        formatter: (cell) => cell.getValue() ? esc(String(cell.getValue())) : '—',
      },
      {
        title: 'Amount', field: 'amount', sorter: 'number',
        headerFilter: 'input', hozAlign: 'right', cssClass: 'amount', width: 130,
        formatter: (cell) => {
          const val = parseFloat(cell.getValue());
          const neg = val < 0;
          cell.getElement().style.color = neg ? 'var(--bs-danger)' : 'var(--bs-success)';
          return (neg ? '-' : '+') + '$' + Math.abs(val).toFixed(2);
        },
      },
      {
        title: '', headerSort: false, hozAlign: 'center', width: 72,
        formatter: () => '<button class="btn btn-danger btn-sm">Del</button>',
        cellClick: (_e, cell) => deleteTx(cell.getRow().getData().id),
      },
    ],
  });
}

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

// ── Category datalist ─────────────────────────────────────────────────────────
async function refreshCategories() {
  const cats = await api('/api/categories');
  const datalist = document.getElementById('catList');
  datalist.innerHTML = '';
  cats.forEach(c => datalist.insertAdjacentHTML('beforeend', `<option value="${c}">`));
}

// ── Transaction list ──────────────────────────────────────────────────────────
async function refreshTable() {
  const txs = await api('/api/transactions');
  txTable.setData(txs);
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

// ── Boot ──────────────────────────────────────────────────────────────────────
async function refreshAll() {
  await Promise.all([refreshCharts(), refreshCategories(), refreshTable()]);
}

document.querySelector('input[name=date]').valueAsDate = new Date();
initTxTable();
refreshAccountSelect();
refreshAll();
