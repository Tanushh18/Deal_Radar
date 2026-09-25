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
      q: '', category: '', subcategory: '', store: '', brand: '', size: '',
      min_price: null, max_price: null, min_discount: 0, has_coupon: false, only_lowest: false,
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
  // Account-only endpoints; visitors (no sign-in) never call them.
  const ACCOUNT_API = /^\/api\/(channels|watchlists|notifications|push)(\/|\?|$)/;

  async function api(path, options = {}) {
    if (state.user?.guest && ACCOUNT_API.test(path)) {
      const error = new Error('Not available without an account.');
      error.status = 401;
      error.guest = true;
      throw error;
    }
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
  let lastOverlayOpen = null;
  function syncOverlayState() {
    const open = !$('#modal').classList.contains('hidden')
      || $('#filters').classList.contains('open')
      || !$('#user-drop').classList.contains('hidden');
    document.body.style.overflow = open ? 'hidden' : '';
    try { window.DealRadarNative?.setPullToRefresh(!open); } catch { /* browser, not the shell */ }
    if (open !== lastOverlayOpen) { lastOverlayOpen = open; postToApp({ type: 'overlay', open }); }
    return open;
  }

  /* ---------------- native app bridge (Expo WebView) ---------------- */
  function postToApp(message) {
    try { window.ReactNativeWebView?.postMessage(JSON.stringify(message)); } catch { /* plain browser */ }
  }
  const inNativeApp = () => !!window.ReactNativeWebView;

  let modalReturnFocus = null;

  function openModal(html, { wide = false } = {}) {
    const body = $('#modal-body');
    const wasOpen = !$('#modal').classList.contains('hidden');
    if (!wasOpen) modalReturnFocus = document.activeElement;
    body.innerHTML = html;
    body.classList.toggle('wide', wide);
    const heading = body.querySelector('h2');
    if (heading) { heading.id = 'modal-title'; body.setAttribute('aria-labelledby', 'modal-title'); }
    $('#modal').classList.remove('hidden');
    syncOverlayState();
    // Re-renders (loading → loaded) keep scroll and focus where they are.
    if (!wasOpen) { body.scrollTop = 0; body.focus({ preventScroll: true }); }
  }
  function closeModal() {
    if ($('#modal').classList.contains('hidden')) return;
    $('#modal').classList.add('hidden');
    syncOverlayState();
    if (modalReturnFocus && document.contains(modalReturnFocus)) modalReturnFocus.focus({ preventScroll: true });
    modalReturnFocus = null;
  }

  /* Keeps Tab inside the open dialog. */
  $('#modal').addEventListener('keydown', (e) => {
    if (e.key !== 'Tab') return;
    const focusable = $$('a[href], button:not([disabled]), input, select, summary, [tabindex]:not([tabindex="-1"])', $('#modal-body'))
      .filter((el) => el.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === $('#modal-body'))) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus();
    }
  });

  /** Small confirm sheet; resolves true only on the confirm button. */
  function confirmDialog(title, message, confirmLabel = 'Delete') {
    return new Promise((resolve) => {
      openModal(`
        <div class="modal-head"><h2>${escapeHtml(title)}</h2></div>
        <div class="modal-pad" style="padding-top:14px">
          <p class="muted">${escapeHtml(message)}</p>
          <div class="empty-actions" style="justify-content:flex-end;margin:20px 0 4px">
            <button class="btn btn-soft" data-confirm="0">Cancel</button>
            <button class="btn btn-danger" data-confirm="1">${escapeHtml(confirmLabel)}</button>
          </div>
        </div>`);
      const done = (ok) => { observer.disconnect(); closeModal(); resolve(ok); };
      $$('[data-confirm]', $('#modal-body')).forEach((b) => b.addEventListener('click', () => done(b.dataset.confirm === '1')));
      $('[data-confirm="0"]', $('#modal-body')).focus();
      // Backdrop / Escape close the modal without a choice → treat as cancel.
      const observer = new MutationObserver(() => {
        if ($('#modal').classList.contains('hidden')) { observer.disconnect(); resolve(false); }
      });
      observer.observe($('#modal'), { attributes: true, attributeFilter: ['class'] });
    });
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
    const initials = user.guest ? 'V' : (name || 'U').slice(0, 1).toUpperCase();
    $('#user-btn').textContent = initials;
    $('#drop-avatar').textContent = initials;
    $('#drop-name').textContent = user.guest ? 'Visitor' : (user.first_name || 'Telegram user');
    $('#drop-handle').textContent = user.username ? '@' + user.username : '';
    $('#greeting').textContent = name ? `${greeting()}, ${name} 👋` : `${greeting()} 👋`;

    measureChrome();
    postToApp({ type: 'signed-in', user: { first_name: user.first_name || '' } });
    registerPushToken();

    const link = takeDeepLinks();
    if (link.q) {
      $('#search-input').value = link.q;
      $('#search-clear').classList.remove('hidden');
      state.filters.q = link.q;
    }

    await loadCategories();
    const mine = await api('/api/channels').catch(() => ({ channels: [] }));
    state.channelDeals = {};
    (mine.channels || []).forEach((c) => { state.channelDeals[c.tg_id] = c.live_deals; });
    const active = (mine.channels || []).filter((c) => c.enabled);
    if (!active.length && !user.guest) {
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
    loadSpotlight();
    startDropCountdown();
    loadAlertCount();
    checkUnseenNotifications();
    if (link.deal) showDealDetail(link.deal);
  }

  /* ---------------- deep links: /?deal=<id>, /?q=<text> ---------------- */
  const pendingLinks = (() => {
    try {
      const params = new URLSearchParams(window.location.search);
      return { deal: params.get('deal') || '', q: (params.get('q') || '').trim() };
    } catch { return { deal: '', q: '' }; }
  })();

  function takeDeepLinks() {
    const link = { ...pendingLinks };
    pendingLinks.deal = ''; pendingLinks.q = '';
    try {
      const url = new URL(window.location.href);
      if (url.searchParams.has('deal') || url.searchParams.has('q')) {
        url.searchParams.delete('deal');
        url.searchParams.delete('q');
        history.replaceState(history.state, '', url.pathname + url.search + url.hash);
      }
    } catch { /* ancient browser: a stale query string is harmless */ }
    return link;
  }

  /* ---------------- push tokens handed over by the native app ---------------- */
  const PUSH_KEY = 'dr-push-token';
  let pushRegisteredKey = null;

  function storedPushToken() {
    try { return JSON.parse(localStorage.getItem(PUSH_KEY) || 'null'); } catch { return null; }
  }

  async function registerPushToken() {
    const saved = storedPushToken();
    if (!saved || !saved.token || !state.user) return;
    const key = `${saved.token}|${saved.platform}`;
    if (pushRegisteredKey === key) return;
    pushRegisteredKey = key;
    try {
      await post('/api/push/register', { token: saved.token, platform: saved.platform || 'unknown' });
    } catch {
      pushRegisteredKey = null;   // retried on the next sign-in or token hand-off
    }
  }

  async function unregisterPushToken() {
    const saved = storedPushToken();
    if (!saved || !saved.token) return;
    await post('/api/push/unregister', { token: saved.token }).catch(() => {});
    pushRegisteredKey = null;
  }

  window.DealRadarWeb = {
    onPushToken(token, platform) {
      if (!token) return;
      try { localStorage.setItem(PUSH_KEY, JSON.stringify({ token: String(token), platform: String(platform || 'unknown') })); }
      catch { /* private mode — still register for this session */ }
      if (!storedPushToken()) {
        if (state.user) post('/api/push/register', { token: String(token), platform: String(platform || 'unknown') }).catch(() => {});
        return;
      }
      registerPushToken();
    },
    openDeal(id) {
      if (!id) return;
      if (!state.user) { pendingLinks.deal = String(id); return; }
      showDealDetail(String(id));
    },
    refresh() {
      if (!state.user) return;
      if (state.page === 'deals') { refreshDeals(true); loadRails(); loadStats(); }
      else if (state.page === 'alerts') { loadAlerts(); loadNotifications(); }
      else if (state.page === 'channels') loadAvailableChannels();
      checkUnseenNotifications();
    },
  };
  postToApp({ type: 'web-ready' });

  /* ============================================================
     NAVIGATION
     ============================================================ */
  function navigate(page) {
    state.page = page;
    ['deals', 'channels', 'alerts', 'saved'].forEach((p) => {
      $(`#page-${p}`).classList.toggle('hidden', p !== page);
    });
    $$('.navlink, .navbtn').forEach((n) => n.classList.toggle('active', n.dataset.nav === page));
    window.scrollTo({ top: 0, behavior: reduceMotion() ? 'auto' : 'smooth' });
    closeFilters();
    closeUserMenu();
    closeSuggest();
    if (page === 'alerts') { loadAlerts(); loadNotifications(); }
    if (page === 'saved') loadSaved();
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
    const label = $('#theme-label');
    if (label) label.textContent = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
    $('#theme-icon')?.setAttribute('href', theme === 'dark' ? '#i-sun' : '#i-moon');
  }

  const savedTheme = () => { try { return localStorage.getItem('dr-theme'); } catch { return null; } };
  // Without an explicit choice the app tracks the OS, including live changes.
  window.matchMedia('(prefers-color-scheme: light)').addEventListener?.('change', (e) => {
    if (!savedTheme()) applyTheme(e.matches ? 'light' : 'dark');
  });

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
      postToApp({ type: 'signed-out' });
      // Must precede the logout call: unregistering needs the session.
      await unregisterPushToken();
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
      f.category, f.subcategory, f.store, f.brand, f.size,
      f.min_price ? 1 : 0, f.max_price ? 1 : 0, f.min_discount ? 1 : 0, f.has_coupon ? 1 : 0,
      f.only_lowest ? 1 : 0, f.all_channels ? 1 : 0,
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
    if (f.size)        chips.push(['size', `Size ${f.size}`]);
    if (f.min_price)   chips.push(['min_price', `Over ${money(f.min_price)}`]);
    if (f.max_price)   chips.push(['max_price', `Under ${money(f.max_price)}`]);
    if (f.has_coupon)  chips.push(['has_coupon', 'Has coupon']);
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
    else if (key === 'max_price') { f.max_price = null; $('#f-max-price').value = ''; syncFilterChips(); }
    else if (key === 'min_price') { f.min_price = null; $('#f-min-price').value = ''; }
    else if (key === 'has_coupon') { f.has_coupon = false; $('#f-coupon').checked = false; }
    else if (key === 'min_discount') { f.min_discount = 0; $('#f-discount').value = 0; $('#f-discount-out').textContent = 'any'; }
    else if (key === 'only_lowest') { f.only_lowest = false; $('#f-lowest').checked = false; }
    else if (key === 'all_channels') { f.all_channels = false; $('#f-all-channels').checked = false; loadFacets(); }
    else if (key === 'subcategory') { f.subcategory = ''; $('#f-subcategory').value = ''; }
    else if (key === 'size') { f.size = ''; $('#f-size').value = ''; }
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
    if (f.size) params.set('size', f.size);
    if (f.min_price) params.set('min_price', f.min_price);
    if (f.max_price) params.set('max_price', f.max_price);
    if (f.has_coupon) params.set('has_coupon', 'true');
    if (f.min_discount) params.set('min_discount', f.min_discount);
    if (f.only_lowest) params.set('only_lowest', 'true');
    if (f.all_channels) params.set('all_channels', 'true');
    // Sent as-is: the backend already degrades "relevance" to score-order
    // by itself when there's no query to rank against (see search.py).
    params.set('sort', f.sort);
    if (f.sort === 'for_you') params.set('device_id', deviceId());
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
    syncSortTabs();
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
      if (isBrowseMode()) $('#result-cats').classList.add('hidden');
      toggleRails();
    }

    try {
      const res = await api('/api/deals?' + buildQuery(), signal ? { signal } : {});
      state.total = res.total;
      renderDeals(res.results, reset);
      if (reset) {
        renderResultCats(res.categories);
        if (isBrowseMode()) maybeLoadCategoryRails(res);
      }

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
      $('#btn-filters-apply').textContent = res.total
        ? `Show ${res.total.toLocaleString()} deal${res.total === 1 ? '' : 's'}`
        : 'Show deals';

      $('#btn-more').classList.toggle('hidden', state.offset + res.count >= res.total);
      if (reset) loadArchive(res.total);
      if (!res.total && !state.filters.q) showEmpty();
    } catch (err) {
      if (err.name === 'AbortError') return; // superseded by a newer search — ignore
      if (err.status === 401) { window.location.reload(); return; }
      if (!reset) {
        // A failed page must not wipe the deals already on screen, and the
        // next attempt has to ask for the same page again.
        state.offset = Math.max(0, state.offset - state.limit);
        toast(navigator.onLine ? 'Couldn’t load more deals — try again.' : 'You’re offline.', 'err');
        return;
      }
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
      sub.textContent = 'Best matches';
    } else if (activeFilterCount()) {
      title.textContent = '🏷️ Filtered deals';
      sub.textContent = 'Matching your filters';
    } else {
      const labels = {
        newest: ['🕘 Latest deals', 'Freshly posted deals'],
        best: ['🏆 Top deals', 'Ranked by DealRadar’s deal score'],
        discount: ['⚡ Biggest discounts', 'Largest drop from the quoted MRP'],
        price_low: ['💸 Cheapest first', 'Lowest prices first'],
        price_high: ['💎 Priciest first', 'Highest prices first'],
        relevance: ['🏆 Top deals', 'Ranked by DealRadar’s deal score'],
        for_you: ['✨ For You', 'Matched to what you follow'],
      };
      const [t, s] = labels[state.filters.sort] || labels.newest;
      title.textContent = t;
      sub.textContent = s;
    }
    head.classList.remove('hidden');
  }

  function applyCategory(name) {
    state.filters.category = name;
    state.filters.subcategory = '';
    renderCategoryChips();
    renderSubcategoryFilter();
    refreshDeals(true);
  }

  /* Categories of the current result set, counted by the backend with the
     category filter ignored — so "All" and every sibling stay reachable. */
  function renderResultCats(categories) {
    const box = $('#result-cats');
    const active = state.filters.category;
    const list = (Array.isArray(categories) ? categories : []).filter((c) => c && c.name && c.count > 0);
    if (active && !list.some((c) => c.name === active)) list.push({ name: active, count: 0 });
    if (isBrowseMode() || !list.length || (list.length < 2 && !active)) {
      box.classList.add('hidden');
      box.innerHTML = '';
      return;
    }
    const total = list.reduce((sum, c) => sum + c.count, 0);
    const chips = [{ name: '', label: 'All', count: total }]
      .concat(list.map((c) => ({ name: c.name, label: c.name, count: c.count })));
    const scroll = box.scrollLeft;
    box.innerHTML = chips.map((c) => `
      <button class="rcat ${active === c.name ? 'active' : ''}" role="tab"
              aria-selected="${active === c.name}" data-rcat="${escapeHtml(c.name)}">
        ${c.name ? `<span class="rcat-icon" aria-hidden="true">${CATEGORY_ICON[c.name] || '🏷️'}</span>` : ''}
        <span class="rcat-label">${escapeHtml(c.label)}</span>
        <span class="rcat-count">${num(c.count)}</span>
      </button>`).join('');
    box.classList.remove('hidden');
    box.scrollLeft = scroll;
  }

  $('#result-cats').addEventListener('click', (e) => {
    const chip = e.target.closest('[data-rcat]');
    if (!chip || chip.dataset.rcat === state.filters.category) return;
    applyCategory(chip.dataset.rcat);
  });

  function showEmpty() {
    const el = $('#empty-state');
    const hasFilters = !!(state.filters.q || activeFilterCount());

    el.innerHTML = `
      <span class="emoji">${hasFilters ? '🔎' : '📭'}</span>
      <h3>No products found</h3>
      <p>${hasFilters
        ? 'Nothing matches your search and filters right now. Try fewer filters or a broader term.'
        : 'New deals arrive every few minutes — check back soon.'}</p>
      <div class="empty-actions">
        ${hasFilters ? '<button class="btn btn-primary" id="empty-clear">Clear search &amp; filters</button>' : ''}

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

  /* ---------------- archive: past deals from the Google Sheet ---------------- */
  let archiveAbort = null;
  async function loadArchive(liveTotal) {
    const wrap = $('#archive-wrap');
    const q = state.filters.q;
    archiveAbort?.abort();
    if (!q) { wrap.classList.add('hidden'); return; }
    const controller = new AbortController();
    archiveAbort = controller;
    try {
      const res = await api('/api/deals?' + buildQuery({ q, limit: 24, offset: 0, sort: 'relevance' }) + '&archive=true', { signal: controller.signal });
      if (controller.signal.aborted) return;
      if (!res.total) {
        wrap.classList.add('hidden');
        if (!liveTotal) showEmpty();
        return;
      }
      $('#archive-sub').textContent = liveTotal
        ? `${res.total.toLocaleString()} earlier deal${res.total === 1 ? '' : 's'} for “${q}” — prices may have changed`
        : `No live deal for “${q}” right now — ${res.total.toLocaleString()} earlier deal${res.total === 1 ? '' : 's'} from our archive`;
      $('#archive-grid').innerHTML = res.results.map(dealCard).join('');
      bindDetailTriggers($('#archive-grid'));
      wrap.classList.remove('hidden');
      if (!liveTotal) $('#empty-state').classList.add('hidden');
    } catch (err) {
      if (err.name !== 'AbortError') wrap.classList.add('hidden');
    }
  }

  /* ---------------- live: "N new deals" pill ---------------- */
  let seenLive = null;
  function noteLiveCount(stats) {
    const live = stats.deals_live || 0;
    if (seenLive != null && live > seenLive && state.page === 'deals') {
      $('#new-deals-text').textContent = `${live - seenLive} new deal${live - seenLive === 1 ? '' : 's'} — tap to see`;
      $('#new-deals').classList.remove('hidden');
    }
    if (seenLive == null || live < seenLive) seenLive = live;
  }
  $('#new-deals').addEventListener('click', () => {
    $('#new-deals').classList.add('hidden');
    seenLive = state.lastStats?.deals_live ?? seenLive;
    refreshDeals(true);
    loadRails();
    window.scrollTo({ top: 0, behavior: reduceMotion() ? 'auto' : 'smooth' });
  });

  /* ---------------- device (no account) ---------------- */
  const readJSON = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } };
  const writeJSON = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } };
  function deviceId() {
    let id = readJSON('dr-device', null);
    if (!id) {
      id = 'web_' + (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, '') : Math.random().toString(36).slice(2) + Date.now().toString(36));
      writeJSON('dr-device', id);
    }
    return id;
  }

  /* ---------------- ♡ saved deals ---------------- */
  const SAVED_KEY = 'dr-saved';
  const savedMap = () => readJSON(SAVED_KEY, {});
  const isSaved = (id) => Boolean(savedMap()[id]);
  const SNAPSHOT = ['id', 'title', 'price', 'mrp', 'discount_pct', 'saving', 'store', 'image_url', 'url', 'coupon',
    'flags', 'posted_at', 'repost_count', 'status', 'is_lowest', 'score', 'price_history_url'];
  const dealCache = new Map();
  function heartButton(deal) {
    dealCache.set(deal.id, deal);
    const on = isSaved(deal.id);
    return `<button class="heart ${on ? 'on' : ''}" data-save="${escapeHtml(deal.id)}" type="button"
      aria-pressed="${on}" aria-label="${on ? 'Remove from saved' : 'Save deal'}">${icon('heart')}</button>`;
  }
  function updateSavedBadges() {
    const n = Object.keys(savedMap()).length;
    [$('#saved-count'), $('#nav-saved-count')].forEach((el) => {
      if (!el) return;
      el.textContent = n > 99 ? '99+' : n;
      el.classList.toggle('hidden', !n);
    });
  }
  function toggleSaved(id) {
    const map = savedMap();
    if (map[id]) {
      delete map[id];
      toast('Removed from saved.', 'info', 2200);
    } else {
      const d = dealCache.get(id) || { id };
      map[id] = { ...Object.fromEntries(SNAPSHOT.map((k) => [k, d[k]])), savedAt: Date.now() };
      toast('Saved ♡ — find it under Saved.', 'ok', 2200);
    }
    writeJSON(SAVED_KEY, map);
    const on = Boolean(map[id]);
    $$(`[data-save="${CSS.escape(id)}"]`).forEach((b) => {
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', String(on));
      if (b.classList.contains('heart')) b.setAttribute('aria-label', on ? 'Remove from saved' : 'Save deal');
      const label = b.querySelector('span');
      if (label) label.textContent = on ? 'Saved' : 'Save';
    });
    updateSavedBadges();
    if (state.page === 'saved') loadSaved();
  }
  // Capture phase: the heart sits inside the card's clickable image, which
  // would otherwise also open the deal.
  document.addEventListener('click', (e) => {
    const heart = e.target.closest?.('[data-save]');
    if (!heart) return;
    e.preventDefault();
    e.stopPropagation();
    toggleSaved(heart.dataset.save);
  }, true);

  async function loadSaved() {
    const map = savedMap();
    const ids = Object.keys(map).sort((a, b) => (map[b].savedAt || 0) - (map[a].savedAt || 0));
    $('#saved-empty').classList.toggle('hidden', ids.length > 0);
    const render = (deals) => {
      $('#saved-grid').innerHTML = deals.map(dealCard).join('');
      bindDetailTriggers($('#saved-grid'));
    };
    render(ids.map((id) => map[id]));
    renderPriceWatch();
    // Refresh prices; a deal gone from the server keeps its snapshot, marked past.
    const fresh = await Promise.all(ids.slice(0, 60).map((id) =>
      api(`/api/deals/${encodeURIComponent(id)}`)
        .then((d) => { dealCache.set(d.id, d); return d; })
        .catch(() => ({ ...map[id], status: 'expired' }))));
    if (state.page === 'saved') render(fresh.concat(ids.slice(60).map((id) => map[id])));
  }

  /* ---------------- 📤 share ---------------- */
  async function shareDeal(deal) {
    const url = `${location.origin}/d/${encodeURIComponent(deal.id)}`;
    const text = `${deal.title}${deal.price != null ? ` — ${money(deal.price)}` : ''}${deal.discount_pct >= 5 ? ` (${deal.discount_pct}% off)` : ''}`;
    if (navigator.share) {
      try { await navigator.share({ title: deal.title, text, url }); return; } catch (err) { if (err.name === 'AbortError') return; }
    }
    openModal(sheetShell('Share deal', `
      <div class="share-grid">
        <a class="btn btn-soft" target="_blank" rel="noopener" href="https://wa.me/?text=${encodeURIComponent(text + '\n' + url)}">WhatsApp</a>
        <a class="btn btn-soft" target="_blank" rel="noopener" href="https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text)}">Telegram</a>
        <button class="btn btn-soft" type="button" id="copy-share">Copy link</button>
      </div>`));
    $('#copy-share')?.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(url); toast('Link copied.', 'ok'); closeModal(); }
      catch { toast(url, 'info', 8000); }
    });
  }

  /* ---------------- 🔔 price-drop alerts (device) ---------------- */
  state.priceAlerts = [];
  function detailActions(deal) {
    dealCache.set(deal.id, deal);
    const target = deal.price ? Math.max(1, Math.floor((deal.price * 0.9) / 10) * 10) : '';
    const existing = state.priceAlerts.find((a) => a.deal_id === deal.id && !a.triggered_at);
    return `
      <div class="deal-actions-row">
        <button class="btn btn-soft btn-sm ${isSaved(deal.id) ? 'on' : ''}" type="button" data-save="${escapeHtml(deal.id)}">${icon('heart', 'ico')}
          <span>${isSaved(deal.id) ? 'Saved' : 'Save'}</span></button>
        <button class="btn btn-soft btn-sm" type="button" id="btn-share-deal">${icon('share', 'ico')} Share</button>
      </div>
      ${deal.price ? `
      <form class="pricealert" id="price-alert-form">
        <div class="pa-copy">🔔 <b>Price-drop alert</b><span>${existing
          ? `Watching for ₹${Number(existing.target_price).toLocaleString('en-IN')} or less`
          : 'Get notified when it gets cheaper'}</span></div>
        <div class="pa-row">
          <span class="pa-cur">₹</span>
          <input class="input" id="pa-target" type="number" min="1" step="1" value="${existing ? existing.target_price : target}" aria-label="Alert me below this price" />
          <button class="btn btn-primary btn-sm" type="submit">${existing ? 'Update' : 'Notify me'}</button>
        </div>
      </form>` : ''}`;
  }
  function bindDetailActions(deal) {
    $('#btn-share-deal')?.addEventListener('click', () => shareDeal(deal));
    $('#price-alert-form')?.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = e.target.querySelector('button[type=submit]');
      const target = Number($('#pa-target').value);
      if (!target || target <= 0) { toast('Enter a target price.', 'err'); return; }
      busy(btn, true);
      try {
        const res = await post('/api/price-alerts', { device_id: deviceId(), deal_id: deal.id, target_price: target });
        if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission().catch(() => {});
        toast(res.alert.triggered_at
          ? `It's already ₹${Number(res.alert.triggered_price).toLocaleString('en-IN')} — at or below your target!`
          : `We'll tell you when it drops to ₹${target.toLocaleString('en-IN')} or less.`, 'ok', 5000);
        await pollPriceAlerts();
      } catch (err) { toast(err.message, 'err'); } finally { busy(btn, false); }
    });
    $('#btn-coupon-dead')?.addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      busy(btn, true);
      try {
        const res = await api(
          `/api/deals/${encodeURIComponent(deal.id)}/coupon-dead?device_id=${encodeURIComponent(deviceId())}`,
          { method: 'POST' },
        );
        toast(res.suppressed ? 'Thanks — we’ve hidden that code.' : 'Thanks for letting us know.', 'ok');
        btn.classList.remove('loading');
        btn.disabled = true;
        btn.textContent = 'Reported';
      } catch (err) { toast(err.message, 'err'); busy(btn, false); }
    });
  }
  async function pollPriceAlerts() {
    try {
      const res = await api(`/api/price-alerts?device_id=${encodeURIComponent(deviceId())}`);
      state.priceAlerts = res.alerts || [];
    } catch { return; }
    const seen = new Set(readJSON('dr-alerts-seen', []));
    const fresh = state.priceAlerts.filter((a) => a.triggered_at && !seen.has(a.id));
    fresh.forEach((a) => {
      const msg = `📉 ${(a.title || 'A deal you watch').slice(0, 60)} dropped to ₹${Number(a.triggered_price).toLocaleString('en-IN')}`;
      toast(msg, 'ok', 8000);
      if ('Notification' in window && Notification.permission === 'granted' && document.hidden) {
        try { new Notification('DealRadar price drop', { body: msg, icon: '/assets/icons/icon-192.png', tag: `pa-${a.id}` }); } catch { /* unsupported */ }
      }
      seen.add(a.id);
    });
    if (fresh.length) writeJSON('dr-alerts-seen', [...seen]);
    if (state.page === 'saved') renderPriceWatch();
  }
  function renderPriceWatch() {
    const list = state.priceAlerts;
    $('#price-watch').classList.toggle('hidden', !list.length);
    $('#price-watch-list').innerHTML = list.map((a) => `
      <div class="alert-row" data-open="${escapeHtml(a.deal_id)}">
        <div class="alert-main">
          <div class="alert-q">${escapeHtml(a.title || 'Deal')}</div>
          <div class="alert-filters">Target ₹${Number(a.target_price).toLocaleString('en-IN')}${a.current_price != null ? ` · now ₹${Number(a.current_price).toLocaleString('en-IN')}` : ''}</div>
          <div class="alert-status ${a.triggered_at ? '' : 'off'}"><span class="sdot"></span>${a.triggered_at
            ? `Dropped to ₹${Number(a.triggered_price).toLocaleString('en-IN')} ✓` : 'Watching'}</div>
        </div>
        <div class="alert-actions"><button class="btn btn-ghost btn-xs" data-pa-del="${a.id}">Remove</button></div>
      </div>`).join('');
  }
  $('#price-watch-list').addEventListener('click', async (e) => {
    const del = e.target.closest('[data-pa-del]');
    if (del) {
      e.stopPropagation();
      try {
        await api(`/api/price-alerts/${del.dataset.paDel}?device_id=${encodeURIComponent(deviceId())}`, { method: 'DELETE' });
        await pollPriceAlerts();
        renderPriceWatch();
      } catch (err) { toast(err.message, 'err'); }
      return;
    }
    const row = e.target.closest('[data-open]');
    if (row) showDealDetail(row.dataset.open);
  });

  /* ---------------- enhanced filters ---------------- */
  function syncFilterChips() {
    const f = state.filters;
    $$('#f-price-bands [data-band]').forEach((c) => c.classList.toggle('active', !f.min_price && Number(c.dataset.band) === Number(f.max_price)));
    $$('#f-discount-chips [data-off]').forEach((c) => c.classList.toggle('active', Number(c.dataset.off) === Number(f.min_discount)));
  }
  $('#f-price-bands').addEventListener('click', (e) => {
    const chip = e.target.closest('[data-band]');
    if (!chip) return;
    const band = Number(chip.dataset.band);
    const same = state.filters.max_price === band && !state.filters.min_price;
    state.filters.max_price = same ? null : band;
    state.filters.min_price = null;
    $('#f-max-price').value = same ? '' : band;
    $('#f-min-price').value = '';
    syncFilterChips();
    refreshDeals(true);
  });
  $('#f-discount-chips').addEventListener('click', (e) => {
    const chip = e.target.closest('[data-off]');
    if (!chip) return;
    const off = Number(chip.dataset.off);
    state.filters.min_discount = state.filters.min_discount === off ? 0 : off;
    $('#f-discount').value = state.filters.min_discount;
    $('#f-discount-out').textContent = state.filters.min_discount ? state.filters.min_discount + '%+' : 'any';
    syncFilterChips();
    refreshDeals(true);
  });
  $('#f-min-price').addEventListener('change', (e) => {
    state.filters.min_price = e.target.value ? Number(e.target.value) : null;
    syncFilterChips();
    refreshDeals(true);
  });
  $('#f-coupon').addEventListener('change', (e) => {
    state.filters.has_coupon = e.target.checked;
    refreshDeals(true);
  });
  let sizeDebounce;
  $('#f-size').addEventListener('input', (e) => {
    clearTimeout(sizeDebounce);
    const value = e.target.value.trim();
    sizeDebounce = setTimeout(() => {
      state.filters.size = value;
      syncFilterChips();
      refreshDeals(true);
    }, 350);
  });

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

  // Discount lives in the price row (like every major deal site); image
  // badges are reserved for status so they never cover the product.
  const hasFlag = (deal, flag) => (deal.flags || []).includes(flag);
  const minBuy = (deal) => {
    const f = (deal.flags || []).find((x) => /^min_buy_\d+$/.test(x));
    return f ? Number(f.split('_')[2]) : 0;
  };
  // A sale ("up to 87%") or a floor price ("from ₹509") must not read as one exact product price.
  const priceOff = (deal) => (deal.discount_pct >= 5
    ? `<span class="price-off">${hasFlag(deal, 'upto_discount') ? 'Up to ' : '-'}${deal.discount_pct}%</span>` : '');
  const priceNow = (deal) => `<span class="price-now">${hasFlag(deal, 'price_from') && deal.price != null
    ? '<small class="price-from">From </small>' : ''}${money(deal.price)}</span>`;

  function dealBadges(deal) {
    const badges = [];
    if (deal.status && deal.status !== 'live') badges.push('<span class="badge badge-score">Past deal</span>');
    if (hasFlag(deal, 'stock_unknown')) badges.push('<span class="badge badge-score">Stock unknown</span>');
    if (deal.is_lowest) badges.push('<span class="badge badge-low">🟢 LOWEST EVER</span>');
    else if (deal.score >= 80) badges.push('<span class="badge badge-hot">🏆 GREAT DEAL</span>');
    else if (isFresh(deal)) badges.push('<span class="badge badge-new">🆕 NEW</span>');
    if (hasFlag(deal, 'suspicious_mrp')) {
      const title = deal.ai_mrp_reason || 'The quoted MRP looks inflated versus this product’s usual price';
      badges.push(`<span class="badge badge-warn" title="${escapeHtml(title)}">⚠️ Check MRP</span>`);
    }
    return badges;
  }

  // 8-point sparkline as an inline SVG polyline — cheap enough to redraw per card.
  function sparklineSvg(points) {
    if (!points || points.length < 2) return '';
    const min = Math.min(...points), max = Math.max(...points);
    const span = max - min || 1;
    const w = 64, h = 20;
    const coords = points.map((p, i) => {
      const x = (i / (points.length - 1)) * w;
      const y = h - ((p - min) / span) * h;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    const trend = points[points.length - 1] <= points[0] ? 'down' : 'up';
    return `<svg class="sparkline sparkline-${trend}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
      <polyline points="${coords}" fill="none" stroke-width="2" />
    </svg>`;
  }

  // Batched after a page of cards paints — one request per page instead of one per card.
  async function loadSparklines(deals) {
    const ids = deals.map((d) => d.id).filter(Boolean);
    if (!ids.length) return;
    try {
      const res = await api(`/api/deals/sparklines?ids=${encodeURIComponent(ids.join(','))}`);
      Object.entries(res.sparklines || {}).forEach(([id, points]) => {
        const host = document.querySelector(`.deal[data-id="${CSS.escape(id)}"] .sparkline-slot`);
        if (host && points.length >= 2) host.innerHTML = sparklineSvg(points);
      });
    } catch { /* purely decorative — a failed fetch just leaves cards without it */ }
  }

  function dealCard(deal) {
    const badges = dealBadges(deal);
    const store = storeName(deal)
      ? `<span class="store-tag">${escapeHtml(storeName(deal))}</span>` : '';

    return `
      <article class="deal" data-id="${escapeHtml(deal.id)}">
        <div class="deal-media" data-detail="${escapeHtml(deal.id)}" role="button" tabindex="0"
             aria-label="${escapeHtml(deal.title)}">
          ${dealMedia(deal, `<div class="badges">${badges.join('')}</div>${store}${heartButton(deal)}`)}
        </div>
        <div class="deal-body">
          <div class="deal-title" title="${escapeHtml(deal.title)}">${highlight(deal.title, state.filters.q)}</div>
          <div class="deal-price">
            ${priceOff(deal)}
            ${priceNow(deal)}
            ${deal.mrp ? `<span class="price-was">${money(deal.mrp)}</span>` : ''}
          </div>
          ${deal.saving ? `<div class="price-save">${icon('down')} Save ${money(deal.saving)}</div>` : ''}
          ${deal.coupon ? `<div><span class="coupon">🏷 ${escapeHtml(deal.coupon)}</span></div>` : ''}
          <div class="deal-meta">
            <span>${timeAgo(deal.posted_at)}</span>
            ${deal.repost_count > 1
              ? `<span class="dot"></span><span class="reposts">Posted ${deal.repost_count}×</span>` : ''}
            ${minBuy(deal) ? `<span class="dot"></span><span>Min ${minBuy(deal)}</span>` : ''}
            <span class="sparkline-slot" aria-hidden="true"></span>
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
    loadSparklines(results);
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
    if (deal.repost_count > 1) out.push(`Posted ${deal.repost_count} times — widely shared deal`);
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
        <div class="detail-layout ${deal.image_url ? 'has-hero' : ''}">
        ${deal.image_url ? `<div class="detail-hero">${dealMedia(deal)}</div>` : ''}
        <div class="modal-pad">
          ${badges.length ? `<div class="detail-badges">${badges.join('')}</div>` : ''}
          <div class="detail-price">
            ${priceOff(deal)}
            ${priceNow(deal)}
            ${deal.mrp ? `<span class="price-was">${money(deal.mrp)}</span>` : ''}
          </div>
          ${deal.saving ? `<span class="detail-save">${icon('down')} You save ${money(deal.saving)}${deal.discount_pct ? ` · ${deal.discount_pct}% off` : ''}</span>` : ''}

          ${verdictChip(deal.price_verdict)}
          ${deal.is_lowest ? `<div class="detail-note good">${icon('trend')}<span>Lowest price we have recorded for this product.</span></div>` : ''}
          ${suspicious ? `<div class="detail-note warn">${icon('alert')}<span>${escapeHtml(deal.ai_mrp_reason || 'The quoted MRP looks inflated versus this product’s price history.')}</span></div>` : ''}
          ${deal.ai_hook ? `<div class="detail-note good">${icon('check')}<span>${escapeHtml(deal.ai_hook)}</span></div>` : ''}

          <div class="scorecard">
            ${scoreDial(deal.score)}
            <div class="scoretext">
              <div class="st-title">${label}</div>
              <div class="st-sub">Deal score ${Math.round(deal.score || 0)} / 100 — ${blurb}</div>
            </div>
          </div>
          ${reasons.length ? `<ul class="reasons">${reasons.map((r) => `<li>${icon('check')}<span>${escapeHtml(r)}</span></li>`).join('')}</ul>` : ''}
          ${detailActions(deal)}

          <div class="price-chart-wrap">
            <div class="price-chart-title">Price history</div>
            <div class="price-chart" id="price-chart-${escapeHtml(id)}"></div>
          </div>

          <dl class="kv">
            <dt>Store</dt><dd>${escapeHtml(deal.store || '—')}</dd>
            <dt>Category</dt><dd>${escapeHtml(deal.category || '—')} › ${escapeHtml(deal.subcategory || '—')}</dd>
            ${deal.brand ? `<dt>Brand</dt><dd>${escapeHtml(deal.brand)}</dd>` : ''}
            ${deal.sizes ? `<dt>Sizes</dt><dd class="raw">${escapeHtml(deal.sizes)}</dd>` : ''}
            ${deal.coupon ? `<dt>Coupon</dt><dd><span class="coupon">${escapeHtml(deal.coupon)}</span>
              <button type="button" class="btn btn-ghost btn-xs" id="btn-coupon-dead" data-deal="${escapeHtml(deal.id)}">Code not working?</button></dd>` : ''}
            <dt>Posted</dt><dd class="raw">${timeAgo(deal.posted_at)}</dd>
            <dt>Shared</dt><dd>${deal.repost_count} time${deal.repost_count === 1 ? '' : 's'}</dd>
            <dt>Expires</dt><dd class="raw">${deal.expires_at ? new Date(deal.expires_at * 1000).toLocaleString() : '—'}</dd>
            ${history.points ? `<dt>History</dt><dd class="raw">${history.points} points · low ${money(history.min)} · high ${money(history.max)}</dd>` : ''}
            <dt>Deal score</dt><dd>${Math.round(deal.score ?? 0)} / 100</dd>
          </dl>

          <details class="raw">
            <summary>Original post</summary>
            <pre class="rawpost">${escapeHtml(deal.raw_text || '')}</pre>
          </details>
          <div class="similar"><h3 class="section-title" style="margin:18px 0 10px">Similar deals</h3><div class="rail" id="similar-row">${railSkeletons(3)}</div></div>
        </div>
        </div>
        ${deal.url ? `
          <div class="detail-cta">
            ${deal.price_history_url ? `<a class="btn btn-soft btn-block" id="btn-price-history" href="${escapeHtml(deal.price_history_url)}"
               target="_blank" rel="noopener noreferrer nofollow">${icon('trend', 'ico')} Price history &amp; stock</a>` : ''}
            <a class="btn btn-primary btn-block" href="${escapeHtml(deal.url)}" target="_blank" rel="noopener noreferrer nofollow">
              Open on ${escapeHtml(storeName(deal) || 'store')} ${icon('external', 'ico')}
            </a>
          </div>` : ''}
      `, { wide: true });
      const chartHost = document.getElementById(`price-chart-${id}`);
      if (chartHost) renderPriceChart(chartHost, fullHistory.points || [], deal.price_history_url);
      bindDetailActions(deal);
      loadSimilar(deal);
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

  function renderPriceChart(container, rawPoints, fallbackUrl = '') {
    const points = (rawPoints || []).filter((p) => p && p.price != null && p.at);
    if (points.length < 2) {
      // Our own history (kept in the Google Sheet) is too thin yet — hand over
      // to BuyHatke's long-term history for this product instead of a dead end.
      container.innerHTML = fallbackUrl
        ? `<div class="price-chart-empty">We've only seen this price once so far.
             <a class="btn btn-soft btn-sm" style="margin-top:10px" href="${escapeHtml(fallbackUrl)}"
                target="_blank" rel="noopener noreferrer nofollow">${icon('trend', 'ico')} See full price history on BuyHatke</a></div>`
        : '<div class="price-chart-empty">Not enough price history yet — '
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
    return `
      <article class="railcard" data-detail="${escapeHtml(deal.id)}" role="button" tabindex="0"
               aria-label="${escapeHtml(deal.title)}">
        <div class="rail-media">${dealMedia(deal)}</div>
        <div class="rail-body">
          <div class="rail-title">${highlight(deal.title, state.filters.q)}</div>
          <div class="rail-price">
            ${priceOff(deal)}
            ${priceNow(deal)}
            ${deal.mrp ? `<span class="price-was">${money(deal.mrp)}</span>` : ''}
          </div>
          ${note}
        </div>
      </article>`;
  }

  /* ---------------- extra home rows + store chips ---------------- */
  const ALL_OFF = { q: '', category: '', subcategory: '', store: '', brand: '', min_price: null, max_price: null,
    min_discount: 0, has_coupon: false, only_lowest: false, offset: 0, limit: 12 };
  async function fillRail(name, overrides, note) {
    const wrap = $(`#${name}-wrap`);
    const row = $(`#${name}-row`);
    try {
      const res = await api('/api/deals?' + buildQuery({ ...ALL_OFF, ...overrides }));
      if (!res.results.length) { delete wrap.dataset.hasData; toggleRails(); return; }
      row.innerHTML = res.results.map((d) => railCard(d, note(d))).join('');
      wrap.dataset.hasData = '1';
      bindDetailTriggers(row);
    } catch { delete wrap.dataset.hasData; }
    toggleRails();
  }
  function endsIn(ts) {
    const h = Math.max(0, (ts - Date.now() / 1000) / 3600);
    return h < 1 ? 'Ends within the hour' : h < 24 ? `Ends in ${Math.round(h)}h` : `Ends in ${Math.round(h / 24)}d`;
  }
  function loadExtraRails() {
    fillRail('ending', { sort: 'ending' }, (d) => `<span class="rail-note hot">${icon('clock')} ${endsIn(d.expires_at)}</span>`);
    fillRail('fresh', { sort: 'newest' }, (d) => `<span class="rail-note">${icon('clock')} ${timeAgo(d.posted_at)}</span>`);
    fillRail('coupons', { sort: 'best', has_coupon: true }, (d) => `<span class="coupon">🏷 ${escapeHtml(d.coupon || '')}</span>`);
  }
  function renderStoreChips() {
    const box = $('#store-chips');
    const stores = (state.facets.stores || []).filter((s) => s.key && s.key !== 'unknown').slice(0, 10);
    box.classList.toggle('hidden', !stores.length);
    box.innerHTML = stores.map((s) => `<button class="chip ${state.filters.store === s.key ? 'active' : ''}" data-store="${escapeHtml(s.key)}">
      ${escapeHtml(titleCase(s.key))} <span class="facet-count">${s.count}</span></button>`).join('');
  }
  $('#store-chips').addEventListener('click', (e) => {
    const chip = e.target.closest('[data-store]');
    if (!chip) return;
    state.filters.store = state.filters.store === chip.dataset.store ? '' : chip.dataset.store;
    renderFacet('#f-stores', state.facets.stores, 'store');
    renderStoreChips();
    refreshDeals(true);
  });

  /* ---------------- 🔎 check any product link ---------------- */
  function verdictChip(v) {
    return v ? `<div class="verdict verdict-${escapeHtml(v.level)}">${escapeHtml(v.label)}</div>` : '';
  }
  async function openCheckPrice(prefill = '') {
    openModal(sheetShell('Check a product’s price', `
      <p class="muted" style="margin:12px 0">Paste any Amazon, Flipkart, Myntra, Ajio… link. We show what it has cost in our deals and its full history.</p>
      <form class="row-inputs" id="check-form">
        <input class="input" id="check-url" type="url" placeholder="https://www.amazon.in/dp/…" value="${escapeHtml(prefill)}" required />
        <button class="btn btn-primary" type="submit">Check</button>
      </form>
      <div id="check-result" style="margin:16px 0 8px"></div>`));
    $('#check-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = e.target.querySelector('button');
      const out = $('#check-result');
      busy(btn, true);
      out.innerHTML = '<p class="muted">Looking it up…</p>';
      try {
        const r = await api('/api/lookup?url=' + encodeURIComponent($('#check-url').value.trim()));
        const cards = (list) => `<div class="grid check-grid">${list.map(dealCard).join('')}</div>`;
        out.innerHTML = `
          ${verdictChip(r.verdict)}
          ${r.price_stats && r.price_stats.points ? `<p class="muted small" style="margin:8px 0">We've seen it ${r.price_stats.points} times · lowest ${money(r.price_stats.min)} · highest ${money(r.price_stats.max)}</p>` : ''}
          <div class="price-chart" id="check-chart"></div>
          ${r.deals.length ? `<h3 class="section-title" style="margin:16px 0 10px">Live deals</h3>${cards(r.deals)}` : ''}
          ${r.archive.length ? `<h3 class="section-title" style="margin:16px 0 10px">🗂️ Earlier deals</h3>${cards(r.archive)}` : ''}
          ${!r.deals.length && !r.archive.length ? '<p class="muted">We haven’t seen this product in our channels yet.</p>' : ''}
          ${r.price_history_url ? `<a class="btn btn-soft btn-block" style="margin-top:14px" target="_blank" rel="noopener noreferrer nofollow" href="${escapeHtml(r.price_history_url)}">${icon('trend', 'ico')} Full price history on BuyHatke</a>` : ''}`;
        if ((r.history || []).length >= 2) renderPriceChart($('#check-chart'), r.history, '');
        else $('#check-chart').remove();
        bindDetailTriggers(out);
      } catch (err) {
        out.innerHTML = `<p class="alert alert-error">${escapeHtml(err.message)}</p>`;
      } finally { busy(btn, false); }
    });
    if (prefill) $('#check-form').requestSubmit();
  }
  $('#btn-check').addEventListener('click', () => openCheckPrice());

  /* ---------------- detail: verdict + similar deals ---------------- */
  async function loadSimilar(deal) {
    const host = $('#similar-row');
    if (!host) return;
    try {
      const res = await api(`/api/deals/${encodeURIComponent(deal.id)}/similar?limit=10`);
      if (!res.results.length) { host.closest('.similar')?.remove(); return; }
      host.innerHTML = res.results.map((d) => railCard(d, d.saving ? `<span class="rail-note">${icon('down')} Save ${money(d.saving)}</span>` : '')).join('');
      bindDetailTriggers(host);
    } catch { host.closest('.similar')?.remove(); }
  }

  function toggleRails() {
    const show = isBrowseMode();
    ['#trending-wrap', '#lowest-wrap', '#ending-wrap', '#fresh-wrap', '#coupons-wrap'].forEach((sel) => {
      const el = $(sel);
      // A rail with no data stays hidden regardless — `has-data` is set by its loader.
      el.classList.toggle('hidden', !show || !el.dataset.hasData);
    });
    const cats = $('#cat-rails');
    cats.classList.toggle('hidden', !show || !cats.children.length);
  }

  async function loadRails() {
    loadTrending();
    loadLowest();
    loadExtraRails();
    catRails.stale = true;
  }

  /* ---------------- category rails (browse mode, below the home rails) ---------------- */
  const CAT_RAILS_MAX = 4;
  const catRails = { stale: true, gen: 0 };

  function maybeLoadCategoryRails(res) {
    if (!catRails.stale) return;
    catRails.stale = false;
    let cats = res.categories;
    if (!Array.isArray(cats)) {
      // Older backend without category counts: rank by what the first page holds.
      const counts = {};
      (res.results || []).forEach((d) => { if (d.category) counts[d.category] = (counts[d.category] || 0) + 1; });
      cats = Object.entries(counts).map(([name, count]) => ({ name, count }));
    }
    const top = cats
      .filter((c) => c && c.name && c.name !== 'Other' && c.count >= 2)
      .sort((a, b) => b.count - a.count)
      .slice(0, CAT_RAILS_MAX);
    const gen = ++catRails.gen;
    if (!top.length) { $('#cat-rails').innerHTML = ''; toggleRails(); return; }
    // Deferred so the main grid paints and settles first.
    (window.requestIdleCallback || ((fn) => setTimeout(fn, 250)))(() => loadCategoryRails(top, gen));
  }

  async function loadCategoryRails(top, gen) {
    const loaded = await Promise.all(top.map((c) => api('/api/deals?' + buildQuery({
      q: '', category: c.name, subcategory: '', store: '', brand: '',
      min_price: null, max_price: null, min_discount: 0, has_coupon: false, only_lowest: false,
      sort: 'best', limit: 12, offset: 0,
    })).then((res) => ({ cat: c, res })).catch(() => null)));
    if (gen !== catRails.gen) return;

    const host = $('#cat-rails');
    const rails = loaded.filter((x) => x && x.res.results && x.res.results.length);
    host.innerHTML = rails.map(({ cat, res }, i) => `
      <section class="rail-section cat-rail">
        <div class="section-head">
          <div>
            <h3 class="section-title">${CATEGORY_ICON[cat.name] || '🏷️'} ${escapeHtml(cat.name)}</h3>
            <div class="section-sub">${num(res.total)} live deal${res.total === 1 ? '' : 's'}</div>
          </div>
          <div class="section-actions">
            <button class="btn btn-ghost btn-xs" data-see-cat="${escapeHtml(cat.name)}">See all</button>
            <div class="rail-nav" data-rail="#cat-rail-${i}">
              <button class="iconbtn" data-dir="-1" aria-label="Scroll ${escapeHtml(cat.name)} left">${icon('chev-left')}</button>
              <button class="iconbtn" data-dir="1" aria-label="Scroll ${escapeHtml(cat.name)} right">${icon('chev-right')}</button>
            </div>
          </div>
        </div>
        <div id="cat-rail-${i}" class="rail">
          ${res.results.map((d) => railCard(d, d.saving
            ? `<span class="rail-note">${icon('down')} Save ${money(d.saving)}</span>`
            : `<span class="rail-note muted-note">${icon('clock')} ${timeAgo(d.posted_at)}</span>`)).join('')}
        </div>
      </section>`).join('');
    $$('.rail-nav', host).forEach(bindRailNav);
    $$('.rail', host).forEach(bindDetailTriggers);
    toggleRails();
  }

  $('#cat-rails').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-see-cat]');
    if (!btn) return;
    applyCategory(btn.dataset.seeCat);
    $('#grid-head').scrollIntoView({ behavior: reduceMotion() ? 'auto' : 'smooth', block: 'start' });
  });

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
        min_price: null, max_price: null, min_discount: 0, has_coupon: false, only_lowest: true,
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
      renderStoreChips();
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
      box.innerHTML = '';
      box.closest('.filter-block')?.classList.add('hidden');
      return;
    }
    box.closest('.filter-block')?.classList.remove('hidden');
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
    noteLiveCount(stats);
    state.lastStats = stats;
    const last = stats.ingest && stats.ingest.last_run;
    const status = statusOverride || (last
      ? `Updated ${timeAgo(last)} · scanning every ${Math.round(stats.poll_interval_seconds / 60)} min`
      : 'Waiting for the first sync…');

    if (state.user?.guest) {
      // Visitors get a storefront headline, not an operations dashboard.
      $('#statstrip').innerHTML = `
        <div class="visitor-hero">
          <div class="vh-copy">
            <h1>Today’s best deals, <span>in one place</span></h1>
            <p>Loot deals and price drops from India’s top deal channels — duplicates merged, prices checked, ranked.</p>
          </div>
          <div class="vh-stats">
            <div><b data-count="${stats.deals_live || 0}">0</b><span>live deals</span></div>
            <div><b class="up" data-count="${stats.deals_today || 0}">0</b><span>added today</span></div>
            <div class="vh-live"><span class="livedot ${last ? '' : 'idle'}" id="livedot" aria-hidden="true"></span>
              <span id="radar-status">${last ? `Updated ${timeAgo(last)}` : 'Live'}</span></div>
          </div>
        </div>`;
      $$('#statstrip [data-count]').forEach((el) => animateCount(el, Number(el.dataset.count)));
      state.countersAnimated = true;
      return;
    }

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
        </div>
      </div>`;

    $$('#statstrip .stat-value').forEach((el) => animateCount(el, Number(el.dataset.count)));
    state.countersAnimated = true;

    $('#footer-status').textContent = last
      ? `Deals are kept ${stats.deal_ttl_hours}h or until the link goes dead.`
      : '';
  }

  /* ============================================================
     DROP COUNTDOWN  (live countdown to the server's real next ingest cycle —
     not a decorative schedule; /api/ping is the cheapest route in the app,
     so polling it every 30s to stay honest costs nothing extra)
     ============================================================ */
  const DROP_RESYNC_MS = 30_000;
  let dropTimer = null;
  let dropResyncTimer = null;
  let nextIngestAt = null;   // ms epoch from the server, or null before the first sync
  let lastIngestAt = null;   // ms epoch of the last cycle we've observed complete

  async function syncIngestTiming() {
    try {
      const p = await api('/api/ping');
      const before = lastIngestAt;
      if (p.next_ingest_at) nextIngestAt = p.next_ingest_at * 1000;
      if (p.last_ingest_at) lastIngestAt = p.last_ingest_at * 1000;
      // A cycle actually finished since our last check — the spotlight (best
      // deal of the latest batch) is worth refreshing now, not on a fixed timer.
      if (before && lastIngestAt && lastIngestAt > before) loadSpotlight();
    } catch { /* keep ticking on the last known value; we'll resync again shortly */ }
  }

  function renderDropCountdown() {
    const el = $('#drop-countdown');
    const timeEl = $('#dc-time');
    if (!el || !timeEl) return;
    if (!nextIngestAt) { el.classList.add('hidden'); return; }
    const remaining = nextIngestAt - Date.now();
    el.classList.remove('hidden');
    if (remaining <= 0) { timeEl.textContent = 'checking now'; return; }
    const h = Math.floor(remaining / 3600000);
    const m = Math.floor((remaining % 3600000) / 60000);
    const s = Math.floor((remaining % 60000) / 1000);
    timeEl.textContent = h > 0 ? `${h}h ${m}m` : `${m}m ${String(s).padStart(2, '0')}s`;
  }

  function startDropCountdown() {
    syncIngestTiming().then(renderDropCountdown);
    if (dropTimer) clearInterval(dropTimer);
    if (dropResyncTimer) clearInterval(dropResyncTimer);
    dropTimer = setInterval(renderDropCountdown, 1000);
    dropResyncTimer = setInterval(syncIngestTiming, DROP_RESYNC_MS);
  }

  /* ============================================================
     SPOTLIGHT  (biggest banner: best deal of the current drop)
     ============================================================ */
  async function loadSpotlight() {
    const el = $('#spotlight');
    if (!el) return;
    // Women Fashion / Beauty first (site focus), then whatever's best overall.
    const tries = ['Women Fashion', 'Beauty', ''];
    let deal = null;
    for (const category of tries) {
      try {
        const qs = category ? `category=${encodeURIComponent(category)}&sort=best&limit=1` : 'sort=best&limit=1';
        const res = await api(`/api/deals?${qs}`);
        if (res.deals && res.deals[0]) { deal = res.deals[0]; break; }
      } catch { /* try next */ }
    }
    if (!deal) { el.classList.add('hidden'); return; }

    el.href = `/?deal=${deal.id}`;
    el.onclick = (e) => { e.preventDefault(); showDealDetail(deal.id); };
    $('#spotlight-img').src = deal.image_url || '';
    $('#spotlight-img').alt = deal.title || '';
    $('#spotlight-badge').textContent = deal.discount_pct ? `${Math.round(deal.discount_pct)}% OFF` : 'DEAL';
    $('#spotlight-kicker').textContent = 'Best of this drop';
    $('#spotlight-title').textContent = deal.title || '';
    $('#spotlight-price').textContent = money(deal.price);
    $('#spotlight-mrp').textContent = deal.mrp && deal.mrp > deal.price ? money(deal.mrp) : '';
    el.classList.remove('hidden');
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

  /* ---------------- query-term highlighting ---------------- */
  const escapeRegExp = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

  function queryTerms(q) {
    const seen = new Set();
    return String(q || '').toLowerCase().split(/[^\p{L}\p{N}]+/u)
      .filter((t) => t.length >= 2 && !seen.has(t) && seen.add(t))
      .sort((a, b) => b.length - a.length)
      .slice(0, 8);
  }

  /* Splits the raw text on the terms and escapes every piece on its own, so a
     term can never match inside an entity like "&amp;" and no markup leaks in. */
  function highlight(text, q) {
    const raw = String(text ?? '');
    const terms = queryTerms(q);
    if (!terms.length) return escapeHtml(raw);
    const re = new RegExp(`(${terms.map(escapeRegExp).join('|')})`, 'giu');
    return raw.split(re)
      .map((part, i) => (i % 2 ? `<mark>${escapeHtml(part)}</mark>` : escapeHtml(part)))
      .join('');
  }

  /* ---------------- instant-search dropdown (combobox) ---------------- */
  const suggest = { data: null, active: -1, timer: 0, abort: null, endpoint: true };
  let suggestOptSeq = 0;

  const suggestOption = (kind, value, inner, cls = '') => `
    <div class="suggest-item ${cls}" role="option" id="sg-opt-${suggestOptSeq++}" aria-selected="false"
         data-kind="${kind}" data-value="${escapeHtml(value)}">${inner}</div>`;

  const suggestGroup = (label, body, extraHead = '') => `
    <div role="group" aria-label="${escapeHtml(label)}">
      <div class="suggest-head"><span aria-hidden="true">${escapeHtml(label)}</span>${extraHead}</div>
      ${body}
    </div>`;

  function suggestIdleHtml() {
    const recent = recents();
    // "Popular" is not invented: these are the brands the catalog actually has
    // the most live deals for, straight from /api/deals/facets.
    const brands = (state.facets.brands || []).slice(0, 6);
    return [
      recent.length ? suggestGroup('Recent searches',
        recent.map((r) => suggestOption('q', r, `${icon('clock')}<span class="s-text">${escapeHtml(r)}</span>`)).join(''),
        '<button type="button" id="clear-recents">Clear</button>') : '',
      brands.length ? suggestGroup('Most deals right now',
        brands.map((b) => suggestOption('q', b.key,
          `${icon('trend')}<span class="s-text">${escapeHtml(titleCase(b.key))}</span><span class="s-count">${num(b.count)}</span>`)).join('')) : '',
    ].join('');
  }

  function suggestDealHtml(deal, typed) {
    const thumb = deal.image_url
      ? `<img src="${escapeHtml(deal.image_url)}" alt="" loading="lazy" decoding="async" onerror="this.remove()" />`
      : '';
    return suggestOption('deal', deal.id, `
      <span class="s-thumb" aria-hidden="true"><span>🛍️</span>${thumb}</span>
      <span class="s-deal">
        <span class="s-title">${highlight(deal.title, typed)}</span>
        <span class="s-price">
          ${priceNow(deal)}
          ${priceOff(deal)}
          ${storeName(deal) ? `<span class="s-store">${escapeHtml(storeName(deal))}</span>` : ''}
        </span>
      </span>`, 's-dealrow');
  }

  function suggestTypedHtml(typed) {
    const fresh = suggest.data && suggest.data.query === typed;
    const d = suggest.data || {};
    const lower = typed.toLowerCase();
    const recent = recents().filter((r) => r.toLowerCase() !== lower && r.toLowerCase().includes(lower)).slice(0, 3);
    const deals = (d.deals || []).slice(0, 6);
    const facet = (kind, list, ico) => (list || []).slice(0, 4).map((x) => {
      const name = kind === 'category' ? x.name : x.key;
      const label = kind === 'category' ? name : titleCase(name);
      const lead = kind === 'category'
        ? `<span class="s-emoji" aria-hidden="true">${CATEGORY_ICON[name] || '🏷️'}</span>` : icon(ico);
      return suggestOption(kind, name,
        `${lead}<span class="s-text">${highlight(label, typed)}</span><span class="s-count">${num(x.count)}</span>`);
    }).join('');

    const categories = facet('category', d.categories, 'tag');
    const brands = facet('brand', d.brands, 'tag');
    const stores = facet('store', d.stores, 'external');
    const empty = fresh && !deals.length && !categories && !brands && !stores;

    return [
      suggestOption('q', typed,
        `${icon('search')}<span class="s-text">Search for “<b>${escapeHtml(typed)}</b>”</span><kbd class="s-kbd">Enter</kbd>`,
        's-query'),
      recent.map((r) => suggestOption('q', r, `${icon('clock')}<span class="s-text">${highlight(r, typed)}</span>`)).join(''),
      deals.length ? suggestGroup('Deals', deals.map((deal) => suggestDealHtml(deal, typed)).join('')) : '',
      categories ? suggestGroup('Categories', categories) : '',
      brands ? suggestGroup('Brands', brands) : '',
      stores ? suggestGroup('Stores', stores) : '',
      !fresh ? '<div class="suggest-status" aria-hidden="true"><span class="s-spin"></span>Finding deals…</div>' : '',
      empty ? '<div class="suggest-status">No instant matches — press Enter to search every deal.</div>' : '',
    ].join('');
  }

  function renderSuggest() {
    const panel = $('#suggest');
    const input = $('#search-input');
    const typed = input.value.trim();
    const html = typed ? suggestTypedHtml(typed) : suggestIdleHtml();
    if (!html.trim()) { closeSuggest(); return; }
    panel.innerHTML = html;
    panel.classList.toggle('stale', !!typed && !(suggest.data && suggest.data.query === typed));
    suggest.active = -1;
    input.removeAttribute('aria-activedescendant');
    panel.classList.remove('hidden');
    input.setAttribute('aria-expanded', 'true');
  }

  function closeSuggest() {
    const input = $('#search-input');
    $('#suggest').classList.add('hidden');
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    suggest.active = -1;
  }

  const suggestOptions = () => $$('#suggest [role="option"]');

  function setActiveOption(index) {
    const options = suggestOptions();
    const input = $('#search-input');
    suggest.active = index;
    options.forEach((o, i) => {
      o.classList.toggle('active', i === index);
      o.setAttribute('aria-selected', String(i === index));
    });
    const current = options[index];
    if (current) {
      input.setAttribute('aria-activedescendant', current.id);
      current.scrollIntoView({ block: 'nearest' });
    } else {
      input.removeAttribute('aria-activedescendant');
    }
  }

  function moveActive(step) {
    const count = suggestOptions().length;
    if (!count) return;
    // -1 is "back in the text box", so arrowing past either end returns there.
    let next = suggest.active + step;
    if (next >= count) next = -1;
    else if (next < -1) next = count - 1;
    setActiveOption(next);
  }

  function activateOption(el) {
    const { kind, value } = el.dataset;
    const typed = $('#search-input').value.trim();
    if (kind === 'q') { runSearch(value); return; }
    if (kind === 'deal') {
      if (typed) pushRecent(typed);
      closeSuggest();
      $('#search-input').blur();
      showDealDetail(value);
      return;
    }
    const f = state.filters;
    if (kind === 'category') {
      f.category = value;
      f.subcategory = '';
      renderCategoryChips();
      renderSubcategoryFilter();
    } else if (kind === 'brand' || kind === 'store') {
      f[kind] = value;
      renderFacet('#f-stores', state.facets.stores, 'store');
      renderFacet('#f-brands', state.facets.brands, 'brand');
    }
    // Typing "boat" and picking the boAt brand means "boAt deals", not
    // "boAt deals that also say boat" — drop the text when it just named the facet.
    const a = value.toLowerCase(), b = typed.toLowerCase();
    runSearch(!b || a.includes(b) || b.includes(a) ? '' : typed);
  }

  async function suggestSource(q, signal) {
    if (suggest.endpoint) {
      try {
        return await api('/api/deals/suggest?' + new URLSearchParams({ q, limit: 6 }), { signal });
      } catch (err) {
        if (err.name === 'AbortError' || (err.status !== 404 && err.status !== 405)) throw err;
        suggest.endpoint = false;   // backend without /suggest — build it from /api/deals instead
      }
    }
    const res = await api('/api/deals?' + buildQuery({
      q, category: '', subcategory: '', store: '', brand: '',
      min_price: null, max_price: null, min_discount: 0, has_coupon: false, only_lowest: false,
      sort: 'relevance', limit: 6, offset: 0,
    }), { signal });
    const lower = q.toLowerCase();
    const named = (list) => (list || []).filter((x) => x.key.toLowerCase().includes(lower)).slice(0, 4);
    return {
      deals: res.results || [],
      categories: (res.categories || []).slice(0, 4),
      brands: named(state.facets.brands),
      stores: named(state.facets.stores),
    };
  }

  async function fetchSuggest(q) {
    suggest.abort?.abort();
    const controller = new AbortController();
    suggest.abort = controller;
    let data;
    try {
      data = await suggestSource(q, controller.signal);
    } catch (err) {
      if (err.name === 'AbortError') return;
      if (err.status === 401) { window.location.reload(); return; }
      data = { deals: [], categories: [], brands: [], stores: [] };
    }
    if (controller.signal.aborted) return;
    suggest.data = { ...data, query: q };
    const input = $('#search-input');
    if (document.activeElement === input && input.value.trim() === q) renderSuggest();
  }

  function scheduleSuggest(value) {
    clearTimeout(suggest.timer);
    if (!value) { suggest.abort?.abort(); return; }
    if (suggest.data && suggest.data.query === value) return;
    suggest.timer = setTimeout(() => fetchSuggest(value), 180);
  }

  function runSearch(q) {
    clearTimeout(suggest.timer);
    suggest.abort?.abort();
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

  $('#search-input').addEventListener('focus', () => {
    renderSuggest();
    scheduleSuggest($('#search-input').value.trim());
  });
  $('#search-input').addEventListener('blur', () => setTimeout(() => {
    if (document.activeElement !== $('#search-input')) closeSuggest();
  }, 120));

  $('#search-input').addEventListener('input', (e) => {
    const value = e.target.value.trim();
    $('#search-clear').classList.toggle('hidden', !value);
    renderSuggest();
    scheduleSuggest(value);
    // Emptying the box by hand is a "clear": the grid goes back to browsing.
    if (!value && state.filters.q) {
      state.filters.q = '';
      refreshDeals(true);
    }
  });

  $('#search-input').addEventListener('keydown', (e) => {
    if (e.isComposing) return;
    const open = !$('#suggest').classList.contains('hidden');
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!open) { renderSuggest(); scheduleSuggest(e.target.value.trim()); return; }
      moveActive(e.key === 'ArrowDown' ? 1 : -1);
    } else if (e.key === 'Enter' && open && suggest.active >= 0) {
      const option = suggestOptions()[suggest.active];
      if (option) { e.preventDefault(); activateOption(option); }
    } else if (e.key === 'Escape' && open) {
      e.preventDefault();
      e.stopPropagation();
      closeSuggest();
    }
  });

  // Keeps focus in the input so a tap on the panel never triggers the blur-close.
  $('#suggest').addEventListener('mousedown', (e) => e.preventDefault());
  $('#suggest').addEventListener('click', (e) => {
    if (e.target.closest('#clear-recents')) {
      try { localStorage.removeItem(RECENTS_KEY); } catch { /* private mode */ }
      renderSuggest();
      return;
    }
    const option = e.target.closest('[role="option"]');
    if (option) activateOption(option);
  });

  $('#search-clear').addEventListener('click', () => {
    $('#search-input').value = '';
    state.filters.q = '';
    suggest.abort?.abort();
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
  /* ---------------- sort tabs (mirror the sort <select>) ---------------- */
  function syncSortTabs() {
    $$('#sort-tabs [data-sort]').forEach((tab) => {
      tab.setAttribute('aria-selected', String(tab.dataset.sort === state.filters.sort));
    });
  }
  $$('#sort-tabs [data-sort]').forEach((tab) => tab.addEventListener('click', () => {
    if (state.filters.sort === tab.dataset.sort) return;
    state.filters.sort = tab.dataset.sort;
    $('#f-sort').value = tab.dataset.sort;
    refreshDeals(true);
    tab.scrollIntoView({ behavior: reduceMotion() ? 'auto' : 'smooth', inline: 'nearest', block: 'nearest' });
  }));

  /* ---------------- grid / list view ---------------- */
  function setView(view) {
    $('#deal-grid').classList.toggle('list', view === 'list');
    $('#archive-grid').classList.toggle('list', view === 'list');
    $$('.viewtoggle [data-view]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.view === view)));
    try { localStorage.setItem('dr-view', view); } catch { /* private mode */ }
  }
  $$('.viewtoggle [data-view]').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));
  setView((() => { try { return localStorage.getItem('dr-view'); } catch { return null; } })() === 'list' ? 'list' : 'grid');

  /* ---------------- rail arrows ---------------- */
  function updateRailNav(nav) {
    const rail = $(nav.dataset.rail);
    if (!rail) return;
    const max = rail.scrollWidth - rail.clientWidth - 2;
    $('[data-dir="-1"]', nav).disabled = rail.scrollLeft <= 2;
    $('[data-dir="1"]', nav).disabled = rail.scrollLeft >= max;
  }
  function bindRailNav(nav) {
    const rail = $(nav.dataset.rail);
    if (!rail || nav.dataset.bound) return;
    nav.dataset.bound = '1';
    $$('[data-dir]', nav).forEach((btn) => btn.addEventListener('click', () => {
      rail.scrollBy({ left: Number(btn.dataset.dir) * rail.clientWidth * 0.85, behavior: reduceMotion() ? 'auto' : 'smooth' });
    }));
    rail.addEventListener('scroll', () => updateRailNav(nav), { passive: true });
    new ResizeObserver(() => updateRailNav(nav)).observe(rail);
    new MutationObserver(() => updateRailNav(nav)).observe(rail, { childList: true });
    updateRailNav(nav);
  }
  $$('.rail-nav').forEach(bindRailNav);

  /* ---------------- infinite scroll (the button stays as a fallback) ---------------- */
  if ('IntersectionObserver' in window) {
    new IntersectionObserver((entries) => {
      const btn = $('#btn-more');
      if (entries.some((e) => e.isIntersecting) && !btn.classList.contains('hidden') && !state.loading) btn.click();
    }, { rootMargin: '600px 0px' }).observe($('#btn-more'));
  }

  $('#btn-top').addEventListener('click', () => window.scrollTo({ top: 0, behavior: reduceMotion() ? 'auto' : 'smooth' }));

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
    state.filters.min_discount = Number(e.target.value);
    syncFilterChips();
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
      category: '', subcategory: '', store: '', brand: '', size: '',
      min_price: null, max_price: null, min_discount: 0, has_coupon: false, only_lowest: false, all_channels: false,
    });
    $('#f-max-price').value = '';
    $('#f-min-price').value = '';
    $('#f-size').value = '';
    $('#f-coupon').checked = false;
    syncFilterChips();
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
    setSyncStatus('Scanning for new deals…', 'syncing');
    try {
      const res = await post('/api/channels/sync');
      const added = (res.new || 0) + (res.merged || 0);
      toast(added
        ? `Synced: ${res.new} new deals, ${res.merged} matched to existing ones.`
        : 'Sync complete — no new deals yet.', 'ok');
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
        const query = btn.closest('.alert-row')?.querySelector('.alert-q')?.textContent || 'this alert';
        const ok = await confirmDialog('Delete alert?', `You’ll stop getting Telegram messages for “${query}”.`);
        if (!ok) return;
        try {
          await del(`/api/watchlists/${btn.dataset.del}`);
          toast('Alert deleted.', 'ok');
        } catch (err) { toast(err.message, 'err'); }
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
        const status = box.closest('.alert-row')?.querySelector('.alert-status');
        const paint = (on) => {
          if (status) {
            status.classList.toggle('off', !on);
            status.innerHTML = `<span class="sdot"></span>${on ? 'Active' : 'Paused'}`;
          }
        };
        const on = box.checked;
        paint(on);
        setAlertCount(state.alertCount + (on ? 1 : -1));
        box.disabled = true;
        try {
          await api(`/api/watchlists/${box.dataset.toggle}?notify=${on}`, { method: 'PATCH' });
        } catch (err) {
          // Optimistic update failed — put the switch back where the server has it.
          box.checked = !on;
          paint(!on);
          setAlertCount(state.alertCount + (on ? -1 : 1));
          toast(err.message, 'err');
        } finally { box.disabled = false; }
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

  /* ---------------- notification history + unseen dot ---------------- */
  const NOTIF_SEEN_KEY = 'dr-notif-seen';

  // Server timestamps are epoch seconds; tolerate ms or ISO strings too.
  function toEpoch(v) {
    if (v == null || v === '') return 0;
    if (typeof v === 'number') return v > 1e12 ? v / 1000 : v;
    const n = Number(v);
    if (!Number.isNaN(n)) return n > 1e12 ? n / 1000 : n;
    const t = Date.parse(v);
    return Number.isNaN(t) ? 0 : t / 1000;
  }
  const notifSeenAt = () => { try { return Number(localStorage.getItem(NOTIF_SEEN_KEY)) || 0; } catch { return 0; } };

  function setUnseen(on) {
    const bell = $('#btn-bell');
    bell.classList.toggle('has-unseen', on);
    bell.setAttribute('aria-label', on ? 'Deal alerts — new matches' : 'Deal alerts');
    $('.navbtn[data-nav="alerts"]')?.classList.toggle('has-unseen', on);
  }

  function markNotificationsSeen(ts) {
    if (ts > notifSeenAt()) {
      try { localStorage.setItem(NOTIF_SEEN_KEY, String(ts)); } catch { /* private mode */ }
    }
    setUnseen(false);
  }

  async function checkUnseenNotifications() {
    if (!state.user) return;
    if (state.page === 'alerts') { setUnseen(false); return; }
    const seen = notifSeenAt();
    try {
      const res = await api(`/api/notifications?${new URLSearchParams({ since: seen, limit: 5 })}`);
      setUnseen((res.notifications || []).some((n) => toEpoch(n.created_at) > seen));
    } catch { /* endpoint not deployed yet, or offline — no dot */ }
  }

  async function loadNotifications() {
    const list = $('#notif-list');
    list.innerHTML = Array.from({ length: 2 }, () => '<div class="skel skel-row"></div>').join('');
    try {
      const res = await api('/api/notifications?limit=30');
      const items = res.notifications || [];
      const seen = notifSeenAt();
      // Server clock, not ours — a fast phone clock would otherwise hide real matches.
      const newest = Math.max(0, ...items.map((n) => toEpoch(n.created_at)));
      markNotificationsSeen(toEpoch(res.now) || newest);
      if (!items.length) {
        list.innerHTML = `
          <div class="notif-empty">
            ${icon('bell')}
            <span>No matches yet. When an alert catches a deal it shows up here and on your devices.</span>
          </div>`;
        return;
      }
      list.innerHTML = items.map((n) => {
        const at = toEpoch(n.created_at);
        return `
          <button type="button" class="notif ${at > seen ? 'unseen' : ''}"
                  data-notif-deal="${escapeHtml(n.deal_id ?? '')}" data-notif-url="${escapeHtml(n.url || '')}">
            <span class="notif-ico" aria-hidden="true">${icon('bell')}</span>
            <span class="notif-main">
              <span class="notif-title">${escapeHtml(n.title || 'Deal alert')}</span>
              ${n.body ? `<span class="notif-body">${escapeHtml(n.body)}</span>` : ''}
            </span>
            <span class="notif-time">${at ? timeAgo(at) : ''}</span>
          </button>`;
      }).join('');
    } catch (err) {
      list.innerHTML = `<div class="notif-empty">${icon('info')}<span>${err.status === 404
        ? 'Notification history isn’t available on this server yet.'
        : escapeHtml(err.message)}</span></div>`;
    }
  }

  $('#notif-list').addEventListener('click', (e) => {
    const row = e.target.closest('.notif');
    if (!row) return;
    const { notifDeal, notifUrl } = row.dataset;
    if (notifDeal) { showDealDetail(notifDeal); return; }
    if (!notifUrl) return;
    try {
      const url = new URL(notifUrl, window.location.origin);
      const linked = url.origin === window.location.origin && url.searchParams.get('deal');
      if (linked) showDealDetail(linked);
      else if (/^https?:$/.test(url.protocol)) window.open(url.href, '_blank', 'noopener');
    } catch { /* malformed URL — nothing sensible to open */ }
  });

  $('#btn-test-notif').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    busy(btn, true);
    try {
      const res = await post('/api/notifications/test');
      toast(res && res.push_sent
        ? 'Test notification sent — check your device.'
        : 'Test notification saved. No device is registered for push, so it only appears here.',
      res && res.push_sent ? 'ok' : 'info', 6000);
      loadNotifications();
    } catch (err) {
      toast(err.status === 404 ? 'Notifications aren’t available on this server yet.' : err.message, 'err');
    } finally { busy(btn, false); }
  });

  /* ============================================================
     BOOT
     ============================================================ */
  $('#modal').addEventListener('click', (e) => {
    if (e.target.dataset.close !== undefined || e.target.closest('[data-close]')) closeModal();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeModal(); closeFilters(); closeUserMenu(); closeSuggest(); }
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) || document.activeElement.isContentEditable;
    if (e.key === '/' && !typing && state.user && $('#modal').classList.contains('hidden')) {
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
      $('#btn-top').classList.toggle('hidden', y < 1400 || state.page !== 'deals');
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
    if (isStandalone() || inNativeApp()) return; // already an app — nothing to offer
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
    syncSortTabs();
    initInstall();

    // No visitor sign-in: the server reads the channels with its own account,
    // so everyone lands on the deals immediately.
    document.documentElement.classList.add('guest');
    await onSignedIn({ first_name: '', guest: true });
    updateSavedBadges();
    pollPriceAlerts();
    offerTelegramChannel();
    loadSaleRail();
  })();

  /* Upcoming sales rail (Big Billion Days, Great Indian Festival…), above the
     category chips. Silently hidden if there are none — never an empty strip. */
  async function loadSaleRail() {
    let events = [];
    try { events = (await api('/api/sale-events')).events || []; } catch { return; }
    const rail = $('#sale-rail');
    if (!events.length) { rail.classList.add('hidden'); return; }
    const fmtDate = (ts) => new Date(ts * 1000).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
    const countdown = (ts) => {
      const days = Math.round((ts * 1000 - Date.now()) / 86400000);
      if (days <= 0) return 'Live now';
      if (days === 1) return 'Tomorrow';
      return `In ${days}d`;
    };
    rail.innerHTML = events.map((e) => `
      <div class="salecard">
        <div class="salecard-top">
          <span class="salecard-store">${escapeHtml(e.store || 'Sale')}</span>
          <span class="salecard-when">${escapeHtml(countdown(e.starts_at))}</span>
        </div>
        <div class="salecard-name">${escapeHtml(e.name)}</div>
        ${e.hype ? `<div class="salecard-hype">${escapeHtml(e.hype)}</div>` : ''}
        <div class="salecard-approx">${e.approximate ? 'Approx. ' : ''}${escapeHtml(fmtDate(e.starts_at))}${
          e.ends_at ? `–${escapeHtml(fmtDate(e.ends_at))}` : ''}</div>
      </div>`).join('');
    rail.classList.remove('hidden');
  }

  /* "Join our Telegram channel" — shown each visit until they tap Join.
     The native app shows its own version, so skip it inside the app's WebView. */
  const TG_JOINED_KEY = 'dr-tg-joined';
  async function offerTelegramChannel() {
    if (inNativeApp()) return;
    try { if (localStorage.getItem(TG_JOINED_KEY)) return; } catch { /* private mode: still offer */ }
    let channel = null;
    try { channel = (await api('/api/auth/config')).telegram_channel; } catch { return; }
    if (!channel || !channel.url) return;
    await new Promise((r) => setTimeout(r, 1500));  // let the deals paint first
    if (!$('#modal').classList.contains('hidden')) return;  // never cover a dialog they opened
    const handle = channel.username ? `@${escapeHtml(channel.username)}` : 'our channel';
    openModal(`
      <div class="tg-invite">
        <button class="tg-invite-close" type="button" data-close aria-label="Close">✕</button>
        <div class="tg-invite-icon" aria-hidden="true">✈️</div>
        <h2>Get the crazy deals first</h2>
        <p class="muted">Verified loot deals — women's accessories, fashion and more — land on
          our Telegram channel <b>${handle}</b> seconds after they go live, before anywhere else.</p>
        <a class="btn btn-primary tg-invite-join" href="${escapeHtml(channel.url)}" target="_blank"
           rel="noopener" id="tg-join">Join on Telegram</a>
        <button class="btn btn-soft tg-invite-later" type="button" data-close>Maybe later</button>
      </div>`);
    $('#tg-join').addEventListener('click', () => {
      try { localStorage.setItem(TG_JOINED_KEY, String(Date.now())); } catch { /* ignore */ }
      closeModal();
    });
  }

  // Keep stats fresh while the tab is open.
  setInterval(() => {
    if (!state.user || document.hidden) return;
    if (state.page === 'deals') loadStats();
    checkUnseenNotifications();
    pollPriceAlerts();
  }, 60000);
})();
