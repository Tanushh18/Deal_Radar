(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const TOKEN_KEY = 'dr-admin-token';
  let loginId = null;

  const token = () => { try { return sessionStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; } };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: { 'X-Admin-Token': token(), ...(options.body ? { 'Content-Type': 'application/json' } : {}) },
    });
    let data = null;
    try { data = await res.json(); } catch { /* empty body */ }
    if (!res.ok) {
      const err = new Error((data && data.detail) || `Request failed (${res.status})`);
      err.status = res.status;
      throw err;
    }
    return data;
  }
  const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });

  function show(el, text, kind) {
    el.textContent = text;
    el.className = `msg ${kind || ''}`;
    el.classList.toggle('hidden', !text);
  }
  function busy(form, on) {
    form.querySelectorAll('button, input').forEach((el) => { el.disabled = on; });
  }
  function step(name) {
    ['phone', 'code', 'password'].forEach((s) => $(`#step-${s}`).classList.toggle('hidden', s !== name));
  }
  const ago = (ts) => {
    if (!ts) return 'never';
    const m = Math.round((Date.now() / 1000 - ts) / 60);
    return m < 1 ? 'just now' : m < 60 ? `${m}m ago` : `${Math.round(m / 60)}h ago`;
  };

  async function refresh() {
    const s = await api('/api/admin/reader');
    const pill = $('#reader-status');
    if (s.account && s.connected) {
      pill.textContent = `● Reading as ${s.account.first_name || ''}${s.account.username ? ' (@' + s.account.username + ')' : ''}`;
      pill.className = 'status-pill ok';
    } else if (s.account) {
      pill.textContent = '● Session expired — log in again';
      pill.className = 'status-pill bad';
    } else {
      pill.textContent = '● Not connected';
      pill.className = 'status-pill bad';
    }
    const reading = s.channels.filter((c) => c.enabled).length;
    $('#channel-count').textContent = `· ${reading} reading · ${s.channels.length - reading} blocked`;
    $('#pause-sync').textContent = s.sync_paused ? 'Resume syncing' : 'Pause syncing';
    $('#pause-sync').classList.toggle('on', !!s.sync_paused);
    $('#pause-note').classList.toggle('hidden', !s.sync_paused);
    const tgPaused = !!(s.live && s.live.posting_paused);
    $('#tg-pause').textContent = tgPaused ? 'Resume posting to Telegram' : 'Pause posting to Telegram';
    $('#tg-pause').classList.toggle('on', tgPaused);
    $('#channels').innerHTML = s.channels.length ? s.channels.map((c) => `
      <tr class="${c.enabled ? '' : 'blocked'}">
        <td><b>${esc(c.title)}</b><br><span class="muted small">${c.username ? '@' + esc(c.username) : 'private'}</span></td>
        <td><span class="tag ${c.enabled ? 'on' : 'off'}">${c.enabled ? 'Reading' : 'Blocked'}</span>${
          c.stale ? ' <span class="tag off" title="No new deal from this channel in 10+ days">⚠ stale</span>' : ''}</td>
        <td class="num">${(c.participants || 0).toLocaleString('en-IN')}</td>
        <td class="num">${(c.live_deals || 0).toLocaleString('en-IN')}</td>
        <td>${ago(c.last_fetched_at)}</td>
        <td><button class="btn ${c.enabled ? 'btn-ghost' : 'btn-soft'} btn-xs" data-block="${c.tg_id}" data-blocked="${c.enabled ? '1' : '0'}">
          ${c.enabled ? 'Block' : 'Unblock'}</button></td>
      </tr>`).join('') : '<tr><td colspan="6" class="muted">No channels yet — connect the reader, then “Refresh from Telegram”.</td></tr>';
  }

  function fmtMinutes(seconds) {
    const m = Math.round(seconds / 60);
    return m % 60 === 0 && m >= 60 ? `${m / 60}h` : `${m}m`;
  }
  async function loadPollInterval() {
    const r = await api('/api/admin/reader/poll-interval');
    $('#poll-minutes').value = Math.round(r.seconds / 60);
    $('#poll-note').textContent = r.is_override
      ? `Custom — the built-in default is ${fmtMinutes(r.default_seconds)}.`
      : `Default (${fmtMinutes(r.default_seconds)}) — no override set.`;
  }
  $('#poll-save').addEventListener('click', async () => {
    const minutes = Number($('#poll-minutes').value);
    if (!Number.isFinite(minutes) || minutes <= 0) { show($('#channel-msg'), 'Enter a number of minutes.', 'err'); return; }
    try {
      const r = await post('/api/admin/reader/poll-interval', { seconds: Math.round(minutes * 60) });
      await loadPollInterval();
      show($('#channel-msg'), `Ingest cycle set to every ${fmtMinutes(r.seconds)} — takes effect on the next cycle, no restart needed.`, 'ok');
    } catch (err) { show($('#channel-msg'), err.message, 'err'); }
  });
  $('#poll-reset').addEventListener('click', async () => {
    try {
      const r = await post('/api/admin/reader/poll-interval/reset', {});
      await loadPollInterval();
      show($('#channel-msg'), `Back to the default — every ${fmtMinutes(r.seconds)}.`, 'ok');
    } catch (err) { show($('#channel-msg'), err.message, 'err'); }
  });

  let presets = {};
  async function loadPriority() {
    const r = await api('/api/admin/reader/priority');
    presets = r.presets;
    const rule = r.rule;
    $('#pr-preset').innerHTML = Object.entries(presets).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join('')
      + '<option value="custom">Custom</option>';
    $('#pr-preset').value = presets[rule.preset] ? rule.preset : 'custom';
    $('#pr-label').value = rule.label;
    $('#pr-cats').innerHTML = r.categories.map((c) => `<label class="catpick"><input type="checkbox" value="${esc(c)}"
      ${rule.categories.includes(c) ? 'checked' : ''}/> ${esc(c)}</label>`).join('');
    $('#pr-keywords').value = rule.keywords.join(', ');
    $('#pr-stores').value = rule.stores.join(', ');
    const empty = !rule.categories.length && !rule.keywords.length && !rule.stores.length;
    $('#priority-now').textContent = empty ? 'Neutral — nothing prioritised' : `● ${rule.label} on top`;
    $('#priority-now').className = `status-pill ${empty ? '' : 'ok'}`;
  }
  $('#pr-preset').addEventListener('change', async (e) => {
    if (e.target.value === 'custom') return;
    try {
      await post('/api/admin/reader/priority', { preset: e.target.value });
      await loadPriority();
      show($('#priority-msg'), `Saved — ${presets[e.target.value]} deals now lead the site and app.`, 'ok');
    } catch (err) { show($('#priority-msg'), err.message, 'err'); }
  });
  $('#priority-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const list = (v) => v.split(',').map((x) => x.trim()).filter(Boolean);
    try {
      const r = await post('/api/admin/reader/priority', {
        preset: 'custom',
        label: $('#pr-label').value.trim() || 'Custom',
        categories: [...document.querySelectorAll('#pr-cats input:checked')].map((i) => i.value),
        keywords: list($('#pr-keywords').value),
        stores: list($('#pr-stores').value),
      });
      await loadPriority();
      show($('#priority-msg'), `Saved “${r.rule.label}”. It applies on the next page load.`, 'ok');
    } catch (err) { show($('#priority-msg'), err.message, 'err'); }
  });

  async function loadDataSource() {
    const d = await api('/api/admin/reader/data-source');
    $('#serve-now').textContent = d.serve_mode === 'sheet' ? '● Sheet (testing)' : '● DB';
    $('#serve-now').className = `status-pill ${d.serve_mode === 'sheet' ? 'bad' : 'ok'}`;
    $('#storage-now').textContent = d.storage_mode === 'turso' ? '● Turso' : '● Local SQLite';
    $('#storage-now').className = `status-pill ${d.storage_mode === 'turso' ? 'ok' : 'bad'}`;
  }
  async function setServeMode(mode) {
    try {
      await post('/api/admin/reader/data-source/serve', { mode });
      await loadDataSource();
      show($('#data-source-msg'), `Now serving deals from ${mode === 'sheet' ? 'the Sheet' : 'the DB'}.`, 'ok');
    } catch (err) { show($('#data-source-msg'), err.message, 'err'); }
  }
  async function setStorageMode(mode) {
    try {
      await post('/api/admin/reader/data-source/storage', { mode });
      await loadDataSource();
      show($('#data-source-msg'), `Storage switched to ${mode === 'turso' ? 'Turso' : 'local SQLite'}. New backend starts empty until it catches up.`, 'ok');
    } catch (err) { show($('#data-source-msg'), err.message, 'err'); }
  }
  $('#serve-db').addEventListener('click', () => setServeMode('db'));
  $('#serve-sheet').addEventListener('click', () => setServeMode('sheet'));
  $('#storage-sqlite').addEventListener('click', () => setStorageMode('sqlite'));

  async function unlock() {
    try {
      await refresh();
      await loadPriority();
      await loadDataSource();
      await loadPollInterval();
      await loadPushStatus().catch(() => {});
      await loadSaleEvents().catch(() => {});
      $('#gate').classList.add('hidden');
      $('#panel').classList.remove('hidden');
    } catch (err) {
      show($('#gate-msg'), err.status === 403 ? 'Wrong admin token.' : err.message, 'err');
      try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* private mode */ }
    }
  }

  $('#gate-form').addEventListener('submit', (e) => {
    e.preventDefault();
    try { sessionStorage.setItem(TOKEN_KEY, $('#token').value.trim()); } catch { /* private mode */ }
    unlock();
  });

  async function finished(res, form) {
    if (res.status === 'password_required') { step('password'); $('#password').focus(); return; }
    step('phone');
    form.reset();
    show($('#login-msg'), 'Connected. This account now reads all channels.', 'ok');
    if (res.session) {
      $('#session').value = `TELEGRAM_SESSION=${res.session}`;
      $('#session-box').classList.remove('hidden');
    }
    await refresh();
  }

  $('#step-phone').addEventListener('submit', async (e) => {
    e.preventDefault();
    busy(e.target, true);
    try {
      const res = await post('/api/admin/reader/send-code', { phone: $('#phone').value });
      loginId = res.login_id;
      step('code');
      show($('#login-msg'), `Code sent to ${res.phone} — check your Telegram app (not SMS).`, 'ok');
      $('#code').focus();
    } catch (err) { show($('#login-msg'), err.message, 'err'); } finally { busy(e.target, false); }
  });

  $('#step-code').addEventListener('submit', async (e) => {
    e.preventDefault();
    busy(e.target, true);
    try { await finished(await post('/api/admin/reader/verify-code', { login_id: loginId, code: $('#code').value.trim() }), e.target); }
    catch (err) { show($('#login-msg'), err.message, 'err'); } finally { busy(e.target, false); }
  });

  $('#step-password').addEventListener('submit', async (e) => {
    e.preventDefault();
    busy(e.target, true);
    try { await finished(await post('/api/admin/reader/verify-password', { login_id: loginId, password: $('#password').value }), e.target); }
    catch (err) { show($('#login-msg'), err.message, 'err'); } finally { busy(e.target, false); }
  });

  $('#copy-session').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('#session').value); $('#copy-session').textContent = 'Copied'; }
    catch { $('#session').select(); }
  });

  $('#add-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    busy(e.target, true);
    try {
      const res = await post('/api/admin/reader/channels', { username: $('#new-channel').value.trim() });
      show($('#channel-msg'), `Added ${res.channel.title}. Deals appear after the next sync.`, 'ok');
      e.target.reset();
      await refresh();
    } catch (err) { show($('#channel-msg'), err.message, 'err'); } finally { busy(e.target, false); }
  });

  $('#channels').addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-block]');
    if (!btn) return;
    const blocking = btn.dataset.blocked === '1';
    btn.disabled = true;
    try {
      await post(`/api/admin/reader/channels/${btn.dataset.block}/block`, { blocked: blocking });
      show($('#channel-msg'), blocking ? 'Blocked — its deals are hidden from the site and app.' : 'Unblocked — it is read from the next sync.', 'ok');
      await refresh();
    } catch (err) { show($('#channel-msg'), err.message, 'err'); btn.disabled = false; }
  });

  $('#tg-pause').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    const pausing = !btn.classList.contains('on');
    btn.disabled = true;
    try {
      await post('/api/admin/reader/telegram-pause', { paused: pausing });
      await refresh();
      show($('#channel-msg'), pausing
        ? 'Telegram posting paused — the site/app keep working, nothing new goes to the channel.'
        : 'Telegram posting resumed.', 'ok');
    } catch (err) { show($('#channel-msg'), err.message, 'err'); } finally { btn.disabled = false; }
  });

  $('#tg-test').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const r = await post('/api/admin/reader/telegram-test');
      const live = r.live && r.live.connected ? 'live listener connected' : 'live listener NOT connected yet';
      show($('#channel-msg'), `Test post sent to ${r.channel} (message #${r.message_id}); ${live}.`, 'ok');
    } catch (err) { show($('#channel-msg'), err.message, 'err'); } finally { btn.disabled = false; }
  });

  $('#refresh').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = 'Refreshing…';
    try {
      const r = await post('/api/admin/reader/refresh');
      show($('#channel-msg'), `Following ${r.followed} channels on Telegram: ${r.added} new, ${r.removed} left, ${r.blocked} blocked.`, 'ok');
      await refresh();
    } catch (err) { show($('#channel-msg'), err.message, 'err'); } finally { btn.disabled = false; btn.textContent = 'Refresh from Telegram'; }
  });

  $('#sync').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = 'Syncing…';
    try {
      const r = await post('/api/admin/reader/sync');
      show($('#channel-msg'), `Sync done: ${r.new || 0} new, ${r.merged || 0} merged, ${r.fetched || 0} posts read.`, 'ok');
      await refresh();
    } catch (err) { show($('#channel-msg'), err.message, 'err'); } finally { btn.disabled = false; btn.textContent = 'Sync now'; }
  });

  $('#pause-sync').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    const pausing = !btn.classList.contains('on');
    btn.disabled = true;
    try {
      await post('/api/admin/reader/pause', { paused: pausing });
      show($('#channel-msg'), pausing
        ? 'Syncing paused — the automatic 5-min sync is off. "Sync now" still works.'
        : 'Syncing resumed.', 'ok');
      await refresh();
    } catch (err) { show($('#channel-msg'), err.message, 'err'); } finally { btn.disabled = false; }
  });

  /* ---------------- send notification: visual deal picker ---------------- */
  let pickedDeal = null;
  let notifySearchTimer = null;
  let dealsById = {};

  function dealOptionHtml(d) {
    const thumb = d.image_url
      ? `<img src="${esc(d.image_url)}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'deal-option-thumb-placeholder'}))" />`
      : '<div class="deal-option-thumb-placeholder"></div>';
    const discount = d.discount_pct ? `<span class="deal-option-discount">${Math.round(d.discount_pct)}% off</span>` : '';
    return `<button type="button" class="deal-option" data-pick="${d.id}">
      ${thumb}
      <span class="deal-option-body">
        <span class="deal-option-title">${esc(d.title)}</span>
        <span class="deal-option-meta">₹${Math.round(d.price || 0)} ${discount} ${d.store ? '· ' + esc(d.store) : ''}</span>
      </span>
    </button>`;
  }

  function renderDealOptions(deals) {
    const box = $('#notify-deal-results');
    dealsById = {};
    for (const d of deals) dealsById[d.id] = d;
    if (!deals.length) {
      box.innerHTML = '<div class="deal-empty">No matching deals.</div>';
      box.classList.remove('hidden');
      return;
    }
    box.innerHTML = deals.map(dealOptionHtml).join('');
    box.classList.remove('hidden');
  }

  async function loadTopDeals() {
    try {
      const r = await api('/api/deals?sort=best&limit=8');
      renderDealOptions(r.deals || []);
    } catch { /* leave hidden */ }
  }

  $('#notify-deal-search').addEventListener('focus', () => {
    if (!$('#notify-deal-search').value.trim() && !$('#notify-deal-results').innerHTML) loadTopDeals();
    else if ($('#notify-deal-results').innerHTML) $('#notify-deal-results').classList.remove('hidden');
  });

  $('#notify-deal-search').addEventListener('input', (e) => {
    clearTimeout(notifySearchTimer);
    const q = e.target.value.trim();
    if (!q) { loadTopDeals(); return; }
    notifySearchTimer = setTimeout(async () => {
      try {
        const r = await api(`/api/deals/suggest?q=${encodeURIComponent(q)}&limit=8`);
        renderDealOptions(r.deals || []);
      } catch { $('#notify-deal-results').classList.add('hidden'); }
    }, 250);
  });

  $('#notify-deal-results').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-pick]');
    if (!btn) return;
    const d = dealsById[btn.dataset.pick];
    if (!d) return;
    pickedDeal = d;
    const discount = d.discount_pct ? `${Math.round(d.discount_pct)}% off · ` : '';
    $('#notify-deal-picked').innerHTML = `
      ${d.image_url ? `<img src="${esc(d.image_url)}" alt="" />` : ''}
      <span class="deal-picked-body">
        <span class="deal-picked-title">${esc(d.title)}</span>
        <span class="deal-picked-meta">${discount}₹${Math.round(d.price || 0)} ${d.store ? '· ' + esc(d.store) : ''}</span>
      </span>`;
    $('#notify-deal-picked').classList.remove('hidden');
    $('#notify-deal-clear').classList.remove('hidden');
    $('#notify-deal-results').classList.add('hidden');
    $('#notify-deal-search').value = '';
    if (!$('#notify-title').value.trim()) $('#notify-title').value = d.title;
  });

  $('#notify-deal-clear').addEventListener('click', () => {
    pickedDeal = null;
    $('#notify-deal-picked').classList.add('hidden');
    $('#notify-deal-picked').innerHTML = '';
    $('#notify-deal-clear').classList.add('hidden');
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('#notify-deal-search') && !e.target.closest('#notify-deal-results')) {
      $('#notify-deal-results').classList.add('hidden');
    }
  });

  $('#notify-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector('button[type=submit]');
    const title = $('#notify-title').value.trim();
    const body = $('#notify-body').value.trim();
    if (!title || !body) { show($('#notify-msg'), 'Write a title and a message.', 'err'); return; }
    btn.disabled = true;
    try {
      const r = await post('/api/admin/reader/broadcast', { title, body, deal_id: pickedDeal?.id || null });
      show($('#notify-msg'), deliveryText(r), r.accepted ? 'ok' : 'err');
      loadPushStatus().catch(() => {});
      e.target.reset();
      pickedDeal = null;
      $('#notify-deal-picked').classList.add('hidden');
      $('#notify-deal-clear').classList.add('hidden');
    } catch (err) { show($('#notify-msg'), err.message, 'err'); } finally { btn.disabled = false; }
  });

  /* ---------------- hot-deal pushes ---------------- */
  // FCM error codes -> what the admin should actually do about them.
  const PUSH_FIXES = {
    FcmNotConfigured: 'FCM_SERVICE_ACCOUNT_JSON is not set on the server (Render → Environment).',
    FcmAuthFailed: 'Google rejected the Firebase key — check FCM_SERVICE_ACCOUNT_JSON (rotated or deleted key?).',
    SENDER_ID_MISMATCH: 'the Firebase key is from a different project than the app.',
    THIRD_PARTY_AUTH_ERROR: 'Firebase could not authenticate with Google’s delivery service.',
    UNREGISTERED: 'those phones uninstalled the app (their tokens were removed).',
    QUOTA_EXCEEDED: 'Firebase rate-limited the send — try again in a bit.',
    UNAVAILABLE: 'Firebase was briefly unavailable — try again.',
    RequestFailed: 'Firebase could not be reached from the server.',
  };
  const since = (ts) => new Date(ts * 1000).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
  function deliveryText(r) {
    const active = r.active_devices ?? r.devices;
    const feed = `Saved to every phone’s feed · ${active} active phone${active === 1 ? '' : 's'}`
      + (r.active_since ? ` (opened since ${since(r.active_since)})` : '') + '.';
    if (!r.tokens) {
      return `${feed} ⚠️ No phone can get an instant push yet, so nothing buzzed — they’ll see it in the app’s feed. `
        + 'A phone gets push once it opens the app (twice after an update) with notifications allowed.';
    }
    let text = `${feed} Push: ${r.accepted}/${r.tokens} delivered via Firebase.`;
    const errs = Object.entries(r.errors || {});
    if (errs.length) {
      text += ' Failed: ' + errs.map(([code, n]) => `${n}× ${code}${PUSH_FIXES[code] ? ' — ' + PUSH_FIXES[code] : ''}`).join('; ');
    }
    return text;
  }
  const when = (ts) => new Date(ts * 1000).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });

  async function loadPushStatus() {
    const s = await api('/api/admin/reader/push-status');
    const pill = $('#push-pill');
    pill.textContent = !s.enabled ? '● Off (BROADCAST_HOT_DEAL=false)'
      : s.quiet_now ? `● Quiet hours (${s.quiet_hours} IST)` : `● On · ${s.pushes_per_cycle} per cycle`;
    pill.className = `status-pill ${s.enabled && !s.quiet_now ? 'ok' : ''}`;
    $('#push-stats').textContent = `${s.active_devices} active phones (opened since ${since(s.active_since)})`
      + ` · ${s.reachable} can receive push · deals need a score of ${s.min_score}+`;
    const warn = $('#push-warning');
    if (s.active_devices && !s.reachable) {
      show(warn, '⚠️ Active phones have no Firebase push token yet, so none will buzz instantly. Each phone needs to '
        + 'open the app (twice after an update) with notifications allowed.', 'err');
    } else {
      show(warn, '', '');
    }
    const plan = (s.planned || []).map((p) => {
      const mins = Math.round((p.fires_at - Date.now() / 1000) / 60);
      return p.result === 'pending' ? `#${p.slot + 1} in ~${Math.max(0, mins)} min`
        : `#${p.slot + 1} ${p.result}${p.why ? ' (' + p.why + ')' : ''}`;
    });
    $('#push-plan').textContent = plan.length ? `This cycle: ${plan.join(' · ')}` : 'Nothing queued yet — the next ingest cycle plans the next pushes.';
    $('#push-recent').innerHTML = (s.recent || []).map((r) => `
      <tr><td>${esc(r.title)}${r.women ? ' <span class="tag on">women</span>' : ''}</td>
        <td class="num">${r.accepted}/${r.tokens}</td><td>${when(r.sent_at)}</td></tr>`).join('')
      || '<tr><td class="muted">No hot-deal pushes sent yet.</td></tr>';
  }
  $('#push-refresh').addEventListener('click', () => loadPushStatus().catch((err) => show($('#push-msg'), err.message, 'err')));
  $('#push-now').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const r = await post('/api/admin/reader/push-now', {});
      if (r.status === 'sent') show($('#push-msg'), `“${r.title}” — ${deliveryText(r)}`, r.accepted ? 'ok' : 'err');
      else show($('#push-msg'), `Nothing sent: ${r.why}.`, 'err');
      await loadPushStatus();
    } catch (err) { show($('#push-msg'), err.message, 'err'); } finally { btn.disabled = false; }
  });

  /* -------------------- Upcoming sales calendar -------------------- */
  function fmtDay(ts) {
    return ts ? new Date(ts * 1000).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '—';
  }
  function toDateInput(ts) {
    return ts ? new Date(ts * 1000).toISOString().slice(0, 10) : '';
  }
  function fromDateInput(value) {
    return value ? Math.floor(new Date(`${value}T00:00:00Z`).getTime() / 1000) : null;
  }
  async function loadSaleEvents() {
    const { events } = await api('/api/admin/reader/sale-events');
    $('#sale-events-list').innerHTML = events.length ? events.map((e) => `
      <div class="sale-event-row">
        <div class="sev-main">
          <div class="sev-name">${esc(e.name)}
            <span class="sev-badge ${e.approximate ? '' : 'confirmed'}">${e.approximate ? 'approx' : 'confirmed'}</span>
            ${e.heads_up_posted ? '<span class="sev-badge posted">posted</span>' : ''}
          </div>
          <div class="sev-meta">${esc(e.store || '—')} · ${fmtDay(e.starts_at)}${e.ends_at ? ' – ' + fmtDay(e.ends_at) : ''}</div>
          ${e.hype ? `<div class="sev-hype">“${esc(e.hype)}”</div>` : ''}
        </div>
        <button class="btn btn-soft btn-xs" data-se-edit="${e.id}">Edit</button>
        <button class="btn btn-soft btn-xs" data-se-hype="${e.id}">Regen AI blurb</button>
        <button class="btn btn-soft btn-xs" data-se-post="${e.id}" ${e.heads_up_posted ? 'disabled' : ''}>Post now</button>
        <button class="btn btn-ghost btn-xs" data-se-delete="${e.id}">Delete</button>
      </div>`).join('') : '<p class="muted">No upcoming sales — add one below.</p>';
    window._saleEvents = events;
  }
  function resetSaleForm() {
    $('#se-id').value = '';
    $('#se-name').value = '';
    $('#se-store').value = '';
    $('#se-start').value = '';
    $('#se-end').value = '';
    $('#se-approx').checked = true;
    $('#se-save').textContent = 'Save event';
  }
  $('#se-cancel-edit').addEventListener('click', resetSaleForm);
  $('#sale-event-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
      id: $('#se-id').value || undefined,
      name: $('#se-name').value.trim(),
      store: $('#se-store').value.trim().toLowerCase(),
      starts_at: fromDateInput($('#se-start').value),
      ends_at: fromDateInput($('#se-end').value),
      approximate: $('#se-approx').checked,
    };
    if (!payload.name) { show($('#sale-events-msg'), 'Name is required.', 'err'); return; }
    try {
      await post('/api/admin/reader/sale-events', payload);
      resetSaleForm();
      await loadSaleEvents();
      show($('#sale-events-msg'), 'Saved.', 'ok');
    } catch (err) { show($('#sale-events-msg'), err.message, 'err'); }
  });
  $('#sale-events-list').addEventListener('click', async (e) => {
    const editId = e.target.dataset.seEdit;
    const hypeId = e.target.dataset.seHype;
    const postId = e.target.dataset.sePost;
    const delId = e.target.dataset.seDelete;
    try {
      if (editId) {
        const ev = (window._saleEvents || []).find((x) => x.id === editId);
        if (!ev) return;
        $('#se-id').value = ev.id;
        $('#se-name').value = ev.name;
        $('#se-store').value = ev.store;
        $('#se-start').value = toDateInput(ev.starts_at);
        $('#se-end').value = toDateInput(ev.ends_at);
        $('#se-approx').checked = !!ev.approximate;
        $('#se-save').textContent = 'Update event';
        $('#se-name').scrollIntoView({ behavior: 'smooth', block: 'center' });
      } else if (hypeId) {
        e.target.disabled = true;
        await post(`/api/admin/reader/sale-events/${hypeId}/hype`, {});
        await loadSaleEvents();
        show($('#sale-events-msg'), 'New AI blurb generated.', 'ok');
      } else if (postId) {
        e.target.disabled = true;
        await post(`/api/admin/reader/sale-events/${postId}/post-now`, {});
        await loadSaleEvents();
        show($('#sale-events-msg'), 'Posted to Telegram.', 'ok');
      } else if (delId) {
        if (!confirm('Delete this event?')) return;
        await api(`/api/admin/reader/sale-events/${delId}`, { method: 'DELETE' });
        await loadSaleEvents();
        show($('#sale-events-msg'), 'Deleted.', 'ok');
      }
    } catch (err) {
      show($('#sale-events-msg'), err.message, 'err');
      await loadSaleEvents();
    }
  });

  if (token()) unlock();
})();
