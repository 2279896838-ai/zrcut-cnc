/**
 * 智刃工艺 ZR-CUT · 浏览器端计算引擎
 *
 * 本文件是 Python 端 zrcut 包中 calc / constraints / recommender / knowledge
 * 四个模块的移植版，用于在纯静态环境（GitHub Pages）中真实执行计算，
 * 而不是播放预录结果。
 *
 * 一致性保证：web/test-engine.mjs 会用 web/golden-vectors.json 逐项比对
 * 本引擎输出与 Python 端基准值，相对误差要求 ≤ 1e-4。
 *
 * 同构设计：浏览器挂到 window.ZR，Node 下走 module.exports。
 */
(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  } else {
    root.ZR = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  /* ==================== 领域常量（与 zrcut/domain.py 一致） ==================== */

  var MACHINE = {
    code: 'EQ-A01-0037',
    name: 'VMC-850 立式加工中心',
    controlSystem: 'FANUC 0i-MF',
    spindleMaxRpm: 8000,
    powerContinuousKw: 11,
    powerPeakKw: 15,
    torqueNominalNm: 70,
    taper: 'BT40',
    travelXyz: [850, 500, 550],
    tableMm: [1000, 500],
    rapidXy: 24000,
    rapidZ: 18000,
    feedMin: 1,
    feedMax: 10000,
    positioning: 0.008,
    repeatability: 0.005,
    coolant: '乳化液外冷',
    coolantPressure: [0.3, 0.5],
    naturalFrequencyHz: 320
  };

  var MATERIALS = {
    M1: {
      code: 'M1', name: '中碳钢', grade: '45，正火', hardness: 'HB 180-220',
      kc: 2000, taylorC: 350, taylorN: 0.25, kRa: 1.6,
      vcMin: 120, vcMax: 180, fzMin: 0.05, fzMax: 0.12,
      aeDMax: 0.5, apDMax: 1.0, requiresCoolant: false
    },
    M2: {
      code: 'M2', name: '铝合金', grade: '6061-T6', hardness: 'HB 95',
      kc: 700, taylorC: 1800, taylorN: 0.40, kRa: 1.2,
      vcMin: 300, vcMax: 500, fzMin: 0.08, fzMax: 0.20,
      aeDMax: 0.7, apDMax: 2.0, requiresCoolant: false
    },
    M3: {
      code: 'M3', name: '不锈钢', grade: '304', hardness: 'HB 185',
      kc: 2600, taylorC: 160, taylorN: 0.20, kRa: 2.2,
      vcMin: 60, vcMax: 100, fzMin: 0.04, fzMax: 0.08,
      aeDMax: 0.4, apDMax: 0.5, requiresCoolant: true
    },
    M4: {
      code: 'M4', name: '球墨铸铁', grade: 'QT600-3', hardness: 'HB 220-270',
      kc: 1500, taylorC: 280, taylorN: 0.25, kRa: 2.0,
      vcMin: 100, vcMax: 150, fzMin: 0.06, fzMax: 0.12,
      aeDMax: 0.5, apDMax: 0.8, requiresCoolant: false
    },
    M5: {
      code: 'M5', name: '钛合金', grade: 'TC4', hardness: 'HB 320',
      kc: 2100, taylorC: 80, taylorN: 0.18, kRa: 2.5,
      vcMin: 30, vcMax: 60, fzMin: 0.03, fzMax: 0.06,
      aeDMax: 0.3, apDMax: 0.3, requiresCoolant: true
    }
  };

  function mkTool(code, kind, d, z, coat, overhang, flute, r, isBall) {
    return {
      code: code, kind: kind, diameter: d, teeth: z, coating: coat,
      overhang: overhang, fluteLength: flute, cornerRadius: r,
      isBall: !!isBall, overhangRatio: overhang / d
    };
  }

  var TOOLS = {
    T1: mkTool('T1', '立铣刀', 10, 4, '硬质合金 TiAlN', 40, 22, 0.8, false),
    T2: mkTool('T2', '立铣刀', 12, 4, '硬质合金 TiAlN', 45, 26, 0.8, false),
    T3: mkTool('T3', '立铣刀', 16, 4, '硬质合金 TiAlN', 55, 32, 0.8, false),
    T4: mkTool('T4', '立铣刀', 20, 4, '硬质合金 AlCrN', 65, 40, 0.8, false),
    T5: mkTool('T5', '球头刀', 10, 2, '硬质合金 TiAlN', 50, 25, 5.0, true),
    T6: mkTool('T6', '面铣刀', 80, 5, '硬质合金 可转位', 80, 0, 1.2, false)
  };

  var OPERATIONS = {
    OP1: { code: 'OP1', name: '端面铣', typicalTools: ['T6'] },
    OP2: { code: 'OP2', name: '侧铣（轮廓）', typicalTools: ['T1', 'T2', 'T3', 'T4'] },
    OP3: { code: 'OP3', name: '型腔粗加工', typicalTools: ['T1', 'T2', 'T3', 'T4'] },
    OP4: { code: 'OP4', name: '曲面精加工', typicalTools: ['T5'] }
  };

  var CHATTER_LOBES = [1, 2, 3];
  var CHATTER_BAND_TOL = 0.03;
  var LD_SAFE_MAX = 4.0;
  var LD_WARN_MAX = 6.0;
  var THIN_WALL_MM = 3.0;
  var LONG_OVERHANG_AP_FACTOR = 0.7;
  var THIN_WALL_AP_FACTOR = 0.5;
  var DEFAULT_RA_REQ = 3.2;

  /* ==================== 计算引擎（与 zrcut/calc.py 一致） ==================== */

  var EPS = 1e-9;

  function spindleSpeed(vc, D) {
    if (vc <= 0) { throw new Error('切削速度必须为正，当前 ' + vc); }
    if (D <= 0) { throw new Error('刀具直径必须为正，当前 ' + D); }
    return 1000 * vc / (Math.PI * D);
  }

  function cuttingSpeedFromRpm(rpm, D) {
    if (rpm <= 0) { throw new Error('主轴转速必须为正，当前 ' + rpm); }
    if (D <= 0) { throw new Error('刀具直径必须为正，当前 ' + D); }
    return Math.PI * D * rpm / 1000;
  }

  function feedRate(fz, z, n) {
    if (fz <= 0) { throw new Error('每齿进给必须为正，当前 ' + fz); }
    if (z <= 0) { throw new Error('齿数必须为正整数，当前 ' + z); }
    if (n <= 0) { throw new Error('主轴转速必须为正，当前 ' + n); }
    return fz * z * n;
  }

  function materialRemovalRate(ap, ae, vf) {
    if (ap <= 0) { throw new Error('轴向切深必须为正，当前 ' + ap); }
    if (ae <= 0) { throw new Error('径向切宽必须为正，当前 ' + ae); }
    if (vf <= 0) { throw new Error('进给速度必须为正，当前 ' + vf); }
    return ap * ae * vf;
  }

  function cuttingPower(mrr, kc) {
    if (mrr <= 0) { throw new Error('材料去除率必须为正，当前 ' + mrr); }
    if (kc <= 0) { throw new Error('比切削力必须为正，当前 ' + kc); }
    return mrr * kc / (60 * 1e6);
  }

  function theoreticalRoughness(fz, cornerRadius, isBall) {
    if (fz <= 0) { throw new Error('每齿进给必须为正，当前 ' + fz); }
    if (cornerRadius <= 0) { throw new Error('刀尖圆弧半径必须为正，当前 ' + cornerRadius); }
    var divisor = isBall ? 8 : 32;
    return fz * fz / (divisor * cornerRadius) * 1000;
  }

  function predictedRoughness(raTh, kRa) {
    if (raTh <= 0) { throw new Error('理论粗糙度必须为正，当前 ' + raTh); }
    if (kRa <= 0) { throw new Error('粗糙度修正系数必须为正，当前 ' + kRa); }
    return raTh * kRa;
  }

  function toolLife(vc, C, n) {
    if (vc <= 0) { throw new Error('切削速度必须为正，当前 ' + vc); }
    if (C <= 0) { throw new Error('Taylor 常数 C 必须为正，当前 ' + C); }
    if (n <= 0) { throw new Error('Taylor 指数 n 必须为正，当前 ' + n); }
    return Math.pow(C / vc, 1 / n);
  }

  function machiningTime(volume, ap, ae, vf) {
    if (volume <= 0) { throw new Error('待去除体积必须为正，当前 ' + volume); }
    return volume / materialRemovalRate(ap, ae, vf);
  }

  function maxFzForRoughness(raReq, cornerRadius, kRa, isBall) {
    if (raReq <= 0) { throw new Error('粗糙度要求必须为正，当前 ' + raReq); }
    if (cornerRadius <= 0) { throw new Error('刀尖圆弧半径必须为正，当前 ' + cornerRadius); }
    if (kRa <= 0) { throw new Error('粗糙度修正系数必须为正，当前 ' + kRa); }
    var divisor = isBall ? 8 : 32;
    return Math.sqrt(raReq * divisor * cornerRadius / (1000 * kRa));
  }

  function toothPassFrequency(z, n) {
    if (z <= 0) { throw new Error('齿数必须为正整数，当前 ' + z); }
    if (n <= 0) { throw new Error('主轴转速必须为正，当前 ' + n); }
    return z * n / 60;
  }

  function computeAll(mat, tool, p) {
    var n = spindleSpeed(p.vc, tool.diameter);
    var vf = feedRate(p.fz, tool.teeth, n);
    var mrr = materialRemovalRate(p.ap, p.ae, vf);
    var pc = cuttingPower(mrr, mat.kc);
    var raTh = theoreticalRoughness(p.fz, tool.cornerRadius, tool.isBall);
    var raPred = predictedRoughness(raTh, mat.kRa);
    var life = toolLife(p.vc, mat.taylorC, mat.taylorN);
    return {
      vc: p.vc, n: n, fz: p.fz, vf: vf, ap: p.ap, ae: p.ae,
      mrr: mrr, pc: pc, raTh: raTh, raPred: raPred, toolLife: life,
      overhangRatio: tool.overhangRatio,
      toothPassHz: toothPassFrequency(tool.teeth, n)
    };
  }

  function apUpperBound(mat, tool, wallThickness) {
    var ap = mat.apDMax * tool.diameter;
    var ld = tool.overhangRatio;
    if (ld > LD_SAFE_MAX && ld <= LD_WARN_MAX) { ap *= LONG_OVERHANG_AP_FACTOR; }
    if (wallThickness != null && wallThickness > 0 && wallThickness < THIN_WALL_MM) {
      ap *= THIN_WALL_AP_FACTOR;
    }
    return ap;
  }

  function aeUpperBound(mat, tool) {
    return mat.aeDMax * tool.diameter;
  }

  /* ==================== 颤振分级（与 constraints.classify_chatter 一致） ==================== */

  function chatterStableRpm(teeth, lobe) {
    if (teeth <= 0) { throw new Error('齿数必须为正整数，当前 ' + teeth); }
    if (lobe <= 0) { throw new Error('叶瓣序数必须为正整数，当前 ' + lobe); }
    return 60 * MACHINE.naturalFrequencyHz / (lobe * teeth);
  }

  function classifyChatter(tool, rpm, ae, ap) {
    var bands = [];
    var inBand = false;
    var i, lobe, center, lo, hi;
    for (i = 0; i < CHATTER_LOBES.length; i++) {
      lobe = CHATTER_LOBES[i];
      center = chatterStableRpm(tool.teeth, lobe);
      lo = center * (1 - CHATTER_BAND_TOL);
      hi = center * (1 + CHATTER_BAND_TOL);
      bands.push({ lobe: lobe, center: center, lo: lo, hi: hi });
      if (rpm >= lo && rpm <= hi) { inBand = true; }
    }
    var aeD = ae / tool.diameter;
    var apD = ap / tool.diameter;
    var bandTxt = bands.map(function (b) { return b.center.toFixed(0); }).join('/');

    if (inBand) {
      if (aeD <= 0.5) {
        var hit = bands.filter(function (b) { return rpm >= b.lo && rpm <= b.hi; })[0];
        return {
          risk: 'LOW',
          detail: 'n=' + rpm.toFixed(1) + ' 落在 k=' + hit.lobe + ' 稳定带内，且 ae/D=' + aeD.toFixed(2) + '≤0.5'
        };
      }
      return {
        risk: 'MEDIUM',
        detail: 'n=' + rpm.toFixed(1) + ' 在稳定带内，但 ae/D=' + aeD.toFixed(2) + '>0.5'
      };
    }

    if (aeD > 0.5 || apD > 1.0) {
      var reasons = [];
      if (aeD > 0.5) { reasons.push('ae/D=' + aeD.toFixed(2) + '>0.5'); }
      if (apD > 1.0) { reasons.push('ap/D=' + apD.toFixed(2) + '>1.0'); }
      return {
        risk: 'HIGH',
        detail: 'n=' + rpm.toFixed(1) + ' 不在 z=' + tool.teeth + ' 稳定带 ' + bandTxt +
          ' 内，且 ' + reasons.join('、') + '；该判据为单自由度简化叶瓣模型，仅作风险分级'
      };
    }

    if (aeD <= 0.3 && apD <= 0.5) {
      return {
        risk: 'LOW',
        detail: 'n=' + rpm.toFixed(1) + ' 不在稳定带内，但 ae/D=' + aeD.toFixed(2) +
          '≤0.3 且 ap/D=' + apD.toFixed(2) + '≤0.5，小切宽低速切削振动能量低'
      };
    }

    return {
      risk: 'MEDIUM',
      detail: 'n=' + rpm.toFixed(1) + ' 不在稳定带内，ae/D=' + aeD.toFixed(2) +
        '、ap/D=' + apD.toFixed(2) + ' 未达 HIGH 判据'
    };
  }

  /* ==================== 十条硬约束（与 constraints.validate 一致） ==================== */

  function makeCtx(raReq, wallThickness, coolantAvailable, vcFloor) {
    return {
      raReq: raReq == null ? DEFAULT_RA_REQ : raReq,
      wallThickness: wallThickness == null ? null : wallThickness,
      coolantAvailable: coolantAvailable !== false,
      vcFloor: vcFloor == null ? null : vcFloor
    };
  }

  function effectiveVcMin(mat, ctx) {
    if (ctx.vcFloor != null) { return Math.min(mat.vcMin, ctx.vcFloor); }
    return mat.vcMin;
  }

  function validate(mat, tool, p, ctx) {
    ctx = ctx || makeCtx();
    var res = computeAll(mat, tool, p);
    var out = [];

    // C-01
    var c01 = res.n <= MACHINE.spindleMaxRpm;
    out.push({
      checkId: 'C-01',
      constraint: 'n ≤ ' + MACHINE.spindleMaxRpm + ' r/min',
      passed: c01, applicable: true,
      actual: round1(res.n), limit: MACHINE.spindleMaxRpm,
      action: c01 ? 'none' : '降 vc',
      detail: '为上限的 ' + (res.n / MACHINE.spindleMaxRpm * 100).toFixed(1) + '%'
    });

    // C-02
    var c02 = res.vf >= MACHINE.feedMin && res.vf <= MACHINE.feedMax;
    out.push({
      checkId: 'C-02',
      constraint: MACHINE.feedMin + ' ≤ vf ≤ ' + MACHINE.feedMax + ' mm/min',
      passed: c02, applicable: true,
      actual: round1(res.vf), limit: MACHINE.feedMax,
      action: c02 ? 'none' : '调整 fz',
      detail: c02 ? '合规' : '超出台架进给范围'
    });

    // C-03
    var c03 = res.pc <= MACHINE.powerContinuousKw;
    out.push({
      checkId: 'C-03',
      constraint: 'Pc ≤ ' + MACHINE.powerContinuousKw + ' kW',
      passed: c03, applicable: true,
      actual: round3(res.pc), limit: MACHINE.powerContinuousKw,
      action: c03 ? 'none' : '降 ap',
      detail: c03
        ? '余量 ' + (MACHINE.powerContinuousKw - res.pc).toFixed(3) + ' kW'
        : '超出 ' + (res.pc - MACHINE.powerContinuousKw).toFixed(3) + ' kW'
    });

    // C-04
    var ld = tool.overhangRatio;
    var c04 = ld <= LD_WARN_MAX;
    var c04Detail;
    if (ld <= LD_SAFE_MAX) {
      c04Detail = 'L/D=' + ld.toFixed(2) + '≤' + LD_SAFE_MAX + '，无需折减';
    } else if (ld <= LD_WARN_MAX) {
      c04Detail = 'L/D=' + ld.toFixed(2) + ' 处于 ' + LD_SAFE_MAX + '-' + LD_WARN_MAX + '，ap 上限折减 0.7';
    } else {
      c04Detail = 'L/D=' + ld.toFixed(2) + '>' + LD_WARN_MAX + '，刚性严重不足，转人工';
    }
    out.push({
      checkId: 'C-04',
      constraint: 'L/D ≤ ' + LD_SAFE_MAX.toFixed(1),
      passed: c04, applicable: true,
      actual: round3(ld), limit: LD_SAFE_MAX,
      action: c04 ? 'none' : '换刀或缩短悬伸',
      detail: c04Detail
    });

    // C-05
    var vcLo = effectiveVcMin(mat, ctx);
    var vcHi = mat.vcMax;
    var vcOk = p.vc >= vcLo && p.vc <= vcHi;
    var fzOk = p.fz >= mat.fzMin && p.fz <= mat.fzMax;
    var parts = [];
    if (!vcOk) { parts.push('vc=' + p.vc + ' 超出 ' + vcLo + '-' + vcHi); }
    if (!fzOk) { parts.push('fz=' + p.fz + ' 超出 ' + mat.fzMin + '-' + mat.fzMax); }
    var c05 = vcOk && fzOk;
    var c05Note = vcLo < mat.vcMin ? '（下标已按主轴上限放宽至 ' + vcLo.toFixed(1) + '）' : '';
    out.push({
      checkId: 'C-05',
      constraint: 'vc ' + vcLo + '-' + vcHi + ' m/min, fz ' + mat.fzMin + '-' + mat.fzMax + ' mm/z',
      passed: c05, applicable: true,
      actual: 'vc=' + p.vc + ',fz=' + p.fz,
      limit: 'vc ' + vcLo + '-' + vcHi + ',fz ' + mat.fzMin + '-' + mat.fzMax,
      action: c05 ? 'none' : '收敛到边界',
      detail: c05 ? ('合规' + c05Note) : parts.join('；')
    });

    // C-06
    var apD = p.ap / tool.diameter;
    var aeD = p.ae / tool.diameter;
    var apLim = mat.apDMax;
    if (ld > LD_SAFE_MAX && ld <= LD_WARN_MAX) { apLim *= 0.7; }
    if (ctx.wallThickness != null && ctx.wallThickness > 0 && ctx.wallThickness < THIN_WALL_MM) {
      apLim *= 0.5;
    }
    var aeLim = mat.aeDMax;
    var c06 = apD <= apLim + EPS && aeD <= aeLim + EPS;
    out.push({
      checkId: 'C-06',
      constraint: 'ap/D≤' + fmtG(apLim) + ', ae/D≤' + fmtG(aeLim),
      passed: c06, applicable: true,
      actual: apD.toFixed(3) + '/' + aeD.toFixed(3),
      limit: fmtG(apLim) + '/' + fmtG(aeLim),
      action: c06 ? 'none' : '降 ap 或降 ae',
      detail: c06 ? '合规' : 'ap/D=' + apD.toFixed(3) + ' 或 ae/D=' + aeD.toFixed(3) + ' 超限'
    });

    // C-07
    var ch = classifyChatter(tool, res.n, p.ae, p.ap);
    var c07 = ch.risk !== 'HIGH';
    out.push({
      checkId: 'C-07',
      constraint: '颤振风险不高于 MEDIUM',
      passed: c07, applicable: true,
      actual: ch.risk, limit: 'LOW/MEDIUM/HIGH',
      action: c07 ? 'none' : '收敛稳定带或降 ap',
      detail: ch.detail
    });

    // C-08
    var c08 = res.raPred <= ctx.raReq + EPS;
    out.push({
      checkId: 'C-08',
      constraint: 'Ra_pred ≤ ' + fmtG(ctx.raReq) + ' μm',
      passed: c08, applicable: true,
      actual: round3(res.raPred), limit: ctx.raReq,
      action: c08 ? 'none' : '降 fz 或换刀',
      detail: c08
        ? '余量 ' + (ctx.raReq - res.raPred).toFixed(3) + ' μm'
        : '超出 ' + (res.raPred - ctx.raReq).toFixed(3) + ' μm'
    });

    // C-09
    var c09Applicable = !!mat.requiresCoolant;
    var c09 = c09Applicable ? ctx.coolantAvailable : true;
    out.push({
      checkId: 'C-09',
      constraint: 'M3/M5 须内冷或高压外冷',
      passed: c09, applicable: c09Applicable,
      actual: ctx.coolantAvailable ? '具备' : '不具备',
      limit: '具备',
      action: c09 ? 'none' : '停机并接入内冷',
      detail: c09Applicable
        ? (mat.name + ' 须内冷或高压外冷，当前' + (c09 ? '已具备' : '不具备'))
        : (mat.name + ' 不强制内冷，建议大流量冲刷')
    });

    // C-10
    var c10Applicable = ctx.wallThickness != null && ctx.wallThickness > 0 && ctx.wallThickness < THIN_WALL_MM;
    var c10, c10Actual, c10Limit, c10Detail, c10Action;
    if (c10Applicable) {
      var apThin = apUpperBound(mat, tool, ctx.wallThickness);
      c10 = p.ap <= apThin + EPS;
      c10Actual = round3(p.ap);
      c10Limit = round3(apThin);
      c10Detail = '薄壁 ' + ctx.wallThickness + 'mm，ap 上限折减为 ' + apThin.toFixed(2) + 'mm（含悬伸折减后统一上限）';
      c10Action = c10 ? 'none' : '降 ap 至折减上限';
    } else {
      c10 = true;
      c10Actual = round3(p.ap);
      c10Limit = 'not_applicable';
      c10Detail = '非薄壁件，不适用';
      c10Action = 'none';
    }
    out.push({
      checkId: 'C-10',
      constraint: '薄壁件（<' + THIN_WALL_MM + 'mm）ap 折减 0.5',
      passed: c10, applicable: c10Applicable,
      actual: c10Actual, limit: c10Limit,
      action: c10Action, detail: c10Detail
    });

    return out;
  }

  /* ==================== 收敛算法（与 constraints.converge 一致） ==================== */

  function diagnose(mat, tool, p, ctx) {
    var res = computeAll(mat, tool, p);
    var need = {};
    var apD = p.ap / tool.diameter;
    var aeD = p.ae / tool.diameter;
    var ld = tool.overhangRatio;

    var apLim = mat.apDMax;
    if (ld > LD_SAFE_MAX && ld <= LD_WARN_MAX) { apLim *= 0.7; }
    if (ctx.wallThickness != null && ctx.wallThickness > 0 && ctx.wallThickness < THIN_WALL_MM) {
      apLim *= 0.5;
    }
    var aeLim = mat.aeDMax;
    var vcLo = effectiveVcMin(mat, ctx);
    var vcHi = mat.vcMax;

    if (res.n > MACHINE.spindleMaxRpm) { need.vc = true; }
    else if (p.vc > vcHi || p.vc < vcLo) { need.vc = true; }

    if (res.vf > MACHINE.feedMax || res.vf < MACHINE.feedMin) { need.fz = true; }
    if (res.raPred > ctx.raReq + EPS) { need.fz = true; }
    if (p.fz > mat.fzMax || p.fz < mat.fzMin) { need.fz = true; }
    if (res.pc > MACHINE.powerContinuousKw) { need.ap = true; }
    if (apD > apLim + EPS) { need.ap = true; }
    if (aeD > aeLim + EPS) { need.ae = true; }

    var ch = classifyChatter(tool, res.n, p.ae, p.ap);
    if (ch.risk === 'HIGH') {
      if (aeD > 0.5) { need.ae = true; }
      else if (apD > 1.0) { need.ap = true; }
      else { need.vc = true; }
    }

    return ['ap', 'ae', 'vc', 'fz'].filter(function (k) { return need[k]; });
  }

  function floorTo(value, step) {
    if (step <= 0) { return value; }
    return Math.floor(value / step) * step;
  }

  function converge(mat, tool, p, ctx, maxIterations) {
    ctx = ctx || makeCtx();
    maxIterations = maxIterations == null ? 8 : maxIterations;

    // 主轴极限优先于材料区间
    var vcFromSpindle = cuttingSpeedFromRpm(MACHINE.spindleMaxRpm * 0.98, tool.diameter);
    var useCtx = ctx;
    if (ctx.vcFloor == null && mat.vcMin > vcFromSpindle) {
      useCtx = makeCtx(ctx.raReq, ctx.wallThickness, ctx.coolantAvailable, vcFromSpindle);
    }

    var cur = { vc: p.vc, fz: p.fz, ap: p.ap, ae: p.ae };
    var history = [];
    var it, results, failed, need, target, changed, res, ratio, cand, cap, newVal, fzCap, vcLo;

    for (it = 0; it <= maxIterations; it++) {
      results = validate(mat, tool, cur, useCtx);
      failed = results.filter(function (r) { return r.applicable && !r.passed; });
      history.push({
        iteration: it,
        vc: round4(cur.vc), fz: round4(cur.fz), ap: round4(cur.ap), ae: round4(cur.ae),
        failed: failed.map(function (r) { return r.checkId; })
      });

      if (failed.length === 0) {
        return {
          converged: true, results: results, iterations: it,
          finalParams: { vc: cur.vc, fz: cur.fz, ap: cur.ap, ae: cur.ae },
          history: history
        };
      }
      if (it === maxIterations) { break; }

      need = diagnose(mat, tool, cur, useCtx);
      if (need.length === 0) { break; }
      target = need[0];
      changed = false;

      if (target === 'ap') {
        res = computeAll(mat, tool, cur);
        if (res.pc > MACHINE.powerContinuousKw) {
          ratio = MACHINE.powerContinuousKw * 0.98 / res.pc;
          cand = floorTo(cur.ap * ratio, 0.5);
        } else {
          cand = cur.ap * 0.8;
        }
        cap = apUpperBound(mat, tool, useCtx.wallThickness);
        newVal = Math.min(cand, cap);
        if (newVal < cur.ap - EPS) { cur.ap = Math.max(newVal, 0.1); changed = true; }

      } else if (target === 'ae') {
        cap = aeUpperBound(mat, tool);
        newVal = Math.min(cur.ae, cap);
        if (newVal < cur.ae - EPS) {
          cur.ae = Math.max(newVal, 0.1); changed = true;
        } else {
          newVal = Math.max(cur.ae * 0.7, 0.1);
          if (newVal < cur.ae - EPS) { cur.ae = newVal; changed = true; }
        }

      } else if (target === 'vc') {
        res = computeAll(mat, tool, cur);
        vcLo = effectiveVcMin(mat, useCtx);
        var cands = [mat.vcMax];
        if (res.n > MACHINE.spindleMaxRpm) {
          cands.push(cuttingSpeedFromRpm(MACHINE.spindleMaxRpm * 0.98, tool.diameter));
        }
        newVal = Math.min.apply(null, [cur.vc].concat(cands));
        if (newVal < cur.vc - EPS) {
          cur.vc = Math.max(newVal, Math.min(vcLo, newVal)); changed = true;
        } else if (res.pc > MACHINE.powerContinuousKw) {
          newVal = Math.max(cur.vc * 0.85, vcLo);
          if (newVal < cur.vc - EPS) { cur.vc = newVal; changed = true; }
        } else if (res.n > MACHINE.spindleMaxRpm) {
          newVal = Math.max(cur.vc * 0.9, vcLo);
          if (newVal < cur.vc - EPS) { cur.vc = newVal; changed = true; }
        }

      } else if (target === 'fz') {
        fzCap = maxFzForRoughness(useCtx.raReq, tool.cornerRadius, mat.kRa, tool.isBall);
        newVal = Math.min(cur.fz, fzCap, mat.fzMax);
        if (newVal < cur.fz - EPS) {
          cur.fz = Math.max(newVal, 0.001); changed = true;
        } else {
          newVal = Math.max(cur.fz * 0.85, 0.001);
          if (newVal < cur.fz - EPS) { cur.fz = newVal; changed = true; }
        }
      }

      if (!changed) { break; }
    }

    results = validate(mat, tool, cur, useCtx);
    failed = results.filter(function (r) { return r.applicable && !r.passed; });
    return {
      converged: failed.length === 0,
      results: results,
      iterations: history.length - 1,
      finalParams: { vc: cur.vc, fz: cur.fz, ap: cur.ap, ae: cur.ae },
      history: history
    };
  }

  /* ==================== 参数推荐（与 recommender.recommend 一致） ==================== */

  var VC_POSITION = { EFFICIENCY: 0.90, QUALITY: 0.50, LIFE: 0.10, BALANCED: 0.70 };
  var FZ_POSITION = { EFFICIENCY: 0.90, QUALITY: 0.30, LIFE: 0.60, BALANCED: 0.70 };

  var STRATEGY_LABEL = {
    EFFICIENCY: '效率优先', QUALITY: '质量优先', LIFE: '寿命优先', BALANCED: '均衡'
  };

  function pickInBand(lo, hi, pos) {
    pos = Math.max(0, Math.min(1, pos));
    return lo + (hi - lo) * pos;
  }

  function autoStrategy(raReq, mat, tool) {
    var typical = theoreticalRoughness(0.08, tool.cornerRadius, tool.isBall) * mat.kRa;
    if (raReq < typical * 1.5) { return 'QUALITY'; }
    if (mat.code === 'M5' || mat.code === 'M3') { return 'LIFE'; }
    return 'EFFICIENCY';
  }

  function confidenceFor(plan, report, raReq, mat) {
    var applicableFailed = report.results.filter(function (r) { return r.applicable && !r.passed; });
    if (applicableFailed.length > 0) { return 'LOW'; }
    if (plan.chatterRisk === 'HIGH') { return 'LOW'; }
    var tol = 1e-6;
    var atBoundary =
      Math.abs(plan.vc - mat.vcMax) < tol || Math.abs(plan.vc - mat.vcMin) < tol ||
      Math.abs(plan.fz - mat.fzMax) < tol || Math.abs(plan.fz - mat.fzMin) < tol;
    var margin = raReq > 0 ? (raReq - plan.raPred) / raReq : 0;
    if (atBoundary || margin < 0.2 || plan.chatterRisk === 'MEDIUM') { return 'MEDIUM'; }
    return 'HIGH';
  }

  function makePlan(mat, tool, label, strategy, p, volume, note) {
    var res = computeAll(mat, tool, p);
    var tm = volume != null ? volume / res.mrr : null;
    var ch = classifyChatter(tool, res.n, p.ae, p.ap);
    return {
      label: label, strategy: strategy,
      vc: p.vc, fz: p.fz, ap: p.ap, ae: p.ae,
      n: res.n, vf: res.vf, mrr: res.mrr, pc: res.pc,
      raTh: res.raTh, raPred: res.raPred, toolLife: res.toolLife,
      machiningTime: tm,
      chatterRisk: ch.risk,
      confidence: 'MEDIUM',
      note: note
    };
  }

  function recommend(req) {
    var mat = MATERIALS[req.material];
    var tool = TOOLS[req.tool];
    var op = OPERATIONS[req.operation];
    if (!mat) { throw new Error('未知材料：' + req.material); }
    if (!tool) { throw new Error('未知刀具：' + req.tool); }
    if (!op) { throw new Error('未知工序：' + req.operation); }

    var raReq = req.raReq == null ? DEFAULT_RA_REQ : req.raReq;
    var volume = req.volume == null ? null : req.volume;
    var coolant = req.coolantAvailable !== false;
    var wall = req.wallThickness == null ? null : req.wallThickness;

    // 刀具工序匹配
    if (op.code !== 'OP2' && op.code !== 'OP3' && op.typicalTools.indexOf(tool.code) < 0) {
      var err = new Error('刀具 ' + tool.code + '（' + tool.kind + '）不适用于工序 ' +
        op.code + '（' + op.name + '）；该工序典型刀具为 ' + op.typicalTools.join(', '));
      err.code = 'TOOL_OPERATION_MISMATCH';
      throw err;
    }

    var strategy = req.strategy || autoStrategy(raReq, mat, tool);

    // C-09 前置硬拦截
    if (mat.requiresCoolant && !coolant) {
      var err2 = new Error(mat.code + '（' + mat.name + '）必须使用内冷或高压外冷，当前冷却条件不具备，' +
        '不得开切；请先接入内冷装置或将冷却压力提升至 0.3-0.5 MPa 以上后重新推荐');
      err2.code = 'COOLANT_REQUIRED';
      throw err2;
    }

    var vcLo = mat.vcMin;
    var vcHi = mat.vcMax;
    var vcTarget = pickInBand(vcLo, vcHi, VC_POSITION[strategy]);
    var vcCap = cuttingSpeedFromRpm(MACHINE.spindleMaxRpm * 0.98, tool.diameter);
    if (vcTarget > vcCap) { vcTarget = Math.max(Math.min(vcCap, vcHi), vcLo); }

    var fzCapRa = maxFzForRoughness(raReq, tool.cornerRadius, mat.kRa, tool.isBall);
    var fzHi = Math.min(mat.fzMax, fzCapRa);
    if (fzHi < mat.fzMin) {
      var err3 = new Error('Ra ' + raReq + ' μm 用 ' + tool.code + ' 无法达到：fz 上限反解为 ' +
        fzCapRa.toFixed(4) + ' mm/z，低于材料下限 ' + mat.fzMin +
        '；建议改用更小 rε 的刀具或放宽 Ra 要求');
      err3.code = 'ROUGHNESS_UNACHIEVABLE';
      throw err3;
    }
    var fzTarget = pickInBand(mat.fzMin, fzHi, FZ_POSITION[strategy]);

    var apCap = apUpperBound(mat, tool, wall);
    var aeCap = aeUpperBound(mat, tool);
    var ctx = makeCtx(raReq, wall, coolant);

    var rawPlans = [];
    rawPlans.push({
      label: 'P0', strategy: strategy,
      vc: vcTarget, fz: fzTarget, ap: apCap, ae: aeCap,
      note: '按所选策略生成的主方案', role: 'primary'
    });

    var alt1Strategy = strategy === 'EFFICIENCY' ? 'QUALITY' : 'EFFICIENCY';
    var alt1Vc = Math.min(pickInBand(vcLo, vcHi, VC_POSITION[alt1Strategy]), vcCap);
    var alt1Fz = pickInBand(mat.fzMin, fzHi, FZ_POSITION[alt1Strategy]);
    var alt1ApFrac = alt1Strategy === 'QUALITY' ? 0.85 : 1.0;
    rawPlans.push({
      label: 'P1', strategy: alt1Strategy,
      vc: alt1Vc, fz: alt1Fz, ap: apCap * alt1ApFrac, ae: aeCap * alt1ApFrac,
      note: '备选一：' + STRATEGY_LABEL[alt1Strategy], role: 'alt'
    });

    var alt2Vc = pickInBand(vcLo, vcHi, VC_POSITION.LIFE);
    var alt2Fz = pickInBand(mat.fzMin, fzHi, FZ_POSITION.LIFE);
    rawPlans.push({
      label: 'P2', strategy: 'LIFE',
      vc: alt2Vc, fz: alt2Fz, ap: apCap * 0.9, ae: aeCap * 0.9,
      note: '备选二：寿命优先，vc 压低换刀具寿命', role: 'alt'
    });

    var finalPlans = [];
    var primaryReport = null;
    var i, raw, rep, fp, plan;
    for (i = 0; i < rawPlans.length; i++) {
      raw = rawPlans[i];
      rep = converge(mat, tool, { vc: raw.vc, fz: raw.fz, ap: raw.ap, ae: raw.ae }, ctx);
      if (!rep.converged) { continue; }
      fp = rep.finalParams;
      plan = makePlan(mat, tool, raw.label, raw.strategy,
        { vc: fp.vc, fz: fp.fz, ap: fp.ap, ae: fp.ae }, volume, raw.note);
      plan.confidence = confidenceFor(plan, rep, raReq, mat);
      finalPlans.push(plan);
      if (raw.role === 'primary') { primaryReport = rep; }
    }

    if (finalPlans.length === 0) {
      var err4 = new Error('在给定约束下无法找到可行参数组合，请放宽 Ra 要求或更换刀具');
      err4.code = 'NO_FEASIBLE_PLAN';
      throw err4;
    }

    var primary = finalPlans[0];
    var alternatives = finalPlans.slice(1);
    var warnings = buildWarnings(primary, primaryReport, mat, tool, raReq, coolant, wall);
    var rationality = buildRationality(primary, mat, tool);

    return {
      request: {
        material: mat.code, tool: tool.code, operation: op.code,
        raReq: raReq, strategy: req.strategy || null, volume: volume
      },
      primary: primary,
      alternatives: alternatives,
      validation: primaryReport,
      warnings: warnings,
      rationality: rationality,
      material: mat,
      tool: tool
    };
  }

  function buildWarnings(plan, report, mat, tool, raReq, coolant, wall) {
    var out = [];
    if (plan.chatterRisk === 'HIGH') {
      out.push('颤振风险 HIGH：n=' + plan.n.toFixed(0) + ' r/min 不在稳定带内且切宽比过大，必须试切首件并监听异响');
    } else if (plan.chatterRisk === 'MEDIUM') {
      out.push('颤振风险 MEDIUM：n=' + plan.n.toFixed(0) + ' r/min 不在稳定带中心，建议首件试切监听异响');
    }
    if (plan.pc > MACHINE.powerContinuousKw * 0.9) {
      out.push('功率贴限：Pc=' + plan.pc.toFixed(2) + ' kW 占连续功率 ' +
        (plan.pc / MACHINE.powerContinuousKw * 100).toFixed(1) + '%，切削中禁止再提高 fz');
    }
    var margin = (raReq - plan.raPred) / raReq;
    if (margin < 0.2) {
      out.push('粗糙度余量仅 ' + (margin * 100).toFixed(1) + '%（预测 ' + plan.raPred.toFixed(3) +
        ' μm / 要求 ' + raReq + ' μm），首件须三点实测');
    }
    if (mat.requiresCoolant && !coolant) {
      out.push(mat.name + ' 必须内冷或高压外冷，当前不具备，不得开切');
    }
    if (tool.overhangRatio > 4.0) {
      out.push('悬伸比 ' + tool.overhangRatio.toFixed(2) + ' > 4.0，切深已折减，严禁改用更长刀杆');
    }
    if (wall != null && wall < 3.0) {
      out.push('薄壁件（壁厚 ' + wall + ' mm），切深已折减 50%，须增加辅助支撑');
    }
    if (report && report.iterations > 0) {
      out.push('原始参数经 ' + report.iterations + ' 轮收敛后才通过硬约束，工艺卡须标注收敛动作');
    }
    return out;
  }

  function buildRationality(plan, mat, tool) {
    var s = '';
    s += '材料 ' + mat.code + '（' + mat.name + '，Kc=' + mat.kc + ' N/mm²，';
    s += 'Taylor C=' + mat.taylorC + '、n=' + mat.taylorN + '，K_ra=' + mat.kRa + '）；';
    s += '刀具 ' + tool.code + '（' + tool.kind + '，D=' + tool.diameter + ' mm，z=' + tool.teeth +
      '，rε=' + tool.cornerRadius + ' mm，L/D=' + tool.overhangRatio.toFixed(2) + '）；';
    s += 'n = 1000 × vc / (π × D) = 1000 × ' + plan.vc + ' / (π × ' + tool.diameter +
      ') = ' + plan.n.toFixed(1) + ' r/min；';
    s += 'vf = fz × z × n = ' + plan.fz + ' × ' + tool.teeth + ' × ' + plan.n.toFixed(1) +
      ' = ' + plan.vf.toFixed(1) + ' mm/min；';
    s += 'MRR = ap × ae × vf = ' + plan.ap + ' × ' + plan.ae + ' × ' + plan.vf.toFixed(1) +
      ' = ' + plan.mrr.toFixed(0) + ' mm³/min；';
    s += 'Pc = MRR × Kc / (60×10⁶) = ' + plan.mrr.toFixed(0) + ' × ' + mat.kc +
      ' / 6×10⁷ = ' + plan.pc.toFixed(3) + ' kW；';
    if (tool.isBall) {
      s += 'Ra_th = fz² / (8 × R) × 1000 = ' + plan.fz + '² / (8 × ' + tool.cornerRadius +
        ') × 1000 = ' + plan.raTh.toFixed(4) + ' μm；';
    } else {
      s += 'Ra_th = fz² / (32 × rε) × 1000 = ' + plan.fz + '² / (32 × ' + tool.cornerRadius +
        ') × 1000 = ' + plan.raTh.toFixed(4) + ' μm；';
    }
    s += 'Ra_pred = Ra_th × K_ra = ' + plan.raTh.toFixed(4) + ' × ' + mat.kRa +
      ' = ' + plan.raPred.toFixed(3) + ' μm；';
    s += 'T = (C / vc)^(1/n) = (' + mat.taylorC + ' / ' + plan.vc + ')^(1/' + mat.taylorN +
      ') = ' + plan.toolLife.toFixed(2) + ' min。';
    return s;
  }

  /* ==================== 知识检索（与 knowledge.py 一致） ==================== */

  var KB_AUTHORITY = { S1: 0.90, S2: 0.85, S3: 1.00, S4: 0.60, S5: 0.80, S6: 0.95, S7: 0.50 };
  var KB_SOURCE_NAME = {
    S1: '刀具厂家手册', S2: '材料切削性能手册', S3: '企业受控工艺卡',
    S4: '历史试切记录', S5: '机床说明书', S6: '安全规程', S7: '专家访谈'
  };
  var W_RELEVANCE = 0.50;
  var W_AUTHORITY = 0.35;
  var W_FRESHNESS = 0.15;
  var KB_THRESHOLD = 0.65;
  var KB_TOP_K = 5;

  var CJK_RE = /[\u4e00-\u9fa5]/g;
  var LATIN_RE = /[A-Za-z0-9_.\-]+/g;

  function tokenize(text) {
    if (!text) { return []; }
    var norm = String(text).normalize('NFKC').toLowerCase();
    var tokens = [];
    var lat = norm.match(LATIN_RE);
    if (lat) { tokens = tokens.concat(lat); }
    var cjk = norm.match(CJK_RE);
    var joined = cjk ? cjk.join('') : '';
    if (joined.length === 1) {
      tokens.push(joined);
    } else {
      for (var i = 0; i < joined.length - 1; i++) {
        tokens.push(joined.substr(i, 2));
      }
    }
    return tokens;
  }

  function KnowledgeBase(items) {
    this.items = (items || []).map(function (it) {
      var copy = {};
      Object.keys(it).forEach(function (k) { copy[k] = it[k]; });
      copy.authority = KB_AUTHORITY[copy.sourceType] || 0.5;
      copy.text = [copy.title, copy.scope, copy.body]
        .concat(Object.keys(copy.metrics || {}).map(function (k) {
          return k + ' ' + copy.metrics[k];
        }))
        .concat([KB_SOURCE_NAME[copy.sourceType] || ''])
        .filter(Boolean).join(' ');
      copy.tokens = tokenize(copy.text);
      copy.freshness = 1.0;
      return copy;
    });
    this._build();
  }

  KnowledgeBase.prototype._build = function () {
    var df = {};
    var total = 0;
    var i, j, toks, seen;
    for (i = 0; i < this.items.length; i++) {
      toks = this.items[i].tokens;
      total += toks.length;
      seen = {};
      for (j = 0; j < toks.length; j++) { seen[toks[j]] = true; }
      Object.keys(seen).forEach(function (t) { df[t] = (df[t] || 0) + 1; });
    }
    this.df = df;
    this.avgdl = this.items.length ? total / this.items.length : 0;
    this.k1 = 1.5;
    this.b = 0.75;
  };

  KnowledgeBase.prototype.idf = function (term) {
    var n = this.items.length;
    var df = this.df[term] || 0;
    return Math.log(1 + (n - df + 0.5) / (df + 0.5));
  };

  KnowledgeBase.prototype.score = function (qTokens, idx) {
    var toks = this.items[idx].tokens;
    var dl = toks.length;
    if (dl === 0 || this.avgdl === 0) { return 0; }
    var tf = {};
    var i;
    for (i = 0; i < toks.length; i++) { tf[toks[i]] = (tf[toks[i]] || 0) + 1; }
    var total = 0;
    for (i = 0; i < qTokens.length; i++) {
      var q = qTokens[i];
      var f = tf[q] || 0;
      if (f === 0) { continue; }
      var denom = f + this.k1 * (1 - this.b + this.b * dl / this.avgdl);
      total += this.idf(q) * f * (this.k1 + 1) / denom;
    }
    return total;
  };

  KnowledgeBase.prototype.search = function (query, options) {
    options = options || {};
    var topK = options.topK || KB_TOP_K;
    var threshold = options.threshold == null ? KB_THRESHOLD : options.threshold;
    var requireHigh = options.requireHighAuthority !== false;

    var qTokens = tokenize(query);
    if (!qTokens.length || !this.items.length) { return []; }

    var raw = [];
    var i, s;
    for (i = 0; i < this.items.length; i++) {
      s = this.score(qTokens, i);
      if (s > 0) { raw.push({ idx: i, bm: s }); }
    }
    if (!raw.length) { return []; }

    // IDF 加权查询覆盖率
    var uniq = {};
    qTokens.forEach(function (t) { uniq[t] = true; });
    var idfMap = {};
    var totalIdf = 0;
    Object.keys(uniq).forEach(function (t) {
      var v = this.idf(t);
      idfMap[t] = v;
      totalIdf += v;
    }, this);
    if (totalIdf <= 0) { return []; }

    var hits = [];
    for (i = 0; i < raw.length; i++) {
      var item = this.items[raw[i].idx];
      var docSet = {};
      item.tokens.forEach(function (t) { docSet[t] = true; });
      var matchedIdf = 0;
      Object.keys(idfMap).forEach(function (t) {
        if (docSet[t]) { matchedIdf += idfMap[t]; }
      });
      var rel = matchedIdf / totalIdf;
      if (rel <= 0) { continue; }
      var auth = item.authority;
      var fresh = item.freshness;
      var score = W_RELEVANCE * rel + W_AUTHORITY * auth + W_FRESHNESS * fresh;
      hits.push({
        item: item, relevance: rel, authority: auth, freshness: fresh,
        score: score, snippet: this._snippet(item, qTokens)
      });
    }
    if (!hits.length) { return []; }

    hits.sort(function (a, b) {
      if (b.score !== a.score) { return b.score - a.score; }
      return b.relevance - a.relevance;
    });

    if (requireHigh && !hits.some(function (h) { return h.authority >= 0.8; })) {
      return [];
    }
    return hits.filter(function (h) { return h.score >= threshold; }).slice(0, topK);
  };

  KnowledgeBase.prototype._snippet = function (item, qTokens) {
    var text = item.body || item.title;
    var width = 90;
    var i, pos;
    for (i = 0; i < qTokens.length; i++) {
      pos = text.indexOf(qTokens[i]);
      if (pos >= 0) {
        var start = Math.max(0, pos - Math.floor(width / 3));
        return text.substr(start, width).replace(/\n/g, ' ');
      }
    }
    return text.substr(0, width).replace(/\n/g, ' ');
  };

  /* ==================== 工具函数 ==================== */

  function round1(x) { return Math.round(x * 10) / 10; }
  function round3(x) { return Math.round(x * 1000) / 1000; }
  function round4(x) { return Math.round(x * 10000) / 10000; }
  function fmtG(x) {
    if (Number.isInteger(x)) { return String(x); }
    return String(parseFloat(x.toFixed(3)));
  }

  /* ==================== 对外接口 ==================== */

  return {
    MACHINE: MACHINE,
    MATERIALS: MATERIALS,
    TOOLS: TOOLS,
    OPERATIONS: OPERATIONS,
    STRATEGY_LABEL: STRATEGY_LABEL,
    KB_SOURCE_NAME: KB_SOURCE_NAME,
    // 计算
    spindleSpeed: spindleSpeed,
    cuttingSpeedFromRpm: cuttingSpeedFromRpm,
    feedRate: feedRate,
    materialRemovalRate: materialRemovalRate,
    cuttingPower: cuttingPower,
    theoreticalRoughness: theoreticalRoughness,
    predictedRoughness: predictedRoughness,
    toolLife: toolLife,
    machiningTime: machiningTime,
    maxFzForRoughness: maxFzForRoughness,
    toothPassFrequency: toothPassFrequency,
    computeAll: computeAll,
    apUpperBound: apUpperBound,
    aeUpperBound: aeUpperBound,
    // 约束
    chatterStableRpm: chatterStableRpm,
    classifyChatter: classifyChatter,
    makeCtx: makeCtx,
    validate: validate,
    diagnose: diagnose,
    converge: converge,
    // 推荐
    recommend: recommend,
    // 知识
    tokenize: tokenize,
    KnowledgeBase: KnowledgeBase
  };
});
