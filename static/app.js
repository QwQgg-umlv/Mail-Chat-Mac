'use strict';
/* ============ 邮聊 前端逻辑 ============ */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const state = {
  cfg: null,
  chats: [],
  msgs: [],
  contact: null,
  activeEmail: null,
  events: [],
  calMonth: null,
  selectedDay: null,
  demo: false,
  mailStatus: { status: '未启动', mode: '' },
  translations: {},
  expanded: {},
  lastFetchTs: 0,
};

/* ---------- 小工具 ---------- */
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function icon(name) { return `<svg class="icon"><use href="#${name}"/></svg>`; }
function tzOff() { return (state.cfg && state.cfg.calendar && state.cfg.calendar.utc_offset_hours) || 8; }
function tzDate(ts) { return new Date(ts * 1000 + tzOff() * 3600 * 1000); }
function tzNowTs() { return Math.floor(Date.now() / 1000); }
function pad2(n) { return String(n).padStart(2, '0'); }

function fmtRel(ts) {
  if (!ts) return '';
  const d = tzDate(ts), now = tzDate(tzNowTs());
  const sameDay = d.getUTCFullYear() === now.getUTCFullYear() && d.getUTCMonth() === now.getUTCMonth() && d.getUTCDate() === now.getUTCDate();
  const yest = new Date(now.getTime() - 86400000);
  const isYest = d.getUTCFullYear() === yest.getUTCFullYear() && d.getUTCMonth() === yest.getUTCMonth() && d.getUTCDate() === yest.getUTCDate();
  const hm = `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`;
  if (sameDay) return hm;
  if (isYest) return '昨天 ' + hm;
  if (d.getUTCFullYear() === now.getUTCFullYear()) return `${d.getUTCMonth() + 1}月${d.getUTCDate()}日`;
  return `${d.getUTCFullYear()}年${d.getUTCMonth() + 1}月${d.getUTCDate()}日`;
}
function fmtDayLabel(ts) {
  const d = tzDate(ts);
  return `${d.getUTCFullYear()}年${d.getUTCMonth() + 1}月${d.getUTCDate()}日`;
}
function dayKey(ts) {
  const d = tzDate(ts);
  return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}`;
}
function avatarHtml(name, email, cls = 'avatar') {
  const text = (name || email || '?').trim().charAt(0).toUpperCase();
  const colors = ['#07C160', '#2E6BE6', '#F5A623', '#E64340', '#8A5CD6', '#12B2A6', '#D6488B', '#5B8DEF', '#B26A2E', '#3AA655'];
  let h = 0; for (const c of (email || '?')) h += c.charCodeAt(0);
  return `<div class="${cls}" style="background:${colors[h % colors.length]}">${esc(text)}</div>`;
}
let toastTimer = null;
function toast(msg, ms = 2600) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add('hidden'), ms);
}
async function api(path, method = 'GET', body = null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  // 25 秒超时保护：网络再怪也不会让界面一直转圈
  const ctrl = new AbortController();
  opts.signal = ctrl.signal;
  const timer = setTimeout(() => ctrl.abort(), 25000);
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new Error(e && e.name === 'AbortError'
      ? '服务器响应超时（25秒），请稍等一下再点一次'
      : '连不上邮聊本地服务，请确认程序还在运行');
  } finally {
    clearTimeout(timer);
  }
  let data = {};
  try { data = await res.json(); } catch (e) { /* 空响应 */ }
  if (!res.ok || data.error) throw new Error(data.error || ('请求失败 ' + res.status));
  return data;
}

/* ---------- 弹层 ---------- */
function openMask() { $('#mask').classList.remove('hidden'); }
function closeAllOverlays() {
  $('#mask').classList.add('hidden');
  $('#actionSheet').classList.add('hidden');
  $('#modal').classList.add('hidden');
}
function showModal({ title, body, actions = [] }) {
  $('#modalTitle').textContent = title;
  $('#modalBody').innerHTML = body;
  const foot = $('#modalFoot');
  foot.innerHTML = '';
  actions.forEach(a => {
    const b = document.createElement('button');
    b.className = a.primary ? 'primary-btn' : 'ghost-btn';
    b.textContent = a.label;
    b.onclick = () => a.onClick && a.onClick();
    foot.appendChild(b);
  });
  openMask();
  $('#modal').classList.remove('hidden');
}
function closeModal() {
  $('#modal').classList.add('hidden');
  if ($('#actionSheet').classList.contains('hidden')) $('#mask').classList.add('hidden');
}

/* ---------- 视图切换 ---------- */
function showTab(name) {
  $$('.view').forEach(v => v.classList.remove('active'));
  $('#view-' + name).classList.add('active');
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  if (name === 'contacts') renderContacts();
  if (name === 'me') renderMe();
}
function openOverlay(sel) { $(sel).classList.add('open'); }
function closeOverlay(sel) { $(sel).classList.remove('open'); }

/* ---------- 聊天列表 ---------- */
async function refreshChats() {
  const d = await api('/api/chats');
  state.chats = d.chats;
  state.demo = !!d.demo;
  renderChats();
}
function renderChats() {
  const q = ($('#searchChats').value || '').trim().toLowerCase();
  if (q) return renderSearchResults(q);   // 有搜索词 → Outlook 式搜索面板
  $('#demoBanner').classList.toggle('hidden', !state.demo);
  const list = $('#chatList');
  const rows = state.chats.filter(c =>
    !q || (c.name || '').toLowerCase().includes(q) || c.email.includes(q) ||
    (c.last_body || '').toLowerCase().includes(q) || (c.last_subject || '').toLowerCase().includes(q));
  if (!rows.length) {
    list.innerHTML = `<div class="empty-tip">还没有邮件往来<br>把邮箱配置好，新邮件会自动出现在这里<br>（“我的 → 邮箱设置”）</div>`;
  } else {
    list.innerHTML = rows.map(c => `
      <div class="chat-item" data-email="${esc(c.email)}">
        ${avatarHtml(c.name, c.email)}
        <div class="chat-main">
          <div class="chat-name">${esc(c.name || c.email)}
            ${c.pinned ? '<span class="tag">置顶</span>' : ''}
            ${c.muted ? '<span class="tag mute">免打扰</span>' : ''}
          </div>
          <div class="chat-sub">${c.last_direction === 'out' ? '我: ' : ''}${esc((c.last_subject || c.last_body || '').replace(/\n/g, ' ').slice(0, 40))}</div>
        </div>
        <div class="chat-side">
          <div class="chat-time">${fmtRel(c.last_ts)}</div>
          ${c.unread && !c.muted ? `<em class="badge">${c.unread > 99 ? '99+' : c.unread}</em>` : ''}
        </div>
      </div>`).join('');
  }
  const total = state.chats.reduce((s, c) => s + (c.muted ? 0 : (c.unread || 0)), 0);
  const badge = $('#tabBadge');
  badge.classList.toggle('hidden', !total);
  badge.textContent = total > 99 ? '99+' : total;
}

/* ---------- 聊天详情 ---------- */
async function openChat(email) {
  state.activeEmail = email;
  const d = await api('/api/messages?email=' + encodeURIComponent(email));
  state.msgs = d.messages || [];
  state.contact = d.contact || { email };
  $('#chatTitle').textContent = state.contact.name || email;
  renderMessages();
  openOverlay('#view-chat');
  renderQuickReplies();
  api('/api/messages/read', 'POST', { email }).then(() => refreshChats()).catch(() => {});
}
function renderMessages() {
  const box = $('#msgList');
  const out = [];
  let lastDay = '';
  for (const m of state.msgs) {
    const dk = dayKey(m.ts);
    if (dk !== lastDay) {
      lastDay = dk;
      out.push(`<div class="day-divider">${fmtDayLabel(m.ts)}</div>`);
    }
    const isOut = m.direction === 'out';
    // 译文优先：自动翻译存数据库(m.translation)，手动翻译在内存(state.translations)
    const trans = (m.translation || state.translations[m.id] || '').trim();
    const hasTrans = !!trans;
    const showOrig = !!(state.showOrig && state.showOrig[m.id]);
    const useTrans = hasTrans && !showOrig && !isOut;
    const shownText = useTrans ? trans : (m.body || '(这封邮件没有文字内容)');
    const long = shownText.length > 320;
    const expanded = state.expanded[m.id];
    out.push(`
      <div class="msg-row ${isOut ? 'out' : 'in'}">
        ${avatarHtml(isOut ? '我' : (state.contact.name || state.contact.email), isOut ? 'me' : state.contact.email, 'msg-avatar')}
        <div style="min-width:0">
          ${m.summary ? `<div class="summary-card"><div class="sc-title">✨ AI 摘要</div>${esc(m.summary)}</div>` : ''}
          <div class="bubble ${long && !expanded ? 'clamped' : ''}" data-id="${m.id}">
            ${m.subject ? `<div class="subj">${esc(m.subject)}</div>` : ''}
            <div class="body">${esc(shownText)}</div>
            ${long && !expanded ? `<span class="expand-link">展开全文 ▾</span>` : ''}
            ${hasTrans && !isOut ? `<button class="orig-btn" data-id="${m.id}">${showOrig ? '🌐 看译文' : '🌐 看原文'}</button>` : ''}
          </div>
          <div class="msg-meta">${fmtRel(m.ts)} · ${esc((isOut ? '已发送' : (state.contact.name || m.email)))}</div>
        </div>
      </div>`);
  }
  box.innerHTML = out.join('') || '<div class="empty-tip">还没有邮件，点右上角 ✏️ 写第一封</div>';
  box.scrollTop = box.scrollHeight;
}

function lastIncoming() {
  for (let i = state.msgs.length - 1; i >= 0; i--) {
    if (state.msgs[i].direction === 'in') return state.msgs[i];
  }
  return state.msgs[state.msgs.length - 1] || null;
}
function renderQuickReplies() {
  const list = (state.cfg && state.cfg.ui && state.cfg.ui.quick_replies) || [];
  $('#quickRow').innerHTML = list.map(q => `<button class="chip">${esc(q)}</button>`).join('');
}

/* ---------- 长按 / 点击 出菜单 ---------- */
function openSheet(msgId) {
  const m = state.msgs.find(x => String(x.id) === String(msgId));
  if (!m) return;
  const items = [
    { key: 'translate', label: '翻译', icon: 'i-translate' },
    { key: 'summary', label: 'AI 总结', icon: 'i-summary' },
    { key: 'events', label: '提取日程', icon: 'i-calendar' },
    { key: 'quote', label: '引用回复', icon: 'i-reply' },
    { key: 'copy', label: '复制', icon: 'i-copy' },
    { key: 'delete', label: '删除', icon: 'i-trash', danger: true },
  ];
  $('#sheetGrid').innerHTML = items.map(i =>
    `<button class="sheet-item ${i.danger ? 'danger' : ''}" data-act="${i.key}">${icon(i.icon)}<span>${i.label}</span></button>`).join('');
  $('#sheetGrid').querySelectorAll('button').forEach(b => {
    b.onclick = () => { closeSheet(); handleAction(b.dataset.act, m); };
  });
  openMask();
  $('#actionSheet').classList.remove('hidden');
}
function closeSheet() {
  $('#actionSheet').classList.add('hidden');
  if ($('#modal').classList.contains('hidden')) $('#mask').classList.add('hidden');
}

async function handleAction(act, m) {
  if (act === 'quote') {
    const quote = `「${(m.body || '').replace(/\n/g, ' ').slice(0, 60)}…」\n`;
    $('#msgInput').value = quote + $('#msgInput').value;
    $('#msgInput').focus();
    return;
  }
  if (act === 'copy') {
    try { await navigator.clipboard.writeText(`${m.subject ? m.subject + '\n' : ''}${m.body}`); toast('已复制'); }
    catch (e) { toast('复制失败，请手动选中文字'); }
    return;
  }
  if (act === 'delete') {
    showModal({
      title: '删除这条邮件？', body: '<p>删除后本机聊天记录里就看不到它了（邮箱里还在）。</p>',
      actions: [
        { label: '取消', onClick: closeModal },
        {
          label: '删除', primary: true, onClick: async () => {
            await api('/api/messages/delete', 'POST', { id: m.id });
            closeModal();
            state.msgs = state.msgs.filter(x => x.id !== m.id);
            renderMessages();
            toast('已删除');
          }
        }]
    });
    return;
  }
  if (act === 'translate') {
    toast('AI 正在翻译…');
    const hasCJK = (m.body.match(/[\u4e00-\u9fa5]/g) || []).length > (m.body.length || 1) * 0.15;
    const target = hasCJK ? '英文' : '中文';
    try {
      const d = await api('/api/ai', 'POST', { action: 'translate', text: `${m.subject ? '【' + m.subject + '】\n' : ''}${m.body}`, target_lang: target });
      state.translations[m.id] = d.result;
      renderMessages();
    } catch (e) { toast(e.message, 4000); }
    return;
  }
  if (act === 'summary') {
    toast('AI 正在总结…');
    try {
      const d = await api('/api/ai', 'POST', { action: 'summarize', message_id: m.id });
      m.summary = d.result;
      const idx = state.msgs.findIndex(x => x.id === m.id);
      if (idx >= 0) state.msgs[idx].summary = d.result;
      renderMessages();
    } catch (e) { toast(e.message, 4000); }
    return;
  }
  if (act === 'events') {
    openEventsModal(m);
  }
}

/* ---------- 提取日程 ---------- */
async function openEventsModal(m) {
  showModal({ title: '提取日程', body: '<p>正在读邮件里的时间…</p>', actions: [{ label: '取消', onClick: closeModal }] });
  let d;
  try {
    d = await api('/api/ai', 'POST', { action: 'extract_events', message_id: m.id });
  } catch (e) {
    showModal({ title: '提取日程', body: `<p>${esc(e.message)}</p>`, actions: [{ label: '知道了', onClick: closeModal }] });
    return;
  }
  const cands = (d.candidates || []).filter(c => c.start_ts);
  if (!cands.length) {
    showModal({
      title: '提取日程',
      body: `<p>这封邮件里没找到明确的时间。</p>${d.ai_error ? `<p class="hint">${esc(d.ai_error)}</p>` : ''}`,
      actions: [{ label: '知道了', onClick: closeModal }]
    });
    return;
  }
  const rows = cands.map((c, i) => `
    <label class="qr-item" style="align-items:flex-start">
      <input type="checkbox" data-i="${i}" checked>
      <span style="flex:1">
        <b>${esc(c.title)}</b><br>
        <span class="hint" style="margin:2px 0">${esc(c.start)}${c.end ? ' ~ ' + esc(c.end) : ''}${c.all_day ? '（全天）' : ''}${c.location ? ' · ' + esc(c.location) : ''} · 来自${esc(c.source)}</span>
      </span>
    </label>`).join('');
  showModal({
    title: '找到这些时间安排',
    body: `<p class="hint">勾选要写进日历的，取消勾选不会添加。</p>${rows}
           ${d.ai_error ? `<p class="hint">${esc(d.ai_error)}</p>` : ''}`,
    actions: [
      { label: '取消', onClick: closeModal },
      {
        label: '加入日历', primary: true, onClick: async () => {
          const picked = $$('#modalBody input[type=checkbox]:checked').map(x => cands[+x.dataset.i]);
          if (!picked.length) { toast('没有勾选任何日程'); return; }
          for (const c of picked) {
            await api('/api/events/save', 'POST', {
              title: c.title, start_ts: c.start_ts, end_ts: c.end_ts || (c.start_ts + 3600),
              all_day: c.all_day ? 1 : 0, location: c.location || '', notes: c.notes || '',
              source_email: state.activeEmail, confirmed: 0
            });
          }
          closeModal();
          toast(`已加入 ${picked.length} 个日程（待确认）`);
          loadEvents();
        }
      }]
  });
}

/* ---------- 发送 ---------- */
async function sendMail(text) {
  const body = (text != null ? text : $('#msgInput').value).trim();
  if (!body) { toast('先写点什么吧'); return; }
  const last = lastIncoming();
  let subject = (last && last.subject) ? last.subject : '';
  if (subject && !/^re:/i.test(subject)) subject = 'Re: ' + subject;
  const btn = $('#btnSend');
  btn.disabled = true;
  try {
    await api('/api/send', 'POST', {
      to: state.activeEmail, subject, body,
      in_reply_to: (last && last.direction === 'in') ? last.msg_id : ''
    });
    if (text == null) $('#msgInput').value = '';
    $('#aiDraft').value = '';
    toast('已发送 ✅');
    const d = await api('/api/messages?email=' + encodeURIComponent(state.activeEmail));
    state.msgs = d.messages || [];
    renderMessages();
    refreshChats();
  } catch (e) {
    toast(e.message, 4500);
  } finally {
    btn.disabled = false;
  }
}

/* ---------- AI 面板 ---------- */
function openAI() {
  if (!state.activeEmail) return;
  $('#aiPanel').classList.add('open');
}
function closeAI() { $('#aiPanel').classList.remove('open'); }
async function generateReply() {
  const intent = $('#aiIntent').value.trim() || '礼貌回复，表示收到';
  const last = lastIncoming();
  if (!last) { toast('还没有可以回复的邮件'); return; }
  const btn = $('#btnAIGenerate');
  btn.disabled = true;
  btn.textContent = '生成中…';
  try {
    const d = await api('/api/ai', 'POST', {
      action: 'reply_draft', message_id: last.id, intent,
      extra: ''
    });
    $('#aiDraft').value = d.result;
    toast('草稿生成好了，可以随意修改');
  } catch (e) {
    toast(e.message, 5000);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `${icon('i-ai')} 生成回复`;
  }
}

/* ---------- Outlook 式搜索（范围筛选 + 结果列表 + 点击直达邮件） ---------- */
let searchScope = 'all';
let searchTimer = null;
function renderSearchResults(q) {
  $('#demoBanner').classList.add('hidden');
  const list = $('#chatList');
  const scopes = [['all', '全部'], ['from', '发件人'], ['subject', '主题'], ['body', '正文']];
  list.innerHTML = `
    <div class="search-scope" id="searchScope">
      ${scopes.map(([k, label]) => `<button class="chip ${searchScope === k ? 'on' : ''}" data-scope="${k}">${label}</button>`).join('')}
    </div>
    <div id="searchHits"><div class="empty-tip">正在搜索…</div></div>`;
  $$('#searchScope .chip').forEach(b => b.onclick = () => {
    searchScope = b.dataset.scope;
    renderSearchResults(q);
  });
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    try {
      const d = await api(`/api/search?q=${encodeURIComponent(q)}&scope=${searchScope}`);
      const hits = d.results || [];
      const box = $('#searchHits');
      if (!box) return;
      if (!hits.length) {
        box.innerHTML = '<div class="empty-tip">没找到相关邮件<br>换个关键词，或点上面切换搜索范围</div>';
        return;
      }
      const hl = (s) => {
        const i = (s || '').toLowerCase().indexOf(q.toLowerCase());
        if (i < 0) return esc(s || '');
        return esc(s.slice(0, i)) + '<b>' + esc(s.slice(i, i + q.length)) + '</b>' + esc(s.slice(i + q.length));
      };
      box.innerHTML = `
        <div class="group-label">找到 ${hits.length} 封邮件（点任意一封直达）</div>
        ${hits.map(m => `
          <div class="search-hit" data-email="${esc(m.email)}" data-id="${m.id}">
            ${avatarHtml(m.from_name || m.email, m.email)}
            <div class="hit-main">
              <div class="hit-top">
                <div class="hit-from">${hl(m.from_name || m.email)}</div>
                <div class="hit-date">${fmtRel(m.ts)}</div>
              </div>
              <div class="hit-subj">${hl(m.subject || '(无主题)')}</div>
              <div class="hit-snip">${hl(m.snippet || '')}</div>
            </div>
          </div>`).join('')}`;
      $$('#searchHits .search-hit').forEach(el => el.onclick = async () => {
        const email = el.dataset.email;
        $('#searchChats').value = '';
        await openChat(email);
        scrollToMsg(parseInt(el.dataset.id, 10));
      });
    } catch (e) {
      const box = $('#searchHits');
      if (box) box.innerHTML = `<div class="empty-tip">搜索失败：${esc(e.message)}</div>`;
    }
  }, 250);
}

/* ---------- 联系人（微信同款：字母分组 + 右侧导轨可拖动） ---------- */
const PY_SURNAMES = { '张':'Z','王':'W','李':'L','刘':'L','陈':'C','杨':'Y','黄':'H','赵':'Z','吴':'W','周':'Z','徐':'X','孙':'S','马':'M','朱':'P','胡':'H','郭':'G','何':'H','林':'L','高':'G','罗':'L','郑':'Z','梁':'L','谢':'X','宋':'S','唐':'T','许':'X','韩':'H','冯':'F','邓':'D','曹':'C','彭':'P','曾':'Z','肖':'X','田':'T','董':'D','袁':'Y','潘':'P','于':'Y','蒋':'J','蔡':'C','余':'Y','杜':'D','叶':'Y','程':'C','苏':'S','魏':'W','吕':'L','丁':'D','任':'R','沈':'S','姚':'Y','卢':'L','姜':'J','崔':'C','钟':'Z','谭':'T','陆':'L','汪':'W','范':'F','金':'J','石':'S','廖':'L','贾':'J','夏':'X','韦':'W','傅':'F','方':'F','白':'B','邹':'Z','孟':'M','熊':'X','秦':'Q','邱':'Q','江':'J','尹':'Y','薛':'X','段':'D','雷':'L','侯':'H','龙':'L','史':'S','陶':'T','黎':'L','贺':'H','顾':'G','毛':'M','郝':'H','龚':'G','邵':'S','万':'W','钱':'Q','严':'Y','武':'W','戴':'D','莫':'M','孔':'K','向':'X','汤':'T','欧阳':'O','诸葛':'Z','司':'S','上官':'S','慕容':'M' };
function contactLetter(name, email) {
  const s = (name || email || '').trim();
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (/[a-zA-Z]/.test(ch)) return ch.toUpperCase();
    if (PY_SURNAMES[ch]) return PY_SURNAMES[ch];
    if (i + 1 < s.length && PY_SURNAMES[ch + s[i + 1]]) return PY_SURNAMES[ch + s[i + 1]];
  }
  return '#';
}
function renderContacts() {
  const q = ($('#searchContacts').value || '').trim().toLowerCase();
  const rows = state.chats.filter(c => !q || (c.name || '').toLowerCase().includes(q) || c.email.includes(q));
  const box = $('#contactList');
  if (!rows.length) {
    box.innerHTML = '<div class="empty-tip">联系人会随邮件往来自动出现</div>';
    return;
  }
  rows.sort((a, b) => (b.starred - a.starred) ||
    contactLetter(a.name, a.email).localeCompare(contactLetter(b.name, b.email)) ||
    (a.name || '').localeCompare(b.name || '', 'zh'));
  const groups = {}, order = [];
  for (const c of rows) {
    const k = c.starred ? '★' : contactLetter(c.name, c.email);
    if (!groups[k]) { groups[k] = []; order.push(k); }
    groups[k].push(c);
  }
  order.sort((a, b) => (a === '★' ? -1 : b === '★' ? 1 : (a === '#' ? 1 : b === '#' ? -1 : a.localeCompare(b))));
  const ALPHA = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ#'.split('');
  const railKeys = ['★', ...ALPHA];
  box.innerHTML = `
    <div class="contacts-wrap">
      ${order.map(k => `
        <div class="group-label" id="grp-${k === '★' ? 'star' : k}">${k === '★' ? '⭐ 星标好友' : k}</div>
        ${groups[k].map(c => `
          <div class="contact-item" data-email="${esc(c.email)}">
            ${avatarHtml(c.name, c.email)}
            <div class="chat-main">
              <div class="chat-name">${esc(c.name || c.email)}</div>
              <div class="chat-sub">${esc(c.email)}</div>
            </div>
            <button class="contact-star ${c.starred ? '' : 'off'}" data-star="${esc(c.email)}">★</button>
          </div>`).join('')}
      `).join('')}
      <div class="alpha-rail" id="alphaRail">
        ${railKeys.map(k => `<span data-jump="${k}" class="${groups[k] ? 'has' : ''}">${k}</span>`).join('')}
      </div>
    </div>`;
  // 右侧字母导轨：点击 / 按住拖动 → 跳到那个字母的分组
  const rail = $('#alphaRail');
  if (rail) {
    let dragging = false;
    const jumpAt = (clientY) => {
      const items = [...rail.querySelectorAll('span')];
      if (!items.length) return;
      let best = null, bestD = Infinity;
      for (const s of items) {
        const r = s.getBoundingClientRect();
        const d = Math.abs((r.top + r.bottom) / 2 - clientY);
        if (d < bestD) { bestD = d; best = s; }
      }
      if (!best) return;
      const key = best.dataset.jump;
      const target = document.getElementById('grp-' + (key === '★' ? 'star' : key));
      if (target) target.scrollIntoView({ block: 'start', behavior: 'auto' });
    };
    rail.addEventListener('pointerdown', (e) => {
      dragging = true;
      try { rail.setPointerCapture(e.pointerId); } catch (err) { }
      jumpAt(e.clientY);
      e.preventDefault();
    });
    rail.addEventListener('pointermove', (e) => { if (dragging) jumpAt(e.clientY); });
    ['pointerup', 'pointercancel'].forEach(ev => rail.addEventListener(ev, () => { dragging = false; }));
  }
}

/* ---------- 我的 / 设置 ---------- */
function switchHtml(id, on) {
  return `<div class="switch ${on ? 'on' : ''}" id="${id}"></div>`;
}
/* —— 设置入口（每个功能一个独立页面）—— */
function meEntry(ico, title, sub, page) {
  return `
    <div class="me-entry" data-goto="${page}">
      <span class="me-ico">${ico}</span>
      <div class="me-txt"><div class="me-t1">${title}</div><div class="me-t2">${sub}</div></div>
      <span class="me-chev">›</span>
    </div>`;
}
const ME_TITLES = {
  mail: '📮 邮箱账号', ai: '🤖 AI 助手', cal: '📅 日历与时间',
  chat: '💬 聊天习惯', import: '📥 邮件导入与整理', theme: '🎨 外观风格', data: '📦 数据与其他',
};
function applyTheme() {
  const t = (state.cfg && state.cfg.ui && state.cfg.ui.theme) || 'dark';
  document.documentElement.dataset.theme = t;
}
function applyMePage() {
  const page = state.mePage || 'hub';
  const hub = $('#meHub'), back = $('#meBack'), profile = $('#meProfile');
  if (!hub) return;
  hub.classList.toggle('hidden', page !== 'hub');
  if (profile) profile.classList.toggle('hidden', page !== 'hub');
  back.classList.toggle('hidden', page === 'hub');
  back.textContent = '‹ 返回 · ' + (ME_TITLES[page] || '');
  $$('#meContent [data-card]').forEach(el =>
    el.classList.toggle('hidden', page !== el.dataset.card));
}
function renderMe() {
  const c = state.cfg;
  if (!c) return;
  applyTheme();
  const m = c.mail, ai = c.ai, cal = c.calendar, ui = c.ui;
  $('#meContent').innerHTML = `
    <div class="profile" id="meProfile">
      ${avatarHtml(m.display_name || m.email || '我', m.email || 'me')}
      <div>
        <div class="profile-name">${esc(m.display_name || m.email || '还没有设置邮箱')}</div>
        <div class="profile-sub">${esc(m.email || '到下面「邮箱账号」填一填')} · <span id="statusLine">${esc(state.mailStatus.status)}</span></div>
      </div>
    </div>

    <div class="me-hub" id="meHub">
      ${meEntry('📮', '邮箱账号', '收发邮件、登录学校邮箱', 'mail')}
      ${meEntry('🤖', 'AI 助手', '总结 / 翻译 / 帮写回复', 'ai')}
      ${meEntry('📅', '日历与时间', '时区、提醒、自动提取日程', 'cal')}
      ${meEntry('💬', '聊天习惯', '快捷回复等小设置', 'chat')}
      ${meEntry('📥', '邮件导入与整理', '全部历史邮件 → 日历', 'import')}
      ${meEntry('🎨', '外观风格', '白色 / 黑色随心换', 'theme')}
      ${meEntry('📦', '数据与其他', '备份、演示数据、使用说明', 'data')}
    </div>
    <div class="me-back hidden" id="meBack">‹ 返回</div>

    <div class="set-card" data-card="mail">
      <div class="set-head">📮 邮箱设置 <small>收发邮件走这里</small></div>
      <div class="set-body">
        <div class="hint">先点一个和你邮箱一样的按钮，会自动填好服务器：</div>
        <div class="preset-row">
          <button class="chip" data-preset="outlook">Outlook / Hotmail</button>
          <button class="chip" data-preset="m365">Microsoft 365（公司学校）</button>
          <button class="chip" data-preset="gmail">Gmail</button>
          <button class="chip" data-preset="qq">QQ 邮箱</button>
          <button class="chip" data-preset="163">163 邮箱</button>
        </div>
        <div class="form-grid">
          <div class="form-row"><label>邮箱地址</label><input id="f_email" value="${esc(m.email)}" placeholder="you@example.com"></div>
          <div class="form-row"><label>显示名字（别人看到的）</label><input id="f_name" value="${esc(m.display_name)}" placeholder="小明"></div>
        </div>
        <div class="form-row">
          <label>登录方式</label>
          <select id="f_auth">
            <option value="oauth2" ${m.auth_method === 'oauth2' ? 'selected' : ''}>学校 / 公司邮箱（Microsoft 365）—— 学校账号登录，不用密码</option>
            <option value="password" ${m.auth_method !== 'oauth2' ? 'selected' : ''}>个人邮箱 —— 用授权码 / 应用密码</option>
          </select>
        </div>
        <div id="oauthBlock">
          <div class="hint">
            🚫 <b>学校不让注册应用？完全不用注册！</b><br>
            已固定使用 <b>Thunderbird 官方公共客户端</b>（密钥已内置）—— 实测只有它能走通：
            其它客户端要么拿不到换令牌的密钥（Apple），要么被微软"第一方预授权"限制（Office/Outlook）。
            点下面的<b>「开始登录（浏览器授权）」</b>就行：
          </div>
          <details>
            <summary>如果学校允许注册应用，也可以自己注册一个（点开看 ①~⑤ 步骤）</summary>
            <div class="hint">
              ① 用<b>学校账号</b>打开 <code>https://portal.azure.com</code> → 搜索「<b>应用注册</b>」→ 点「<b>新注册</b>」<br>
              ② 名字随便填（比如 <code>邮聊</code>）；账号类型选「<b>仅此组织目录中的帐户</b>」→ 点「注册」<br>
              ③ 左边「<b>身份验证</b>」→「添加平台」→「<b>移动和桌面应用程序</b>」→ 勾选 <code>https://localhost</code> → 保存<br>
              ④ 左边「<b>API 权限</b>」→「添加权限」→「<b>委托的权限</b>」→ 搜索并添加这 3 个：
                 <code>IMAP.AccessAsUser.All</code>、<code>SMTP.Send</code>、<code>offline_access</code><br>
              ⑤ 回到「概述」页，复制「<b>应用程序(客户端) ID</b>」粘到下面 → 点「<b>开始登录</b>」
            </div>
          </details>
          <div class="form-grid">
            <div class="form-row"><label>公共客户端（已固定为 Thunderbird，内置密钥）</label><input id="f_oauth_cid" value="9e5f94bc-e8a4-4e73-b8be-63364c29d753" readonly></div>
            <div class="form-row"><label>学校 / 租户（一般不用改）</label><input id="f_oauth_tenant" value="${esc((m.oauth || {}).tenant || 'organizations')}" placeholder="organizations"></div>
          </div>
          <div class="btn-row">
            <button class="primary-btn" id="btnOAuthStart" style="margin-top:0">开始登录（浏览器授权）</button>
            <button class="ghost-btn" id="btnOAuthLogout" style="margin-top:0">退出登录</button>
          </div>
          <div class="hint" id="oauthStatus">状态：${(m.oauth && m.oauth.refresh_token) ? '✅ 已登录（令牌会自动续期，不用再登录）' : (window.__oauthPending ? '⏳ 已拿到登录码，等待你在网页上登录…（后台自动完成）' : (window.__oauthLastError ? '❌ ' + esc(window.__oauthLastError) : '未登录'))}</div>
        </div>
        <div id="passwordBlock">
          <div class="form-row">
            <label>授权码 / 应用密码（不是登录密码！）</label>
            <input id="f_password" type="password" value="${esc(m.password)}" placeholder="在邮箱设置里生成的那个密码">
          </div>
        </div>
        <div class="form-grid">
          <div class="form-row"><label>收件服务器 (IMAP)</label><input id="f_imap" value="${esc(m.imap_host)}"></div>
          <div class="form-row"><label>收件端口</label><input id="f_imap_port" type="number" value="${esc(m.imap_port)}"></div>
          <div class="form-row"><label>发件服务器 (SMTP)</label><input id="f_smtp" value="${esc(m.smtp_host)}"></div>
          <div class="form-row"><label>发件端口</label><input id="f_smtp_port" type="number" value="${esc(m.smtp_port)}"></div>
        </div>
        <div class="switch-row"><span>实时收信（推荐开着）</span>${switchHtml('sw_idle', m.use_idle)}</div>
        <div class="switch-row"><span>新邮件自动 AI 摘要</span>${switchHtml('sw_autosum', m.auto_summary)}</div>
        <div class="switch-row"><span>新邮件弹窗提醒</span>${switchHtml('sw_notify', m.notify)}</div>
        <div class="btn-row">
          <button class="ghost-btn" id="btnTestMail">测试连接</button>
          <button class="primary-btn" id="btnSaveMail" style="margin-top:0">保存邮箱设置</button>
        </div>
      </div>
    </div>

    <div class="set-card" data-card="ai">
      <div class="set-head">🤖 AI 助手 <small>${ai.api_key ? '已配置' : '还没填 API'}</small></div>
      <div class="set-body">
        <div class="form-row"><label>接口地址（Base URL）</label><input id="f_ai_base" value="${esc(ai.base_url)}" placeholder="https://api.openai.com/v1"></div>
        <div class="form-grid">
          <div class="form-row"><label>API Key</label><input id="f_ai_key" type="password" value="${esc(ai.api_key)}" placeholder="sk-..."></div>
          <div class="form-row"><label>模型名</label><input id="f_ai_model" value="${esc(ai.model)}" placeholder="gpt-4o-mini"></div>
        </div>
        <div class="form-row"><label>创造力（0 = 稳重，1 = 奔放）</label><input id="f_ai_temp" type="number" step="0.1" min="0" max="2" value="${esc(ai.temperature)}"></div>
        <details>
          <summary>点开修改 AI 提示词（不改也能直接用）</summary>
          <div class="form-row"><label>总结提示词</label><textarea id="f_p_summary" rows="5">${esc(ai.prompts.summary)}</textarea></div>
          <div class="form-row"><label>翻译提示词（{target_lang} 是目标语言）</label><textarea id="f_p_translate" rows="4">${esc(ai.prompts.translate)}</textarea></div>
          <div class="form-row"><label>代写回复提示词</label><textarea id="f_p_reply" rows="6">${esc(ai.prompts.reply)}</textarea></div>
          <div class="form-row"><label>提取日程提示词</label><textarea id="f_p_events" rows="6">${esc(ai.prompts.extract_events)}</textarea></div>
        </details>
        <div class="btn-row">
          <button class="ghost-btn" id="btnTestAI">测试 AI</button>
          <button class="primary-btn" id="btnSaveAI" style="margin-top:0">保存 AI 设置</button>
        </div>
      </div>
    </div>

    <div class="set-card" data-card="cal">
      <div class="set-head">📅 日历与时间</div>
      <div class="set-body">
        <div class="form-grid">
          <div class="form-row"><label>时区名字</label><input id="f_tz" value="${esc(cal.timezone)}" placeholder="例如：北京时间"></div>
          <div class="form-row"><label>时区偏移（小时）</label><input id="f_tzoff" type="number" step="0.5" value="${esc(cal.utc_offset_hours)}"></div>
          <div class="form-row"><label>每周从哪天开始</label>
            <select id="f_weekstart">
              <option value="1" ${cal.week_start == 1 ? 'selected' : ''}>星期一</option>
              <option value="0" ${cal.week_start == 0 ? 'selected' : ''}>星期日</option>
            </select></div>
          <div class="form-row"><label>提前提醒（小时）</label><input id="f_remind" type="number" value="${esc(cal.remind_hours)}"></div>
        </div>
        <div class="switch-row"><span>自动把邮件里的时间放进日历（待确认）</span>${switchHtml('sw_autoext', cal.auto_extract)}</div>
        <div class="btn-row"><button class="primary-btn" id="btnSaveCal" style="margin-top:0">保存时间设置</button></div>
      </div>
    </div>

    <div class="set-card" data-card="chat">
      <div class="set-head">💬 聊天习惯</div>
      <div class="set-body">
        <div class="hint">快捷回复：点一下就能发的话，最多 8 条</div>
        <div id="qrList">${(ui.quick_replies || []).map((q, i) => `
          <div class="qr-item"><input data-qr="${i}" value="${esc(q)}"><button data-qr-del="${i}">删除</button></div>`).join('')}</div>
        <div class="btn-row">
          <button class="ghost-btn" id="btnAddQR">＋ 加一条</button>
          <button class="primary-btn" id="btnSaveUI" style="margin-top:0">保存</button>
        </div>
      </div>
    </div>

    <div class="set-card" data-card="import">
      <div class="set-head">📥 全部邮件导入与日历整理</div>
      <div class="set-body">
        <div class="hint">连接邮箱后会<b>自动</b>把全部历史邮件收进来，并把里面提到的时间<b>全部整理进日历</b>（橙色=待确认）。
        想重新整理或换用 AI，点下面按钮。</div>
        <div class="form-grid">
          <div class="form-row"><label>历史邮件导入数量（0 = 全部）</label><input id="f_histlimit" type="number" value="${esc(m.history_limit != null ? m.history_limit : 0)}"></div>
          <div class="form-row"><label>&nbsp;</label><span class="hint" style="margin:8px 0">邮件很多时“全部”会跑一阵子，进度见下方</span></div>
        </div>
        <div class="switch-row"><span>整理时用 AI 提取（更准，但消耗 AI 额度）</span>${switchHtml('sw_aiext', c.calendar.ai_backfill)}</div>
        <div class="btn-row"><button class="primary-btn" id="btnBackfill" style="margin-top:0">📥 开始导入并整理进日历</button></div>
        <div class="hint" id="progressLine">${esc((window.__progressText) || '（还没有整理任务）')}</div>
      </div>
    </div>

    <div class="set-card" data-card="theme">
      <div class="set-head">🎨 外观风格</div>
      <div class="set-body">
        <div class="theme-row">
          <div class="theme-opt ${(ui.theme || 'dark') === 'light' ? 'sel' : ''}" data-theme-set="light">
            <div class="theme-preview light"></div>
            <div class="theme-name">☀️ 白色风格</div>
          </div>
          <div class="theme-opt ${(ui.theme || 'dark') !== 'light' ? 'sel' : ''}" data-theme-set="dark">
            <div class="theme-preview dark"></div>
            <div class="theme-name">🌙 黑色风格（科技）</div>
          </div>
        </div>
        <div class="hint">点一下立即换，程序会自动记住你的选择。</div>
      </div>
    </div>

    <div class="set-card" data-card="data">
      <div class="set-head">📦 数据与其他</div>
      <div class="set-body">
        <div class="btn-row" style="margin-top:4px">
          <button class="ghost-btn" id="btnSeedDemo">载入演示数据</button>
          <button class="ghost-btn" id="btnClearDemo2">清除演示数据</button>
          <button class="ghost-btn" id="btnExport">导出全部数据</button>
          <button class="ghost-btn" id="btnHelp2">使用说明</button>
        </div>
        <div class="hint">所有邮件、日程、设置都保存在这台电脑的 <code>邮聊</code> 文件夹里，不会上传到别处。</div>
      </div>
    </div>`;
  bindMeEvents();
  applyMePage();
}

function bindMeEvents() {
  // 入口列表：点哪个进哪个独立页面；返回条回到入口
  $$('#meContent [data-goto]').forEach(b => b.onclick = () => {
    state.mePage = b.dataset.goto;
    renderMe();
  });
  const backBtn = $('#meBack');
  if (backBtn) backBtn.onclick = () => { state.mePage = 'hub'; renderMe(); };

  // 外观风格：点选立即换 + 记住
  $$('#meContent [data-theme-set]').forEach(b => b.onclick = async () => {
    const t = b.dataset.themeSet;
    state.cfg.ui = state.cfg.ui || {};
    state.cfg.ui.theme = t;
    applyTheme();
    try { await api('/api/config', 'POST', { config: { ui: { theme: t } } }); } catch (e) { /* 本地已生效 */ }
    renderMe();
    toast(t === 'light' ? '已换成白色风格 ☀️' : '已换成黑色风格 🌙');
  });

  const presets = {
    outlook: { imap: 'outlook.office365.com', imap_port: 993, smtp: 'smtp.office365.com', smtp_port: 587 },
    m365: { imap: 'outlook.office365.com', imap_port: 993, smtp: 'smtp.office365.com', smtp_port: 587 },
    gmail: { imap: 'imap.gmail.com', imap_port: 993, smtp: 'smtp.gmail.com', smtp_port: 587 },
    qq: { imap: 'imap.qq.com', imap_port: 993, smtp: 'smtp.qq.com', smtp_port: 465 },
    '163': { imap: 'imap.163.com', imap_port: 993, smtp: 'smtp.163.com', smtp_port: 465 },
  };
  $$('#meContent [data-preset]').forEach(b => b.onclick = () => {
    const p = presets[b.dataset.preset];
    $('#f_imap').value = p.imap; $('#f_imap_port').value = p.imap_port;
    $('#f_smtp').value = p.smtp; $('#f_smtp_port').value = p.smtp_port;
    if (b.dataset.preset === 'm365') {
      $('#f_auth').value = 'oauth2';
      toggleAuth();
      toast('Microsoft 365 请用「学校账号登录」，按下面 ①~⑤ 操作即可');
    } else {
      $('#f_auth').value = 'password';
      toggleAuth();
      toast('服务器已填好，记得填邮箱地址和授权码');
    }
  });

  const toggleAuth = () => {
    const isOAuth = $('#f_auth').value === 'oauth2';
    $('#oauthBlock').classList.toggle('hidden', !isOAuth);
    $('#passwordBlock').classList.toggle('hidden', isOAuth);
  };
  $('#f_auth').onchange = toggleAuth;
  toggleAuth();

  // 免注册的公共客户端 ID：点一下就填好
  $$('#meContent [data-cid]').forEach(b => b.onclick = () => {
    $('#f_oauth_cid').value = b.dataset.cid;
    $('#f_auth').value = 'oauth2';
    toggleAuth();
    toast('已填好！现在点下面的「开始登录」');
  });

  $('#btnOAuthStart').onclick = async () => {
    const cid = $('#f_oauth_cid').value.trim();
    if (!cid) { toast('客户端 ID 丢失，请刷新页面'); return; }
    const btn = $('#btnOAuthStart');
    btn.disabled = true;
    btn.textContent = '正在打开授权页面…';
    toast('正在打开微软授权页面…');
    try {
      const d = await api('/api/oauth/start', 'POST', {
        mode: 'browser',
        client_id: cid, tenant: $('#f_oauth_tenant').value.trim() || 'organizations'
      });
      showBrowserLoginModal(d);
    } catch (e) { toast(e.message, 6000); }
    finally {
      btn.disabled = false;
      btn.textContent = '开始登录（浏览器授权）';
    }
  };
  $('#btnOAuthCode') && ($('#btnOAuthCode').onclick = async () => {
    const cid = $('#f_oauth_cid').value.trim();
    if (!cid) { toast('先填「应用程序(客户端) ID」（可以点上面 ①~④ 按钮）'); return; }
    toast('正在向微软申请登录码…');
    try {
      const d = await api('/api/oauth/start', 'POST', {
        mode: 'code',
        client_id: cid, tenant: $('#f_oauth_tenant').value.trim() || 'organizations'
      });
      showOAuthLoginModal(d);
    } catch (e) { toast(e.message, 6000); }
  });
  $('#btnOAuthLogout').onclick = async () => {
    await api('/api/oauth/logout', 'POST', {});
    toast('已退出登录');
    renderMe();
  };

  const sw = (id, key, section) => {
    const el = $('#' + id);
    if (!el) return;
    el.onclick = () => {
      el.classList.toggle('on');
      state.cfg[section][key] = el.classList.contains('on');
    };
  };
  sw('sw_idle', 'use_idle', 'mail');
  sw('sw_autosum', 'auto_summary', 'mail');
  sw('sw_notify', 'notify', 'mail');
  sw('sw_autoext', 'auto_extract', 'calendar');
  sw('sw_aiext', 'ai_backfill', 'calendar');

  $('#btnBackfill').onclick = async () => {
    const btn = $('#btnBackfill');
    btn.disabled = true;
    btn.textContent = '正在启动…';
    try {
      // 顺手把数量和开关存下来
      await api('/api/config', 'POST', {
        config: {
          mail: { history_limit: parseInt($('#f_histlimit').value || '0', 10) || 0 },
          calendar: { ai_backfill: state.cfg.calendar.ai_backfill }
        }
      });
      const r = await api('/api/backfill', 'POST', { use_ai: state.cfg.calendar.ai_backfill });
      toast(r.started ? '开始整理，进度看下方' : ('已经在整理中：' + (r.text || '')));
    } catch (e) { toast(e.message, 5000); }
    finally {
      btn.disabled = false;
      btn.textContent = '📥 开始导入并整理进日历';
    }
  };

  $('#btnSaveMail').onclick = async () => {
    const oldOauth = (state.cfg.mail && state.cfg.mail.oauth) || {};
    const patch = {
      mail: {
        enabled: true,
        auth_method: $('#f_auth').value,
        email: $('#f_email').value.trim(),
        display_name: $('#f_name').value.trim(),
        password: $('#f_password') ? $('#f_password').value : '',
        imap_host: $('#f_imap').value.trim(),
        imap_port: +$('#f_imap_port').value,
        smtp_host: $('#f_smtp').value.trim(),
        smtp_port: +$('#f_smtp_port').value,
        oauth: {
          client_id: ($('#f_oauth_cid') ? $('#f_oauth_cid').value.trim() : '') || oldOauth.client_id || '',
          tenant: ($('#f_oauth_tenant') ? $('#f_oauth_tenant').value.trim() : '') || oldOauth.tenant || 'organizations',
          refresh_token: oldOauth.refresh_token || '',
          access_token: oldOauth.access_token || '',
          expires_at: oldOauth.expires_at || 0,
        }
      }
    };
    patch.mail.use_idle = state.cfg.mail.use_idle;
    patch.mail.auto_summary = state.cfg.mail.auto_summary;
    patch.mail.notify = state.cfg.mail.notify;
    const d = await api('/api/config', 'POST', { config: patch });
    state.cfg = d.config;
    if (state.cfg.mail.notify && 'Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission();
    }
    const isOAuth = state.cfg.mail.auth_method === 'oauth2';
    const loggedIn = !!(state.cfg.mail.oauth && state.cfg.mail.oauth.refresh_token);
    toast(isOAuth && !loggedIn ? '已保存。现在请点「开始登录」完成学校账号登录' : '邮箱设置已保存，正在连接…');
    renderMe();
  };
  $('#btnTestMail').onclick = async () => {
    const oldOauth = (state.cfg.mail && state.cfg.mail.oauth) || {};
    const payload = {
      email: $('#f_email').value.trim(),
      auth_method: $('#f_auth').value,
      password: $('#f_password') ? $('#f_password').value : '',
      oauth: oldOauth,
      imap_host: $('#f_imap').value.trim(), imap_port: +$('#f_imap_port').value,
    };
    toast('正在测试连接…');
    try {
      const d = await api('/api/mail/test', 'POST', payload);
      showModal({ title: '连接测试', body: `<p style="white-space:pre-wrap">${esc(d.message)}</p>`, actions: [{ label: '好', onClick: closeModal }] });
    } catch (e) { toast(e.message, 5000); }
  };
  $('#btnSaveAI').onclick = async () => {
    const patch = {
      ai: {
        base_url: $('#f_ai_base').value.trim(),
        api_key: $('#f_ai_key').value.trim(),
        model: $('#f_ai_model').value.trim(),
        temperature: parseFloat($('#f_ai_temp').value || '0.4'),
        prompts: {
          summary: $('#f_p_summary').value,
          translate: $('#f_p_translate').value,
          reply: $('#f_p_reply').value,
          extract_events: $('#f_p_events').value,
        }
      }
    };
    const d = await api('/api/config', 'POST', { config: patch });
    state.cfg = d.config;
    toast(d.ai_configured ? 'AI 设置好了，可以用了 ✅' : '已保存（API Key 还没填，AI 功能暂不可用）');
    renderMe();
  };
  $('#btnTestAI').onclick = async () => {
    toast('正在测试 AI…');
    try {
      const d = await api('/api/ai', 'POST', { action: 'translate', text: '这是一条测试消息，收到请回复。', target_lang: '英文' });
      showModal({ title: 'AI 测试成功', body: `<p>AI 把测试句翻译成了：</p><p style="background:#F2FBF5;padding:10px;border-radius:8px">${esc(d.result)}</p>`, actions: [{ label: '好', onClick: closeModal }] });
    } catch (e) {
      showModal({ title: 'AI 测试失败', body: `<p>${esc(e.message)}</p>`, actions: [{ label: '好', onClick: closeModal }] });
    }
  };
  $('#btnSaveCal').onclick = async () => {
    const patch = {
      calendar: {
        timezone: $('#f_tz').value.trim(),
        utc_offset_hours: parseFloat($('#f_tzoff').value || '8'),
        week_start: +$('#f_weekstart').value,
        remind_hours: +$('#f_remind').value,
        auto_extract: state.cfg.calendar.auto_extract,
      }
    };
    const d = await api('/api/config', 'POST', { config: patch });
    state.cfg = d.config;
    toast('时间设置已保存');
    renderCalendar();
    renderMe();
  };
  $('#btnAddQR').onclick = () => {
    const list = state.cfg.ui.quick_replies || [];
    if (list.length >= 8) { toast('最多 8 条'); return; }
    list.push('新快捷回复');
    state.cfg.ui.quick_replies = list;
    renderMe();
  };
  $$('#meContent [data-qr-del]').forEach(b => b.onclick = () => {
    state.cfg.ui.quick_replies.splice(+b.dataset.qrDel, 1);
    renderMe();
  });
  $('#btnSaveUI').onclick = async () => {
    const vals = $$('#meContent [data-qr]').map(i => i.value.trim()).filter(Boolean);
    const d = await api('/api/config', 'POST', { config: { ui: { quick_replies: vals, show_original: state.cfg.ui.show_original, long_press_hint: true } } });
    state.cfg = d.config;
    toast('已保存');
    renderQuickReplies();
  };
  $('#btnSeedDemo').onclick = async () => { await api('/api/demo/seed', 'POST', {}); toast('演示数据已载入'); refreshChats(); };
  $('#btnClearDemo2').onclick = async () => { await api('/api/demo/clear', 'POST', {}); toast('演示数据已清除'); refreshChats(); };
  $('#btnExport').onclick = async () => {
    const events = await api('/api/events');
    const blob = new Blob([JSON.stringify({ config: state.cfg, chats: state.chats, events: events.events }, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = '邮聊数据备份.json';
    a.click();
    toast('已导出');
  };
  $('#btnHelp2').onclick = showHelp;

  // 如果正在等待网页登录，继续在后台盯状态
  if (window.__oauthPending) startOAuthPoll();
}

/* ---------- 学校账号（OAuth）登录弹窗 ---------- */
let oauthTimer = null;
function stopOAuthPoll() {
  if (oauthTimer) { clearInterval(oauthTimer); oauthTimer = null; }
}
function startOAuthPoll() {
  stopOAuthPoll();
  window.__oauthPending = true;
  oauthTimer = setInterval(async () => {
    try {
      const s = await api('/api/oauth/status');
      const el = $('#oauthWait');
      const st = $('#oauthStatus');
      if (s.state === 'pending') {
        window.__oauthLastError = null;
        if (el) el.textContent = '等待你在网页上登录…（关掉这个窗也没关系，后台会自动完成）';
        if (st) st.textContent = '状态：⏳ 已拿到登录码，等待你在网页上登录…';
      } else if (s.state === 'ok') {
        stopOAuthPoll();
        window.__oauthPending = false;
        window.__oauthLastError = null;
        if (el) el.textContent = '✅ ' + s.message;
        if (st) st.textContent = '状态：✅ ' + s.message;
        toast('登录成功！邮件正在同步…');
        setTimeout(() => { closeModal(); renderMe(); refreshChats(); }, 1500);
      } else {
        stopOAuthPoll();
        window.__oauthPending = false;
        window.__oauthLastError = s.message || '登录失败';
        if (el) el.innerHTML = '❌ ' + esc(s.message || '登录失败');
        if (st) st.innerHTML = '状态：❌ ' + esc(s.message || '登录失败');
      }
    } catch (e) { /* 网络抖动，下一轮再试 */ }
  }, 2500);
}
function showBrowserLoginModal(d) {
  showModal({
    title: '登录学校邮箱（浏览器授权）',
    body: `
      <p>1️⃣ 已经在浏览器里打开<b>微软授权页面</b>（没弹出来就点下面「打开授权页面」）</p>
      <p>2️⃣ 在网页上点 <b>「接受 / 同意」</b></p>
      <p>3️⃣ ⚠️ 登录完浏览器<b>很可能显示一个"打不开"的页面 —— 这是正常的！</b>
         把<b>地址栏里的整串链接</b>（以 http 开头、带有 <code>code=</code> 字样）复制，粘到下面，点按钮：</p>
      <textarea id="oauthPaste" rows="2" style="width:100%;border:1px solid #ddd;border-radius:8px;padding:8px"
        placeholder="在这里粘贴浏览器地址栏的整串链接"></textarea>
      <button class="primary-btn" id="btnOauthPaste" style="margin-top:8px">我粘好了，完成登录</button>
      <p class="hint">如果网页直接显示"✅ 授权完成"，就不用粘贴，等几秒即可。<br>
      当前回跳方式：<code>${esc(d.redirect_uri)}</code>（若网页报 <b>AADSTS50011</b>，关掉网页回来<b>再点一次「开始登录」</b>自动换格式）</p>
      <p class="hint" id="oauthWait">等待授权完成…（关掉这个窗也没关系，后台自动完成）</p>`,
    actions: [
      { label: '打开授权页面', onClick: () => { try { window.open(d.url, '_blank'); } catch (e) { } } },
      { label: '知道了 / 关闭', onClick: () => { closeModal(); renderMe(); } }]
  });
  const pasteBtn = $('#btnOauthPaste');
  if (pasteBtn) pasteBtn.onclick = async () => {
    const url = $('#oauthPaste').value.trim();
    if (!url) { toast('先粘贴地址栏的整串链接'); return; }
    try {
      const r = await api('/api/oauth/complete', 'POST', { url });
      toast(r.message || '收到！正在完成登录…');
    } catch (e) { toast(e.message, 6000); }
  };
  startOAuthPoll();
}
function showOAuthLoginModal(d) {
  showModal({
    title: '登录学校邮箱',
    body: `
      <p>1️⃣ 用手机或电脑打开这个网址：</p>
      <p style="font-size:19px"><b>${esc(d.verification_uri)}</b></p>
      <p>2️⃣ 输入这串登录码（点一下就复制）：</p>
      <p style="font-size:34px;letter-spacing:8px;text-align:center;background:#F2FBF5;padding:16px;border-radius:12px;cursor:pointer" id="oauthCode">${esc(d.user_code)}</p>
      <p>3️⃣ 用你的<b>学校账号</b>登录（密码只输在微软的页面上，邮聊看不到）</p>
      <p class="hint" id="oauthWait">等待你在网页上登录…（关掉这个窗也没关系，后台会自动完成）</p>`,
    actions: [
      { label: '打开登录页面', onClick: () => { try { window.open(d.verification_uri_complete || d.verification_uri, '_blank'); } catch (e) { } } },
      { label: '知道了 / 关闭', onClick: () => { closeModal(); renderMe(); } }]
  });
  const codeEl = $('#oauthCode');
  if (codeEl) codeEl.onclick = async () => {
    try { await navigator.clipboard.writeText(d.user_code); toast('登录码已复制'); }
    catch (e) { toast('复制失败，请手动记下这串码'); }
  };
  startOAuthPoll();
}

function showHelp() {
  showModal({
    title: '使用说明（超简单版）',
    body: `
      <p><b>1. 这是什么？</b><br>把你的邮箱变成微信：每个人是一个好友，每封邮件是一条聊天消息。</p>
      <p><b>2. 学校 / 公司邮箱怎么连？（Microsoft 365）</b><br>
      「我的 → 邮箱设置」→ 登录方式选「<b>学校账号登录</b>」→ 点「<b>① Office / Outlook</b>」按钮（<b>不用注册任何东西</b>）→
      点「<b>开始登录</b>」→ 在微软的页面上输一串登录码。<b>全程不需要你的密码</b>。不行就换 ②~⑤ 试。</p>
      <p><b>3. 个人邮箱（QQ / 163 / Gmail）怎么连？</b><br>
      登录方式选「<b>授权码</b>」。注意：<b>授权码不是你的登录密码</b>！去邮箱网页版的「设置 → 安全 → 生成授权码/应用密码」，生成一串字母，填进来。</p>
      <p><b>4. AI 怎么用？</b><br>「我的 → AI 助手」填接口地址、API Key、模型名。之后在聊天里点 ✨ 就能让 AI 总结、翻译、帮你写回复。</p>
      <p><b>5. 日历怎么用？</b><br>聊天页右上角 📅 打开日历。邮件里写了时间的，会自动进日历（标着“待确认”，点确认就好）。</p>
      <p><b>6. 小技巧</b><br>点一下消息能翻译/总结/提取日程/引用/复制；输入框旁边的 ✨ 是 AI 写回复；Enter 发送，Shift+Enter 换行。</p>`,
    actions: [{ label: '明白了', primary: true, onClick: closeModal }]
  });
}

/* ---------- 日历 ---------- */
function initCalendar() {
  state.calMonth = tzDate(tzNowTs());
  state.calMonth = new Date(Date.UTC(state.calMonth.getUTCFullYear(), state.calMonth.getUTCMonth(), 1));
  state.selectedDay = dayKey(tzNowTs());
  renderCalendar();
}
function eventsForDay(key) {
  return state.events.filter(e => {
    const k = dayKey(e.start_ts);
    if (k === key) return true;
    if (e.all_day && e.end_ts && dayKey(e.end_ts - 1) === key) return true;
    return false;
  });
}
async function loadEvents() {
  const d = await api('/api/events');
  state.events = d.events || [];
  if ($('#view-calendar').classList.contains('open')) renderCalendar();
}
function renderCalendar() {
  if (!state.calMonth) return;
  const y = state.calMonth.getUTCFullYear(), mo = state.calMonth.getUTCMonth();
  $('#calTitle').textContent = `${y}年${mo + 1}月`;
  const weekStart = (state.cfg && state.cfg.calendar && +state.cfg.calendar.week_start) || 1;
  const names = ['日', '一', '二', '三', '四', '五', '六'];
  const head = [];
  for (let i = 0; i < 7; i++) head.push(names[(weekStart + i) % 7]);
  $('#calWeekdays').innerHTML = head.map(n => `<div>${n}</div>`).join('');

  const first = new Date(Date.UTC(y, mo, 1));
  let lead = (first.getUTCDay() - weekStart + 7) % 7;
  const cells = [];
  const todayKeyStr = dayKey(tzNowTs());
  for (let i = 0; i < 42; i++) {
    const d = new Date(Date.UTC(y, mo, 1 - lead + i));
    const key = `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}`;
    const evs = eventsForDay(key);
    const other = d.getUTCMonth() !== mo;
    cells.push(`
      <div class="cal-cell ${other ? 'other' : ''} ${key === todayKeyStr ? 'today' : ''} ${key === state.selectedDay ? 'selected' : ''}" data-day="${key}">
        <div class="cal-day">${d.getUTCDate()}</div>
        <div class="cal-dots">${evs.slice(0, 3).map(e => `<div class="cal-dot ${e.confirmed ? '' : 'pending'}"></div>`).join('')}</div>
        ${evs.length ? `<div class="cal-ev">${esc(evs[0].title)}</div>` : ''}
      </div>`);
  }
  $('#calGrid').innerHTML = cells.join('');
  $$('#calGrid .cal-cell').forEach(c => c.onclick = () => {
    state.selectedDay = c.dataset.day;
    renderCalendar();
  });

  const dayEvs = eventsForDay(state.selectedDay);
  const dObj = state.selectedDay.split('-');
  $('#dayTitle').textContent = `${+dObj[1]}月${+dObj[2]}日的日程${dayEvs.length ? '（' + dayEvs.length + '）' : ''}`;
  $('#dayAgenda').innerHTML = dayEvs.length ? dayEvs.map(e => eventHtml(e)).join('')
    : '<div class="hint">这一天没有安排，点右上角 ＋ 可以手动加一个。</div>';

  const now = tzNowTs();
  const upcoming = state.events.filter(e => (e.end_ts || e.start_ts) >= now).slice(0, 8);
  $('#upcomingList').innerHTML = upcoming.length ? upcoming.map(e => eventHtml(e)).join('')
    : '<div class="hint">暂时没有即将到来的日程。</div>';
}
function eventHtml(e) {
  const st = tzDate(e.start_ts);
  const timeStr = e.all_day ? '全天'
    : `${pad2(st.getUTCHours())}:${pad2(st.getUTCMinutes())}${e.end_ts ? ' - ' + pad2(tzDate(e.end_ts).getUTCHours()) + ':' + pad2(tzDate(e.end_ts).getUTCMinutes()) : ''}`;
  return `
    <div class="event-item ${e.confirmed ? '' : 'pending'}" data-ev="${e.id}" ${e.source_msg_id ? `data-evjump="${e.id}"` : ''}>
      <div class="event-time">${fmtRel(e.start_ts)}<br>${timeStr}</div>
      <div class="event-main">
        <div class="event-title">${esc(e.title)} ${e.confirmed ? '' : '<span class="tag" style="background:#FF9F0A;color:#fff;font-size:11px;padding:1px 6px;border-radius:5px">待确认</span>'}</div>
        <div class="event-sub">${e.location ? '📍 ' + esc(e.location) + '<br>' : ''}${esc(e.notes || '')}</div>
        ${e.source_msg_id ? `<div class="src-chip" data-evjump="${e.id}">📧 来自 ${esc(e.source_email || '')} 的邮件 · 点这里看原文 →</div>`
          : (e.source_email ? `<div class="src-chip">来自 ${esc(e.source_email)} 的邮件</div>` : '')}
        <div class="event-actions">
          ${e.confirmed ? '' : `<button class="ok" data-evok="${e.id}">确认</button>`}
          <button data-evedit="${e.id}">编辑</button>
          <button class="del" data-evdel="${e.id}">删除</button>
        </div>
      </div>
    </div>`;
}

function scrollToMsg(msgId) {
  renderMessages();
  requestAnimationFrame(() => {
    const el = document.querySelector(`#msgList .bubble[data-id="${msgId}"]`);
    if (!el) {
      toast('这封邮件没在当前列表里（可能太久了），已打开这个人的聊天');
      return;
    }
    el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    el.classList.add('flash');
    setTimeout(() => el.classList.remove('flash'), 2600);
  });
}

