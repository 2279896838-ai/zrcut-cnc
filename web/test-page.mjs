/**
 * 页面无头冒烟测试。
 *
 * 用极简 DOM stub 在 Node 里执行 docs/index.html 内联的应用逻辑，
 * 验证：页面能启动、七个页签可切换、各面板能真实渲染出结果。
 *
 * 这不是替代真实浏览器测试，而是抓「未定义函数」「元素缺失」
 * 「计算抛异常」这类会让页面白屏的致命错误。
 *
 * 用法：
 *   node web/test-page.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const HTML_PATH = path.join(ROOT, 'docs', 'index.html');

const html = fs.readFileSync(HTML_PATH, 'utf8');

/* ==================== 极简 DOM 实现 ==================== */

class ClassList {
  constructor() { this._set = new Set(); }
  add(...names) { names.forEach((n) => this._set.add(n)); }
  remove(...names) { names.forEach((n) => this._set.delete(n)); }
  toggle(name, force) {
    const on = force === undefined ? !this._set.has(name) : !!force;
    if (on) { this._set.add(name); } else { this._set.delete(name); }
    return on;
  }
  contains(name) { return this._set.has(name); }
  toString() { return Array.from(this._set).join(' '); }
}

class NodeStub {
  constructor(tag, ownerDoc) {
    this.tagName = String(tag).toUpperCase();
    this.ownerDocument = ownerDoc;
    this.children = [];
    this.parentNode = null;
    this._text = '';
    this._attrs = {};
    this._listeners = {};
    this.classList = new ClassList();
    this.style = {};
    this.disabled = false;
    this.value = '';
    this.offsetWidth = 1;
  }

  get firstChild() { return this.children.length ? this.children[0] : null; }
  get lastChild() { return this.children.length ? this.children[this.children.length - 1] : null; }
  get childNodes() { return this.children; }
  get textContent() {
    // 真实 DOM 的 textContent 同时包含自身文本节点与子元素文本，
    // 因此这里把 _text 与 children 的文本拼接，而不是二选一。
    var own = this._text || '';
    if (this.children.length === 0) { return own; }
    return own + this.children.map((c) => c.textContent).join('');
  }
  set textContent(v) {
    this._text = String(v);
    this.children = [];
  }
  get className() { return this.classList.toString(); }
  set className(v) {
    this.classList = new ClassList();
    String(v).split(/\s+/).filter(Boolean).forEach((n) => this.classList.add(n));
  }

  appendChild(child) {
    if (!child) { return child; }
    child.parentNode = this;
    this.children.push(child);
    // 模拟 <select>：加入被选中项时同步 value
    if (this.tagName === 'SELECT' && child.selected) {
      this.value = child.value;
    }
    // 模拟 <option> 默认选中第一个
    if (child.tagName === 'OPTION' && this.tagName === 'SELECT' && this.value === '') {
      this.value = child.value;
      child.selected = true;
    }
    return child;
  }
  removeChild(child) {
    const i = this.children.indexOf(child);
    if (i >= 0) {
      this.children.splice(i, 1);
      child.parentNode = null;
      // 模拟浏览器：select 清空后 value 归空
      if (this.tagName === 'SELECT' && this.children.length === 0) {
        this.value = '';
      }
    }
    return child;
  }
  insertBefore(child, ref) {
    const i = this.children.indexOf(ref);
    if (i < 0) { return this.appendChild(child); }
    child.parentNode = this;
    this.children.splice(i, 0, child);
    return child;
  }
  setAttribute(k, v) { this._attrs[k] = String(v); }
  getAttribute(k) { return k in this._attrs ? this._attrs[k] : null; }
  removeAttribute(k) { delete this._attrs[k]; }
  hasAttribute(k) { return k in this._attrs; }
  addEventListener(type, fn) {
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  }
  removeEventListener(type, fn) {
    const arr = this._listeners[type];
    if (!arr) { return; }
    const i = arr.indexOf(fn);
    if (i >= 0) { arr.splice(i, 1); }
  }
  dispatchEvent(evt) {
    const arr = this._listeners[evt.type] || [];
    arr.slice().forEach((fn) => fn.call(this, evt));
    return true;
  }
  click() {
    this.dispatchEvent({ type: 'click', target: this, preventDefault() {} });
  }

