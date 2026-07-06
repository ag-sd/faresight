// ── State ─────────────────────────────────────────────────────────────────────
let catChart, monthChart, cashflowChart;
let _byMonth = [];
let _cashflow = [];

const MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

function fmtMoney(v) {
  return (v < 0 ? '-' : '') + '$' + Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 });
}

// ── Badges ────────────────────────────────────────────────────────────────────
function renderDeltaChip(elId, delta, downIsGood) {
  const el = document.getElementById(elId);
  if (delta === null || delta === 0) { el.classList.add('d-none'); return; }
  const up = delta > 0;
  const good = downIsGood ? !up : up;
  el.className = `badge rounded-pill ${good ? 'text-bg-success' : 'text-bg-danger'}`;
  el.textContent = `${up ? '▲' : '▼'} ${fmtMoney(Math.abs(delta))}`;
}

async function refreshBadges() {
  const b = await api('/api/summary/badges');
  const nw = document.getElementById('badgeNetWorth');
  nw.textContent = fmtMoney(b.net_worth);
  nw.title = `Assets ${fmtMoney(b.assets)} · Liabilities ${fmtMoney(b.liabilities)}`;

  document.getElementById('badgeSpend').textContent = fmtMoney(Math.abs(b.month_spend));
  document.getElementById('badgeIncome').textContent = fmtMoney(b.month_income);
  document.getElementById('badgeSavingsRate').textContent =
    b.savings_rate == null ? '—' : `${(b.savings_rate * 100).toFixed(0)}%`;

  // Delta chips vs. last month — hidden when there is no prior-month data.
  renderDeltaChip('badgeSpendDelta',
    b.prev_month_spend === 0 ? null : Math.abs(b.month_spend) - Math.abs(b.prev_month_spend),
    true);   // spending less than last month is good
  renderDeltaChip('badgeIncomeDelta',
    b.prev_month_income === 0 ? null : b.month_income - b.prev_month_income,
    false);  // earning more than last month is good
}

// ── Charts ────────────────────────────────────────────────────────────────────
function populateYearPicker(selId, rows) {
  const years = [...new Set(rows.map(r => r.year))].sort((a, b) => b - a);
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

function renderCashFlowChart() {
  const year = Number(document.getElementById('cashflowYear').value);
  const rows = _cashflow.filter(r => r.year === year);
  if (cashflowChart) cashflowChart.destroy();
  cashflowChart = new Chart(document.getElementById('cashflowChart'), {
    type: 'bar',
    data: {
      labels: rows.map(r => MONTH_NAMES[r.month - 1]),
      datasets: [
        { label: 'Income', data: rows.map(r => r.income), backgroundColor: '#34c759' },
        // spend is negative in the API, so bars draw below the zero line.
        { label: 'Spending', data: rows.map(r => r.spend), backgroundColor: '#ff375f' },
        {
          type: 'line', label: 'Net',
          data: rows.map(r => r.net),
          borderColor: '#0071e3', borderWidth: 2,
          pointRadius: 3, fill: false,
        },
      ],
    },
    options: { plugins: { legend: { display: true } }, maintainAspectRatio: false },
  });
}

async function refreshCharts() {
  [_byMonth, _cashflow] = await Promise.all([
    api('/api/summary/by-month'),
    api('/api/summary/cashflow'),
  ]);
  populateYearPicker('catYear', _byMonth);
  populateYearPicker('monthYear', _byMonth);
  populateYearPicker('cashflowYear', _cashflow);
  await refreshCatChart();
  renderMonthChart();
  renderCashFlowChart();
}

function onCatPickerChange() { refreshCatChart(); }
function onMonthYearChange() { renderMonthChart(); }
function onCashflowYearChange() { renderCashFlowChart(); }

// ── Insights ──────────────────────────────────────────────────────────────────
function fmtShortDate(iso) {
  const d = new Date(iso + 'T00:00:00');
  return `${MONTH_NAMES[d.getMonth()]} ${d.getDate()}`;
}

const CADENCE_LABEL = { weekly: '/wk', monthly: '/mo', yearly: '/yr' };

async function refreshInsights() {
  const [recurring, movers, merchants] = await Promise.all([
    api('/api/insights/recurring'),
    api('/api/insights/category-trends'),
    api('/api/insights/top-merchants?limit=5'),
  ]);

  // Recurring & subscriptions
  const totalEl = document.getElementById('recurringTotal');
  const recList = document.getElementById('recurringList');
  if (recurring.items.length) {
    const n = recurring.items.length;
    totalEl.textContent =
      `${fmtMoney(Math.abs(recurring.monthly_total))}/mo across ${n} charge${n !== 1 ? 's' : ''}`;
    recList.innerHTML = recurring.items.map(i => `
      <li class="list-group-item d-flex justify-content-between align-items-center px-0">
        <div>
          <span class="fw-medium">${esc(i.description)}</span>
          <span class="badge rounded-pill text-bg-light ms-1">${esc(i.cadence)}</span>
          ${i.price_changed
            ? `<span class="badge rounded-pill text-bg-danger ms-1">▲ was ${fmtMoney(Math.abs(i.previous_amount))}</span>`
            : ''}
        </div>
        <div class="text-end small">
          <div class="fw-medium">${fmtMoney(Math.abs(i.amount))}${CADENCE_LABEL[i.cadence] ?? ''}</div>
          <div class="text-muted">next ~${fmtShortDate(i.next_expected)}</div>
        </div>
      </li>`).join('');
  }

  // Top movers — delta is current − previous on raw negatives:
  // more negative = spent more than last month = red ▲.
  const moversList = document.getElementById('topMoversList');
  if (movers.length) {
    moversList.innerHTML = movers.slice(0, 5).map(m => {
      const spentMore = m.delta < 0;
      const chip = `<span class="badge rounded-pill ${spentMore ? 'text-bg-danger' : 'text-bg-success'}">` +
        `${spentMore ? '▲' : '▼'} ${fmtMoney(Math.abs(m.delta))}</span>`;
      const avg = m.avg_3mo != null
        ? `<span class="text-muted small ms-2">3-mo avg ${fmtMoney(Math.abs(m.avg_3mo))}</span>` : '';
      return `
        <li class="list-group-item d-flex justify-content-between align-items-center px-0">
          <div>
            <span class="badge rounded-pill" style="background-color:${categoryColor(m.category)}">${esc(m.category)}</span>
            ${avg}
          </div>
          <div class="d-flex align-items-center gap-2">
            <span class="small fw-medium">${fmtMoney(Math.abs(m.current))}</span>
            ${chip}
          </div>
        </li>`;
    }).join('');
  }

  // Top merchants
  const merchList = document.getElementById('topMerchantsList');
  if (merchants.length) {
    merchList.innerHTML = merchants.map(m => `
      <li class="list-group-item d-flex justify-content-between align-items-center px-0">
        <span class="text-truncate me-2">${esc(m.description)}</span>
        <span class="small text-nowrap"><span class="text-muted">${m.count}×</span>
          <span class="fw-medium ms-1">${fmtMoney(Math.abs(m.total))}</span></span>
      </li>`).join('');
  }
}

// ── Boot ──────────────────────────────────────────────────────────────────────
async function refreshAll() {
  await Promise.all([refreshBadges(), refreshCharts(), refreshInsights()]);
}

(() => {
  // Default month picker to current month before first chart render
  document.getElementById('catMonth').value = String(new Date().getMonth() + 1);
  refreshAll();
})();
