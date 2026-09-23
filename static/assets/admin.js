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
    $('#channels').innerHTML = s.channels.length ? s.channels.map((c) => `
      <tr class="${c.enabled ? '' : 'blocked'}">
        <td><b>${esc(c.title)}</b><br><span class="muted small">${c.username ? '@' + esc(c.username) : 'private'}</span></td>
        <td><span class="tag ${c.enabled ? 'on' : 'off'}">${c.enabled ? 'Reading' : 'Blocked'}</span></td>
        <td class="num">${(c.participants || 0).toLocaleString('en-IN')}</td>
        <td class="num">${(c.live_deals || 0).toLocaleString('en-IN')}</td>
        <td>${ago(c.last_fetched_at)}</td>
        <td><button class="btn ${c.enabled ? 'btn-ghost' : 'btn-soft'} btn-xs" data-block="${c.tg_id}" data-blocked="${c.enabled ? '1' : '0'}">
          ${c.enabled ? 'Block' : 'Unblock'}</button></td>
      </tr>`).join('') : '<tr><td colspan="6" class="muted">No channels yet — connect the reader, then “Refresh from Telegram”.</td></tr>';
  }

  async function unlock() {
    try {
      await refresh();
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

  if (token()) unlock();
})();
