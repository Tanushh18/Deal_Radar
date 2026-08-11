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
      all_channels: false, sort: 'relevance',
    },
    offset: 0,
    limit: 48,
    total: 0,
    loading: false,       // guards "Load more" only — see searchAbortController for search
    availableChannels: [],
    selectedChannels: new Set(),
    categories: [],
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

  /* ---------------- UI helpers ---------------- */
  function toast(message, kind = 'info', ms = 4200) {
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    el.textContent = message;
    $('#toasts').appendChild(el);
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transition = 'opacity .25s';
      setTimeout(() => el.remove(), 260);
    }, ms);
  }

  function busy(btn, on) {
    if (!btn) return;
    btn.classList.toggle('loading', on);
    btn.disabled = on;
  }

  const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));

  const money = (n) => (n == null ? '—' : '₹' + Math.round(n).toLocaleString('en-IN'));

  function timeAgo(ts) {
    if (!ts) return '';
    const secs = Math.max(0, Date.now() / 1000 - ts);
    if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
    if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
    return `${Math.floor(secs / 86400)}d ago`;
  }

  function openModal(html) {
    $('#modal-body').innerHTML = html;
    $('#modal').classList.remove('hidden');
  }
  const closeModal = () => $('#modal').classList.add('hidden');

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

  async function onSignedIn(user) {
    state.user = user;
    $('#view-login').classList.add('hidden');
    $('#view-app').classList.remove('hidden');

    const initials = (user.first_name || user.username || 'U').slice(0, 1).toUpperCase();
    $('#user-btn').textContent = initials;
    $('#drop-name').textContent = user.first_name || 'Telegram user';
    $('#drop-handle').textContent = user.username ? '@' + user.username : '';

    await loadCategories();
    const mine = await api('/api/channels').catch(() => ({ channels: [] }));
    const active = mine.channels.filter((c) => c.enabled);
    if (!active.length) {
      // Nothing tracked yet — send them straight to channel selection.
      toast('Pick the deal channels you want DealRadar to read.', 'info', 6000);
      navigate('channels');
      loadAvailableChannels();
    } else {
      navigate('deals');
      refreshDeals(true);
      loadFacets();
      loadTrending();
    }
    loadStats();
  }

  /* ============================================================
     NAVIGATION
     ============================================================ */
  function navigate(page) {
    state.page = page;
    ['deals', 'channels', 'alerts'].forEach((p) => {
      $(`#page-${p}`).classList.toggle('hidden', p !== page);
    });
    $$('.navlink').forEach((n) => n.classList.toggle('active', n.dataset.nav === page));
    window.scrollTo({ top: 0, behavior: 'smooth' });
    if (page === 'alerts') loadAlerts();
    if (page === 'channels' && !state.availableChannels.length) loadAvailableChannels();
  }

  $$('[data-nav]').forEach((el) => el.addEventListener('click', () => navigate(el.dataset.nav)));

  /* user dropdown */
  $('#user-btn').addEventListener('click', (e) => {
    e.stopPropagation();
    $('#user-drop').classList.toggle('hidden');
  });
  document.addEventListener('click', () => $('#user-drop').classList.add('hidden'));
  $('#user-drop').addEventListener('click', (e) => e.stopPropagation());

  $$('#user-drop .drop-item').forEach((item) => item.addEventListener('click', async () => {
    const action = item.dataset.action;
    $('#user-drop').classList.add('hidden');
    if (action === 'logout') {
      await post('/api/auth/logout').catch(() => {});
      window.location.reload();
    } else if (action === 'theme') {
      const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      localStorage.setItem('dr-theme', next);
    } else if (action === 'status') {
      showStatus();
    }
  }));

  async function showStatus() {
    openModal('<p class="muted">Loading system status…</p>');
    try {
      const health = await api('/api/health');
      const sheetsInfo = health.checks.sheets || {};
      const ingestInfo = health.checks.ingest || {};
      openModal(`
        <div class="modal-head">
          <h2>System status</h2>
          <button class="btn btn-ghost btn-xs" data-close>Close</button>
        </div>
        <dl class="kv">
          <dt>Service</dt><dd>${escapeHtml(health.status)} · up ${Math.floor(health.uptime_seconds / 60)} min</dd>
          <dt>Database</dt><dd>${escapeHtml(health.checks.database)}</dd>
          <dt>Telegram API</dt><dd>${health.checks.telegram_configured ? 'configured' : 'not configured'}</dd>
          <dt>Google Sheets</dt><dd>${sheetsInfo.configured ? (sheetsInfo.connected ? 'connected' : 'configured, not connected') : 'not configured'}</dd>
          <dt>Rows in Sheets</dt><dd>${sheetsInfo.rows_tracked ?? 0}</dd>
          <dt>Last sheet flush</dt><dd>${escapeHtml(sheetsInfo.last_flush || 'never')}</dd>
          <dt>Ingest cycles</dt><dd>${ingestInfo.cycles ?? 0}</dd>
          <dt>Last sync</dt><dd>${ingestInfo.last_run_ago_seconds != null ? Math.floor(ingestInfo.last_run_ago_seconds / 60) + ' min ago' : 'not yet'}</dd>
          <dt>Last error</dt><dd>${escapeHtml(ingestInfo.last_error || sheetsInfo.last_error || 'none')}</dd>
        </dl>
        <p class="fineprint">Ping endpoint: <code>/api/ping</code> · API docs: <a href="/api/docs" target="_blank" rel="noopener">/api/docs</a></p>
      `);
    } catch (err) {
      openModal(`<p class="alert alert-error">${escapeHtml(err.message)}</p>`);
    }
  }

  /* ============================================================
     DEALS
     ============================================================ */
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
      <button class="chip ${state.filters.category === c.name ? 'active' : ''}" data-cat="${escapeHtml(c.name)}">
        ${escapeHtml(c.label)}
      </button>`).join('');
    $$('.chip', row).forEach((chip) => chip.addEventListener('click', () => {
      state.filters.category = chip.dataset.cat;
      state.filters.subcategory = '';
      renderCategoryChips();
      renderSubcategoryFilter();
      refreshDeals(true);
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

  function buildQuery() {
    const f = state.filters;
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
    params.set('sort', f.q ? f.sort : (f.sort === 'relevance' ? 'best' : f.sort));
    params.set('limit', state.limit);
    params.set('offset', state.offset);
    return params.toString();
  }

  function skeletons(n = 8) {
    return Array.from({ length: n }, () => '<div class="skel"></div>').join('');
  }

  async function refreshDeals(reset = false) {
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

      $('#btn-more').classList.toggle('hidden', state.offset + res.count >= res.total);
      if (!res.total) showEmpty();
    } catch (err) {
      if (err.name === 'AbortError') return; // superseded by a newer search — ignore
      if (err.status === 401) { window.location.reload(); return; }
      toast(err.message, 'err');
      $('#deal-grid').innerHTML = '';
    } finally {
      if (!reset) state.loading = false;
    }
  }

  function showEmpty() {
    const el = $('#empty-state');
    const hasQuery = !!state.filters.q;
    el.innerHTML = `
      <span class="emoji">${hasQuery ? '🔍' : '📭'}</span>
      <h3>${hasQuery ? 'No deals match that yet' : 'No deals stored yet'}</h3>
      <p>${hasQuery
        ? 'Try a broader word, drop the filters, or tick “Include untracked channels”. New deals arrive with every sync.'
        : 'Track a few deal channels, then hit Sync. The first fetch pulls recent history, after that it polls automatically.'}</p>
      <div style="margin-top:18px; display:flex; gap:9px; justify-content:center;">
        ${hasQuery
          ? '<button class="btn btn-soft" id="empty-clear">Clear search</button>'
          : '<button class="btn btn-primary" id="empty-channels">Choose channels</button>'}
        <button class="btn btn-soft" id="empty-sync">Sync now</button>
      </div>`;
    el.classList.remove('hidden');
    $('#empty-clear')?.addEventListener('click', () => {
      $('#search-input').value = '';
      state.filters.q = '';
      refreshDeals(true);
    });
    $('#empty-channels')?.addEventListener('click', () => navigate('channels'));
    $('#empty-sync')?.addEventListener('click', () => syncNow());
  }

  function dealCard(deal) {
    const badges = [];
    if (deal.discount_pct >= 10) badges.push(`<span class="badge badge-off">${deal.discount_pct}% OFF</span>`);
    if (deal.is_lowest) badges.push('<span class="badge badge-low">ALL-TIME LOW</span>');
    if (deal.repost_count > 2) badges.push(`<span class="badge badge-hot">${deal.repost_count}× POSTED</span>`);

    const media = deal.image_url
      ? `<img src="${escapeHtml(deal.image_url)}" alt="" loading="lazy"
             onerror="this.parentElement.innerHTML='<div class=\\'placeholder\\'>🛍️</div>'" />`
      : '<div class="placeholder">🛍️</div>';

    return `
      <article class="deal" data-id="${escapeHtml(deal.id)}">
        <div class="deal-media">
          ${media}
          <div class="badges">${badges.join('')}</div>
          ${deal.store ? `<span class="store-tag">${escapeHtml(deal.store)}</span>` : ''}
        </div>
        <div class="deal-body">
          <div class="deal-title" title="${escapeHtml(deal.title)}">${escapeHtml(deal.title)}</div>
          <div class="deal-price">
            <span class="price-now">${money(deal.price)}</span>
            ${deal.mrp ? `<span class="price-was">${money(deal.mrp)}</span>` : ''}
            ${deal.saving ? `<span class="price-save">save ${money(deal.saving)}</span>` : ''}
          </div>
          ${deal.coupon ? `<div><span class="coupon">🏷 ${escapeHtml(deal.coupon)}</span></div>` : ''}
          <div class="deal-meta">
            <span>${timeAgo(deal.posted_at)}</span>
            ${deal.repost_count > 1 ? `<span class="dot"></span><span class="reposts">seen in ${deal.repost_count} channels</span>` : ''}
            ${deal.channel_title ? `<span class="dot"></span><span>${escapeHtml(deal.channel_title)}</span>` : ''}
          </div>
        </div>
        <div class="deal-actions">
          <button class="btn btn-soft btn-sm" data-detail="${escapeHtml(deal.id)}">Details</button>
          ${deal.url ? `<a class="btn btn-primary btn-sm" href="${escapeHtml(deal.url)}" target="_blank" rel="noopener noreferrer nofollow">Buy now</a>` : ''}
        </div>
      </article>`;
  }

  function renderDeals(results, reset) {
    const grid = $('#deal-grid');
    const html = results.map(dealCard).join('');
    if (reset) grid.innerHTML = html; else grid.insertAdjacentHTML('beforeend', html);
    $('#empty-state').classList.toggle('hidden', results.length > 0 || state.total > 0);
    $$('[data-detail]', grid).forEach((btn) => {
      if (btn.dataset.bound) return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', () => showDealDetail(btn.dataset.detail));
    });
  }

  async function showDealDetail(id) {
    openModal('<p class="muted">Loading…</p>');
    try {
      const deal = await api(`/api/deals/${encodeURIComponent(id)}`);
      const history = deal.price_history || {};
      openModal(`
        <div class="modal-head">
          <h2 style="font-size:1.1rem">${escapeHtml(deal.title)}</h2>
          <button class="btn btn-ghost btn-xs" data-close>Close</button>
        </div>
        <div class="deal-price" style="margin-bottom:6px">
          <span class="price-now">${money(deal.price)}</span>
          ${deal.mrp ? `<span class="price-was">${money(deal.mrp)}</span>` : ''}
          ${deal.discount_pct ? `<span class="price-save">${deal.discount_pct}% off</span>` : ''}
        </div>
        ${deal.is_lowest ? '<p class="small" style="color:var(--gold);font-weight:700">📉 Lowest price we have recorded for this product</p>' : ''}
        ${(deal.flags || []).includes('suspicious_mrp') ? '<p class="small" style="color:var(--warn);font-weight:700">⚠️ The quoted MRP looks inflated versus this product’s price history</p>' : ''}
        <dl class="kv">
          <dt>Store</dt><dd>${escapeHtml(deal.store || '—')}</dd>
          <dt>Category</dt><dd>${escapeHtml(deal.category || '—')} › ${escapeHtml(deal.subcategory || '—')}</dd>
          ${deal.brand ? `<dt>Brand</dt><dd>${escapeHtml(deal.brand)}</dd>` : ''}
          ${deal.sizes ? `<dt>Sizes</dt><dd>${escapeHtml(deal.sizes)}</dd>` : ''}
          ${deal.coupon ? `<dt>Coupon</dt><dd><span class="coupon">${escapeHtml(deal.coupon)}</span></dd>` : ''}
          <dt>Posted</dt><dd>${timeAgo(deal.posted_at)} in ${escapeHtml(deal.channel_title || 'a channel')}</dd>
          <dt>Reposted</dt><dd>${deal.repost_count} channel${deal.repost_count === 1 ? '' : 's'}</dd>
          <dt>Expires</dt><dd>${deal.expires_at ? new Date(deal.expires_at * 1000).toLocaleString() : '—'}</dd>
          ${history.points ? `<dt>Price history</dt><dd>${history.points} points · low ${money(history.min)} · high ${money(history.max)}</dd>` : ''}
          <dt>Deal score</dt><dd>${deal.score ?? 0} / 100</dd>
        </dl>
        <details style="margin:14px 0">
          <summary class="muted small" style="cursor:pointer">Original channel post</summary>
          <pre style="white-space:pre-wrap;font-size:.8rem;color:var(--text-2);margin-top:9px">${escapeHtml(deal.raw_text || '')}</pre>
        </details>
        ${deal.url ? `<a class="btn btn-primary btn-block" href="${escapeHtml(deal.url)}" target="_blank" rel="noopener noreferrer nofollow">Open on ${escapeHtml(deal.store || 'store')}</a>` : ''}
      `);
    } catch (err) {
      openModal(`<p class="alert alert-error">${escapeHtml(err.message)}</p>`);
    }
  }

  async function loadTrending() {
    try {
      const res = await api('/api/deals/trending?limit=10');
      const wrap = $('#trending-wrap');
      if (!res.results.length) { wrap.classList.add('hidden'); return; }
      $('#trending-row').innerHTML = res.results.map((d) => `
        <a class="trend-card" href="${escapeHtml(d.url || '#')}" target="_blank" rel="noopener noreferrer nofollow">
          <div class="trend-title">${escapeHtml(d.title)}</div>
          <div class="trend-meta">
            <b>${money(d.price)}</b>
            <span class="reposts">${d.repost_count}× posted</span>
          </div>
        </a>`).join('');
      wrap.classList.remove('hidden');
    } catch { /* non-fatal */ }
  }

  async function loadFacets() {
    try {
      const facets = await api('/api/deals/facets');
      renderFacet('#f-stores', facets.stores, 'store');
      renderFacet('#f-brands', facets.brands, 'brand');
    } catch { /* non-fatal */ }
  }

  function renderFacet(selector, items, key) {
    const box = $(selector);
    if (!items || !items.length) {
      box.innerHTML = '<span class="muted small">No data yet</span>';
      return;
    }
    box.innerHTML = items.map((item) => `
      <button class="facet ${state.filters[key] === item.key ? 'active' : ''}" data-key="${escapeHtml(item.key)}">
        <span>${escapeHtml(item.key)}</span><span class="facet-count">${item.count}</span>
      </button>`).join('');
    $$('.facet', box).forEach((btn) => btn.addEventListener('click', () => {
      state.filters[key] = state.filters[key] === btn.dataset.key ? '' : btn.dataset.key;
      renderFacet(selector, items, key);
      refreshDeals(true);
    }));
  }

  async function loadStats() {
    try {
      const stats = await api('/api/stats');
      $('#statstrip').innerHTML = `
        <div class="stat"><div class="stat-value">${(stats.deals_live || 0).toLocaleString()}</div><div class="stat-label">Live deals</div></div>
        <div class="stat"><div class="stat-value">${(stats.deals_today || 0).toLocaleString()}</div><div class="stat-label">Added today</div></div>
        <div class="stat"><div class="stat-value">${(stats.channels || 0).toLocaleString()}</div><div class="stat-label">Channels watched</div></div>
        <div class="stat"><div class="stat-value">${stats.deal_ttl_hours}h</div><div class="stat-label">Deal retention</div></div>`;
      const last = stats.ingest && stats.ingest.last_run;
      $('#footer-status').textContent = last
        ? `Last sync ${timeAgo(last)} · polling every ${Math.round(stats.poll_interval_seconds / 60)} min`
        : 'Waiting for the first sync…';
    } catch { /* non-fatal */ }
  }

  /* search + filter wiring */
  $('#search-form').addEventListener('submit', (e) => {
    e.preventDefault();
    state.filters.q = $('#search-input').value.trim();
    refreshDeals(true);
  });

  let searchTimer;
  $('#search-input').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    const value = e.target.value.trim();
    searchTimer = setTimeout(() => {
      if (value === state.filters.q) return;
      state.filters.q = value;
      refreshDeals(true);
    }, 420);
  });

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

  async function syncNow() {
    const btn = $('#btn-sync');
    busy(btn, true);
    toast('Fetching new deals from Telegram…', 'info');
    try {
      const res = await post('/api/channels/sync');
      const added = (res.new || 0) + (res.merged || 0);
      toast(added
        ? `Synced: ${res.new} new deals, ${res.merged} matched to existing ones.`
        : 'Sync complete — no new deals in your channels yet.', 'ok');
      refreshDeals(true);
      loadFacets();
      loadTrending();
      loadStats();
    } catch (err) {
      toast(err.message, 'err');
    } finally { busy(btn, false); }
  }
  $('#btn-sync').addEventListener('click', syncNow);

  /* ============================================================
     CHANNELS
     ============================================================ */
  async function loadAvailableChannels() {
    const list = $('#channel-list');
    list.innerHTML = '<p class="muted">Reading your Telegram channel list…</p>';
    try {
      const res = await api('/api/channels/available');
      state.availableChannels = res.channels;
      state.selectedChannels = new Set(res.channels.filter((c) => c.tracked).map((c) => c.tg_id));
      renderChannels();
    } catch (err) {
      list.innerHTML = `<p class="alert alert-error">${escapeHtml(err.message)}</p>`;
    }
  }

  function renderChannels() {
    const filter = $('#channel-filter').value.trim().toLowerCase();
    const items = state.availableChannels.filter(
      (c) => !filter || c.title.toLowerCase().includes(filter) || (c.username || '').toLowerCase().includes(filter)
    );
    $('#channel-count').textContent =
      `${state.selectedChannels.size} selected · ${state.availableChannels.length} channels found`;

    if (!items.length) {
      $('#channel-list').innerHTML = `<p class="muted">${state.availableChannels.length
        ? 'No channels match that filter.'
        : 'No broadcast channels found on your account. Join some deal channels in Telegram, then hit Refresh.'}</p>`;
      return;
    }

    $('#channel-list').innerHTML = items.map((c) => `
      <label class="channel ${state.selectedChannels.has(c.tg_id) ? 'on' : ''}" data-id="${c.tg_id}">
        <input type="checkbox" ${state.selectedChannels.has(c.tg_id) ? 'checked' : ''} />
        <div class="channel-info">
          <div class="channel-name">${escapeHtml(c.title)}</div>
          <div class="channel-sub">
            ${c.username ? '@' + escapeHtml(c.username) : 'private channel'}
            ${c.participants ? ' · ' + c.participants.toLocaleString() + ' members' : ''}
          </div>
        </div>
      </label>`).join('');

    $$('.channel').forEach((el) => {
      el.querySelector('input').addEventListener('change', (e) => {
        const id = Number(el.dataset.id);
        if (e.target.checked) state.selectedChannels.add(id); else state.selectedChannels.delete(id);
        el.classList.toggle('on', e.target.checked);
        $('#channel-count').textContent =
          `${state.selectedChannels.size} selected · ${state.availableChannels.length} channels found`;
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
    try {
      const res = await api('/api/watchlists');
      if (!res.watchlists.length) {
        list.innerHTML = '<p class="muted">No alerts yet. Create one above, or search for something and use “Alert me about this search”.</p>';
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
            <div>
              <div class="alert-q">${escapeHtml(w.query)}</div>
              <div class="alert-filters">${bits.length ? escapeHtml(bits.join(' · ')) : 'no extra filters'} · ${w.alerts_sent} sent</div>
            </div>
            <div class="spacer"></div>
            <label class="check"><input type="checkbox" ${w.notify ? 'checked' : ''} data-toggle="${w.id}" /><span>Notify</span></label>
            <button class="btn btn-soft btn-xs" data-test="${w.id}">Test</button>
            <button class="btn btn-ghost btn-xs" data-del="${w.id}">Delete</button>
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
    } catch (err) { toast(err.message, 'err'); }
  });

  /* ============================================================
     BOOT
     ============================================================ */
  $('#modal').addEventListener('click', (e) => {
    if (e.target.dataset.close !== undefined || e.target.closest('[data-close]')) closeModal();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeModal();
    if (e.key === '/' && document.activeElement.tagName !== 'INPUT' && state.user) {
      e.preventDefault();
      $('#search-input').focus();
    }
  });

  (async function boot() {
    const saved = localStorage.getItem('dr-theme');
    if (saved) document.documentElement.dataset.theme = saved;

    try {
      const me = await api('/api/auth/me');
      if (me.authenticated) {
        await onSignedIn(me.user);
        return;
      }
    } catch { /* fall through to the login screen */ }

    $('#view-login').classList.remove('hidden');
    initAuthScreen();
  })();

  // Keep stats fresh while the tab is open.
  setInterval(() => { if (state.user && state.page === 'deals') loadStats(); }, 60000);
})();