async function jumpToLatestUnread(email) {
  await openChat(email);
  const unread = state.msgs.filter(m => m.direction === 'in' && !m.read);
  const target = unread.length ? unread[unread.length - 1] : null;
  if (target) {
    scrollToMsg(target.id);
    toast('📬 已定位到最新未读');
  } else {
    // 没有未读 → 翻到最上面（最早的一封）
    renderMessages();
    requestAnimationFrame(() => {
      const box = $('#msgList');
      if (box) box.scrollTo({ top: 0, behavior: 'smooth' });
    });
    toast('没有未读，已翻到最上面（最早的一封）');
  }
}

async function jumpToSource(ev) {
  if (!ev) return;
  if (!ev.source_msg_id || !ev.source_email) {
    openEventEditor(ev);   // 手动加的日程没有来源邮件 → 打开编辑
    return;
  }
  closeOverlay('#view-calendar');
  await openChat(ev.source_email);
  scrollToMsg(ev.source_msg_id);
  toast('已跳到来源邮件 📧');
}
function openEventEditor(ev) {
  const e = ev || {};
  const st = e.start_ts ? tzDate(e.start_ts) : tzDate(tzNowTs());
  const en = e.end_ts ? tzDate(e.end_ts) : null;
  const dateStr = `${st.getUTCFullYear()}-${pad2(st.getUTCMonth() + 1)}-${pad2(st.getUTCDate())}`;
  const startStr = `${pad2(st.getUTCHours())}:${pad2(st.getUTCMinutes())}`;
  const endStr = en ? `${pad2(en.getUTCHours())}:${pad2(en.getUTCMinutes())}` : '';
  showModal({
    title: e.id ? '编辑日程' : '新建日程',
    body: `
      <div class="form-row"><label>标题</label><input id="ev_title" value="${esc(e.title || '')}" placeholder="例如：产品评审会"></div>
      <div class="form-grid">
        <div class="form-row"><label>日期</label><input id="ev_date" type="date" value="${dateStr}"></div>
        <div class="form-row"><label>开始时间</label><input id="ev_start" type="time" value="${startStr}"></div>
        <div class="form-row"><label>结束时间</label><input id="ev_end" type="time" value="${endStr}"></div>
        <div class="form-row"><label>地点</label><input id="ev_loc" value="${esc(e.location || '')}"></div>
      </div>
      <div class="form-row"><label>备注</label><textarea id="ev_notes" rows="2">${esc(e.notes || '')}</textarea></div>
      <div class="switch-row"><span>全天</span><div class="switch ${e.all_day ? 'on' : ''}" id="ev_allday"></div></div>
    `,
    actions: [
      { label: '取消', onClick: closeModal },
      {
        label: '保存', primary: true, onClick: async () => {
          const title = $('#ev_title').value.trim() || '未命名日程';
          const date = $('#ev_date').value;
          const allDay = $('#ev_allday').classList.contains('on');
          const off = tzOff();
          const offStr = (off >= 0 ? '+' : '-') + pad2(Math.floor(Math.abs(off))) + ':' + pad2(Math.round((Math.abs(off) % 1) * 60));
          const parseT = (t, fallback) => t ? Date.parse(`${date}T${t}:00${offStr}`) / 1000 : fallback;
          const startTs = allDay ? Date.parse(`${date}T00:00:00${offStr}`) / 1000 : parseT($('#ev_start').value, Date.parse(`${date}T09:00:00${offStr}`) / 1000);
          const endTs = allDay ? startTs + 86400 : parseT($('#ev_end').value, startTs + 3600);
          await api('/api/events/save', 'POST', {
            id: e.id || null, title, start_ts: startTs, end_ts: endTs,
            all_day: allDay ? 1 : 0, location: $('#ev_loc').value.trim(),
            notes: $('#ev_notes').value.trim(), confirmed: 1
          });
          closeModal();
          toast('日程已保存');
          loadEvents();
        }
      }]
  });
  $('#ev_allday').onclick = () => $('#ev_allday').classList.toggle('on');
}