  _walk(fn) {
    for (const c of this.children) { fn(c); c._walk(fn); }
  }
  _match(sel) {
    if (sel.startsWith('#')) { return this.getAttribute('id') === sel.slice(1); }
    if (sel.startsWith('.')) { return this.classList.contains(sel.slice(1)); }
    if (sel.startsWith('[')) {
      const m = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(sel);
      if (!m) { return false; }
      if (m[2] === undefined) { return this.hasAttribute(m[1]); }
      return this.getAttribute(m[1]) === m[2];
    }
    return this.tagName === sel.toUpperCase();
  }
  querySelector(sel) {
    let found = null;
    this._walk((n) => { if (!found && n._match(sel.trim())) { found = n; } });
    return found;
  }
  querySelectorAll(sel) {
    const out = [];
    this._walk((n) => { if (n._match(sel.trim())) { out.push(n); } });
    return out;
  }
}

class DocumentStub extends NodeStub {
  constructor() {
    super('#document', null);
    this.ownerDocument = this;
    this.readyState = 'complete';
    this.title = '';
    this.documentElement = new NodeStub('html', this);
    this.body = new NodeStub('body', this);
    this.head = new NodeStub('head', this);
    this.appendChild(this.documentElement);
    this.documentElement.appendChild(this.head);
    this.documentElement.appendChild(this.body);
    this._registry = new Map();
  }
  createElement(tag) { return new NodeStub(tag, this); }
  createTextNode(text) {
    const n = new NodeStub('#text', this);
    n._text = String(text);
    return n;
  }
  getElementById(id) {
    return this._registry.get(id) || null;
  }
  register(el) {
    const id = el.getAttribute('id');
    if (id) { this._registry.set(id, el); }
  }
}

/* ==================== 从 HTML 提取脚本并执行 ==================== */

function extractScripts(source) {
  const out = [];
  const re = /<script id="([^"]+)"(?:\s+type="application\/json")?>([\s\S]*?)<\/script>/g;
  let m;
  while ((m = re.exec(source)) !== null) {
    out.push({ id: m[1], body: m[2] });
  }
  return out;
}

const scripts = extractScripts(html);
if (scripts.length < 3) {
  console.error(`[失败] 期望至少 3 个内联脚本，实际 ${scripts.length}`);
  process.exit(1);
}

// 建立 DOM：把 HTML 里带 id 的元素登记进文档
const doc = new DocumentStub();

// 从 HTML 里抓取所有 id，创建对应元素（值从 input 的 value 属性取）
const idRe = /<(input|select|div|button|section|header|nav|footer|main|pre|table|span|style|script|a|h1|h2|h3)([^>]*?)id="([\w-]+)"([^>]*)>/g;
let im;
const created = [];
while ((im = idRe.exec(html)) !== null) {
  const tag = im[1];
  const id = im[3];
  const attrs = im[2] + im[4];
  const el = doc.createElement(tag);
  el.setAttribute('id', id);
  const valM = /\bvalue="([^"]*)"/.exec(attrs);
  if (valM && (tag === 'input' || tag === 'select')) { el.value = valM[1]; }
  const clsM = /\bclass="([^"]*)"/.exec(attrs);
  if (clsM) { el.className = clsM[1]; }
  const dataTabM = /\bdata-tab="([^"]*)"/.exec(attrs);
  if (dataTabM) { el.setAttribute('data-tab', dataTabM[1]); }
  const dataKbqM = /\bdata-kbq="([^"]*)"/.exec(attrs);
  if (dataKbqM) { el.setAttribute('data-kbq', dataKbqM[1]); }
  doc.body.appendChild(el);
  doc.register(el);
  created.push({ id, tag, el });
}

// 把每个 <select> 的选项也建出来（app.js 会 clearNode 后重新填充，所以只需初始空）
const selectIds = created.filter((c) => c.tag === 'select').map((c) => c.id);

// 构造 window / location / history
const locationStub = {
  hash: '',
  href: 'http://localhost/',
  search: ''
};
const historyStub = {
  replaceState(_s, _t, url) {
    if (typeof url === 'string' && url.startsWith('#')) {
      locationStub.hash = url;
    }
  }
};

