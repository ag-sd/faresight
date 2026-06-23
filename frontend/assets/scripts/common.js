// ── API helpers ───────────────────────────────────────────────────────────────
const api = async (path, opts = {}) => {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  if (!res.ok) throw new Error(await res.text());
  if (res.status === 204) return null;
  return res.json();
};

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── NAS banners ───────────────────────────────────────────────────────────────
const NAS_BASE = 'alert d-flex align-items-center mb-0 rounded-0 border-0 border-bottom px-4 py-2 small fw-medium';

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
refreshNasBanner();
