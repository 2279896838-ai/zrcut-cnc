/**
 * JS 引擎一致性测试。
 *
 * 用 Python 端生成的 web/golden-vectors.json 作为基准，逐项比对
 * web/static/engine.js 的输出。任何一项偏差超过 1e-4 即失败。
 *
 * 用法：
 *   node web/test-engine.mjs
 * 退出码：
 *   0 全部通过；1 存在偏差
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const GOLDEN = path.join(HERE, 'golden-vectors.json');
const ENGINE = path.join(HERE, 'static', 'engine.js');

const require = createRequire(import.meta.url);
const ZR = require(ENGINE);
const golden = JSON.parse(fs.readFileSync(GOLDEN, 'utf8'));

const TOL = 1e-4;
let checks = 0;
let fails = [];

function near(label, got, want, tol = TOL) {
  checks += 1;
  if (want === null || want === undefined) {
    if (got !== null && got !== undefined) {
      fails.push(`${label}: 期望 null，实际 ${got}`);
    }
    return;
  }
  if (typeof want === 'string') {
    if (String(got) !== want) {
      fails.push(`${label}: 期望 "${want}"，实际 "${got}"`);
    }
    return;
  }
  if (typeof want === 'boolean') {
    if (Boolean(got) !== want) {
      fails.push(`${label}: 期望 ${want}，实际 ${got}`);
    }
    return;
  }
  const diff = Math.abs(Number(got) - Number(want));
  const denom = Math.abs(want);
  const rel = denom < 1e-12 ? diff : diff / denom;
  if (!(rel <= tol)) {
    fails.push(`${label}: 期望 ${want}，实际 ${got}（相对偏差 ${(rel * 100).toFixed(4)}%）`);
  }
}

function section(title) {
  console.log('');
  console.log('── ' + title + ' ' + '─'.repeat(Math.max(0, 56 - title.length)));
}

/* ==================== 1. 领域常量 ==================== */
section('领域常量');
{
  const g = golden.domain;
  // 机床
  near('machine.spindleMaxRpm', ZR.MACHINE.spindleMaxRpm, g.machine.spindleMaxRpm);
  near('machine.powerContinuousKw', ZR.MACHINE.powerContinuousKw, g.machine.powerContinuousKw);
  near('machine.feedMax', ZR.MACHINE.feedMax, g.machine.feedMax);
  near('machine.naturalFrequencyHz', ZR.MACHINE.naturalFrequencyHz, g.machine.naturalFrequencyHz);
  near('machine.code', ZR.MACHINE.code, g.machine.code);

  // 材料
  Object.keys(g.materials).forEach((code) => {
    const gm = g.materials[code];
    const jm = ZR.MATERIALS[code];
    if (!jm) { fails.push(`材料缺失: ${code}`); checks += 1; return; }
    ['kc', 'taylorC', 'taylorN', 'kRa', 'vcMin', 'vcMax', 'fzMin', 'fzMax', 'aeDMax', 'apDMax'].forEach((k) => {
      near(`materials.${code}.${k}`, jm[k], gm[k]);
    });
    near(`materials.${code}.requiresCoolant`, jm.requiresCoolant, gm.requiresCoolant);
  });

  // 刀具
  Object.keys(g.tools).forEach((code) => {
    const gt = g.tools[code];
    const jt = ZR.TOOLS[code];
    if (!jt) { fails.push(`刀具缺失: ${code}`); checks += 1; return; }
    ['diameter', 'teeth', 'overhang', 'cornerRadius', 'overhangRatio'].forEach((k) => {
      near(`tools.${code}.${k}`, jt[k], gt[k]);
    });
    near(`tools.${code}.isBall`, jt.isBall, gt.isBall);
  });

  // 工序
  Object.keys(g.operations).forEach((code) => {
    const go = g.operations[code];
    const jo = ZR.OPERATIONS[code];
    if (!jo) { fails.push(`工序缺失: ${code}`); checks += 1; return; }
    near(`operations.${code}.name`, jo.name, go.name);
    near(`operations.${code}.typicalTools`, jo.typicalTools.join(','), go.typicalTools.join(','));
  });
  console.log(`  材料 ${Object.keys(g.materials).length} 种、刀具 ${Object.keys(g.tools).length} 把、工序 ${Object.keys(g.operations).length} 类`);
}

