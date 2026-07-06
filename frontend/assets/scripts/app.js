// ── State ─────────────────────────────────────────────────────────────────────
let catChart, monthChart, txTable;
let _accounts = [];
let _byMonth = [];

const MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

// ── Tabulator: transactions ───────────────────────────────────────────────────
function initTxTable() {
  txTable = new Tabulator('#txTable', {
    ajaxURL: '/api/transactions',
    pagination: true,
    paginationMode: 'remote',
    paginationSize: 25,
    layout: 'fitColumns',
    movableColumns: true,
    initialSort: [{ column: 'date', dir: 'desc' }],
    dataSendParams: { size: 'limit' },
    ajaxResponse: (_url, _p, response) => ({
      data: response.data,
      last_page: Math.ceil(response.total / response.limit),
    }),
    columns: txColumns({ accounts: () => _accounts, withEdit: true }),
  });
}

// ── Charts ────────────────────────────────────────────────────────────────────
function populateYearPicker(selId) {
  const years = [...new Set(_byMonth.map(r => r.year))].sort((a, b) => b - a);
  const currentYear = new Date().getFullYear();
  if (!years.includes(currentYear)) years.unshift(currentYear);
  const sel = document.getElementById(selId);
  const prev = sel.value;
  sel.innerHTML = years.map(y => `<option value="${y}">${y}</option>`).join('');
  sel.value = (prev && years.includes(Number(prev))) ? prev : String(currentYear);
}

async function refreshCatChart() {
  const year  = document.getElementById('catYear').value;
  const month = document.getElementById('catMonth').value;
  if (!year) return;
  const qs = month ? `?year=${year}&month=${month}` : `?year=${year}`;
  const byCat = await api(`/api/summary/by-category-for-period${qs}`);
  if (catChart) catChart.destroy();
  catChart = new Chart(document.getElementById('catChart'), {
    type: 'pie',
    data: {
      labels: byCat.map(r => r.category),
      datasets: [{ data: byCat.map(r => Math.abs(r.total)), backgroundColor: byCat.map(r => categoryColor(r.category)) }],
    },
    options: { plugins: { legend: { position: 'right' } }, maintainAspectRatio: false },
  });
}

function renderMonthChart() {
  const year = Number(document.getElementById('monthYear').value);
  const filtered = _byMonth.filter(r => r.year === year);
  const totals = filtered.map(r => Math.abs(r.total));
  const avg = totals.length ? totals.reduce((a, b) => a + b, 0) / totals.length : 0;
  if (monthChart) monthChart.destroy();
  monthChart = new Chart(document.getElementById('monthChart'), {
    type: 'bar',
    data: {
      labels: filtered.map(r => MONTH_NAMES[r.month - 1]),
      datasets: [
        { label: 'Spending', data: totals, backgroundColor: '#0071e3' },
        {
          type: 'line', label: 'Monthly avg',
          data: totals.map(() => avg),
          borderColor: '#ff9f0a', borderWidth: 2, borderDash: [6, 3],
          pointRadius: 0, fill: false,
        },
      ],
    },
    options: { plugins: { legend: { display: true } }, maintainAspectRatio: false },
  });
}

async function refreshCharts() {
  _byMonth = await api('/api/summary/by-month');
  populateYearPicker('catYear');
  populateYearPicker('monthYear');
  await Promise.all([refreshCatChart(), Promise.resolve(renderMonthChart())]);
}

function onCatPickerChange() { refreshCatChart(); }
function onMonthYearChange() { renderMonthChart(); }

// ── Transaction list ──────────────────────────────────────────────────────────
async function refreshTable() {
  await txTable.setPage(1);
}

// openEditModal / saveCategory live in common.js; this page just supplies the refresh.
afterCategorySave = refreshAll;

// ── Boot ──────────────────────────────────────────────────────────────────────
async function refreshAll() {
  await Promise.all([refreshCharts(), refreshTable()]);
}

(async () => {
  _accounts = await api('/api/accounts');
  // initTxTable after accounts load so txColumns captures the populated list.
  initTxTable();
  // Default month picker to current month before first chart render
  document.getElementById('catMonth').value = String(new Date().getMonth() + 1);
  refreshAll();
})();
