'use strict';

const EXAMPLES = [
  '总共有多少张专辑？',
  '每位艺人的专辑数量是多少？',
  '销售额最高的 5 位客户是谁？',
  '每种音乐类型各有多少首曲目？',
];

const chat = document.getElementById('chat');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');
const badge = document.getElementById('badge');
const examples = document.getElementById('examples');

let currentBubble = null;
let sending = false;

// ---------------- 工具 ----------------
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// 极简 Markdown：代码块、表格、行内代码、加粗、段落。
function renderMarkdown(text) {
  if (!text) return '';
  let src = escapeHtml(String(text));

  const codes = [];
  src = src.replace(/```[^\n]*\n([\s\S]*?)```/g, (m, code) => {
    codes.push('<pre><code>' + code + '</code></pre>');
    return '\u0000' + (codes.length - 1) + '\u0000';
  });

  const lines = src.split('\n');
  const out = [];
  let table = [];

  const flushTable = () => {
    if (!table.length) return;
    const head = '<tr>' + table[0].map(c => '<th>' + c + '</th>').join('') + '</tr>';
    const body = table.slice(1)
      .map(r => '<tr>' + r.map(c => '<td>' + c + '</td>').join('') + '</tr>')
      .join('');
    out.push('<table><thead>' + head + '</thead><tbody>' + body + '</tbody></table>');
    table = [];
  };

  const inline = (t) => t
    .replace(/\u0000(\d+)\u0000/g, (m, i) => codes[Number(i)])
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

  for (const raw of lines) {
    const line = raw.trimEnd();
    const t = line.trim();
    if (t.startsWith('|') && t.endsWith('|')) {
      const cells = t.slice(1, -1).split('|').map(c => c.trim());
      if (cells.every(c => /^:?-{2,}:?$/.test(c))) continue; // 表头分隔行
      table.push(cells);
      continue;
    }
    flushTable();
    if (t === '') continue;
    out.push('<p>' + inline(t) + '</p>');
  }
  flushTable();

  return out.join('');
}

// ---------------- 渲染 ----------------
function scrollDown() {
  chat.scrollTop = chat.scrollHeight;
}

function appendUser(text) {
  const msg = document.createElement('div');
  msg.className = 'message user';
  msg.innerHTML = '<div class="bubble">' + escapeHtml(text).replace(/\n/g, '<br>') + '</div>';
  chat.appendChild(msg);
  scrollDown();
}

function appendAssistant() {
  const msg = document.createElement('div');
  msg.className = 'message assistant';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.innerHTML =
    '<div class="progress"><span class="spinner"></span><span class="stage-text">正在准备…</span></div>';
  msg.appendChild(bubble);
  chat.appendChild(msg);
  currentBubble = bubble;
  scrollDown();
}

function setStage(label, extra) {
  if (!currentBubble) return;
  const p = currentBubble.querySelector('.progress');
  if (!p) return;
  p.querySelector('.stage-text').textContent = label + (extra ? ' · ' + extra : '');
}

function finishAssistant(html) {
  if (!currentBubble) return;
  currentBubble.innerHTML = html;
  currentBubble = null;
  scrollDown();
}

function buildDetails(d) {
  const rows = [];
  if (d.sql) rows.push(['SQL', '<pre><code>' + escapeHtml(d.sql) + '</code></pre>']);
  const meta = [];
  if (d.tables && d.tables.length) meta.push('涉及表：' + d.tables.join(', '));
  if (d.row_count != null) meta.push('返回行数：' + d.row_count);
  if (d.repair_attempts) meta.push('修复次数：' + d.repair_attempts);
  if (d.truncated) meta.push('结果已截断');
  if (meta.length) rows.push(['概要', escapeHtml(meta.join(' · '))]);
  if (d.evidence && d.evidence.length) rows.push(['依据', escapeHtml(d.evidence.join('；'))]);
  if (d.caveats && d.caveats.length) rows.push(['说明', escapeHtml(d.caveats.join('；'))]);
  if (d.error) rows.push(['错误', escapeHtml(d.error)]);
  if (!rows.length) return null;

  const body = rows.map(([k, v]) =>
    '<div class="detail-row"><span class="detail-key">' + k + '</span>' +
    '<div class="detail-val">' + v + '</div></div>'
  ).join('');
  return '<details class="details"><summary>执行详情</summary>' + body + '</details>';
}

function onResult(d) {
  const html = '<div class="answer">' + renderMarkdown(d.answer) + '</div>' +
    (buildDetails(d) || '');
  finishAssistant(html);
}

function onError(payload) {
  finishAssistant('<div class="error">处理失败：' +
    escapeHtml(payload.message || '未知错误') + '</div>');
}

// ---------------- SSE ----------------
function handleFrame(raw) {
  let type = null;
  let data = null;
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) type = line.slice(6).trim();
    else if (line.startsWith('data:')) data = line.slice(5).trim();
  }
  if (!type || data == null) return;

  let payload;
  try { payload = JSON.parse(data); } catch (e) { return; }

  if (type === 'stage') {
    let extra = '';
    if (payload.sql) extra = payload.sql;
    else if (payload.row_count != null) extra = '返回 ' + payload.row_count + ' 行';
    setStage(payload.label || payload.stage, extra);
  } else if (type === 'result') {
    onResult(payload);
  } else if (type === 'error') {
    onError(payload);
  }
}

async function send() {
  const text = input.value.trim();
  if (!text || sending) return;

  sending = true;
  sendBtn.disabled = true;
  input.value = '';
  autoResize();
  appendUser(text);
  appendAssistant();

  try {
    const resp = await fetch('/api/query/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: text }),
    });

    if (!resp.ok || !resp.body) {
      const errText = await resp.text();
      onError({ message: 'HTTP ' + resp.status + ' ' + errText });
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        handleFrame(frame);
      }
    }
  } catch (err) {
    onError({ message: String(err) });
  } finally {
    sending = false;
    sendBtn.disabled = false;
  }
}

function autoResize() {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 160) + 'px';
}

// ---------------- 初始化 ----------------
function renderExamples() {
  examples.innerHTML = EXAMPLES
    .map(q => '<button class="chip" type="button">' + escapeHtml(q) + '</button>')
    .join('');
  examples.querySelectorAll('.chip').forEach(btn => {
    btn.addEventListener('click', () => {
      input.value = btn.textContent;
      input.focus();
      autoResize();
    });
  });
}

async function loadHealth() {
  try {
    const r = await fetch('/api/health');
    const d = await r.json();
    badge.textContent = 'backend=' + d.backend + ' · model=' + d.model;
    badge.classList.add('ok');
  } catch (e) {
    badge.textContent = '后端未连接';
    badge.classList.add('err');
  }
}

sendBtn.addEventListener('click', send);
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});
input.addEventListener('input', autoResize);

renderExamples();
loadHealth();