/* ==================== 2. 计算引擎 ==================== */
section('计算引擎');
{
  golden.calc.forEach((v, i) => {
    const mat = ZR.MATERIALS[v.input.material];
    const tool = ZR.TOOLS[v.input.tool];
    const r = ZR.computeAll(mat, tool, {
      vc: v.input.vc, fz: v.input.fz, ap: v.input.ap, ae: v.input.ae
    });
    const tag = `[${i}] ${v.input.material}+${v.input.tool}`;
    near(`${tag} n`, r.n, v.output.n);
    near(`${tag} vf`, r.vf, v.output.vf);
    near(`${tag} mrr`, r.mrr, v.output.mrr);
    near(`${tag} pc`, r.pc, v.output.pc);
    near(`${tag} raTh`, r.raTh, v.output.raTh);
    near(`${tag} raPred`, r.raPred, v.output.raPred);
    near(`${tag} toolLife`, r.toolLife, v.output.toolLife);
    near(`${tag} overhangRatio`, r.overhangRatio, v.output.overhangRatio);
    near(`${tag} toothPassHz`, r.toothPassHz, v.output.toothPassHz);
    if (v.output.machiningTime !== null) {
      near(`${tag} machiningTime`, v.input.volume / r.mrr, v.output.machiningTime);
    }
  });
  console.log(`  ${golden.calc.length} 组算例 × 10 指标`);
}

/* ==================== 3. 十条约束校验 ==================== */
section('十条约束校验');
{
  golden.validation.forEach((v, i) => {
    const mat = ZR.MATERIALS[v.input.material];
    const tool = ZR.TOOLS[v.input.tool];
    const ctx = ZR.makeCtx(v.input.raReq, v.input.wallThickness, v.input.coolantAvailable);
    const results = ZR.validate(mat, tool, {
      vc: v.input.vc, fz: v.input.fz, ap: v.input.ap, ae: v.input.ae
    }, ctx);
    const tag = `[${i}] ${v.input.material}+${v.input.tool}`;
    if (results.length !== v.output.length) {
      fails.push(`${tag}: 检查项数 ${results.length} != ${v.output.length}`);
      checks += 1;
      return;
    }
    results.forEach((r, j) => {
      const want = v.output[j];
      near(`${tag} ${want.checkId}.passed`, r.passed, want.passed);
      near(`${tag} ${want.checkId}.applicable`, r.applicable, want.applicable);
    });
  });
  console.log(`  ${golden.validation.length} 组算例 × 10 条约束`);
}

/* ==================== 4. 颤振分级 ==================== */
section('颤振分级');
{
  golden.chatter.forEach((v, i) => {
    const tool = ZR.TOOLS[v.input.tool];
    const got = ZR.classifyChatter(tool, v.input.rpm, v.input.ae, v.input.ap);
    near(`[${i}] ${v.input.tool} n=${v.input.rpm} risk`, got.risk, v.output.risk);
  });
  console.log(`  ${golden.chatter.length} 组判定`);

  // 稳定带
  Object.keys(golden.stableBands).forEach((z) => {
    Object.keys(golden.stableBands[z]).forEach((k) => {
      near(`stableBand z=${z} k=${k}`, ZR.chatterStableRpm(Number(z), Number(k)), golden.stableBands[z][k]);
    });
  });
  console.log('  稳定带中心值一致');
}