const sandbox = {
  window: null,
  document: doc,
  location: locationStub,
  history: historyStub,
  console,
  setTimeout: (fn) => { try { fn(); } catch (e) { /* 忽略动画回调错误 */ } return 0; },
  clearTimeout: () => {},
  Math,
  Date,
  JSON,
  Number,
  String,
  Object,
  Array,
  Error,
  parseInt,
  parseFloat,
  isNaN,
  Node: NodeStub,
  Event: class { constructor(type) { this.type = type; } },
  HTMLElement: NodeStub
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.window.scrollTo = () => {};
sandbox.window.print = () => {};
sandbox.window.addEventListener = () => {};
sandbox.window.innerWidth = 1280;
sandbox.window.innerHeight = 800;

const context = vm.createContext(sandbox);

const failures = [];
const checks = [];

function check(label, fn) {
  try {
    const r = fn();
    checks.push({ label, ok: true });
    return r;
  } catch (e) {
    checks.push({ label, ok: false, err: e.message });
    failures.push(`${label}: ${e.message}`);
    return null;
  }
}

// 执行脚本：engine → app → mount
const byId = new Map(scripts.map((s) => [s.id, s.body]));

check('engine.js 执行', () => {
  vm.runInContext(byId.get('zrcut-engine'), context, { filename: 'engine.js' });
  if (!context.ZR) { throw new Error('未挂载 ZR 全局对象'); }
});

check('知识数据挂载', () => {
  const mount = byId.get('zrcut-mount');
  // 数据脚本是 application/json，需要先塞进 DOM
  const dataNode = new NodeStub('script', doc);
  dataNode.setAttribute('id', 'zrcut-data');
  // 取原始 JSON 文本（第一个 type=application/json 的脚本）
  const dataScript = scripts.find((s) => s.body.trim().startsWith('{"items"'));
  if (!dataScript) { throw new Error('未找到知识数据脚本'); }
  dataNode._text = dataScript.body;
  doc.body.appendChild(dataNode);
  doc.register(dataNode);
  vm.runInContext(mount, context, { filename: 'mount.js' });
  const items = context.window.ZRCUT_KNOWLEDGE_ITEMS;
  if (!Array.isArray(items) || items.length === 0) {
    throw new Error('知识条目未挂载');
  }
});

check('app.js 执行并启动', () => {
  vm.runInContext(byId.get('zrcut-app'), context, { filename: 'app.js' });
});

// 启动后检查各面板是否渲染出内容。
// 注意：stub 未还原 HTML 嵌套，结果容器是 panel 的兄弟节点，
// 因此断言直接针对结果容器而非 panel。
function textOf(id) {
  const el = doc.getElementById(id);
  return el ? el.textContent || '' : '';
}

function preview(s, n = 160) {
  const t = String(s).replace(/\s+/g, ' ').trim();
  return t.length > n ? t.slice(0, n) + '…' : t;
}

check('参数推荐已渲染', () => {
  const t = textOf('recResult');
  if (t.length < 80) {
    throw new Error(`结果过少（长度 ${t.length}）：${preview(t)}`);
  }
  if (t.indexOf('主方案') < 0) { throw new Error('缺少「主方案」：' + preview(t)); }
  if (t.indexOf('P0') < 0) { throw new Error('缺少主方案标签：' + preview(t)); }
  if (t.indexOf('Pc') < 0) { throw new Error('缺少功率指标：' + preview(t)); }
});

check('系统能力已渲染', () => {
  const t = textOf('aboutContent');
  if (t.length < 200) {
    throw new Error(`内容过少（长度 ${t.length}）：${preview(t)}`);
  }
  if (t.indexOf('284') < 0) { throw new Error('缺少测试数量：' + preview(t)); }
  if (t.indexOf('数值可信链') < 0) { throw new Error('缺少架构说明：' + preview(t)); }
});

// 逐个点击页签按钮，确认均可切换且不抛异常
const tabs = created.filter((c) => c.tag === 'button' && c.el.getAttribute('data-tab'));
tabs.forEach((t) => {
  const name = t.el.getAttribute('data-tab');
  check(`页签切换 → ${name}`, () => {
    t.el.click();
    if (context.window.location.hash !== '#' + name) {
      throw new Error('hash 未更新为 ' + name);
    }
    const panel = doc.getElementById('panel-' + name);
    if (!panel) { throw new Error('面板不存在'); }
    if (!panel.classList.contains('is-active')) {
      throw new Error('面板未被激活');
    }
  });
});

// 触发各面板的计算按钮
const resultMap = {
  recRun: 'recResult', valRun: 'valResult', valOverrun: 'valResult',
  prdRun: 'prdResult', cardRun: 'cardResult', clsRun: 'clsResult',
  clsPresetSevere: 'clsResult', kbRun: 'kbResult'
};

const actions = [
  { id: 'valRun', name: '约束校验', expect: '校验概览' },
  { id: 'valOverrun', name: '越限算例', expect: '收敛过程' },
  { id: 'prdRun', name: '结果预测', expect: '置信区间' },
  { id: 'cardRun', name: '工艺卡', expect: 'PC-' },
  { id: 'clsRun', name: '回填闭环', expect: '偏差判定' },
  { id: 'kbRun', name: '知识检索', expect: '权威度' }
];

actions.forEach((a) => {
  check(`执行「${a.name}」`, () => {
    const btn = doc.getElementById(a.id);
    if (!btn) { throw new Error('按钮不存在: ' + a.id); }
    if (btn.disabled) { throw new Error('按钮处于禁用状态'); }
    btn.click();
    const target = doc.getElementById(resultMap[a.id]);
    if (!target) { throw new Error('结果容器不存在'); }
    const text = target.textContent || '';
    if (text.length < 20) {
      throw new Error(`结果为空（长度 ${text.length}）`);
    }
    if (text.indexOf(a.expect) < 0) {
      throw new Error(`缺少「${a.expect}」：` + preview(text));
    }
  });
});

// 签发按钮
check('工艺卡签发', () => {
  const btn = doc.getElementById('cardIssue');
  if (!btn) { throw new Error('签发按钮不存在'); }
  if (btn.disabled) { throw new Error('签发按钮仍禁用'); }
  btn.click();
  const t = doc.getElementById('cardResult').textContent;
  if (t.indexOf('LOCKED') < 0) { throw new Error('未变为 LOCKED 状态'); }
});

// 越限算例应检出失败项
check('越限算例检出失败', () => {
  doc.getElementById('valOverrun').click();
  const t = textOf('valResult');
  if (t.indexOf('C-03') < 0 && t.indexOf('C-06') < 0) {
    throw new Error('未显示 C-03/C-06 约束：' + preview(t));
  }
  if (t.indexOf('收敛') < 0) {
    throw new Error('未显示收敛过程：' + preview(t));
  }
});

// 严重档应触发冻结
check('严重档触发冻结', () => {
  doc.getElementById('clsPresetSevere').click();
  const t = textOf('clsResult');
  if (t.indexOf('SEVERE') < 0 && t.indexOf('严重') < 0) {
    throw new Error('严重档判定未生效：' + preview(t));
  }
  if (t.indexOf('冻结') < 0) {
    throw new Error('缺少冻结说明：' + preview(t));
  }
});

// 无关查询应返回空
check('无关查询返回空', () => {
  const q = doc.getElementById('kbQuery');
  q.value = '火星探测器轨道参数';
  doc.getElementById('kbRun').click();
  const t = textOf('kbResult');
  if (t.indexOf('未检索到') < 0) {
    throw new Error('未正确返回空结果提示：' + preview(t));
  }
});

// 有依据的查询应命中 KB 条目
check('有效查询命中条目', () => {
  const q = doc.getElementById('kbQuery');
  q.value = '304 不锈钢 内冷';
  doc.getElementById('kbRun').click();
  const t = textOf('kbResult');
  if (t.indexOf('KB-SST-0008') < 0) {
    throw new Error('未命中预期条目：' + preview(t));
  }
});

/* ==================== 输出 ==================== */
const okCount = checks.filter((c) => c.ok).length;
console.log('='.repeat(64));
console.log('智刃工艺 · 页面无头冒烟测试');
console.log('='.repeat(64));
checks.forEach((c) => {
  console.log(`  ${c.ok ? '✓' : '✗'} ${c.label}${c.ok ? '' : '  → ' + c.err}`);
});
console.log('-'.repeat(64));
console.log(`合计 ${checks.length} 项，通过 ${okCount} 项，失败 ${failures.length} 项`);
if (failures.length) {
  console.log('');
  console.log('失败明细：');
  failures.forEach((f) => console.log('  ✗ ' + f));
  process.exit(1);
}
console.log('页面可正常启动，七个页签与全部交互按钮均能产出结果');
console.log('='.repeat(64));
process.exit(0);