/* ---------- 新邮件提醒 ---------- */
function notifyNewMail(subject) {
  if (!(state.cfg && state.cfg.mail && state.cfg.mail.notify)) return;
  if (!('Notification' in window)) return;
  if (Notification.permission === 'granted') {
    try { new Notification('邮聊 · 新邮件', { body: subject || '(无主题)' }); } catch (e) { /* 忽略 */ }
  }
}

/* ---------- 实时推送 ---------- */
function startSSE() {
  const es = new EventSource('/api/stream');
  es.onmessage = (e) => {
    let d = {};
    try { d = JSON.parse(e.data); } catch (err) { return; }
    if (d.type === 'mail') {
      refreshChats();
      notifyNewMail(d.subject);
      if (state.activeEmail && d.email === state.activeEmail && $('#view-chat').classList.contains('open')) {
        api('/api/messages?email=' + encodeURIComponent(state.activeEmail)).then(x => {
          state.msgs = x.messages || [];
          renderMessages();
          api('/api/messages/read', 'POST', { email: state.activeEmail }).catch(() => {});
        }).catch(() => {});
      }
      if (d.subject) toast('📮 新邮件：' + d.subject);
    } else if (d.type === 'status') {
      state.mailStatus = { status: d.status, mode: d.mode };
      const el = $('#statusLine');
      if (el) el.textContent = d.status;
    } else if (d.type === 'events') {
      loadEvents();
    } else if (d.type === 'progress') {
      window.__progressText = d.text;
      const pl = $('#progressLine');
      if (pl) pl.textContent = d.text;
      if (d.done) {
        toast(d.text, 8000);
        renderMe();
        loadEvents();
      }
    } else if (d.type === 'translated') {
      // 自动翻译完成：正在看这个人的聊天就刷新气泡
      if (state.activeChat === d.email) loadMessages();
      refreshChats();
    } else if (d.type === 'summary' || d.type === 'read' || d.type === 'contacts') {
      refreshChats();
    } else if (d.type === 'config') {
      applyTheme();
      api('/api/config').then(d2 => {
        state.cfg = d2.config;
        if ($('#view-me').classList.contains('active')) renderMe();  // 登录完成自动刷新状态
      }).catch(() => {});
    }
  };
  es.onerror = () => { /* 断线时 EventSource 自动重连 */ };
}