/* ==================== 5. 收敛算法 ==================== */
section('收敛算法');
{
  golden.converge.forEach((v, i) => {
    const mat = ZR.MATERIALS[v.input.material];
    const tool = ZR.TOOLS[v.input.tool];
    const ctx = ZR.makeCtx(v.input.raReq);
    const rep = ZR.converge(mat, tool, {
      vc: v.input.vc, fz: v.input.fz, ap: v.input.ap, ae: v.input.ae
    }, ctx);
    const tag = `[${i}] ${v.input.material}+${v.input.tool}`;
    near(`${tag} converged`, rep.converged, v.output.converged);
    near(`${tag} iterations`, rep.iterations, v.output.iterations);
    ['vc', 'fz', 'ap', 'ae'].forEach((k) => {
      near(`${tag} final.${k}`, rep.finalParams[k], v.output.finalParams[k]);
    });
  });
  console.log(`  ${golden.converge.length} 组收敛，参数与轮数一致`);
}

/* ==================== 6. 参数推荐 ==================== */
section('参数推荐');
{
  let okCount = 0;
  let errCount = 0;
  golden.recommend.forEach((v, i) => {
    const tag = `[${i}] ${v.input.material}+${v.input.tool}+${v.input.operation} Ra${v.input.raReq}`;
    let rec;
    try {
      rec = ZR.recommend(v.input);
    } catch (e) {
      if (v.error) {
        near(`${tag} error`, true, true);
        errCount += 1;
        return;
      }
      fails.push(`${tag}: 意外抛出 ${e.message}`);
      checks += 1;
      return;
    }
    if (v.error) {
      fails.push(`${tag}: 期望抛出 ${v.error}，实际成功`);
      checks += 1;
      return;
    }
    okCount += 1;
    const p = rec.primary;
    near(`${tag} vc`, p.vc, v.output.vc);
    near(`${tag} fz`, p.fz, v.output.fz);
    near(`${tag} ap`, p.ap, v.output.ap);
    near(`${tag} ae`, p.ae, v.output.ae);
    near(`${tag} n`, p.n, v.output.n);
    near(`${tag} pc`, p.pc, v.output.pc);
    near(`${tag} raPred`, p.raPred, v.output.raPred);
    near(`${tag} toolLife`, p.toolLife, v.output.toolLife);
    near(`${tag} strategy`, p.strategy, v.output.strategy);
    near(`${tag} chatterRisk`, p.chatterRisk, v.output.chatterRisk);
    near(`${tag} confidence`, p.confidence, v.output.confidence);
    near(`${tag} altCount`, rec.alternatives.length, v.output.altCount);
  });
  console.log(`  成功推荐 ${okCount} 组，预期拒绝 ${errCount} 组`);
}

/* ==================== 7. 知识检索 ==================== */
section('知识检索');
{
  // 分词
  Object.keys(golden.knowledge.tokens).forEach((q) => {
    const got = ZR.tokenize(q).join(',');
    const want = golden.knowledge.tokens[q].join(',');
    near(`tokenize("${q}")`, got, want);
  });

  const kb = new ZR.KnowledgeBase(golden.knowledge.items);
  let hitChecks = 0;
  golden.knowledge.queries.forEach((q) => {
    const hits = kb.search(q.q);
    const gotIds = hits.map((h) => h.item.kbId).join(',');
    const wantIds = q.hits.join(',');
    near(`search("${q.q}")`, gotIds, wantIds);
    hitChecks += 1;
  });
  console.log(`  ${golden.knowledge.items.length} 条目、${hitChecks} 条查询命中一致`);
}

/* ==================== 汇总 ==================== */
console.log('');
console.log('='.repeat(64));
console.log(`合计比对 ${checks} 项，失败 ${fails.length} 项`);
if (fails.length) {
  console.log('');
  console.log('失败明细（最多 40 条）：');
  fails.slice(0, 40).forEach((f) => console.log('  ✗ ' + f));
  if (fails.length > 40) { console.log(`  … 另 ${fails.length - 40} 条`); }
  process.exit(1);
}
console.log('JS 引擎与 Python 端数值完全一致（相对误差 ≤ 1e-4）');
console.log('='.repeat(64));
process.exit(0);
