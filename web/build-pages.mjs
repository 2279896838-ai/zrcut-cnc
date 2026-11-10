#!/usr/bin/env node
/**
 * 打包 GitHub Pages 单页应用。
 *
 * 把 web/static/ 下的 HTML、CSS、两个 JS 以及知识条目数据
 * 全部内联成单个 docs/index.html，不引用任何外部资源，
 * 因此可直接部署到 GitHub Pages 或在本地双击打开。
 *
 * 用法：
 *   node web/build-pages.mjs
 * 产物：
 *   docs/index.html
 */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const WEB_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(WEB_DIR, '..');
const STATIC_DIR = path.join(WEB_DIR, 'static');
const GOLDEN = path.join(WEB_DIR, 'golden-vectors.json');
const OUT = path.join(ROOT, 'docs', 'index.html');

function readText(p) {
  return fs.readFileSync(p, 'utf8');
}

/** 序列化为可安全内联进 script 标签的 JSON */
function jsonForScript(value) {
  return JSON.stringify(value)
    .replace(/</g, '\\u003c')
    .replace(/>/g, '\\u003e')
    .replace(/&/g, '\\u0026')
    .replace(/\u2028/g, '\\u2028')
    .replace(/\u2029/g, '\\u2029');
}

/** 内联脚本，拒绝含不可安全内联序列的内容 */
function inlineScript(code, id) {
  if (/<\/script/i.test(code)) {
    throw new Error(`脚本 ${id} 含 </script 序列，无法安全内联`);
  }
  if (code.indexOf('<!--') !== -1) {
    throw new Error(`脚本 ${id} 含 <!-- 序列，无法安全内联`);
  }
  return `<script id="${id}">\n${code}\n</script>`;
}

function main() {
  const htmlPath = path.join(STATIC_DIR, 'index.html');
  const cssPath = path.join(STATIC_DIR, 'style.css');
  const enginePath = path.join(STATIC_DIR, 'engine.js');
  const appPath = path.join(STATIC_DIR, 'app.js');

  let html = readText(htmlPath);
  const css = readText(cssPath);
  const engine = readText(enginePath);
  const app = readText(appPath);
  const golden = JSON.parse(readText(GOLDEN));

  if (/<\/style/i.test(css)) {
    throw new Error('style.css 含 </style 序列，无法安全内联');
  }

  const knowledgeItems = golden.knowledge.items;
  if (!Array.isArray(knowledgeItems) || knowledgeItems.length === 0) {
    throw new Error('golden-vectors.json 缺少 knowledge.items');
  }

  // 1) 内联 CSS
  const cssPattern = /<link[^>]*href="\/static\/style\.css"[^>]*>/i;
  if (!cssPattern.test(html)) {
    throw new Error('index.html 中未找到 /static/style.css 引用');
  }
  const banner = '\n\n/* ===== build-pages 注入：单文件 Pages 构建 ===== */\n';
  html = html.replace(cssPattern, () => `<style id="zrcut-style">${banner}${css}\n</style>`);

  // 2) 内联知识数据
  const dataScript =
    `<script id="zrcut-data" type="application/json">` +
    jsonForScript({ items: knowledgeItems }) +
    `</script>`;

  // 3) 内联 engine.js
  const enginePattern = /<script[^>]*src="\/static\/engine\.js"[^>]*><\/script>/i;
  if (!enginePattern.test(html)) {
    throw new Error('index.html 中未找到 /static/engine.js 引用');
  }
  html = html.replace(enginePattern, () => inlineScript(engine, 'zrcut-engine'));

  // 4) 内联 app.js，并在其后追加数据挂载脚本
  const appPattern = /<script[^>]*src="\/static\/app\.js"[^>]*><\/script>/i;
  if (!appPattern.test(html)) {
    throw new Error('index.html 中未找到 /static/app.js 引用');
  }
  const mountScript =
    '<script id="zrcut-mount">\n' +
    '(function(){try{' +
    'var node=document.getElementById("zrcut-data");' +
    'var data=JSON.parse(node.textContent);' +
    'window.ZRCUT_KNOWLEDGE_ITEMS=data.items;' +
    '}catch(e){window.ZRCUT_KNOWLEDGE_ITEMS=[];' +
    'console.error("知识数据挂载失败",e);}})();\n' +
    '</script>';
  html = html.replace(appPattern, () => dataScript + '\n' + inlineScript(app, 'zrcut-app') + '\n' + mountScript);

  // 5) 元信息
  const buildTime = new Date().toISOString();
  html = html.replace(
    /<title>[\s\S]*?<\/title>/i,
    '<title>智刃工艺 ZR-CUT · CNC 切削工艺参数智能推荐系统</title>'
  );
  html = html.replace(
    '</head>',
    `  <meta name="zrcut-build" content="pages-single-file" />\n` +
    `  <meta name="zrcut-build-time" content="${buildTime}" />\n</head>`
  );
  html = html.replace(
    '<!DOCTYPE html>',
    '<!DOCTYPE html>\n<!-- 由 web/build-pages.mjs 生成：单文件静态应用，含内联计算引擎与知识数据。 -->'
  );

  // 6) 产物校验
  const mustContain = [
    'id="zrcut-engine"',
    'id="zrcut-app"',
    'id="zrcut-data"',
    'id="zrcut-style"',
    'data-tab="recommend"',
    'data-tab="validate"',
    'data-tab="knowledge"',
    '智刃工艺',
    '参数推荐',
    '约束校验',
    '工艺卡',
    '回填闭环',
    '十条硬约束'
  ];
  mustContain.forEach((token) => {
    if (html.indexOf(token) === -1) {
      throw new Error(`产物缺少关键内容：${token}`);
    }
  });

  // 7) 禁止外部与相对资源引用
  if (/(?:src|href)\s*=\s*["']\/(?:static|api)\//i.test(html)) {
    throw new Error('产物仍引用 /static 或 /api 相对资源');
  }
  if (/(?:src|href)\s*=\s*["']https?:\/\//i.test(html)) {
    throw new Error('产物含外部 http(s) 资源引用');
  }
  if (/@import\s+url\(/i.test(html)) {
    throw new Error('产物 CSS 含外部 @import');
  }
  if (/<script[^>]*\ssrc=/i.test(html)) {
    throw new Error('产物仍有外链 script');
  }
  if (/<link[^>]*rel="stylesheet"/i.test(html)) {
    throw new Error('产物仍有外部样式表');
  }

  // 8) 写出
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, html, 'utf8');

  const bytes = Buffer.byteLength(html, 'utf8');
  const sha = crypto.createHash('sha256').update(html).digest('hex').slice(0, 16).toUpperCase();
  const version = crypto.createHash('sha256').update(html).digest('hex').slice(0, 7);

  console.log('='.repeat(64));
  console.log('智刃工艺 · GitHub Pages 单文件构建完成');
  console.log('='.repeat(64));
  console.log(`输出    ${OUT}`);
  console.log(`大小    ${(bytes / 1024).toFixed(1)} KB`);
  console.log(`SHA256  ${sha}…`);
  console.log(`版本号  ${version}`);
  console.log(`知识条目 ${knowledgeItems.length} 条`);
  console.log(`引擎    ${(engine.length / 1024).toFixed(1)} KB`);
  console.log(`应用    ${(app.length / 1024).toFixed(1)} KB`);
  console.log(`样式    ${(css.length / 1024).toFixed(1)} KB`);
  console.log('='.repeat(64));
  console.log(`本地打开 file:///${OUT.replace(/\\/g, '/')}`);
}

main();