/* ---------- 写新邮件 ---------- */
function openCompose() {
  showModal({
    title: '写新邮件',
    body: `
      <div class="form-row"><label>收件人邮箱</label><input id="nc_to" placeholder="someone@example.com"></div>
      <div class="form-row"><label>主题</label><input id="nc_sub" placeholder="邮件主题"></div>
      <div class="form-row"><label>正文</label><textarea id="nc_body" rows="6" placeholder="写点什么…（也可以用聊天里的 ✨ AI 帮你写）"></textarea></div>`,
    actions: [
      { label: '取消', onClick: closeModal },
      {
        label: '发送', primary: true, onClick: async () => {
          const to = $('#nc_to').value.trim();
          if (!to) { toast('先填收件人'); return; }
          try {
            await api('/api/send', 'POST', { to, subject: $('#nc_sub').value.trim(), body: $('#nc_body').value });
            closeModal();
            toast('已发送 ✅');
            refreshChats();
            openChat(to.toLowerCase());
          } catch (e) { toast(e.message, 5000); }
        }
      }]
  });
}

/* ---------- 联系人详情 ---------- */
function openContactInfo() {
  const c = state.contact || {};
  showModal({
    title: '联系人详情',
    body: `
      <div class="form-row"><label>名字（只在本机显示）</label><input id="ci_name" value="${esc(c.name || '')}"></div>
      <div class="hint">邮箱：${esc(c.email || '')}</div>
      <div class="switch-row"><span>星标好友</span><div class="switch ${c.starred ? 'on' : ''}" id="ci_star"></div></div>
      <div class="switch-row"><span>置顶聊天</span><div class="switch ${c.pinned ? 'on' : ''}" id="ci_pin"></div></div>
      <div class="switch-row"><span>消息免打扰</span><div class="switch ${c.muted ? 'on' : ''}" id="ci_mute"></div></div>`,
    actions: [
      { label: '关闭', onClick: closeModal },
      {
        label: '保存', primary: true, onClick: async () => {
          await api('/api/contacts/update', 'POST', {
            email: c.email, name: $('#ci_name').value.trim(),
            starred: $('#ci_star').classList.contains('on') ? 1 : 0,
            pinned: $('#ci_pin').classList.contains('on') ? 1 : 0,
            muted: $('#ci_mute').classList.contains('on') ? 1 : 0,
          });
          closeModal();
          $('#chatTitle').textContent = $('#ci_name').value.trim() || c.email;
          toast('已保存');
          refreshChats();
        }
      }]
  });
  ['ci_star', 'ci_pin', 'ci_mute'].forEach(id => {
    const el = $('#' + id);
    if (el) el.onclick = () => el.classList.toggle('on');
  });
}

