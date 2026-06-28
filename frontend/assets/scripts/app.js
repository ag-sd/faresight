// ── State ─────────────────────────────────────────────────────────────────────
let catChart, monthChart, txTable;
let _accounts = [];

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
  'Transfers & Fees':              '#8e8e93',
  'Other':                         '#aeaeb2',
};

function categoryColor(cat) {
  return CATEGORY_COLORS[cat] ?? '#6c757d';
}

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
        title: 'AI Category', field: 'model_category', widthGrow: 2,
        headerFilter: 'input',
        formatter: (cell) => {
          const { model_category, model_confidence } = cell.getRow().getData();
          if (model_confidence === -1) {
            return '<span class="text-secondary fst-italic small">Pending</span>';
          }
          if (!model_category) return `<span class="badge rounded-pill" style="background-color:${categoryColor('Other')}">Uncategorized</span>`;
          const color = categoryColor(model_category);
          const pill = `<span class="badge rounded-pill" style="background-color:${color}">${esc(model_category)}</span>`;
          const conf = model_confidence != null
            ? `<small class="text-secondary ms-1" style="font-size:0.72em">${model_confidence}/10</small>`
            : '';
          return pill + conf;
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
    api('/api/summary/by-model-category'),
    api('/api/summary/by-month'),
  ]);

  if (catChart) catChart.destroy();
  catChart = new Chart(document.getElementById('catChart'), {
    type: 'doughnut',
    data: {
      labels: byCat.map(r => r.category),
      datasets: [{ data: byCat.map(r => Math.abs(r.total)), backgroundColor: byCat.map(r => categoryColor(r.category)) }],
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
