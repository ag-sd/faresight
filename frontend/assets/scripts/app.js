// ── State ─────────────────────────────────────────────────────────────────────
let catChart, monthChart, txTable;
let _accounts = [];
let _byMonth = [];
let _activeAccountType = 'credit_card';

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
  'Transfers & Fees':              '#8e8e93',
  'Other':                         '#aeaeb2',
};

const MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

function categoryColor(cat) {
  return CATEGORY_COLORS[cat] ?? '#6c757d';
}

// ── Tabulator: transactions ───────────────────────────────────────────────────
function initTxTable() {
  txTable = new Tabulator('#txTable', {
    ajaxURL: '/api/transactions',
    ajaxParams: { account_type: _activeAccountType },
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
    columns: [
      {
        title: 'Date', field: 'date', sorter: 'date',
        headerFilter: 'input', width: 120,
      },
      {
        title: 'Description', field: 'description',
        headerFilter: 'input', widthGrow: 3,
        formatter: (cell) => esc(String(cell.getValue())),
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
        title: '', headerSort: false, hozAlign: 'center', width: 48,
        formatter: () => '<button class="btn btn-link btn-sm p-0 text-secondary"><i class="fa-regular fa-pen-to-square"></i></button>',
        cellClick: (_e, cell) => openEditModal(cell.getRow().getData()),
      },
    ],
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
  const byCat = await api(`/api/summary/by-category-for-period${qs}&account_type=credit_card`);
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
  _byMonth = await api('/api/summary/by-month?account_type=credit_card');
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

let _editTxId = null;

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
  sel.innerHTML = Object.keys(CATEGORY_COLORS)
    .sort()
    .map(c => `<option value="${esc(c)}">${esc(c)}</option>`)
    .join('');
  sel.value = (tx.model_category && tx.model_category in CATEGORY_COLORS) ? tx.model_category : 'Other';
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
  await refreshAll();
}

// ── Boot ──────────────────────────────────────────────────────────────────────
async function refreshAll() {
  await Promise.all([refreshCharts(), refreshTable()]);
}

initTxTable();
(async () => {
  _accounts = await api('/api/accounts');
  // Default month picker to current month before first chart render
  document.getElementById('catMonth').value = String(new Date().getMonth() + 1);
  refreshAll();

  document.querySelectorAll('#txTabs .nav-link').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#txTabs .nav-link').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _activeAccountType = btn.dataset.accountType;
      txTable.setData('/api/transactions', { account_type: _activeAccountType });
    });
  });
})();
