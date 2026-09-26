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

  // Paint one status in the top strip chip + the Overview tile. kind: 'ok' | 'bad' | 'warn' | ''.
  function setStatus(chipId, chipText, tileId, tileText, kind) {
    const chip = chipId && $(chipId);
    if (chip) { chip.textContent = chipText; chip.className = `chip ${kind || ''}`; }
    const tile = tileId && $(tileId);
    if (tile) { tile.textContent = tileText; tile.className = `tile-value ${kind || ''}`; }
  }

  async function refresh() {
    const s = await api('/api/admin/reader');
    const pill = $('#reader-status');
    const connected = !!(s.account && s.connected);
    if (connected) {
      const who = `${s.account.first_name || ''}${s.account.username ? ' (@' + s.account.username + ')' : ''}`;
      pill.textContent = `● Reading as ${who}`;
      pill.className = 'status-pill ok';
      setStatus('#st-reader', '● Reader connected', '#gl-reader', `Connected as ${who}`, 'ok');
    } else if (s.account) {
      pill.textContent = '● Session expired — log in again';
      pill.className = 'status-pill bad';
      setStatus('#st-reader', '● Reader session expired', '#gl-reader', 'Session expired — log in again', 'bad');
    } else {
      pill.textContent = '● Not connected';
      pill.className = 'status-pill bad';
      setStatus('#st-reader', '● Reader not connected', '#gl-reader', 'Not connected', 'bad');
    }
    // The login form is only needed when the account isn't connected; keep it folded away otherwise.
    const loginMidway = !$('#step-code').classList.contains('hidden') || !$('#step-password').classList.contains('hidden');
    if (!loginMidway) $('#reader-login').open = !connected;
    const reading = s.channels.filter((c) => c.enabled).length;
    const stale = s.channels.filter((c) => c.enabled && c.stale).length;
    $('#channel-count').textContent = `· ${reading} reading · ${s.channels.length - reading} blocked`;
    setStatus(null, '', '#gl-channels', `${reading} reading · ${s.channels.length - reading} blocked${stale ? ` · ${stale} stale` : ''}`,
      reading ? (stale ? 'warn' : '') : 'bad');
    $('#pause-sync').textContent = s.sync_paused ? 'Resume syncing' : 'Pause syncing';
    $('#pause-sync').classList.toggle('on', !!s.sync_paused);
    $('#pause-note').classList.toggle('hidden', !s.sync_paused);
    $('#sync-pill').textContent = s.sync_paused ? '⏸ Paused' : '● Running';
    $('#sync-pill').className = `status-pill ${s.sync_paused ? 'warn' : 'ok'}`;
    setStatus('#st-sync', s.sync_paused ? '⏸ Fetching paused' : '● Fetching on', '#gl-sync',
      s.sync_paused ? 'Paused — showing existing deals' : 'Running', s.sync_paused ? 'warn' : 'ok');
    const tgPaused = !!(s.live && s.live.posting_paused);
    $('#tg-pause').textContent = tgPaused ? 'Resume posting to Telegram' : 'Pause posting to Telegram';
    $('#tg-pause').classList.toggle('on', tgPaused);
    $('#tg-pill').textContent = tgPaused ? '⏸ Paused' : '● Posting';
    $('#tg-pill').className = `status-pill ${tgPaused ? 'warn' : 'ok'}`;
    setStatus('#st-tg', tgPaused ? '⏸ Telegram posts paused' : '● Telegram posts on', '#gl-tg',
      tgPaused ? 'Paused' : 'On', tgPaused ? 'warn' : 'ok');
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

  /* ---------------- How often to fetch deals (poll interval: manual / auto) ---------------- */
  // Seconds -> "1.5 min" / "10 min" (UI always talks in minutes).
  function fmtMin(seconds) {
    const m = Math.round((Number(seconds) || 0) / 6) / 10;
    return `${Number.isInteger(m) ? m : m.toFixed(1)} min`;
  }
  const toMinutes = (seconds) => Math.round((Number(seconds) || 0) / 30) / 2; // nearest half-minute
  let poll = null;          // last GET body
  let pollDirty = false;    // user is editing manual value — don't overwrite on auto-refresh
  let pollAutoDirty = false;
  const pollBounds = () => ({
    min: Math.max(1, toMinutes(poll?.min_seconds ?? 60)),
    max: toMinutes(poll?.max_seconds ?? 3600) || 60,
  });

  function renderPoll(r) {
    poll = r || {};
    const mode = poll.mode === 'auto' ? 'auto' : 'manual';
    const { min, max } = pollBounds();
    ['#poll-range', '#poll-minutes', '#poll-auto-min', '#poll-auto-max'].forEach((id) => { $(id).min = min; $(id).max = max; });
    document.querySelectorAll('#poll-mode [data-mode]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === mode)));
    // Auto only makes sense when the server knows about it.
    $('#poll-mode [data-mode="auto"]').disabled = !poll.auto && mode !== 'auto';
    $('#poll-mode [data-mode="auto"]').title = poll.auto || mode === 'auto' ? '' : 'Needs the latest server update';
    $('#poll-manual').classList.toggle('hidden', mode !== 'manual');
    $('#poll-auto').classList.toggle('hidden', mode !== 'auto');

    const effect = `Every ${fmtMin(poll.seconds)}`;
    $('#poll-effect').textContent = `In effect now: every ${fmtMin(poll.seconds)}${mode === 'auto' ? ' (auto)' : ''}`;
    $('#poll-effect').className = 'status-pill ok';
    setStatus('#st-poll', `⏱ ${effect.toLowerCase()}${mode === 'auto' ? ' · auto' : ''}`, '#gl-poll',
      `${effect}${mode === 'auto' ? ' (Auto)' : ' (Manual)'}`, '');

    // Manual
    const manualSec = poll.manual_seconds ?? poll.seconds;
    if (!pollDirty) {
      $('#poll-minutes').value = toMinutes(manualSec);
      $('#poll-range').value = toMinutes(manualSec);
    }
    if (poll.default_seconds) $('#poll-reset').textContent = `Reset to default (${fmtMin(poll.default_seconds)})`;
    $('#poll-note').textContent = poll.default_seconds == null ? ''
      : poll.is_override ? `Custom value — the built-in default is ${fmtMin(poll.default_seconds)}.`
        : `Using the default (${fmtMin(poll.default_seconds)}).`;

    // Auto
    const a = poll.auto;
    if (a && !pollAutoDirty) {
      if (a.min_seconds) $('#poll-auto-min').value = toMinutes(a.min_seconds);
      if (a.max_seconds) $('#poll-auto-max').value = toMinutes(a.max_seconds);
    }
    $('#poll-auto-readout').classList.toggle('hidden', !a);
    if (a) {
      $('#poll-auto-now').textContent = `Right now: every ${fmtMin(poll.seconds)}`
        + (a.decided_at ? ` · decided ${ago(a.decided_at)}` : '');
      $('#poll-auto-reason').textContent = a.reason || '';
      const sg = a.signals || {};
      const chips = [];
      const n = (v) => Number(v || 0).toLocaleString('en-IN');
      if (sg.live_deals != null) chips.push(`Live deals <b>${n(sg.live_deals)}</b>${sg.target_live ? ` / target ${n(sg.target_live)}` : ''}`);
      if (sg.app_users != null || sg.web_users != null) {
        chips.push(`People on app <b>${n(sg.app_users)}</b> · site <b>${n(sg.web_users)}</b>`);
      } else if (sg.active_users != null) chips.push(`People active <b>${n(sg.active_users)}</b>`);
      if (sg.new_per_cycle != null) chips.push(`New deals per cycle <b>${n(Math.round(sg.new_per_cycle * 10) / 10)}</b>`);
      $('#poll-signals').innerHTML = chips.map((c) => `<span class="stat-chip">${c}</span>`).join('');
    }

    // Telegram flood-wait (applies in both modes)
    let fw = a && a.signals && Number(a.signals.flood_wait_until);
    if (fw && fw > 1e12) fw /= 1000; // tolerate milliseconds
    const flood = $('#poll-flood');
    if (fw && fw > Date.now() / 1000) {
      const until = new Date(fw * 1000).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
      flood.textContent = `⚠️ Telegram asked us to slow down — fetching is held at ≥10 min until ${until}.`;
      flood.classList.remove('hidden');
      $('#st-poll').className = 'chip warn';
    } else {
      flood.classList.add('hidden');
    }
  }

  async function loadPollInterval() {
    renderPoll(await api('/api/admin/reader/poll-interval'));
  }

  function readMinutes(el, label) {
    const v = Number(el.value);
    const { min, max } = pollBounds();
    if (!el.value || !Number.isFinite(v) || v < min || v > max) {
      throw new Error(`${label}: enter ${min}–${max} minutes.`);
    }
    return Math.round(v * 60);
  }

  $('#poll-range').addEventListener('input', () => { pollDirty = true; $('#poll-minutes').value = $('#poll-range').value; });
  $('#poll-minutes').addEventListener('input', () => {
    pollDirty = true;
    if ($('#poll-minutes').value) $('#poll-range').value = $('#poll-minutes').value;
  });
  ['#poll-auto-min', '#poll-auto-max'].forEach((id) => $(id).addEventListener('input', () => { pollAutoDirty = true; }));

  $('#poll-mode').addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-mode]');
    if (!btn || btn.getAttribute('aria-pressed') === 'true') return;
    const mode = btn.dataset.mode;
    const buttons = $('#poll-mode').querySelectorAll('button');
    buttons.forEach((b) => { b.disabled = true; });
    try {
      const body = { mode };
      if (mode === 'auto' && poll && poll.auto) {
        body.auto_min_seconds = poll.auto.min_seconds;
        body.auto_max_seconds = poll.auto.max_seconds;
      }
      const r = await post('/api/admin/reader/poll-mode', body);
      pollDirty = false; pollAutoDirty = false;
      if (r && r.seconds != null) renderPoll(r); else await loadPollInterval();
      show($('#poll-msg'), mode === 'auto'
        ? 'Switched to Auto — the server now picks the speed each cycle.'
        : 'Switched to Manual — the server uses your fixed value.', 'ok');
    } catch (err) { show($('#poll-msg'), err.message, 'err'); } finally {
      buttons.forEach((b) => { b.disabled = false; });
      if (poll) renderPoll(poll);
    }
  });

  $('#poll-save').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    let seconds;
    try { seconds = readMinutes($('#poll-minutes'), 'Minutes'); } catch (err) { show($('#poll-msg'), err.message, 'err'); return; }
    btn.disabled = true;
    try {
      const r = await post('/api/admin/reader/poll-interval', { seconds });
      pollDirty = false;
      await loadPollInterval();
      show($('#poll-msg'), `Saved — fetching every ${fmtMin(r && r.seconds != null ? r.seconds : seconds)} from the next cycle, no restart needed.`, 'ok');
    } catch (err) { show($('#poll-msg'), err.message, 'err'); } finally { btn.disabled = false; }
  });

  $('#poll-reset').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const r = await post('/api/admin/reader/poll-interval/reset', {});
      pollDirty = false;
      await loadPollInterval();
      show($('#poll-msg'), `Back to the default — every ${fmtMin(r && r.seconds != null ? r.seconds : poll?.default_seconds)}.`, 'ok');
    } catch (err) { show($('#poll-msg'), err.message, 'err'); } finally { btn.disabled = false; }
  });

  $('#poll-auto-save').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    let lo, hi;
    try {
      lo = readMinutes($('#poll-auto-min'), 'Fastest');
      hi = readMinutes($('#poll-auto-max'), 'Slowest');
      if (lo > hi) throw new Error('Fastest must be the same as or shorter than Slowest.');
    } catch (err) { show($('#poll-msg'), err.message, 'err'); return; }
    btn.disabled = true;
    try {
      const r = await post('/api/admin/reader/poll-mode', { mode: 'auto', auto_min_seconds: lo, auto_max_seconds: hi });
      pollAutoDirty = false;
      if (r && r.seconds != null) renderPoll(r); else await loadPollInterval();
      show($('#poll-msg'), `Saved — Auto will stay between ${fmtMin(lo)} and ${fmtMin(hi)}.`, 'ok');
    } catch (err) { show($('#poll-msg'), err.message, 'err'); } finally { btn.disabled = false; }
  });

  // Keep the card fresh every 30 s while it's on screen.
  setInterval(() => {
    if (document.hidden || $('#panel').classList.contains('hidden') || $('#tab-fetching').classList.contains('hidden')) return;
    loadPollInterval().catch(() => { /* transient — next tick retries */ });
  }, 30000);

  /* ---------------- tabs ---------------- */
  const TAB_KEY = 'dr-admin-tab';
  const TABS = ['overview', 'fetching', 'users', 'notifications', 'sources'];
  function showTab(name, remember = true) {
    if (!TABS.includes(name)) name = 'overview';
    document.querySelectorAll('[data-panel]').forEach((p) => p.classList.toggle('hidden', p.dataset.panel !== name));
    document.querySelectorAll('#admin-nav [data-tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === name)));
    const active = $(`#admin-nav [data-tab="${name}"]`);
    const nav = $('#admin-nav');
    if (active && nav.scrollWidth > nav.clientWidth) nav.scrollLeft = active.offsetLeft - 16;
    if (remember) { try { localStorage.setItem(TAB_KEY, name); } catch { /* storage blocked */ } }
    if (name === 'fetching' && !$('#panel').classList.contains('hidden')) loadPollInterval().catch(() => {});
    if (name === 'notifications' && !$('#panel').classList.contains('hidden')) loadNotifyAuto().catch(() => {});
  }
  $('#admin-nav').addEventListener('click', (e) => {
    const b = e.target.closest('[data-tab]');
    if (!b) return;
    showTab(b.dataset.tab);
    window.scrollTo({ top: 0 });
  });
  document.addEventListener('click', (e) => {
    const g = e.target.closest('[data-goto]');
    if (!g) return;
    showTab(g.dataset.goto);
    window.scrollTo({ top: 0 });
  });
  let savedTab = 'overview';
  try { savedTab = localStorage.getItem(TAB_KEY) || 'overview'; } catch { /* storage blocked */ }
  showTab(savedTab, false);

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
    setStatus(null, '', '#gl-priority', empty ? 'Nothing — neutral order' : rule.label, empty ? '' : 'ok');
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
      await loadPollInterval().catch((err) => show($('#poll-msg'), err.message, 'err'));
      await loadPushStatus().catch(() => {});
      await loadNotifyAuto().catch((err) => show($('#na-msg'), err.message, 'err'));
      await loadSaleEvents().catch(() => {});
      $('#gate').classList.add('hidden');
      $('#panel').classList.remove('hidden');
      $('#status-strip').classList.remove('hidden');
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
      show($('#tgpost-msg'), pausing
        ? 'Telegram posting paused — the site/app keep working, nothing new goes to the channel.'
        : 'Telegram posting resumed.', 'ok');
    } catch (err) { show($('#tgpost-msg'), err.message, 'err'); } finally { btn.disabled = false; }
  });

  $('#tg-test').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const r = await post('/api/admin/reader/telegram-test');
      const live = r.live && r.live.connected ? 'live listener connected' : 'live listener NOT connected yet';
      show($('#tgpost-msg'), `Test post sent to ${r.channel} (message #${r.message_id}); ${live}.`, 'ok');
    } catch (err) { show($('#tgpost-msg'), err.message, 'err'); } finally { btn.disabled = false; }
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
      show($('#fetch-msg'), `Sync done: ${r.new || 0} new, ${r.merged || 0} merged, ${r.fetched || 0} posts read.`, 'ok');
      await refresh();
    } catch (err) { show($('#fetch-msg'), err.message, 'err'); } finally { btn.disabled = false; btn.textContent = 'Sync now'; }
  });

  $('#pause-sync').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    const pausing = !btn.classList.contains('on');
    btn.disabled = true;
    try {
      await post('/api/admin/reader/pause', { paused: pausing });
      show($('#fetch-msg'), pausing
        ? 'Syncing paused — automatic fetching is off. "Sync now" still works.'
        : 'Syncing resumed.', 'ok');
      await refresh();
    } catch (err) { show($('#fetch-msg'), err.message, 'err'); } finally { btn.disabled = false; }
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
    const pushKind = !s.enabled ? 'bad' : s.quiet_now ? 'warn' : 'ok';
    setStatus('#st-push', !s.enabled ? '● Pushes off' : s.quiet_now ? '☾ Pushes: quiet hours' : '● Pushes on',
      '#gl-push', !s.enabled ? 'Off' : s.quiet_now ? `Quiet hours (${s.quiet_hours} IST)` : `On · ${s.pushes_per_cycle} per cycle`, pushKind);
    setStatus('#st-phones', `📱 ${s.active_devices} active phone${s.active_devices === 1 ? '' : 's'}`, '#gl-phones',
      `${s.active_devices} active · ${s.reachable} can get push`, s.active_devices && !s.reachable ? 'warn' : '');
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

  /* ---------------- Automatic notifications (manual / auto scheduler) ---------------- */
  const NA_FIELDS = ['daily_cap', 'min_gap_minutes', 'crazy_per_day', 'nudge_after_days', 'nudge_every_days', 'nudge_max'];
  const NA_KIND = { crazy: 'Crazy deal', best: 'Best deal', nudge: 'Nudge' };
  const naKind = (k) => NA_KIND[k] || esc(k || 'Push');
  const istTime = (ts) => {
    if (!ts) return '—';
    try { return new Date(ts * 1000).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' }); }
    catch { return new Date(ts * 1000).toLocaleTimeString(); }
  };
  let na = null;
  let naDirty = false;

  function naSetEnabled(on) {
    $('#na-card').querySelectorAll('button, input').forEach((el) => { el.disabled = !on; });
  }

  function renderNotifyAuto(r) {
    na = r || {};
    const mode = na.mode === 'auto' ? 'auto' : 'manual';
    $('#na-missing').classList.add('hidden');
    naSetEnabled(true);
    document.querySelectorAll('#na-mode [data-mode]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === mode)));
    $('#na-help-manual').classList.toggle('hidden', mode !== 'manual');
    $('#na-help-auto').classList.toggle('hidden', mode !== 'auto');
    $('#na-pill').textContent = mode === 'auto' ? '● Auto' : '● Manual';
    $('#na-pill').className = `status-pill ${mode === 'auto' ? 'ok' : ''}`;
    setStatus('#st-notify', `🔔 Notifications: ${mode}`, '#gl-notify', mode === 'auto' ? 'Auto' : 'Manual', mode === 'auto' ? 'ok' : '');
    $('#push-card h2').textContent = mode === 'auto' ? 'Hot-deal pushes (manual mode — replaced by Auto)' : 'Hot-deal pushes (manual mode)';

    const st = na.settings || {};
    document.querySelectorAll('#na-help-auto [data-na]').forEach((el) => {
      const v = st[el.dataset.na];
      el.textContent = v == null ? 'N' : v;
    });
    $('#na-quiet-hours').textContent = na.quiet_hours || '…';
    if (!naDirty) NA_FIELDS.forEach((f) => { if (st[f] != null) $(`#na-${f}`).value = st[f]; });

    const t = na.today || {};
    const chips = [];
    if (t.sent != null) chips.push(`Sent today <b>${t.sent}${t.cap != null ? ' / ' + t.cap : ''}</b>`);
    if (t.crazy_sent != null) chips.push(`Crazy deals <b>${t.crazy_sent}</b>`);
    if (t.nudges_sent != null) chips.push(`Nudges <b>${t.nudges_sent}</b>`);
    if (na.reachable_devices != null) chips.push(`Can get push <b>${na.reachable_devices}</b>`);
    if (na.inactive_devices != null) chips.push(`Away (nudge-able) <b>${na.inactive_devices}</b>`);
    if (na.quiet_now != null) chips.push(na.quiet_now ? `☾ <b>Quiet hours now</b> (${esc(na.quiet_hours || '')} IST)` : 'Not quiet hours');
    $('#na-today').innerHTML = chips.map((c) => `<span class="stat-chip">${c}</span>`).join('');

    const nx = na.next;
    $('#na-next').innerHTML = mode !== 'auto' ? ''
      : nx ? `Next up: <b>${naKind(nx.kind)}</b> at <b>${istTime(nx.at)} IST</b>${nx.why ? ` — ${esc(nx.why)}` : ''}`
        : 'Next up: nothing planned right now.';

    const plan = na.plan || [];
    $('#na-plan').innerHTML = plan.length ? plan.map((p) => {
      const tag = p.status === 'sent' ? 'on' : p.status === 'skipped' ? 'mute' : 'warn';
      return `<li class="${esc(p.status)}"><span class="t">${istTime(p.at)}</span><span>${naKind(p.kind)}</span>
        <span class="tag ${tag}">${esc(p.status || 'pending')}</span>${p.why ? `<span class="why">${esc(p.why)}</span>` : ''}</li>`;
    }).join('') : `<li class="muted">${mode === 'auto' ? 'Nothing planned for today yet.' : 'Only used in Auto mode.'}</li>`;

    $('#na-recent').innerHTML = (na.recent || []).map((x) => `
      <tr><td>${naKind(x.kind)}</td><td>${esc(x.title)}</td>
        <td>${esc(x.audience || '')}${x.count != null ? ` · ${Number(x.count).toLocaleString('en-IN')}` : ''}</td>
        <td>${x.sent_at ? when(x.sent_at) : ''}</td></tr>`).join('')
      || '<tr><td class="muted">Nothing sent automatically yet.</td></tr>';
  }

  function naUnavailable() {
    na = null;
    $('#na-missing').classList.remove('hidden');
    naSetEnabled(false);
    $('#na-pill').textContent = 'Not available';
    $('#na-pill').className = 'status-pill';
    setStatus('#st-notify', '🔔 Notifications: update needed', '#gl-notify', 'Needs server update', 'warn');
  }

  async function loadNotifyAuto() {
    try {
      renderNotifyAuto(await api('/api/admin/reader/notify-auto'));
    } catch (err) {
      if (err.status === 404) { naUnavailable(); return; }
      throw err;
    }
  }

  NA_FIELDS.forEach((f) => $(`#na-${f}`).addEventListener('input', () => { naDirty = true; }));

  $('#na-mode').addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-mode]');
    if (!btn || btn.disabled || btn.getAttribute('aria-pressed') === 'true') return;
    const mode = btn.dataset.mode;
    $('#na-mode').querySelectorAll('button').forEach((b) => { b.disabled = true; });
    try {
      renderNotifyAuto(await post('/api/admin/reader/notify-auto', { mode }));
      show($('#na-msg'), mode === 'auto'
        ? 'Switched to Auto — the server now decides which pushes go out.'
        : 'Switched to Manual — back to the random hot-deal pushes.', 'ok');
      loadPushStatus().catch(() => {});
    } catch (err) {
      show($('#na-msg'), err.message, 'err');
      if (na) renderNotifyAuto(na);
    } finally {
      if (na) $('#na-mode').querySelectorAll('button').forEach((b) => { b.disabled = false; });
    }
  });

  $('#na-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = {};
    for (const f of NA_FIELDS) {
      const el = $(`#na-${f}`);
      if (el.value === '') continue;
      const v = Number(el.value);
      if (!Number.isInteger(v) || v < Number(el.min) || v > Number(el.max)) {
        show($('#na-msg'), `${el.closest('label').firstChild.textContent.trim()}: enter a whole number ${el.min}–${el.max}.`, 'err');
        return;
      }
      body[f] = v;
    }
    const btn = $('#na-save');
    btn.disabled = true;
    try {
      const r = await post('/api/admin/reader/notify-auto', body);
      naDirty = false;
      renderNotifyAuto(r);
      show($('#na-msg'), 'Saved — applies from the next planning round.', 'ok');
    } catch (err) { show($('#na-msg'), err.message, 'err'); } finally { btn.disabled = !na; }
  });

  $('#na-preview-btn').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    const box = $('#na-preview');
    btn.disabled = true;
    try {
      const p = await api('/api/admin/reader/notify-auto/preview');
      const deal = (label, d) => `<div><div class="k">${label}</div>${d
        ? `<b>${esc(d.title)}</b>${d.price != null ? ` · ₹${Math.round(d.price).toLocaleString('en-IN')}` : ''}${d.why ? `<div class="muted small" style="margin:2px 0 0">${esc(d.why)}</div>` : ''}`
        : '<span class="muted">Nothing qualifies right now.</span>'}</div>`;
      const n = p.sample_nudge;
      box.innerHTML = deal('Crazy deal', p.crazy) + deal('Best deal', p.best)
        + `<div><div class="k">Come-back nudge</div>${Number(p.nudge_eligible || 0).toLocaleString('en-IN')} phone(s) would get one${
          n ? `: <b>${esc(n.title)}</b> — ${esc(n.body)}` : '.'}</div>`
        + '<div class="muted small">Preview only — nothing was sent.</div>';
      box.classList.remove('hidden');
    } catch (err) {
      show($('#na-msg'), err.status === 404 ? 'Needs the latest server update.' : err.message, 'err');
    } finally { btn.disabled = !na; }
  });

  // Refresh every 60 s while the Notifications tab is on screen.
  setInterval(() => {
    if (document.hidden || $('#panel').classList.contains('hidden') || $('#tab-notifications').classList.contains('hidden')) return;
    loadNotifyAuto().catch(() => { /* transient */ });
  }, 60000);

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
      <div class="sale-event-row" style="${e.hidden ? 'opacity:.55' : ''}">
        <div class="sev-main">
          <div class="sev-name">${esc(e.name)}
            <span class="sev-badge">${e.template ? 'calendar' : 'custom'}</span>
            ${e.hidden ? '<span class="sev-badge">hidden this year</span>' : ''}
            <span class="sev-badge ${e.approximate ? '' : 'confirmed'}">${e.approximate ? 'approx' : 'confirmed'}</span>
            ${e.heads_up_posted ? '<span class="sev-badge posted">posted</span>' : ''}
          </div>
          <div class="sev-meta">${esc(e.store || '—')} · ${fmtDay(e.starts_at)}${e.ends_at ? ' – ' + fmtDay(e.ends_at) : ''}</div>
          ${e.hype ? `<div class="sev-hype">“${esc(e.hype)}”</div>` : ''}
        </div>
        <button class="btn btn-soft btn-xs" data-se-edit="${e.id}">Edit</button>
        <button class="btn btn-soft btn-xs" data-se-hype="${e.id}">Regen AI blurb</button>
        <button class="btn btn-soft btn-xs" data-se-post="${e.id}" ${e.heads_up_posted ? 'disabled' : ''}>Post now</button>
        ${e.hidden
          ? `<button class="btn btn-soft btn-xs" data-se-restore="${e.id}">Restore</button>`
          : `<button class="btn btn-ghost btn-xs" data-se-delete="${e.id}">${e.template ? 'Hide this year' : 'Delete'}</button>`}
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
    const restoreId = e.target.dataset.seRestore;
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
        $('#sale-event-editor').open = true;
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
        const ev = (window._saleEvents || []).find((x) => x.id === delId);
        if (!confirm(ev && ev.template ? 'Hide this sale for this year? It comes back next year.' : 'Delete this event?')) return;
        await api(`/api/admin/reader/sale-events/${delId}`, { method: 'DELETE' });
        await loadSaleEvents();
        show($('#sale-events-msg'), ev && ev.template ? 'Hidden for this year.' : 'Deleted.', 'ok');
      } else if (restoreId) {
        const ev = (window._saleEvents || []).find((x) => x.id === restoreId);
        if (!ev) return;
        // Saving it again (without the hidden flag) brings it back.
        await post('/api/admin/reader/sale-events', {
          id: ev.id, name: ev.name, store: ev.store, starts_at: ev.starts_at, ends_at: ev.ends_at, approximate: ev.approximate,
        });
        await loadSaleEvents();
        show($('#sale-events-msg'), 'Restored.', 'ok');
      }
    } catch (err) {
      show($('#sale-events-msg'), err.message, 'err');
      await loadSaleEvents();
    }
  });

  if (token()) unlock();
})();
