// ── State ─────────────────────────────────────────────────────────────────────
let catChart, monthChart, txTable;
let _accounts = [];

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
        title: 'Source', field: 'account_id', widthGrow: 1,
        formatter: (cell) => {
          const id = cell.getValue();
          if (!id) return '—';
          const acct = _accounts.find(a => a.id === id);
          return acct ? esc(acct.name) : String(id);
        },
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

// ── Boot ──────────────────────────────────────────────────────────────────────
async function refreshAll() {
  await Promise.all([refreshCharts(), refreshTable()]);
}

initTxTable();
(async () => {
  _accounts = await api('/api/accounts');
  refreshAll();
})();
