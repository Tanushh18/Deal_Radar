/* ============================================================
   DealRadar — frontend
   Vanilla JS SPA. No build step: what you see is what ships.
   ============================================================ */
(() => {
  'use strict';

  const $  = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = {
    user: null,
    page: 'deals',
    filters: {
      q: '', category: '', subcategory: '', store: '', brand: '',
      max_price: null, min_discount: 0, only_lowest: false,
      all_channels: false, sort: 'newest',
    },
    offset: 0,
    limit: 48,
    total: 0,
    loading: false,       // guards "Load more" only — see searchAbortController for search
    availableChannels: [],
    channelDeals: {},     // tg_id -> live deal count, from /api/channels
    selectedChannels: new Set(),
    categories: [],
    facets: { stores: [], brands: [] },
    alertCount: 0,
    lastStats: null,
    countersAnimated: false,
  };

  // Fast typing can fire a new search before the previous one resolves. Rather
  // than drop the newer request (the old `state.loading` guard did this, and
  // could leave the grid showing results for a stale query), cancel the old
  // one so only the latest ever gets to render.
  let searchAbortController = null;

  /* ---------------- API ---------------- */
  async function api(path, options = {}) {
    const res = await fetch(path, {
      credentials: 'same-origin',
      headers: options.body ? { 'Content-Type': 'application/json' } : {},
      ...options,
    });
    let data = null;
    try { data = await res.json(); } catch { data = null; }
    if (!res.ok) {
      const message = (data && (data.detail || data.message)) || `Request failed (${res.status})`;
      const error = new Error(typeof message === 'string' ? message : JSON.stringify(message));
      error.status = res.status;
      throw error;
    }
    return data;
  }
  const post = (path, body) => api(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined });
  const del  = (path) => api(path, { method: 'DELETE' });

  /* ---------------- formatting helpers ---------------- */
  const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));

  const money = (n) => (n == null ? '—' : '₹' + Math.round(n).toLocaleString('en-IN'));
  // The parser stores store/brand names lowercased ("amazon", "cuttli").
  const titleCase = (s) => String(s || '').replace(/\b[a-z]/g, (c) => c.toUpperCase());
  const storeName = (deal) => (deal.store && deal.store !== 'unknown' ? titleCase(deal.store) : '');
  const num = (n) => (n || 0).toLocaleString('en-IN');
  const icon = (name, cls = '') => `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;

  function timeAgo(ts) {
    if (!ts) return '';
    const secs = Math.max(0, Date.now() / 1000 - ts);
    if (secs < 60) return 'just now';
    if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
    if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
    return `${Math.floor(secs / 86400)}d ago`;
  }

  const reduceMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------------- UI helpers ---------------- */
  const TOAST_ICON = { ok: 'check', err: 'alert', info: 'info' };

  function toast(message, kind = 'info', ms = 4200) {
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    el.innerHTML = `<span class="t-ico">${icon(TOAST_ICON[kind] || 'info')}</span><span></span>`;
    el.lastElementChild.textContent = message;
    $('#toasts').appendChild(el);
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(8px)';
      setTimeout(() => el.remove(), 260);
    }, ms);
  }

  function busy(btn, on) {
    if (!btn) return;
    btn.classList.toggle('loading', on);
    btn.disabled = on;
  }

  /* An open sheet sits at scroll position 0, which is exactly when the Android
     shell arms pull-to-refresh — so a downward drag inside a sheet would reload
     the app. Tell the shell to stand down while any overlay is up. */
  function syncOverlayState() {
    const open = !$('#modal').classList.contains('hidden')
      || $('#filters').classList.contains('open')
      || !$('#user-drop').classList.contains('hidden');
    document.body.style.overflow = open ? 'hidden' : '';
    try { window.DealRadarNative?.setPullToRefresh(!open); } catch { /* browser, not the shell */ }
    return open;
  }

  function openModal(html) {
    $('#modal-body').innerHTML = html;
    $('#modal').classList.remove('hidden');
    syncOverlayState();
  }
  function closeModal() {
    $('#modal').classList.add('hidden');
    syncOverlayState();
  }

  /* The sticky results toolbar has to sit exactly under the app bar, whose
     height changes with the viewport (the search field wraps onto its own row
     on phones). Measuring beats guessing. */
  function measureChrome() {
    const bar = $('#appbar');
    if (!bar || bar.offsetParent === null) return;
    document.documentElement.style.setProperty('--appbar-h', `${Math.round(bar.getBoundingClientRect().height)}px`);
  }

  /* ============================================================
     AUTH
     ============================================================ */
  let loginId = null;

  function showStep(step) {
    ['phone', 'code', 'password'].forEach((s) => {
      $(`#step-${s}`).classList.toggle('hidden', s !== step);
    });
    $('#auth-error').classList.add('hidden');
  }

  function authError(message) {
    const el = $('#auth-error');
    el.textContent = message;
    el.classList.remove('hidden');
  }

  async function initAuthScreen() {
    try {
      const config = await api('/api/auth/config');
      if (!config.telegram_configured) {
        const warn = $('#auth-warning');
        warn.innerHTML = '<b>Server not configured.</b> TELEGRAM_API_ID and TELEGRAM_API_HASH '
          + 'are missing, so sign-in is disabled. Add them in your environment and redeploy.';
        warn.classList.remove('hidden');
        $$('#step-phone button, #step-phone input').forEach((el) => { el.disabled = true; });
      }
    } catch { /* the banner is a nicety, not a requirement */ }
  }

  $('#step-phone').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector('button[type=submit]');
    busy(btn, true);
    try {
      const res = await post('/api/auth/send-code', { phone: $('#input-phone').value });
      loginId = res.login_id;
      $('#code-phone').textContent = res.phone;
      showStep('code');
      $('#input-code').focus();
    } catch (err) {
      authError(err.message);
    } finally { busy(btn, false); }
  });

  $('#step-code').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector('button[type=submit]');
    busy(btn, true);
    try {
      const res = await post('/api/auth/verify-code', { login_id: loginId, code: $('#input-code').value });
      if (res.status === 'password_required') {
        showStep('password');
        $('#input-password').focus();
      } else {
        await onSignedIn(res.user);
      }
    } catch (err) {
      authError(err.message);
    } finally { busy(btn, false); }
  });

  $('#step-password').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector('button[type=submit]');
    busy(btn, true);
    try {
      const res = await post('/api/auth/verify-password', {
        login_id: loginId, password: $('#input-password').value,
      });
      await onSignedIn(res.user);
    } catch (err) {
      authError(err.message);
    } finally { busy(btn, false); }
  });

  $$('[data-back]').forEach((btn) => btn.addEventListener('click', () => showStep(btn.dataset.back)));

  function greeting() {
    const h = new Date().getHours();
    if (h < 5)  return 'Still up? Fresh deals below';
    if (h < 12) return 'Good morning';
    if (h < 17) return 'Good afternoon';
    if (h < 22) return 'Good evening';
    return 'Good night';
  }

  async function onSignedIn(user) {
    state.user = user;
    $('#boot')?.remove();
    $('#view-login').classList.add('hidden');
    $('#view-app').classList.remove('hidden');

    const name = user.first_name || user.username || '';
    const initials = (name || 'U').slice(0, 1).toUpperCase();
    $('#user-btn').textContent = initials;
    $('#drop-avatar').textContent = initials;
    $('#drop-name').textContent = user.first_name || 'Telegram user';
    $('#drop-handle').textContent = user.username ? '@' + user.username : '';
    $('#greeting').textContent = name ? `${greeting()}, ${name} 👋` : `${greeting()} 👋`;

    measureChrome();

    await loadCategories();
    const mine = await api('/api/channels').catch(() => ({ channels: [] }));
    state.channelDeals = {};
    (mine.channels || []).forEach((c) => { state.channelDeals[c.tg_id] = c.live_deals; });
    const active = (mine.channels || []).filter((c) => c.enabled);
    if (!active.length) {
      // Nothing tracked yet — send them straight to channel selection.
      toast('Pick the deal channels you want DealRadar to read.', 'info', 6000);
      navigate('channels');
      loadAvailableChannels();
    } else {
      navigate('deals');
      refreshDeals(true);
      loadFacets();
      loadRails();
    }
    loadStats();
    loadAlertCount();
  }

  /* ============================================================
     NAVIGATION
     ============================================================ */
  function navigate(page) {
    state.page = page;
    ['deals', 'channels', 'alerts'].forEach((p) => {
      $(`#page-${p}`).classList.toggle('hidden', p !== page);
    });
    $$('.navlink, .navbtn').forEach((n) => n.classList.toggle('active', n.dataset.nav === page));
    window.scrollTo({ top: 0, behavior: reduceMotion() ? 'auto' : 'smooth' });
    closeFilters();
    closeUserMenu();
    closeSuggest();
    if (page === 'alerts') loadAlerts();
    if (page === 'channels' && !state.availableChannels.length) loadAvailableChannels();
  }

  $$('[data-nav]').forEach((el) => el.addEventListener('click', () => navigate(el.dataset.nav)));

  /* ---------------- account menu ---------------- */
  function openUserMenu() {
    $('#user-drop').classList.remove('hidden');
    $('#nav-account').classList.add('active');
    if (isSheetLayout()) $('#sheet-backdrop').classList.remove('hidden');
    syncOverlayState();
  }
  function closeUserMenu() {
    if ($('#user-drop').classList.contains('hidden')) return;
    $('#user-drop').classList.add('hidden');
    $('#nav-account').classList.remove('active');
    $$('.navbtn').forEach((n) => n.classList.toggle('active', n.dataset.nav === state.page));
    if (!$('#filters').classList.contains('open')) $('#sheet-backdrop').classList.add('hidden');
    syncOverlayState();
  }
  const toggleUserMenu = () => (
    $('#user-drop').classList.contains('hidden') ? openUserMenu() : closeUserMenu()
  );

  $('#user-btn').addEventListener('click', (e) => { e.stopPropagation(); toggleUserMenu(); });
  $('#nav-account').addEventListener('click', (e) => { e.stopPropagation(); toggleUserMenu(); });
  document.addEventListener('click', closeUserMenu);
  $('#user-drop').addEventListener('click', (e) => e.stopPropagation());

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    // Keeps the Android/PWA status bar the same colour as the page it sits above.
    const meta = $('#meta-theme-color');
    if (meta) meta.setAttribute('content', theme === 'dark' ? '#080b12' : '#f7f8fa');
    document.querySelector('meta[name="color-scheme"]')
      ?.setAttribute('content', theme === 'dark' ? 'dark light' : 'light dark');
  }

  /* Delegated, not bound per item: the Android shell injects its own
     "App settings" entry into this menu after load, and it should dismiss the
     sheet like every other row. */
  $('#user-drop').addEventListener('click', async (e) => {
    const item = e.target.closest('.drop-item');
    if (!item) return;
    const action = item.dataset.action;
    closeUserMenu();
    if (!action) return;             // injected by the native shell — it handles itself
    if (action === 'logout') {
      await post('/api/auth/logout').catch(() => {});
      window.location.reload();
    } else if (action === 'theme') {
      const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      applyTheme(next);
      try { localStorage.setItem('dr-theme', next); } catch { /* private mode */ }
    } else if (action === 'status') {
      showStatus();
    } else if (action === 'install') {
      promptInstall();
    }
  });

  async function showStatus() {
    openModal(sheetShell('System status', '<p class="muted">Loading system status…</p>'));
    try {
      const health = await api('/api/health');
      const sheetsInfo = health.checks.sheets || {};
      const ingestInfo = health.checks.ingest || {};
      openModal(sheetShell('System status', `
        <dl class="kv">
          <dt>Service</dt><dd>${escapeHtml(health.status)} · up ${Math.floor(health.uptime_seconds / 60)} min</dd>
          <dt>Database</dt><dd>${escapeHtml(health.checks.database)}</dd>
          <dt>Telegram API</dt><dd>${health.checks.telegram_configured ? 'configured' : 'not configured'}</dd>
          <dt>Google Sheets</dt><dd>${sheetsInfo.configured ? (sheetsInfo.connected ? 'connected' : 'configured, not connected') : 'not configured'}</dd>
          <dt>Rows in Sheets</dt><dd>${sheetsInfo.rows_tracked ?? 0}</dd>
          <dt>Last sheet flush</dt><dd class="raw">${escapeHtml(sheetsInfo.last_flush || 'never')}</dd>
          <dt>Ingest cycles</dt><dd>${ingestInfo.cycles ?? 0}</dd>
          <dt>Last sync</dt><dd>${ingestInfo.last_run_ago_seconds != null ? Math.floor(ingestInfo.last_run_ago_seconds / 60) + ' min ago' : 'not yet'}</dd>
          <dt>Last error</dt><dd class="raw">${escapeHtml(ingestInfo.last_error || sheetsInfo.last_error || 'none')}</dd>
        </dl>
        <p class="fineprint">Ping endpoint: <code>/api/ping</code> · API docs: <a href="/api/docs" target="_blank" rel="noopener">/api/docs</a></p>
      `));
    } catch (err) {
      openModal(sheetShell('System status', `<p class="alert alert-error">${escapeHtml(err.message)}</p>`));
    }
  }

  /** Standard sheet chrome: sticky title bar + padded body. */
  function sheetShell(title, bodyHtml) {
    return `
      <div class="modal-head">
        <h2>${escapeHtml(title)}</h2>
        <button class="btn btn-soft btn-xs" data-close>Close</button>
      </div>
      <div class="modal-pad">${bodyHtml}</div>`;
  }

  /* ============================================================
     CATEGORIES
     ============================================================ */
  // Keyed by the taxonomy names the backend actually returns; anything new
  // falls back to a neutral tag icon rather than breaking the row.
  const CATEGORY_ICON = {
    '': '✨',
    'Electronics': '📱',
    'Women Fashion': '👗',
    'Men Fashion': '👔',
    'Footwear': '👟',
    'Appliances': '🔌',
    'Home & Kitchen': '🏠',
    'Beauty': '💄',
    'Grocery': '🛒',
    'Baby & Kids': '🧸',
    'Bags & Luggage': '🎒',
    'Books & Stationery': '📚',
    'Sports & Fitness': '🏋️',
    'Other': '🎁',
  };

  async function loadCategories() {
    try {
      const res = await api('/api/deals/categories');
      state.categories = res.categories || [];
      renderCategoryChips();
      const select = $('#a-category');
      state.categories.forEach((c) => {
        const opt = document.createElement('option');
        opt.value = c.name; opt.textContent = c.name;
        select.appendChild(opt);
      });
    } catch { /* non-fatal */ }
  }

  function renderCategoryChips() {
    const row = $('#category-chips');
    const chips = [{ name: '', label: 'All deals' }]
      .concat(state.categories.map((c) => ({ name: c.name, label: c.name })));
    row.innerHTML = chips.map((c) => `
      <button class="cat ${state.filters.category === c.name ? 'active' : ''}"
              data-cat="${escapeHtml(c.name)}" role="tab"
              aria-selected="${state.filters.category === c.name}">
        <span class="cat-icon" aria-hidden="true">${CATEGORY_ICON[c.name] || '🏷️'}</span>
        <span class="cat-label">${escapeHtml(c.label)}</span>
      </button>`).join('');

    $$('.cat', row).forEach((chip) => chip.addEventListener('click', () => {
      state.filters.category = chip.dataset.cat;
      state.filters.subcategory = '';
      renderCategoryChips();
      renderSubcategoryFilter();
      refreshDeals(true);
      // Keep the chosen category in view rather than leaving it half-scrolled.
      chip.scrollIntoView({ behavior: reduceMotion() ? 'auto' : 'smooth', inline: 'center', block: 'nearest' });
    }));
  }

  function renderSubcategoryFilter() {
    const block = $('#f-subcategory-block');
    const select = $('#f-subcategory');
    const category = state.categories.find((c) => c.name === state.filters.category);

    if (!category) {
      block.classList.add('hidden');
      select.innerHTML = '<option value="">All</option>';
      return;
    }
    select.innerHTML = '<option value="">All</option>' + category.subcategories.map((s) => (
      `<option value="${escapeHtml(s)}" ${state.filters.subcategory === s ? 'selected' : ''}>${escapeHtml(s)}</option>`
    )).join('');
    block.classList.remove('hidden');
  }

  /* ============================================================
     FILTER STATE / CHIPS
     ============================================================ */
  function activeFilterCount() {
    const f = state.filters;
    return [
      f.category, f.subcategory, f.store, f.brand,
      f.max_price ? 1 : 0, f.min_discount ? 1 : 0, f.only_lowest ? 1 : 0, f.all_channels ? 1 : 0,
    ].filter(Boolean).length;
  }

  const isBrowseMode = () => !state.filters.q && activeFilterCount() === 0;

  function updateFiltersBadge() {
    const count = activeFilterCount();
    const badge = $('#filters-badge');
    badge.textContent = count;
    badge.classList.toggle('hidden', count === 0);
    renderActiveChips();
  }

  function renderActiveChips() {
    const f = state.filters;
    const chips = [];
    if (f.category)    chips.push(['category', f.category]);
    if (f.subcategory) chips.push(['subcategory', f.subcategory]);
    if (f.store)       chips.push(['store', f.store]);
    if (f.brand)       chips.push(['brand', f.brand]);
    if (f.max_price)   chips.push(['max_price', `Under ${money(f.max_price)}`]);
    if (f.min_discount) chips.push(['min_discount', `${f.min_discount}%+ off`]);
    if (f.only_lowest) chips.push(['only_lowest', 'All-time lows']);
    if (f.all_channels) chips.push(['all_channels', 'All channels']);

    const box = $('#active-filters');
    box.classList.toggle('hidden', !chips.length);
    if (!chips.length) { box.innerHTML = ''; return; }

    box.innerHTML = chips.map(([key, label]) => `
      <button class="chip removable" data-clear="${key}">
        ${escapeHtml(label)}
        <span class="chip-x">${icon('close')}</span>
      </button>`).join('');

    $$('[data-clear]', box).forEach((btn) => btn.addEventListener('click', () => {
      clearFilter(btn.dataset.clear);
    }));
  }

  function clearFilter(key) {
    const f = state.filters;
    if (key === 'category') { f.category = ''; f.subcategory = ''; renderCategoryChips(); renderSubcategoryFilter(); }
    else if (key === 'max_price') { f.max_price = null; $('#f-max-price').value = ''; }
    else if (key === 'min_discount') { f.min_discount = 0; $('#f-discount').value = 0; $('#f-discount-out').textContent = 'any'; }
    else if (key === 'only_lowest') { f.only_lowest = false; $('#f-lowest').checked = false; }
    else if (key === 'all_channels') { f.all_channels = false; $('#f-all-channels').checked = false; loadFacets(); }
    else if (key === 'subcategory') { f.subcategory = ''; $('#f-subcategory').value = ''; }
    else { f[key] = ''; renderFacet('#f-stores', state.facets.stores, 'store'); renderFacet('#f-brands', state.facets.brands, 'brand'); }
    refreshDeals(true);
  }

  /* ---------------- filter sheet ---------------- */
  const isSheetLayout = () => window.innerWidth < 1024;

  function openFilters() {
    $('#filters').classList.add('open');
    $('#sheet-backdrop').classList.remove('hidden');
    syncOverlayState();
  }
  function closeFilters() {
    const sheet = $('#filters');
    if (!sheet.classList.contains('open')) return;
    sheet.classList.remove('open');
    sheet.style.transform = '';
    if ($('#user-drop').classList.contains('hidden')) $('#sheet-backdrop').classList.add('hidden');
    syncOverlayState();
  }
  $('#btn-filters-toggle').addEventListener('click', openFilters);
  $('#btn-filters-close').addEventListener('click', closeFilters);
  $('#btn-filters-apply').addEventListener('click', closeFilters);
  $('#sheet-backdrop').addEventListener('click', () => { closeFilters(); closeUserMenu(); });

  /* Drag-to-dismiss on the sheet handle — the gesture Android users expect. */
  (function initSheetDrag() {
    const sheet = $('#filters');
    const grab = $('#sheet-grab');
    let startY = 0, dy = 0, dragging = false;

    grab.addEventListener('pointerdown', (e) => {
      if (!isSheetLayout()) return;
      dragging = true; startY = e.clientY; dy = 0;
      sheet.classList.add('dragging');
      grab.setPointerCapture(e.pointerId);
    });
    grab.addEventListener('pointermove', (e) => {
      if (!dragging) return;
      dy = Math.max(0, e.clientY - startY);
      sheet.style.transform = `translateY(${dy}px)`;
    });
    const end = () => {
      if (!dragging) return;
      dragging = false;
      sheet.classList.remove('dragging');
      sheet.style.transform = '';
      if (dy > 110) closeFilters();
    };
    grab.addEventListener('pointerup', end);
    grab.addEventListener('pointercancel', end);
  })();

  /* ============================================================
     DEALS
     ============================================================ */
  function buildQuery(overrides = {}) {
    const f = { ...state.filters, ...overrides };
    const params = new URLSearchParams();
    if (f.q) params.set('q', f.q);
    if (f.category) params.set('category', f.category);
    if (f.subcategory) params.set('subcategory', f.subcategory);
    if (f.store) params.set('store', f.store);
    if (f.brand) params.set('brand', f.brand);
    if (f.max_price) params.set('max_price', f.max_price);
    if (f.min_discount) params.set('min_discount', f.min_discount);
    if (f.only_lowest) params.set('only_lowest', 'true');
    if (f.all_channels) params.set('all_channels', 'true');
    // Sent as-is: the backend already degrades "relevance" to score-order
    // by itself when there's no query to rank against (see search.py).
    params.set('sort', f.sort);
    params.set('limit', overrides.limit ?? state.limit);
    params.set('offset', overrides.offset ?? state.offset);
    return params.toString();
  }

  const skeletonCard = () => `
    <div class="skel skel-card">
      <div class="sk-media"></div>
      <div class="sk-lines">
        <div class="sk-line"></div><div class="sk-line w70"></div><div class="sk-line w45"></div>
      </div>
    </div>`;
  const skeletons = (n = 8) => Array.from({ length: n }, skeletonCard).join('');
  const railSkeletons = (n = 4) => Array.from({ length: n },
    () => `<div class="skel skel-card skel-rail"><div class="sk-media"></div>
           <div class="sk-lines"><div class="sk-line"></div><div class="sk-line w45"></div></div></div>`).join('');

  async function refreshDeals(reset = false) {
    updateFiltersBadge();
    updateGridHeading();
    if (!reset) {
      // Pagination ("Load more"): duplicate clicks should be dropped, not raced.
      if (state.loading) return;
      state.loading = true;
    }

    let signal;
    if (reset) {
      // New search supersedes whatever was in flight — cancel it outright.
      searchAbortController?.abort();
      const controller = new AbortController();
      searchAbortController = controller;
      signal = controller.signal;
      state.offset = 0;
      $('#deal-grid').innerHTML = skeletons();
      $('#empty-state').classList.add('hidden');
      $('#btn-more').classList.add('hidden');
      toggleRails();
    }

    try {
      const res = await api('/api/deals?' + buildQuery(), signal ? { signal } : {});
      state.total = res.total;
      renderDeals(res.results, reset);

      const summary = $('#results-summary');
      if (res.total) {
        const shown = Math.min(state.offset + res.count, res.total);
        summary.innerHTML = `<b>${res.total.toLocaleString()}</b> deal${res.total === 1 ? '' : 's'}`
          + (state.filters.q ? ` for “${escapeHtml(state.filters.q)}”` : '')
          + ` · showing ${shown}`;
      } else {
        summary.textContent = '';
      }
      $('#filter-count').textContent = res.total
        ? `${res.total.toLocaleString()} deal${res.total === 1 ? '' : 's'} match`
        : 'No deals match';

      $('#btn-more').classList.toggle('hidden', state.offset + res.count >= res.total);
      if (!res.total) showEmpty();
    } catch (err) {
      if (err.name === 'AbortError') return; // superseded by a newer search — ignore
      if (err.status === 401) { window.location.reload(); return; }
      showError(err);
    } finally {
      if (!reset) state.loading = false;
    }
  }

  function updateGridHeading() {
    const head = $('#grid-head');
    const title = $('#grid-title');
    const sub = $('#grid-sub');
    if (state.filters.q) {
      title.textContent = `🔎 Results for “${state.filters.q}”`;
      sub.textContent = 'Best matches across your channels';
    } else if (activeFilterCount()) {
      title.textContent = '🏷️ Filtered deals';
      sub.textContent = 'Matching your filters';
    } else {
      const labels = {
        newest: ['🕘 Latest deals', 'Freshly parsed from your channels'],
        best: ['🏆 Top deals', 'Ranked by DealRadar’s deal score'],
        discount: ['⚡ Biggest discounts', 'Largest drop from the quoted MRP'],
        price_low: ['💸 Cheapest first', 'Lowest price across your channels'],
        price_high: ['💎 Priciest first', 'Highest price across your channels'],
        relevance: ['🏆 Top deals', 'Ranked by DealRadar’s deal score'],
      };
      const [t, s] = labels[state.filters.sort] || labels.newest;
      title.textContent = t;
      sub.textContent = s;
    }
    head.classList.remove('hidden');
  }

  function showEmpty() {
    const el = $('#empty-state');
    const hasFilters = !!(state.filters.q || activeFilterCount());

    el.innerHTML = `
      <span class="emoji">${hasFilters ? '🔎' : '📭'}</span>
      <h3>No products found</h3>
      <p>${hasFilters
        ? 'Nothing matches your search and filters right now. Try fewer filters or a broader term.'
        : 'No deals have come in yet. Pick a few channels and run a sync to fill your feed.'}</p>
      <div class="empty-actions">
        ${hasFilters ? '<button class="btn btn-primary" id="empty-clear">Clear search &amp; filters</button>' : ''}
        <button class="btn ${hasFilters ? 'btn-soft' : 'btn-primary'}" id="empty-channels">Choose channels</button>
        <button class="btn btn-soft" id="empty-sync">Sync now</button>
      </div>`;
    el.classList.remove('hidden');
    $('#deal-grid').innerHTML = '';
    $('#empty-clear')?.addEventListener('click', () => {
      $('#search-input').value = '';
      state.filters.q = '';
      $('#search-clear').classList.add('hidden');
      $('#btn-clear-filters').click(); // resets the rest of the filters and re-queries
    });
    $('#empty-channels')?.addEventListener('click', () => navigate('channels'));
    $('#empty-sync')?.addEventListener('click', () => syncNow());
  }

  function showError(err) {
    const offline = !navigator.onLine;
    const el = $('#empty-state');
    el.innerHTML = `
      <span class="emoji">${offline ? '📶' : '⚠️'}</span>
      <h3>${offline ? 'You’re offline' : 'Something went wrong'}</h3>
      <p>${offline
        ? 'We’ll refresh automatically as soon as you’re back online.'
        : 'We couldn’t load the deals right now.'}</p>
      <div class="empty-actions">
        <button class="btn btn-primary" id="error-retry">Try again</button>
      </div>
      <p class="fineprint" style="margin-top:16px">${escapeHtml(err.message || '')}</p>`;
    el.classList.remove('hidden');
    $('#deal-grid').innerHTML = '';
    $('#results-summary').textContent = '';
    $('#error-retry').addEventListener('click', () => refreshDeals(true));
  }

  /* ---------------- deal card ---------------- */
  const FRESH_SECONDS = 5400;   // 90 min — "new" only while it genuinely is

  function isFresh(deal) {
    return deal.posted_at && (Date.now() / 1000 - deal.posted_at) < FRESH_SECONDS;
  }

  function dealMedia(deal, extra = '') {
    // The placeholder sits underneath the image, so a broken URL reveals it
    // instead of wiping the badges that share this container.
    const img = deal.image_url
      ? `<img src="${escapeHtml(deal.image_url)}" alt="" loading="lazy" decoding="async" onerror="this.remove()" />`
      : '';
    return `<div class="placeholder" aria-hidden="true">🛍️</div>${img}${extra}`;
  }

  function dealBadges(deal) {
    const badges = [];
    if (deal.discount_pct >= 10) badges.push(`<span class="badge badge-off">-${deal.discount_pct}%</span>`);
    if (deal.is_lowest) badges.push('<span class="badge badge-low">🟢 LOWEST EVER</span>');
    else if (deal.score >= 80) badges.push('<span class="badge badge-hot">🏆 GREAT DEAL</span>');
    else if (isFresh(deal)) badges.push('<span class="badge badge-new">🆕 NEW</span>');
    return badges;
  }

  function dealCard(deal) {
    const badges = dealBadges(deal);
    const store = storeName(deal)
      ? `<span class="store-tag">${escapeHtml(storeName(deal))}</span>` : '';

    return `
      <article class="deal" data-id="${escapeHtml(deal.id)}">
        <div class="deal-media" data-detail="${escapeHtml(deal.id)}" role="button" tabindex="0"
             aria-label="${escapeHtml(deal.title)}">
          ${dealMedia(deal, `<div class="badges">${badges.join('')}</div>${store}`)}
        </div>
        <div class="deal-body">
          <div class="deal-title" title="${escapeHtml(deal.title)}">${escapeHtml(deal.title)}</div>
          <div class="deal-price">
            <span class="price-now">${money(deal.price)}</span>
            ${deal.mrp ? `<span class="price-was">${money(deal.mrp)}</span>` : ''}
          </div>
          ${deal.saving ? `<div class="price-save">${icon('down')} Save ${money(deal.saving)}</div>` : ''}
          ${deal.coupon ? `<div><span class="coupon">🏷 ${escapeHtml(deal.coupon)}</span></div>` : ''}
          <div class="deal-meta">
            <span>${timeAgo(deal.posted_at)}</span>
            ${deal.repost_count > 1
              ? `<span class="dot"></span><span class="reposts">${deal.repost_count} channels</span>` : ''}
          </div>
        </div>
        <div class="deal-actions">
          <button class="btn btn-soft btn-sm" data-detail="${escapeHtml(deal.id)}">Details</button>
          ${deal.url
            ? `<a class="btn btn-primary btn-sm btn-buy" href="${escapeHtml(deal.url)}" target="_blank" rel="noopener noreferrer nofollow">Buy now</a>`
            : ''}
        </div>
      </article>`;
  }

  function renderDeals(results, reset) {
    const grid = $('#deal-grid');
    const html = results.map(dealCard).join('');
    if (reset) grid.innerHTML = html; else grid.insertAdjacentHTML('beforeend', html);
    $('#empty-state').classList.toggle('hidden', results.length > 0 || state.total > 0);
    bindDetailTriggers(grid);
  }

  function bindDetailTriggers(root) {
    $$('[data-detail]', root).forEach((el) => {
      if (el.dataset.bound) return;
      el.dataset.bound = '1';
      el.addEventListener('click', () => showDealDetail(el.dataset.detail));
      if (el.getAttribute('role') === 'button') {
        el.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); showDealDetail(el.dataset.detail); }
        });
      }
    });
  }

  /* Press feedback that survives a scroll: the card only "depresses" while the
     finger is actually held on it, and releases on scroll/cancel. */
  (function initPressFeedback() {
    let pressed = null;
    const release = () => { pressed?.classList.remove('pressed'); pressed = null; };
    document.addEventListener('pointerdown', (e) => {
      const card = e.target.closest?.('.deal, .railcard');
      if (!card) return;
      pressed = card;
      card.classList.add('pressed');
    }, { passive: true });
    ['pointerup', 'pointercancel', 'pointerleave'].forEach((ev) =>
      document.addEventListener(ev, release, { passive: true }));
    window.addEventListener('scroll', release, { passive: true });
  })();

  /* ---------------- deal detail sheet ---------------- */
  function scoreLabel(score) {
    if (score >= 80) return ['Excellent deal', 'Among the strongest we have seen'];
    if (score >= 60) return ['Good deal', 'Better than most deals in this category'];
    if (score >= 40) return ['Fair deal', 'Reasonable, but not exceptional'];
    return ['Average deal', 'Worth comparing before you buy'];
  }

  function scoreDial(score) {
    const pct = Math.max(0, Math.min(100, Math.round(score || 0)));
    const r = 24, c = 2 * Math.PI * r;
    return `
      <div class="scoredial">
        <svg viewBox="0 0 58 58" aria-hidden="true">
          <circle class="track-ring" cx="29" cy="29" r="${r}" fill="none" stroke-width="5" />
          <circle class="value-ring" cx="29" cy="29" r="${r}" fill="none" stroke-width="5"
                  stroke-linecap="round" stroke-dasharray="${c.toFixed(1)}"
                  stroke-dashoffset="${(c * (1 - pct / 100)).toFixed(1)}" />
        </svg>
        <b>${pct}</b>
      </div>`;
  }

  function dealReasons(deal) {
    const history = deal.price_history || {};
    const out = [];
    if (deal.is_lowest) out.push('Lowest price we have recorded for this product');
    if (deal.discount_pct >= 10) out.push(`${deal.discount_pct}% below the quoted MRP`);
    if (history.median && deal.price && history.points >= 3 && history.median > deal.price) {
      const below = Math.round((1 - deal.price / history.median) * 100);
      if (below >= 5) out.push(`${below}% below its typical price (${history.points} price points)`);
    }
    if (deal.repost_count > 1) out.push(`Posted in ${deal.repost_count} of the channels you track`);
    return out;
  }

  async function showDealDetail(id) {
    openModal(sheetShell('Deal', '<p class="muted" style="padding:16px 0">Loading…</p>'));
    try {
      const [deal, fullHistory] = await Promise.all([
        api(`/api/deals/${encodeURIComponent(id)}`),
        api(`/api/deals/${encodeURIComponent(id)}/history`).catch(() => ({ points: [] })),
      ]);
      const history = deal.price_history || {};
      const badges = dealBadges(deal);
      const [label, blurb] = scoreLabel(deal.score || 0);
      const reasons = dealReasons(deal);
      const suspicious = (deal.flags || []).includes('suspicious_mrp');

      openModal(`
        <div class="modal-head">
          <h2>${escapeHtml(deal.title)}</h2>
          <button class="btn btn-soft btn-xs" data-close>Close</button>
        </div>
        ${deal.image_url ? `<div class="detail-hero">${dealMedia(deal)}</div>` : ''}
        <div class="modal-pad">
          ${badges.length ? `<div class="detail-badges">${badges.join('')}</div>` : ''}
          <div class="detail-price">
            <span class="price-now">${money(deal.price)}</span>
            ${deal.mrp ? `<span class="price-was">${money(deal.mrp)}</span>` : ''}
          </div>
          ${deal.saving ? `<span class="detail-save">${icon('down')} You save ${money(deal.saving)}${deal.discount_pct ? ` · ${deal.discount_pct}% off` : ''}</span>` : ''}

          ${deal.is_lowest ? `<div class="detail-note good">${icon('trend')}<span>Lowest price we have recorded for this product.</span></div>` : ''}
          ${suspicious ? `<div class="detail-note warn">${icon('alert')}<span>The quoted MRP looks inflated versus this product’s price history.</span></div>` : ''}

          <div class="scorecard">
            ${scoreDial(deal.score)}
            <div class="scoretext">
              <div class="st-title">${label}</div>
              <div class="st-sub">Deal score ${Math.round(deal.score || 0)} / 100 — ${blurb}</div>
            </div>
          </div>
          ${reasons.length ? `<ul class="reasons">${reasons.map((r) => `<li>${icon('check')}<span>${escapeHtml(r)}</span></li>`).join('')}</ul>` : ''}

          <div class="price-chart-wrap">
            <div class="price-chart-title">Price history</div>
            <div class="price-chart" id="price-chart-${escapeHtml(id)}"></div>
          </div>

          <dl class="kv">
            <dt>Store</dt><dd>${escapeHtml(deal.store || '—')}</dd>
            <dt>Category</dt><dd>${escapeHtml(deal.category || '—')} › ${escapeHtml(deal.subcategory || '—')}</dd>
            ${deal.brand ? `<dt>Brand</dt><dd>${escapeHtml(deal.brand)}</dd>` : ''}
            ${deal.sizes ? `<dt>Sizes</dt><dd class="raw">${escapeHtml(deal.sizes)}</dd>` : ''}
            ${deal.coupon ? `<dt>Coupon</dt><dd><span class="coupon">${escapeHtml(deal.coupon)}</span></dd>` : ''}
            <dt>Posted</dt><dd class="raw">${timeAgo(deal.posted_at)} in ${escapeHtml(deal.channel_title || 'a channel')}</dd>
            <dt>Reposted</dt><dd>${deal.repost_count} channel${deal.repost_count === 1 ? '' : 's'}</dd>
            <dt>Expires</dt><dd class="raw">${deal.expires_at ? new Date(deal.expires_at * 1000).toLocaleString() : '—'}</dd>
            ${history.points ? `<dt>History</dt><dd class="raw">${history.points} points · low ${money(history.min)} · high ${money(history.max)}</dd>` : ''}
            <dt>Deal score</dt><dd>${Math.round(deal.score ?? 0)} / 100</dd>
          </dl>

          <details class="raw">
            <summary>Original channel post</summary>
            <pre class="rawpost">${escapeHtml(deal.raw_text || '')}</pre>
          </details>
        </div>
        ${deal.url ? `
          <div class="detail-cta">
            <a class="btn btn-primary btn-block" href="${escapeHtml(deal.url)}" target="_blank" rel="noopener noreferrer nofollow">
              Open on ${escapeHtml(storeName(deal) || 'store')} ${icon('external', 'ico')}
            </a>
          </div>` : ''}
      `);
      const chartHost = document.getElementById(`price-chart-${id}`);
      if (chartHost) renderPriceChart(chartHost, fullHistory.points || []);
    } catch (err) {
      openModal(sheetShell('Deal', `<p class="alert alert-error">${escapeHtml(err.message)}</p>`));
    }
  }

  /* ---------------- price history chart ---------------- */
  const SVG_NS = 'http://www.w3.org/2000/svg';

  function svgEl(tag, attrs = {}) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const key in attrs) el.setAttribute(key, attrs[key]);
    return el;
  }

  function renderPriceChart(container, rawPoints) {
    const points = (rawPoints || []).filter((p) => p && p.price != null && p.at);
    if (points.length < 2) {
      container.innerHTML = '<div class="price-chart-empty">Not enough price history yet — '
        + 'check back once this deal has been seen a few more times.</div>';
      return;
    }

    const sorted = [...points].sort((a, b) => a.at - b.at);
    const times = sorted.map((p) => p.at);
    const prices = sorted.map((p) => p.price);

    const W = 600, H = 170;
    const padTop = 16, padBottom = 30, padLeft = 50, padRight = 12;
    const x0 = padLeft, x1 = W - padRight, y0 = padTop, y1 = H - padBottom;

    const tMin = times[0], tMax = times[times.length - 1];
    const rawMin = Math.min(...prices), rawMax = Math.max(...prices);
    let pMin = rawMin, pMax = rawMax;
    if (pMin === pMax) { pMin -= 1; pMax += 1; }
    const pad = (pMax - pMin) * 0.12;
    pMin -= pad; pMax += pad;

    const xScale = (t) => (tMax === tMin ? (x0 + x1) / 2 : x0 + ((t - tMin) / (tMax - tMin)) * (x1 - x0));
    const yScale = (p) => y1 - ((p - pMin) / (pMax - pMin)) * (y1 - y0);
    const fmtPrice = (p) => '₹' + Math.round(p).toLocaleString('en-IN');
    const fmtDate = (ts) => new Date(ts * 1000).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });

    const svg = svgEl('svg', {
      viewBox: `0 0 ${W} ${H}`, role: 'img',
      'aria-label': `Price history from ${fmtPrice(rawMin)} to ${fmtPrice(rawMax)}`,
    });

    // gridlines at the low/mid/high of the actual (unpadded) price range
    const gridVals = rawMin === rawMax ? [rawMin] : [rawMin, (rawMin + rawMax) / 2, rawMax];
    gridVals.forEach((v) => {
      const y = yScale(v);
      svg.appendChild(svgEl('line', { x1: x0, x2: x1, y1: y, y2: y, stroke: 'var(--border)', 'stroke-width': 1 }));
      const label = svgEl('text', { x: x0 - 8, y: y + 3, 'text-anchor': 'end', 'font-size': 10, fill: 'var(--text-3)' });
      label.textContent = fmtPrice(v);
      svg.appendChild(label);
    });

    // step-after path: price holds flat, then jumps at the next observed change —
    // a straight diagonal would imply a gradual change that never actually happened.
    let linePath = `M ${xScale(times[0])} ${yScale(prices[0])}`;
    for (let i = 1; i < sorted.length; i++) {
      linePath += ` H ${xScale(times[i])} V ${yScale(prices[i])}`;
    }
    const areaPath = `${linePath} L ${x1} ${y1} L ${x0} ${y1} Z`;

    svg.appendChild(svgEl('path', { d: areaPath, fill: 'var(--accent)', 'fill-opacity': 0.1, stroke: 'none' }));
    svg.appendChild(svgEl('path', {
      d: linePath, fill: 'none', stroke: 'var(--accent)', 'stroke-width': 2,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round',
    }));

    const xStart = svgEl('text', { x: x0, y: H - 8, 'text-anchor': 'start', 'font-size': 10, fill: 'var(--text-3)' });
    xStart.textContent = fmtDate(times[0]);
    const xEnd = svgEl('text', { x: x1, y: H - 8, 'text-anchor': 'end', 'font-size': 10, fill: 'var(--text-3)' });
    xEnd.textContent = fmtDate(times[times.length - 1]);
    svg.appendChild(xStart);
    svg.appendChild(xEnd);

    // the extreme (lowest price) is worth a direct label even without hovering
    const lastIdx = sorted.length - 1;
    const lowIdx = prices.indexOf(rawMin);
    if (lowIdx !== lastIdx) {
      const lx = xScale(times[lowIdx]), ly = yScale(prices[lowIdx]);
      svg.appendChild(svgEl('circle', { cx: lx, cy: ly, r: 5, fill: 'var(--gold)', stroke: 'var(--surface)', 'stroke-width': 2 }));
      const lowLabel = svgEl('text', {
        x: lx, y: ly - 10, 'text-anchor': lowIdx < sorted.length / 2 ? 'start' : 'end',
        'font-size': 10, 'font-weight': 700, fill: 'var(--gold)',
      });
      lowLabel.textContent = 'Lowest ' + fmtPrice(rawMin);
      svg.appendChild(lowLabel);
    }

    // current price — the endpoint always gets a direct label
    const endX = xScale(times[lastIdx]), endY = yScale(prices[lastIdx]);
    const endIsLow = lowIdx === lastIdx;
    svg.appendChild(svgEl('circle', {
      cx: endX, cy: endY, r: 5, fill: endIsLow ? 'var(--gold)' : 'var(--accent)',
      stroke: 'var(--surface)', 'stroke-width': 2,
    }));
    const endLabel = svgEl('text', {
      x: endX - 8, y: endY - 10, 'text-anchor': 'end', 'font-size': 10, 'font-weight': 700,
      fill: endIsLow ? 'var(--gold)' : 'var(--text)',
    });
    endLabel.textContent = (endIsLow ? 'Lowest · ' : '') + fmtPrice(prices[lastIdx]);
    svg.appendChild(endLabel);

    // hover layer: crosshair snaps to the nearest observed point, per the skill's
    // "readers aim at a date, never at a 2px line" guidance
    const crosshair = svgEl('line', { x1: 0, x2: 0, y1: y0, y2: y1, stroke: 'var(--text-3)', 'stroke-width': 1, opacity: 0 });
    const hoverDot = svgEl('circle', { r: 5, fill: 'var(--accent)', stroke: 'var(--surface)', 'stroke-width': 2, opacity: 0 });
    const overlay = svgEl('rect', { x: x0, y: 0, width: Math.max(x1 - x0, 1), height: H, fill: 'transparent' });
    svg.appendChild(crosshair);
    svg.appendChild(hoverDot);
    svg.appendChild(overlay);

    container.innerHTML = '';
    container.appendChild(svg);

    const tooltip = document.createElement('div');
    tooltip.className = 'chart-tooltip';
    const ctValue = document.createElement('div');
    ctValue.className = 'ct-value';
    const ctDate = document.createElement('div');
    ctDate.className = 'ct-date';
    tooltip.appendChild(ctValue);
    tooltip.appendChild(ctDate);
    container.appendChild(tooltip);

    function nearestIndex(clientX) {
      const rect = svg.getBoundingClientRect();
      const svgX = ((clientX - rect.left) / rect.width) * W;
      let best = 0, bestDist = Infinity;
      times.forEach((t, i) => {
        const dist = Math.abs(xScale(t) - svgX);
        if (dist < bestDist) { bestDist = dist; best = i; }
      });
      return best;
    }

    function showAt(i) {
      const x = xScale(times[i]), y = yScale(prices[i]);
      crosshair.setAttribute('x1', x);
      crosshair.setAttribute('x2', x);
      crosshair.setAttribute('opacity', 1);
      hoverDot.setAttribute('cx', x);
      hoverDot.setAttribute('cy', y);
      hoverDot.setAttribute('opacity', 1);

      ctValue.textContent = fmtPrice(prices[i]);
      ctDate.textContent = new Date(times[i] * 1000).toLocaleDateString('en-IN', {
        day: 'numeric', month: 'short', year: 'numeric',
      });

      const rect = container.getBoundingClientRect();
      tooltip.style.left = (x / W) * rect.width + 'px';
      tooltip.style.top = Math.max((y / H) * rect.height - 10, 20) + 'px';
      tooltip.classList.add('visible');
    }

    function hide() {
      crosshair.setAttribute('opacity', 0);
      hoverDot.setAttribute('opacity', 0);
      tooltip.classList.remove('visible');
    }

    overlay.addEventListener('pointermove', (e) => showAt(nearestIndex(e.clientX)));
    overlay.addEventListener('pointerdown', (e) => showAt(nearestIndex(e.clientX)));
    overlay.addEventListener('pointerleave', hide);
  }

  /* ============================================================
     HOME RAILS  (only shown while browsing — a search replaces them)
     ============================================================ */
  function railCard(deal, note) {
    const badges = [];
    if (deal.discount_pct >= 10) badges.push(`<span class="badge badge-off">-${deal.discount_pct}%</span>`);
    return `
      <article class="railcard" data-detail="${escapeHtml(deal.id)}" role="button" tabindex="0"
               aria-label="${escapeHtml(deal.title)}">
        <div class="rail-media">${dealMedia(deal, `<div class="badges">${badges.join('')}</div>`)}</div>
        <div class="rail-body">
          <div class="rail-title">${escapeHtml(deal.title)}</div>
          <div class="rail-price">
            <span class="price-now">${money(deal.price)}</span>
            ${deal.mrp ? `<span class="price-was">${money(deal.mrp)}</span>` : ''}
          </div>
          ${note}
        </div>
      </article>`;
  }

  function toggleRails() {
    const show = isBrowseMode();
    ['#trending-wrap', '#lowest-wrap'].forEach((sel) => {
      const el = $(sel);
      // A rail with no data stays hidden regardless — `has-data` is set by its loader.
      el.classList.toggle('hidden', !show || !el.dataset.hasData);
    });
  }

  async function loadRails() {
    loadTrending();
    loadLowest();
  }

  async function loadTrending() {
    const wrap = $('#trending-wrap');
    const row = $('#trending-row');
    row.innerHTML = railSkeletons();
    try {
      const res = await api('/api/deals/trending?limit=12');
      if (!res.results.length) {
        delete wrap.dataset.hasData;
        wrap.classList.add('hidden');
        return;
      }
      row.innerHTML = res.results.map((d) => railCard(d,
        `<span class="rail-note hot">${icon('trend')} ${d.repost_count}× posted</span>`)).join('');
      wrap.dataset.hasData = '1';
      bindDetailTriggers(row);
      toggleRails();
    } catch {
      delete wrap.dataset.hasData;
      wrap.classList.add('hidden');
    }
  }

  async function loadLowest() {
    const wrap = $('#lowest-wrap');
    const row = $('#lowest-row');
    row.innerHTML = railSkeletons();
    try {
      const query = buildQuery({
        q: '', category: '', subcategory: '', store: '', brand: '',
        max_price: null, min_discount: 0, only_lowest: true,
        sort: 'best', limit: 12, offset: 0,
      });
      const res = await api('/api/deals?' + query);
      if (!res.results.length) {
        delete wrap.dataset.hasData;
        wrap.classList.add('hidden');
        return;
      }
      row.innerHTML = res.results.map((d) => railCard(d,
        d.saving ? `<span class="rail-note">${icon('down')} Save ${money(d.saving)}</span>`
                 : `<span class="rail-note">${icon('check')} All-time low</span>`)).join('');
      wrap.dataset.hasData = '1';
      bindDetailTriggers(row);
      toggleRails();
    } catch {
      delete wrap.dataset.hasData;
      wrap.classList.add('hidden');
    }
  }

  $('#btn-see-lows').addEventListener('click', () => {
    state.filters.only_lowest = true;
    $('#f-lowest').checked = true;
    refreshDeals(true);
    $('#grid-head').scrollIntoView({ behavior: reduceMotion() ? 'auto' : 'smooth', block: 'start' });
  });

  /* ============================================================
     FACETS
     ============================================================ */
  async function loadFacets() {
    try {
      const facets = await api('/api/deals/facets');
      state.facets = { stores: facets.stores || [], brands: facets.brands || [] };
      renderFacet('#f-stores', state.facets.stores, 'store');
      renderFacet('#f-brands', state.facets.brands, 'brand');
    } catch { /* non-fatal */ }
  }

  // Facets are collapsed to a handful with an explicit "show all" rather than
  // given their own inner scrollbar: a scroller nested inside the filter sheet
  // is the classic mobile trap where neither list scrolls the way you meant.
  const FACET_PREVIEW = 6;
  const facetExpanded = { store: false, brand: false };

  function renderFacet(selector, items, key) {
    const box = $(selector);
    if (!items || !items.length) {
      box.innerHTML = '<span class="muted small">No data yet</span>';
      return;
    }
    const expanded = facetExpanded[key];
    // A selected facet must stay visible even if it sits past the preview cut.
    const selectedIdx = items.findIndex((i) => i.key === state.filters[key]);
    const cut = expanded ? items.length : Math.max(FACET_PREVIEW, selectedIdx + 1);
    const shown = items.slice(0, cut);

    box.innerHTML = shown.map((item) => `
      <button class="facet ${state.filters[key] === item.key ? 'active' : ''}" data-key="${escapeHtml(item.key)}">
        <span>${escapeHtml(item.key)}</span><span class="facet-count">${item.count}</span>
      </button>`).join('')
      + (items.length > cut || expanded
        ? `<button class="facet-more" data-more="1">${expanded ? 'Show fewer' : `Show all ${items.length}`}</button>`
        : '');

    $$('.facet', box).forEach((btn) => btn.addEventListener('click', () => {
      state.filters[key] = state.filters[key] === btn.dataset.key ? '' : btn.dataset.key;
      renderFacet(selector, items, key);
      refreshDeals(true);
    }));
    $('[data-more]', box)?.addEventListener('click', () => {
      facetExpanded[key] = !expanded;
      renderFacet(selector, items, key);
    });
  }

  /* ============================================================
     STATS / LIVE STATUS
     ============================================================ */
  function animateCount(el, to) {
    if (reduceMotion() || state.countersAnimated || to <= 0) { el.textContent = num(to); return; }
    const start = performance.now(), dur = 650;
    const step = (now) => {
      const p = Math.min(1, (now - start) / dur);
      el.textContent = num(Math.round(to * (1 - Math.pow(1 - p, 3))));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function renderStats(stats, statusOverride) {
    state.lastStats = stats;
    const last = stats.ingest && stats.ingest.last_run;
    const status = statusOverride || (last
      ? `Updated ${timeAgo(last)} · scanning every ${Math.round(stats.poll_interval_seconds / 60)} min`
      : 'Waiting for the first sync…');

    $('#statstrip').innerHTML = `
      <div class="radarcard">
        <div class="radar-top">
          <span class="livedot ${last ? '' : 'idle'}" id="livedot" aria-hidden="true"></span>
          <span class="radar-status" id="radar-status">${escapeHtml(status)}</span>
        </div>
        <div class="radar-stats">
          <div class="stat">
            <div class="stat-value" data-count="${stats.deals_live || 0}">0</div>
            <div class="stat-label">Live deals</div>
          </div>
          <div class="stat">
            <div class="stat-value up" data-count="${stats.deals_today || 0}">0</div>
            <div class="stat-label">Added today</div>
          </div>
          <div class="stat">
            <div class="stat-value" data-count="${stats.channels || 0}">0</div>
            <div class="stat-label">Channels</div>
          </div>
        </div>
      </div>`;

    $$('#statstrip .stat-value').forEach((el) => animateCount(el, Number(el.dataset.count)));
    state.countersAnimated = true;

    $('#footer-status').textContent = last
      ? `Deals are kept ${stats.deal_ttl_hours}h or until the link goes dead.`
      : '';
  }

  async function loadStats() {
    try {
      renderStats(await api('/api/stats'));
    } catch { /* non-fatal */ }
  }

  function setSyncStatus(text, mode) {
    const dot = $('#livedot');
    const label = $('#radar-status');
    if (!dot || !label) return;
    label.textContent = text;
    dot.classList.toggle('syncing', mode === 'syncing');
    dot.classList.toggle('idle', mode === 'idle');
  }

  async function loadAlertCount() {
    try {
      const res = await api('/api/watchlists');
      setAlertCount((res.watchlists || []).filter((w) => w.notify).length);
    } catch { /* non-fatal */ }
  }

  function setAlertCount(count) {
    const n = Math.max(0, count);
    state.alertCount = n;
    [$('#bell-count'), $('#nav-alert-count')].forEach((el) => {
      if (!el) return;
      el.textContent = n > 99 ? '99+' : n;
      el.classList.toggle('hidden', !n);
    });
  }

  /* ============================================================
     SEARCH
     ============================================================ */
  const RECENTS_KEY = 'dr-recent';

  function recents() {
    try { return JSON.parse(localStorage.getItem(RECENTS_KEY) || '[]').slice(0, 6); }
    catch { return []; }
  }
  function pushRecent(q) {
    if (!q) return;
    try {
      const list = [q, ...recents().filter((r) => r.toLowerCase() !== q.toLowerCase())].slice(0, 6);
      localStorage.setItem(RECENTS_KEY, JSON.stringify(list));
    } catch { /* private mode */ }
  }

  function renderSuggest() {
    const panel = $('#suggest');
    const typed = $('#search-input').value.trim().toLowerCase();
    const recent = recents().filter((r) => !typed || r.toLowerCase().includes(typed));
    // "Popular" is not invented: these are the brands the catalog actually has
    // the most live deals for, straight from /api/deals/facets.
    const brands = (state.facets.brands || [])
      .filter((b) => !typed || b.key.toLowerCase().includes(typed))
      .slice(0, 8);

    if (!recent.length && !brands.length) { closeSuggest(); return; }

    panel.innerHTML = `
      ${recent.length ? `
        <div class="suggest-head">Recent searches <button type="button" id="clear-recents">Clear</button></div>
        ${recent.map((r) => `<button type="button" class="suggest-item" data-q="${escapeHtml(r)}">
            ${icon('clock')}<span>${escapeHtml(r)}</span></button>`).join('')}` : ''}
      ${brands.length ? `
        <div class="suggest-head">Most deals right now</div>
        ${brands.map((b) => `<button type="button" class="suggest-item" data-q="${escapeHtml(b.key)}">
            ${icon('search')}<span>${escapeHtml(b.key)}</span><span class="s-count">${b.count}</span></button>`).join('')}` : ''}`;

    panel.classList.remove('hidden');
    $$('.suggest-item', panel).forEach((btn) => btn.addEventListener('mousedown', (e) => {
      e.preventDefault();          // beat the blur, so the click always lands
      runSearch(btn.dataset.q);
    }));
    $('#clear-recents')?.addEventListener('mousedown', (e) => {
      e.preventDefault();
      try { localStorage.removeItem(RECENTS_KEY); } catch { /* private mode */ }
      renderSuggest();
    });
  }

  const closeSuggest = () => $('#suggest').classList.add('hidden');

  function runSearch(q) {
    $('#search-input').value = q;
    state.filters.q = q;
    pushRecent(q);
    $('#search-clear').classList.toggle('hidden', !q);
    closeSuggest();
    $('#search-input').blur();
    refreshDeals(true);
  }

  $('#search-form').addEventListener('submit', (e) => {
    e.preventDefault();
    runSearch($('#search-input').value.trim());
  });

  $('#search-input').addEventListener('focus', renderSuggest);
  $('#search-input').addEventListener('blur', () => setTimeout(closeSuggest, 120));

  let searchTimer;
  $('#search-input').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    const value = e.target.value.trim();
    $('#search-clear').classList.toggle('hidden', !value);
    renderSuggest();
    searchTimer = setTimeout(() => {
      if (value === state.filters.q) return;
      state.filters.q = value;
      refreshDeals(true);
    }, 420);
  });

  $('#search-clear').addEventListener('click', () => {
    $('#search-input').value = '';
    state.filters.q = '';
    $('#search-clear').classList.add('hidden');
    closeSuggest();
    refreshDeals(true);
    loadRails();
  });

  /* ---------------- filter controls ---------------- */
  $('#f-sort').addEventListener('change', (e) => {
    state.filters.sort = e.target.value;
    refreshDeals(true);
  });
  $('#f-subcategory').addEventListener('change', (e) => {
    state.filters.subcategory = e.target.value;
    refreshDeals(true);
  });
  $('#f-max-price').addEventListener('change', (e) => {
    state.filters.max_price = e.target.value ? Number(e.target.value) : null;
    refreshDeals(true);
  });
  $('#f-discount').addEventListener('input', (e) => {
    $('#f-discount-out').textContent = e.target.value > 0 ? e.target.value + '%+' : 'any';
  });
  $('#f-discount').addEventListener('change', (e) => {
    state.filters.min_discount = Number(e.target.value);
    refreshDeals(true);
  });
  $('#f-lowest').addEventListener('change', (e) => {
    state.filters.only_lowest = e.target.checked;
    refreshDeals(true);
  });
  $('#f-all-channels').addEventListener('change', (e) => {
    state.filters.all_channels = e.target.checked;
    refreshDeals(true);
    loadFacets();
  });
  $('#btn-clear-filters').addEventListener('click', () => {
    Object.assign(state.filters, {
      category: '', subcategory: '', store: '', brand: '',
      max_price: null, min_discount: 0, only_lowest: false, all_channels: false,
    });
    $('#f-max-price').value = '';
    $('#f-discount').value = 0;
    $('#f-discount-out').textContent = 'any';
    $('#f-lowest').checked = false;
    $('#f-all-channels').checked = false;
    renderCategoryChips();
    renderSubcategoryFilter();
    loadFacets();
    refreshDeals(true);
  });

  $('#btn-more').addEventListener('click', () => {
    state.offset += state.limit;
    refreshDeals(false);
  });

  /* ============================================================
     SYNC
     ============================================================ */
  async function syncNow() {
    const btn = $('#btn-sync');
    btn.classList.add('spinning');
    busy(btn, true);
    setSyncStatus('Scanning your Telegram channels…', 'syncing');
    try {
      const res = await post('/api/channels/sync');
      const added = (res.new || 0) + (res.merged || 0);
      toast(added
        ? `Synced: ${res.new} new deals, ${res.merged} matched to existing ones.`
        : 'Sync complete — no new deals in your channels yet.', 'ok');
      refreshDeals(true);
      loadFacets();
      loadRails();
      // Status line last: loadStats() repaints the whole card, so setting the
      // sync result before it would be immediately overwritten.
      await loadStats();
      setSyncStatus(added
        ? `Updated just now · ${res.new} new, ${res.merged} matched`
        : 'Updated just now · no new deals', null);
    } catch (err) {
      toast(err.message, 'err');
      setSyncStatus('Sync failed — pull down or tap sync to retry', 'idle');
    } finally {
      busy(btn, false);
      btn.classList.remove('spinning');
    }
  }
  $('#btn-sync').addEventListener('click', syncNow);

  /* ============================================================
     CHANNELS
     ============================================================ */
  const AVATAR_HUES = [212, 258, 168, 24, 340, 190, 45, 285];

  function channelAvatar(channel) {
    const title = channel.title || '?';
    let hash = 0;
    for (let i = 0; i < title.length; i++) hash = (hash * 31 + title.charCodeAt(i)) >>> 0;
    const hue = AVATAR_HUES[hash % AVATAR_HUES.length];
    const initial = title.trim().slice(0, 1).toUpperCase() || '#';
    return `<div class="channel-avatar" aria-hidden="true"
                 style="background:linear-gradient(140deg, hsl(${hue} 72% 52%), hsl(${(hue + 28) % 360} 72% 44%))">
              ${escapeHtml(initial)}</div>`;
  }

  async function loadAvailableChannels() {
    const list = $('#channel-list');
    list.innerHTML = Array.from({ length: 5 }, () => '<div class="skel skel-row"></div>').join('');
    try {
      const res = await api('/api/channels/available');
      state.availableChannels = res.channels;
      state.selectedChannels = new Set(res.channels.filter((c) => c.tracked).map((c) => c.tg_id));
      renderChannels();
    } catch (err) {
      list.innerHTML = `
        <div class="empty">
          <span class="emoji">⚠️</span>
          <h3>Couldn’t read your channels</h3>
          <p>${escapeHtml(err.message)}</p>
          <div class="empty-actions"><button class="btn btn-primary" id="ch-retry">Try again</button></div>
        </div>`;
      $('#ch-retry')?.addEventListener('click', loadAvailableChannels);
    }
  }

  function channelCountLabel() {
    return `${state.selectedChannels.size} selected · ${state.availableChannels.length} channels found`;
  }

  function renderChannels() {
    const filter = $('#channel-filter').value.trim().toLowerCase();
    const items = state.availableChannels.filter(
      (c) => !filter || c.title.toLowerCase().includes(filter) || (c.username || '').toLowerCase().includes(filter)
    );
    $('#channel-count').textContent = channelCountLabel();

    if (!items.length) {
      $('#channel-list').innerHTML = `
        <div class="empty">
          <span class="emoji">📡</span>
          <h3>${state.availableChannels.length ? 'No matches' : 'No channels found'}</h3>
          <p>${state.availableChannels.length
            ? 'No channels match that filter.'
            : 'No broadcast channels found on your account. Join some deal channels in Telegram, then hit Refresh.'}</p>
        </div>`;
      return;
    }

    $('#channel-list').innerHTML = items.map((c) => {
      const on = state.selectedChannels.has(c.tg_id);
      const deals = state.channelDeals[c.tg_id];
      return `
      <label class="channel ${on ? 'on' : ''}" data-id="${c.tg_id}">
        ${channelAvatar(c)}
        <div class="channel-info">
          <div class="channel-name">${escapeHtml(c.title)}</div>
          <div class="channel-sub">
            <span>${c.username ? '@' + escapeHtml(c.username) : 'private channel'}</span>
            ${c.participants ? `<span class="dot"></span><span>${num(c.participants)} members</span>` : ''}
            ${deals ? `<span class="dot"></span><span class="channel-deals">${num(deals)} deals</span>` : ''}
          </div>
        </div>
        <span class="switch">
          <input type="checkbox" ${on ? 'checked' : ''} aria-label="Track ${escapeHtml(c.title)}" />
          <span class="track"></span>
        </span>
      </label>`;
    }).join('');

    $$('.channel').forEach((el) => {
      el.querySelector('input').addEventListener('change', (e) => {
        const id = Number(el.dataset.id);
        if (e.target.checked) state.selectedChannels.add(id); else state.selectedChannels.delete(id);
        el.classList.toggle('on', e.target.checked);
        $('#channel-count').textContent = channelCountLabel();
      });
    });
  }

  $('#channel-filter').addEventListener('input', renderChannels);
  $('#btn-refresh-channels').addEventListener('click', async (e) => {
    busy(e.currentTarget, true);
    await loadAvailableChannels();
    busy(e.currentTarget, false);
  });

  $('#btn-save-channels').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    busy(btn, true);
    try {
      await post('/api/channels/track', { tg_ids: Array.from(state.selectedChannels) });
      toast(`Tracking ${state.selectedChannels.size} channels. Fetching deals…`, 'ok');
      navigate('deals');
      await syncNow();
    } catch (err) {
      toast(err.message, 'err');
    } finally { busy(btn, false); }
  });

  $('#btn-add-public').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    const username = $('#input-public').value.trim();
    if (!username) return;
    busy(btn, true);
    try {
      const res = await post('/api/channels/add-public', { username });
      toast(`Added ${res.channel.title}.`, 'ok');
      $('#input-public').value = '';
      await loadAvailableChannels();
    } catch (err) {
      toast(err.message, 'err');
    } finally { busy(btn, false); }
  });

  /* ============================================================
     ALERTS
     ============================================================ */
  async function loadAlerts() {
    const list = $('#alert-list');
    list.innerHTML = Array.from({ length: 2 }, () => '<div class="skel skel-row"></div>').join('');
    try {
      const res = await api('/api/watchlists');
      setAlertCount((res.watchlists || []).filter((w) => w.notify).length);
      if (!res.watchlists.length) {
        list.innerHTML = `
          <div class="empty">
            <span class="emoji">🔔</span>
            <h3>No alerts yet</h3>
            <p>Create an alert above and DealRadar will message you in Telegram the moment a matching deal appears.</p>
          </div>`;
        return;
      }
      list.innerHTML = res.watchlists.map((w) => {
        const f = w.filters || {};
        const bits = [
          f.category, f.store,
          f.max_price ? 'under ' + money(f.max_price) : '',
          f.min_discount ? f.min_discount + '%+ off' : '',
        ].filter(Boolean);
        return `
          <div class="alert-row" data-id="${w.id}">
            <div class="alert-main">
              <div class="alert-q">${escapeHtml(w.query)}</div>
              <div class="alert-filters">${bits.length ? escapeHtml(bits.join(' · ')) : 'no extra filters'} · ${w.alerts_sent} sent</div>
              <div class="alert-status ${w.notify ? '' : 'off'}">
                <span class="sdot"></span>${w.notify ? 'Active' : 'Paused'}
              </div>
            </div>
            <div class="alert-actions">
              <span class="switch">
                <input type="checkbox" ${w.notify ? 'checked' : ''} data-toggle="${w.id}"
                       aria-label="Notify for ${escapeHtml(w.query)}" />
                <span class="track"></span>
              </span>
              <button class="btn btn-soft btn-xs" data-test="${w.id}">Test</button>
              <button class="btn btn-ghost btn-xs" data-del="${w.id}" aria-label="Delete alert">Delete</button>
            </div>
          </div>`;
      }).join('');

      $$('[data-del]', list).forEach((btn) => btn.addEventListener('click', async () => {
        await del(`/api/watchlists/${btn.dataset.del}`).catch((err) => toast(err.message, 'err'));
        loadAlerts();
      }));
      $$('[data-test]', list).forEach((btn) => btn.addEventListener('click', async () => {
        busy(btn, true);
        try {
          await post(`/api/watchlists/${btn.dataset.test}/test`);
          toast('Test alert sent — check Saved Messages in Telegram.', 'ok');
        } catch (err) { toast(err.message, 'err'); } finally { busy(btn, false); }
      }));
      $$('[data-toggle]', list).forEach((box) => box.addEventListener('change', async () => {
        const row = box.closest('.alert-row');
        const status = row?.querySelector('.alert-status');
        if (status) {
          status.classList.toggle('off', !box.checked);
          status.innerHTML = `<span class="sdot"></span>${box.checked ? 'Active' : 'Paused'}`;
        }
        setAlertCount(state.alertCount + (box.checked ? 1 : -1));
        await api(`/api/watchlists/${box.dataset.toggle}?notify=${box.checked}`, { method: 'PATCH' })
          .catch((err) => toast(err.message, 'err'));
      }));
    } catch (err) {
      list.innerHTML = `<p class="alert alert-error">${escapeHtml(err.message)}</p>`;
    }
  }

  $('#alert-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector('button[type=submit]');
    busy(btn, true);
    try {
      await post('/api/watchlists', {
        query: $('#a-query').value.trim(),
        category: $('#a-category').value,
        max_price: $('#a-max-price').value ? Number($('#a-max-price').value) : null,
        min_discount: Number($('#a-discount').value || 0),
        notify: true,
      });
      e.target.reset();
      toast('Alert created. You’ll get matches in Telegram Saved Messages.', 'ok');
      loadAlerts();
    } catch (err) {
      toast(err.message, 'err');
    } finally { busy(btn, false); }
  });

  $('#btn-save-search').addEventListener('click', async () => {
    const q = state.filters.q;
    if (!q) { toast('Search for something first, then save it as an alert.', 'info'); return; }
    try {
      await post('/api/watchlists', {
        query: q,
        category: state.filters.category,
        store: state.filters.store,
        max_price: state.filters.max_price,
        min_discount: state.filters.min_discount,
        notify: true,
      });
      toast(`Alert saved for “${q}”.`, 'ok');
      setAlertCount(state.alertCount + 1);
    } catch (err) { toast(err.message, 'err'); }
  });

  /* ============================================================
     BOOT
     ============================================================ */
  $('#modal').addEventListener('click', (e) => {
    if (e.target.dataset.close !== undefined || e.target.closest('[data-close]')) closeModal();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeModal(); closeFilters(); closeUserMenu(); closeSuggest(); }
    if (e.key === '/' && document.activeElement.tagName !== 'INPUT' && state.user) {
      e.preventDefault();
      $('#search-input').focus();
    }
  });

  /* App bar elevates once the page moves; the toolbar gets its divider then too. */
  let ticking = false;
  window.addEventListener('scroll', () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      const y = window.scrollY;
      $('#appbar').classList.toggle('scrolled', y > 4);
      $('#toolbar')?.classList.toggle('stuck', y > 90);
      ticking = false;
    });
  }, { passive: true });

  window.addEventListener('resize', measureChrome);
  window.addEventListener('orientationchange', () => setTimeout(measureChrome, 120));

  window.addEventListener('online', () => {
    if (state.user && state.page === 'deals') { refreshDeals(true); loadStats(); }
  });
  window.addEventListener('offline', () => toast('You’re offline — showing the last loaded deals.', 'info'));

  /* ---------------- PWA: install + offline shell ---------------- */
  let deferredInstallPrompt = null;

  function isStandalone() {
    return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  }
  const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent);

  function initInstall() {
    if (isStandalone()) return; // already installed — nothing to offer
    if (isIOS()) {
      // iOS never fires beforeinstallprompt; "Add to Home Screen" is manual-only.
      $('#install-item').classList.remove('hidden');
      return;
    }
    window.addEventListener('beforeinstallprompt', (e) => {
      e.preventDefault();
      deferredInstallPrompt = e;
      $('#install-item').classList.remove('hidden');
    });
    window.addEventListener('appinstalled', () => {
      $('#install-item').classList.add('hidden');
      deferredInstallPrompt = null;
    });
  }

  async function promptInstall() {
    if (isIOS()) {
      openModal(sheetShell('Install DealRadar', `
        <p class="muted" style="margin-bottom:12px">iOS doesn't let apps trigger this automatically — three taps:</p>
        <ol style="padding-left:20px; display:grid; gap:9px; font-size:.9rem; color:var(--text-2)">
          <li>Tap the <b>Share</b> button in Safari's toolbar</li>
          <li>Scroll down and tap <b>Add to Home Screen</b></li>
          <li>Tap <b>Add</b> — DealRadar opens full-screen from your home screen next time</li>
        </ol>`));
      return;
    }
    if (!deferredInstallPrompt) {
      toast('Your browser doesn’t support an install prompt here — look for an install icon in the address bar.', 'info');
      return;
    }
    deferredInstallPrompt.prompt();
    await deferredInstallPrompt.userChoice;
    deferredInstallPrompt = null;
    $('#install-item').classList.add('hidden');
  }

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').catch(() => { /* offline shell is a nicety, not required */ });
    });
  }

  (async function boot() {
    // The inline <head> script already applied the saved theme; this keeps the
    // theme-color meta in step with it.
    applyTheme(document.documentElement.dataset.theme || 'dark');
    initInstall();

    try {
      const me = await api('/api/auth/me');
      if (me.authenticated) {
        await onSignedIn(me.user);
        return;
      }
    } catch { /* fall through to the login screen */ }

    $('#boot')?.remove();
    $('#view-login').classList.remove('hidden');
    initAuthScreen();
  })();

  // Keep stats fresh while the tab is open.
  setInterval(() => { if (state.user && state.page === 'deals') loadStats(); }, 60000);
})();