/* ---------- 事件绑定 ---------- */
function bindEvents() {
  $$('.tab').forEach(t => t.onclick = () => showTab(t.dataset.tab));
  $('#btnCalendar').onclick = () => { loadEvents(); initCalendarSafe(); openOverlay('#view-calendar'); renderCalendar(); };
  $('#btnReadAll').onclick = async () => {
    try {
      await api('/api/messages/read_all', 'POST', {});
      toast('✅ 已把所有邮件标为已读');
      refreshChats();
    } catch (e) { toast(e.message, 4000); }
  };
  // 双击聊天项 = 快速跳到最新未读那封
  $('#chatList').addEventListener('dblclick', (e) => {
    const item = e.target.closest('.chat-item');
    if (!item) return;
    jumpToLatestUnread(item.dataset.email || '');
  });
  $('#btnCompose').onclick = openCompose;
  $('#btnCompose2').onclick = openCompose;
  $('#btnBackChat').onclick = () => { closeOverlay('#view-chat'); refreshChats(); };
  $('#btnBackCal').onclick = () => closeOverlay('#view-calendar');
  $('#btnChatInfo').onclick = openContactInfo;
  $('#searchChats').oninput = renderChats;
  $('#searchContacts').oninput = renderContacts;
  $('#btnClearDemo').onclick = async () => { await api('/api/demo/clear', 'POST', {}); toast('演示数据已清除'); refreshChats(); };
  $('#btnHelp').onclick = showHelp;

  // 发送
  $('#btnSend').onclick = () => sendMail();
  $('#msgInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMail(); }
  });
  $('#quickRow').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    $('#msgInput').value = b.textContent;
    $('#msgInput').focus();
  });

  // AI
  $('#btnAI').onclick = openAI;
  $('#btnCloseAI').onclick = closeAI;
  $('#btnAIGenerate').onclick = generateReply;
  $('#btnAIRegen').onclick = generateReply;
  $('#btnAIInsert').onclick = () => {
    const d = $('#aiDraft').value.trim();
    if (!d) { toast('还没有草稿'); return; }
    $('#msgInput').value = d;
    closeAI();
    $('#msgInput').focus();
  };
  $('#btnAISend').onclick = () => {
    const d = $('#aiDraft').value.trim();
    if (!d) { toast('还没有草稿'); return; }
    closeAI();
    sendMail(d);
  };
  $('#aiIntentChips').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (b) $('#aiIntent').value = b.textContent;
  });

  // 消息：点击/长按 → 菜单
  let pressTimer = null, pressed = false;
  const msgList = $('#msgList');
  msgList.addEventListener('click', (e) => {
    const bubble = e.target.closest('.bubble');
    if (!bubble) return;
    if (e.target.classList.contains('expand-link')) {
      state.expanded[bubble.dataset.id] = true;
      renderMessages();
      return;
    }
    if (e.target.classList.contains('orig-btn')) {
      // 「看原文 / 看译文」切换（不要弹菜单）
      state.showOrig = state.showOrig || {};
      const id = bubble.dataset.id;
      state.showOrig[id] = !state.showOrig[id];
      renderMessages();
      return;
    }
    if (pressed) { pressed = false; return; }
    openSheet(bubble.dataset.id);
  });
  msgList.addEventListener('contextmenu', (e) => {
    const bubble = e.target.closest('.bubble');
    if (!bubble) return;
    e.preventDefault();
    openSheet(bubble.dataset.id);
  });
  msgList.addEventListener('pointerdown', (e) => {
    const bubble = e.target.closest('.bubble');
    if (!bubble) return;
    pressed = false;
    pressTimer = setTimeout(() => {
      pressed = true;
      if (navigator.vibrate) navigator.vibrate(20);
      openSheet(bubble.dataset.id);
    }, 480);
  });
  ['pointerup', 'pointerleave', 'pointercancel', 'scroll'].forEach(ev =>
    msgList.addEventListener(ev, () => clearTimeout(pressTimer), true));

  // 弹层
  $('#sheetCancel').onclick = closeSheet;
  $('#mask').onclick = closeAllOverlays;
  $('#modalClose').onclick = closeModal;

  // 联系人列表
  $('#contactList').addEventListener('click', (e) => {
    const star = e.target.closest('[data-star]');
    if (star) {
      e.stopPropagation();
      const email = star.dataset.star;
      const c = state.chats.find(x => x.email === email);
      api('/api/contacts/update', 'POST', { email, starred: c && c.starred ? 0 : 1 }).then(() => {
        refreshChats().then(renderContacts);
      });
      return;
    }
    const item = e.target.closest('.contact-item');
    if (item) openChat(item.dataset.email);
  });

  // 聊天列表
  $('#chatList').addEventListener('click', (e) => {
    const item = e.target.closest('.chat-item');
    if (item) openChat(item.dataset.email);
  });

  // 日历
  $('#calPrev').onclick = () => { state.calMonth = new Date(Date.UTC(state.calMonth.getUTCFullYear(), state.calMonth.getUTCMonth() - 1, 1)); renderCalendar(); };
  $('#calNext').onclick = () => { state.calMonth = new Date(Date.UTC(state.calMonth.getUTCFullYear(), state.calMonth.getUTCMonth() + 1, 1)); renderCalendar(); };
  $('#btnCalToday').onclick = () => { initCalendarSafe(); renderCalendar(); };
  $('#btnCalAdd').onclick = () => openEventEditor(null);
  $('#calContent').addEventListener('click', (e) => {
    const ok = e.target.closest('[data-evok]');
    if (ok) {
      api('/api/events/save', 'POST', { id: +ok.dataset.evok, confirmed: 1 }).then(() => { toast('已确认'); loadEvents(); });
      return;
    }
    const del = e.target.closest('[data-evdel]');
    if (del) {
      showModal({
        title: '删除日程？', body: '<p>删掉后可以从邮件里重新提取。</p>',
        actions: [
          { label: '取消', onClick: closeModal },
          {
            label: '删除', primary: true, onClick: async () => {
              await api('/api/events/delete', 'POST', { id: +del.dataset.evdel });
              closeModal(); toast('已删除'); loadEvents();
            }
          }]
      });
      return;
    }
    const ed = e.target.closest('[data-evedit]');
    if (ed) {
      const ev = state.events.find(x => x.id === +ed.dataset.evedit);
      if (ev) openEventEditor(ev);
      return;
    }
    // 点事件条目 → 直接跳到来源邮件
    const jump = e.target.closest('[data-evjump]');
    if (jump) {
      const ev = state.events.find(x => x.id === +jump.dataset.evjump);
      if (ev) jumpToSource(ev);
    }
  });

  // ESC 关闭
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeAllOverlays();
      closeAI();
    }
  });
}
function initCalendarSafe() {
  if (!state.calMonth) initCalendar();
  else {
    state.selectedDay = dayKey(tzNowTs());
    const n = tzDate(tzNowTs());
    state.calMonth = new Date(Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), 1));
  }
}

/* ---------- 启动 ---------- */
async function boot() {
  try {
    const d = await api('/api/config');
    state.cfg = d.config;
    state.mailStatus = d.config && d.config.app ? state.mailStatus : state.mailStatus;
    state.aiConfigured = d.ai_configured;
  } catch (e) {
    toast('本地服务未连接：' + e.message, 6000);
    return;
  }
  bindEvents();
  renderQuickReplies();
  await refreshChats();
  await loadEvents();
  initCalendar();
  startSSE();
  // 若后台有未完成的登录，页面刷新后自动接上，状态不丢
  api('/api/oauth/status').then(s => {
    if (s.state === 'pending') {
      window.__oauthPending = true;
      startOAuthPoll();
    } else if (s.state === 'error' && s.message) {
      window.__oauthLastError = s.message;
    }
    renderMe();
  }).catch(() => {});
  api('/api/status').then(d => {
    state.mailStatus = d.mail || state.mailStatus;
    state.demo = !!d.demo;
    renderChats();
  }).catch(() => {});
}
document.addEventListener('DOMContentLoaded', boot);
