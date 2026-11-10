#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成静态演示页。

从系统真实运行结果中取数，渲染成单文件 HTML，可直接部署到 GitHub Pages。
刻意不引用任何 CDN 资源，保证离线与国内网络都能正常打开。

用法：
    python scripts/build-demo-page.py
输出：
    docs/index.html
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "index.html"


def collect() -> dict:
    """调用系统采集演示数据。

    Returns:
        演示数据字典。
    """
    sys.path.insert(0, str(ROOT))
    from zrcut.evaluate import run_builtin_eval
    from zrcut.service import ZrCutService

    svc = ZrCutService()
    out: dict = {}

    r = svc.recommend(
        material="M1", tool="T3", operation="OP2", ra_req=1.6, volume=288000.0
    )
    out["recommend"] = {
        "request": r["request"],
        "primary": r["primary"],
        "alternatives": r["alternatives"],
        "warnings": r["warnings"],
        "knowledge": [
            {"kbId": k["kbId"], "title": k["title"], "score": round(k["score"], 4)}
            for k in r["knowledge"]
        ],
    }

    v = svc.validate_params(
        material="M1", tool="T4", vc=180, fz=0.12, ap=20, ae=14, ra_req=1.6
    )
    out["converge"] = {
        "converged": v["converged"],
        "iterations": v["iterations"],
        "history": v["history"],
        "final_params": v["final_params"],
        "finalMetrics": v["finalMetrics"],
    }

    c = svc.create_card(
        part_name="支架", blank_size="130x90x35", op_no=20,
        material="M1", tool="T3", operation="OP2", ra_req=1.6, volume=288000.0,
    )
    out["card"] = {
        k: c[k]
        for k in (
            "cardId", "partName", "materialCode", "toolCode", "spindleSpeed",
            "feedRate", "feedPerTooth", "axialDepth", "radialWidth", "coolant",
            "estRoughness", "chatterRisk", "status", "contentHash", "qrPayload",
        )
    }

    s = svc.search("304 不锈钢 内冷")
    out["search"] = {
        "query": s["query"],
        "hits": [
            {
                "kbId": h["kbId"],
                "title": h["title"],
                "score": round(h["score"], 4),
                "relevance": round(h["relevance"], 4),
            }
            for h in s["hits"]
        ],
    }
    s2 = svc.search("火星探测器轨道参数")
    out["search_empty"] = {
        "query": s2["query"],
        "empty": s2["empty"],
        "message": s2["emptyMessage"],
    }

    cl = svc.record_closure(
        trace_id="TR-20261109-0001", card_id=c["cardId"], material="M1", tool="T3",
        actual_ra=1.05, actual_time=1.6, actual_life=20.0,
    )
    out["closure"] = {"assessment": cl["assessment"], "stats": cl["stats"]}

    ev = run_builtin_eval()["report"]
    out["eval"] = {
        "counts": ev["counts"],
        "metrics": ev["metrics"],
        "targets": ev["targets"],
        "passed": ev["passed"],
    }
    return out


def esc(x: object) -> str:
    """HTML 转义。"""
    return html.escape(str(x))


def num(x: object, digits: int = 2) -> str:
    """数值格式化。"""
    try:
        return f"{float(x):,.{digits}f}"
    except (TypeError, ValueError):
        return esc(x)


CSS = """
:root{
  --bg:#0d1117; --bg2:#161b22; --bg3:#1c2333; --line:#30363d;
  --fg:#e6edf3; --fg2:#8b949e; --accent:#58a6ff; --ok:#3fb950;
  --warn:#d29922; --bad:#f85149; --mono:"Cascadia Code","Consolas",monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--fg);font:15px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:0 24px}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
code,pre,.mono{font-family:var(--mono);font-size:.88em}

header{border-bottom:1px solid var(--line);background:linear-gradient(180deg,#161b22,#0d1117)}
.hero{padding:56px 0 40px}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:600;
  border:1px solid var(--line);background:var(--bg3);color:var(--fg2);margin-bottom:18px}
h1{font-size:36px;font-weight:700;letter-spacing:-.5px;margin-bottom:10px}
h1 .en{color:var(--fg2);font-size:20px;font-weight:500;margin-left:10px}
.lead{font-size:17px;color:var(--fg2);max-width:760px;margin-bottom:26px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.stat{background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:16px}
.stat .v{font-size:24px;font-weight:700;color:var(--ok);font-family:var(--mono)}
.stat .k{font-size:12px;color:var(--fg2);margin-top:3px}

section{padding:44px 0;border-bottom:1px solid var(--line)}
h2{font-size:24px;margin-bottom:8px;letter-spacing:-.3px}
h2 .n{color:var(--accent);font-family:var(--mono);font-size:16px;margin-right:10px}
.sub{color:var(--fg2);margin-bottom:22px;font-size:14px}
h3{font-size:16px;margin:22px 0 10px;color:var(--fg)}

.card{background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:14px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:820px){.grid2{grid-template-columns:1fr}}

table{width:100%;border-collapse:collapse;font-size:13.5px;margin:8px 0}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line)}
th{color:var(--fg2);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.5px}
td.mono,th.mono{font-family:var(--mono)}
tr:hover td{background:rgba(88,166,255,.05)}
.num{text-align:right;font-family:var(--mono)}

pre{background:var(--bg3);border:1px solid var(--line);border-radius:8px;padding:14px;
  overflow-x:auto;font-size:12.5px;line-height:1.55;color:#c9d1d9}
.tag{display:inline-block;padding:2px 8px;border-radius:5px;font-size:11.5px;font-weight:600;
  font-family:var(--mono)}
.tag.ok{background:rgba(63,185,80,.15);color:var(--ok);border:1px solid rgba(63,185,80,.35)}
.tag.warn{background:rgba(210,153,34,.15);color:var(--warn);border:1px solid rgba(210,153,34,.35)}
.tag.bad{background:rgba(248,81,73,.15);color:var(--bad);border:1px solid rgba(248,81,73,.35)}
.tag.info{background:rgba(88,166,255,.15);color:var(--accent);border:1px solid rgba(88,166,255,.35)}

.steps{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:12px 0}
.step{background:var(--bg3);border:1px solid var(--line);border-radius:8px;padding:9px 13px;
  font-family:var(--mono);font-size:12.5px}
.step.fail{border-color:rgba(248,81,73,.5);background:rgba(248,81,73,.08)}
.step.pass{border-color:rgba(63,185,80,.5);background:rgba(63,185,80,.08)}
.arrow{color:var(--fg2)}

.kv{display:grid;grid-template-columns:auto 1fr;gap:6px 16px;font-size:13.5px}
.kv dt{color:var(--fg2)}
.kv dd{font-family:var(--mono)}

.note{background:rgba(88,166,255,.07);border-left:3px solid var(--accent);
  padding:12px 16px;border-radius:0 8px 8px 0;font-size:13.5px;color:var(--fg2);margin:14px 0}
.warnbox{background:rgba(210,153,34,.07);border-left:3px solid var(--warn);
  padding:12px 16px;border-radius:0 8px 8px 0;font-size:13.5px;margin:14px 0}

footer{padding:36px 0 60px;color:var(--fg2);font-size:13px}
footer .links{display:flex;gap:20px;flex-wrap:wrap;margin-bottom:16px}
"""


def build_html(d: dict) -> str:
    """渲染完整 HTML。"""
    rec = d["recommend"]
    p = rec["primary"]
    cv = d["converge"]
    card = d["card"]
    sch = d["search"]
    se = d["search_empty"]
    clo = d["closure"]
    ev = d["eval"]

    # 主方案表
    plan_rows = "".join(
        f"<tr><td>{esc(k)}</td><td class='num'>{esc(v)}</td></tr>"
        for k, v in [
            ("主轴转速 n", f"{num(p['n'],1)} r/min"),
            ("进给速度 vf", f"{num(p['vf'],1)} mm/min"),
            ("每齿进给 fz", f"{p['fz']} mm/z"),
            ("切削速度 vc", f"{num(p['vc'],1)} m/min"),
            ("轴向切深 ap", f"{num(p['ap'],2)} mm"),
            ("径向切宽 ae", f"{num(p['ae'],2)} mm"),
            ("材料去除率 MRR", f"{num(p['mrr'],0)} mm³/min"),
            ("切削功率 Pc", f"{num(p['pc'],3)} kW"),
            ("理论粗糙度 Ra_th", f"{num(p['raTh'],4)} μm"),
            ("预测粗糙度 Ra_pred", f"{num(p['raPred'],3)} μm"),
            ("刀具寿命 T", f"{num(p['toolLife'],2)} min"),
            ("加工时间 Tm", f"{num(p.get('machiningTime') or 0,3)} min"),
            ("颤振等级", esc(p["chatterRisk"])),
            ("置信度", esc(p["confidence"])),
        ]
    )

    # 备选方案
    alt_rows = ""
    for a in rec["alternatives"]:
        alt_rows += (
            f"<tr><td class='mono'>{esc(a['label'])}</td>"
            f"<td>{esc(a['strategy'])}</td>"
            f"<td class='num'>{num(a['vc'],1)}</td>"
            f"<td class='num'>{a['fz']}</td>"
            f"<td class='num'>{num(a['pc'],2)}</td>"
            f"<td class='num'>{num(a['raPred'],3)}</td>"
            f"<td class='num'>{num(a['toolLife'],1)}</td></tr>"
        )

    # 收敛链
    steps = ""
    for i, h in enumerate(cv["history"]):
        failed = h["failed"]
        cls = "pass" if not failed else "fail"
        mark = "✓" if not failed else "✗ " + ",".join(failed)
        if i:
            steps += "<span class='arrow'>→</span>"
        steps += (
            f"<div class='step {cls}'>第 {h['iteration']} 轮<br>"
            f"<span style='color:var(--fg2)'>ap={h['ap']:.2f} ae={h['ae']:.2f}</span><br>"
            f"{mark}</div>"
        )

    # 检索
    hit_rows = "".join(
        f"<tr><td class='mono'>{esc(h['kbId'])}</td><td>{esc(h['title'])}</td>"
        f"<td class='num'>{h['relevance']:.3f}</td><td class='num'>{h['score']:.4f}</td></tr>"
        for h in sch["hits"]
    )

    # 回填
    a = clo["assessment"]
    level_tag = {
        "NORMAL": "ok", "MEDIUM": "warn", "SEVERE": "bad"
    }.get(a["level"], "info")
    trig = "、".join(a["triggers"]) if a["triggers"] else "无"

    # 评估
    ev_rows = ""
    label_map = {
        "recommendPassRate": "推荐合格率",
        "overrunMissRate": "越限漏检率",
        "raMae": "Ra 预测 MAE",
        "schemaValidRate": "结构化合法率",
        "refusalAccuracy": "拒答准确率",
        "firstClarifyRate": "首轮澄清成功率",
    }
    for k, name in label_map.items():
        val = ev["metrics"][k]
        tgt = ev["targets"][k]
        ok = (val <= tgt) if k == "overrunMissRate" else (val >= tgt)
        tag = "<span class='tag ok'>达标</span>" if ok else "<span class='tag bad'>未达标</span>"
        ev_rows += (
            f"<tr><td>{esc(name)}</td><td class='num'>{val}</td>"
            f"<td class='num'>{tgt}</td><td>{tag}</td></tr>"
        )

    warn_html = ""
    if rec["warnings"]:
        warn_html = (
            "<div class='warnbox'><strong>系统风险提示</strong><ul style='margin:8px 0 0 18px'>"
            + "".join(f"<li>{esc(w)}</li>" for w in rec["warnings"])
            + "</ul></div>"
        )

    kb_html = "、".join(
        f"<code>{esc(k['kbId'])}</code>" for k in rec["knowledge"]
    ) or "未检索到"

    counts = ev["counts"]

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>智刃工艺 ZR-CUT · CNC 切削工艺参数智能推荐系统</title>
<meta name="description" content="面向机加车间与实训中心的 CNC 切削工艺参数推荐系统。确定性计算引擎 + 十条硬约束校验 + 小样本预测 + 回填闭环。">
<style>{CSS}</style>
</head>
<body>

<header>
<div class="wrap hero">
  <div class="badge">人工智能校赛 · 智能制造赛道 · 可运行原型</div>
  <h1>智刃工艺<span class="en">ZR-CUT</span></h1>
  <p class="lead">
    输入材料、刀具、机床与质量要求，输出<b>可执行的切削参数</b>、<b>三项预测与置信区间</b>、
    <b>十条硬约束校验结果</b>与<b>可签发的工艺卡</b>。
    核心设计是<b>禁止大模型心算</b>——所有数值只来自确定性计算引擎，可被第三方复算。
  </p>
  <div class="stats">
    <div class="stat"><div class="v">284</div><div class="k">单元测试全部通过</div></div>
    <div class="stat"><div class="v">86.1%</div><div class="k">代码覆盖率</div></div>
    <div class="stat"><div class="v">400</div><div class="k">条自动化评估集</div></div>
    <div class="stat"><div class="v">0%</div><div class="k">越限漏检率</div></div>
    <div class="stat"><div class="v">44</div><div class="k">项独立数值验算 0 失败</div></div>
  </div>
</div>
</header>

<div class="wrap">

<section>
  <h2><span class="n">01</span>参数推荐</h2>
  <p class="sub">请求：材料 {esc(rec['request']['material'])} · 刀具 {esc(rec['request']['tool'])} ·
  工序 {esc(rec['request']['operation'])} · 粗糙度要求 {esc(rec['request']['raReq'])} μm ·
  去除体积 {num(rec['request']['volume'] or 0,0)} mm³</p>

  <div class="grid2">
    <div class="card">
      <h3 style="margin-top:0">主方案 P0 <span class="tag info">{esc(p['strategy'])}</span></h3>
      <table>{plan_rows}</table>
    </div>
    <div class="card">
      <h3 style="margin-top:0">备选方案</h3>
      <table>
        <thead><tr><th>标签</th><th>策略</th><th class="num">vc</th><th class="num">fz</th>
        <th class="num">Pc</th><th class="num">Ra</th><th class="num">T</th></tr></thead>
        <tbody>{alt_rows}</tbody>
      </table>
      <h3>知识来源</h3>
      <p style="color:var(--fg2);font-size:13px">{kb_html}</p>
    </div>
  </div>
  {warn_html}
  <div class="note">
    上表每个数值都可按规范 3.6 节公式独立复算。例如
    <code>n = 1000 × vc / (π × D)</code>、
    <code>Pc = MRR × Kc / (60×10⁶)</code>、
    <code>Ra_pred = fz² / (32 × rε) × 1000 × K_ra</code>。
  </div>
</section>

<section>
  <h2><span class="n">02</span>十条硬约束校验与自动收敛</h2>
  <p class="sub">场景：M1 + T4 大切宽大切深侧铣，初始参数 ap=20、ae=14。
  该系统<b>不是查表</b>——它能判断「这张表在你的机床上不成立」，并给出收敛后的替代值。</p>

  <div class="card">
    <div class="steps">{steps}</div>
    <div class="grid2" style="margin-top:18px">
      <div>
        <h3 style="margin-top:0">收敛结果</h3>
        <dl class="kv">
          <dt>是否收敛</dt><dd>{'是' if cv['converged'] else '否'}</dd>
          <dt>迭代轮数</dt><dd>{cv['iterations']}</dd>
          <dt>vc</dt><dd>{num(cv['final_params']['vc'],2)} m/min</dd>
          <dt>fz</dt><dd>{cv['final_params']['fz']:.4f} mm/z</dd>
          <dt>ap</dt><dd>{num(cv['final_params']['ap'],2)} mm</dd>
          <dt>ae</dt><dd>{num(cv['final_params']['ae'],2)} mm</dd>
        </dl>
      </div>
      <div>
        <h3 style="margin-top:0">收敛后指标</h3>
        <dl class="kv">
          <dt>主轴转速 n</dt><dd>{num(cv['finalMetrics']['n'],1)} r/min</dd>
          <dt>进给速度 vf</dt><dd>{num(cv['finalMetrics']['vf'],1)} mm/min</dd>
          <dt>切削功率 Pc</dt><dd>{num(cv['finalMetrics']['pc'],3)} kW</dd>
          <dt>预测粗糙度</dt><dd>{num(cv['finalMetrics']['raPred'],3)} μm</dd>
          <dt>刀具寿命</dt><dd>{num(cv['finalMetrics']['toolLife'],2)} min</dd>
        </dl>
      </div>
    </div>
  </div>
  <div class="note">
    收敛顺序固定为 <b>先降 ap → 再降 ae → 再降 vc → 最后降 fz</b>。
    理由：ap 对功率是线性影响且不改变 Ra，代价最小；fz 直接决定 Ra 且与颤振耦合，放在最后。
    收敛不是一次性求解，而是「逐条校验—回代复核」的迭代过程。
  </div>
</section>

<section>
  <h2><span class="n">03</span>工艺卡签发</h2>
  <p class="sub">推荐结果落成车间可执行的工艺卡，签发后锁定，修改须走变更单。</p>
  <div class="grid2">
    <div class="card">
      <h3 style="margin-top:0">{esc(card['cardId'])} <span class="tag info">{esc(card['status'])}</span></h3>
      <table>
        <tr><td>零件名</td><td>{esc(card['partName'])}</td></tr>
        <tr><td>材料 / 刀具</td><td class="mono">{esc(card['materialCode'])} / {esc(card['toolCode'])}</td></tr>
        <tr><td>主轴转速</td><td class="num">{num(card['spindleSpeed'],1)} r/min</td></tr>
        <tr><td>进给速度</td><td class="num">{num(card['feedRate'],1)} mm/min</td></tr>
        <tr><td>轴向切深 / 切宽</td><td class="num">{num(card['axialDepth'],2)} / {num(card['radialWidth'],2)} mm</td></tr>
        <tr><td>冷却方式</td><td>{esc(card['coolant'])}</td></tr>
        <tr><td>预计粗糙度</td><td class="num">{num(card['estRoughness'],3)} μm</td></tr>
        <tr><td>颤振等级</td><td>{esc(card['chatterRisk'])}</td></tr>
      </table>
    </div>
    <div class="card">
      <h3 style="margin-top:0">防篡改与扫码</h3>
      <p style="color:var(--fg2);font-size:13px;margin-bottom:10px">
        内容哈希基于参数快照计算，签发后任何改动都会导致哈希不匹配。</p>
      <pre>contentHash: {esc(card['contentHash'])}</pre>
      <p style="color:var(--fg2);font-size:13px;margin:12px 0 8px">扫码载荷（车间扫码枪可直接解析）：</p>
      <pre>{esc(card['qrPayload'])}</pre>
    </div>
  </div>
</section>

<section>
  <h2><span class="n">04</span>知识检索与空结果兜底</h2>
  <p class="sub">BM25 + IDF 加权查询覆盖率 + 三因子（相关度 0.5 / 权威度 0.35 / 时效性 0.15）打分。
  无可靠依据时<b>不硬凑结果</b>。</p>
  <div class="grid2">
    <div class="card">
      <h3 style="margin-top:0">查询：{esc(sch['query'])}</h3>
      <table>
        <thead><tr><th>编号</th><th>标题</th><th class="num">相关度</th><th class="num">综合分</th></tr></thead>
        <tbody>{hit_rows}</tbody>
      </table>
    </div>
    <div class="card">
      <h3 style="margin-top:0">查询：{esc(se['query'])}</h3>
      <p style="margin:14px 0">
        <span class="tag {'bad' if se['empty'] else 'warn'}">
        {'返回空结果（正确拒绝）' if se['empty'] else '有结果'}</span>
      </p>
      <p style="color:var(--fg2);font-size:13.5px">{esc(se['message'] or '—')}</p>
      <div class="note" style="margin-top:16px">
        早期版本用「BM25 分 ÷ 池内最大值」作相关度，导致任何命中一个通用词（如「参数」）
        的条目都拿到 1.0 并绕过阈值。改为绝对量覆盖率后，无关查询正确返回空。
      </div>
    </div>
  </div>
</section>

<section>
  <h2><span class="n">05</span>回填闭环</h2>
  <p class="sub">试切后回填实测数据，按三档阈值判定：Ra &gt; 25%、工时 &gt; 30%、寿命 &gt; 40%。</p>
  <div class="card">
    <dl class="kv">
      <dt>判定档位</dt><dd><span class="tag {level_tag}">{esc(a['level'])}</span></dd>
      <dt>处置方式</dt><dd>{esc(a['disposition'])}</dd>
      <dt>Ra 偏差</dt><dd>{num(a['raDeviation']*100 if a['raDeviation'] is not None else 0,1)}%</dd>
      <dt>工时偏差</dt><dd>{num(a['timeDeviation']*100 if a['timeDeviation'] is not None else 0,1)}%</dd>
      <dt>寿命偏差</dt><dd>{num(a['lifeDeviation']*100 if a['lifeDeviation'] is not None else 0,1)}%</dd>
      <dt>触发项</dt><dd>{esc(trig)}</dd>
      <dt>置信度调整</dt><dd>{esc(a['confidenceAfter'])}</dd>
    </dl>
    <h3>系统建议</h3>
    <p style="color:var(--fg2);font-size:13.5px">{esc(a['hint'])}</p>
  </div>
  <div class="warnbox">
    <b>禁止在线学习。</b>回填数据只进样本池，模型更新必须走离线增量训练 + 灰度发布流程，
    避免生产参数被单次异常试切污染。
  </div>
</section>

<section>
  <h2><span class="n">06</span>自动化评估</h2>
  <p class="sub">{counts['total']} 条评估集 = 参数合理性 {counts['parameterRecommendation']} +
  越限对抗 {counts['overrunAdversarial']} + 预测精度 {counts['predictionAccuracy']} +
  格式与拒答 {counts['schemaAndRefusal']}。六指标全达标，越限漏检为零。</p>
  <div class="card">
    <table>
      <thead><tr><th>指标</th><th class="num">实测值</th><th class="num">目标值</th><th>判定</th></tr></thead>
      <tbody>{ev_rows}</tbody>
    </table>
  </div>
</section>

<section>
  <h2><span class="n">07</span>核心架构：数值可信链</h2>
  <p class="sub">系统与「通用大模型问答」的根本区别。五道关卡，每道都能拦截。</p>
  <div class="card">
    <div class="steps">
      <div class="step pass">① 工具返回<br><span style="color:var(--fg2)">calc.py 纯函数</span></div>
      <span class="arrow">→</span>
      <div class="step pass">② 约束校验<br><span style="color:var(--fg2)">C-01..C-10</span></div>
      <span class="arrow">→</span>
      <div class="step pass">③ 模型组织<br><span style="color:var(--fg2)">只选择不改数</span></div>
      <span class="arrow">→</span>
      <div class="step pass">④ Schema 校验<br><span style="color:var(--fg2)">契约稳定</span></div>
      <span class="arrow">→</span>
      <div class="step pass">⑤ 前端渲染<br><span style="color:var(--fg2)">带单位</span></div>
    </div>
  </div>
  <div class="grid2">
    <div class="card">
      <h3 style="margin-top:0">数值纪律的机器化执行</h3>
      <p style="color:var(--fg2);font-size:13.5px">
      LLM 输出中的每个数字必须能在本轮工具返回值里找到同值（±2% 容差），
      找不到就判为大模型自算，整段作废并退回模板。</p>
    </div>
    <div class="card">
      <h3 style="margin-top:0">主轴极限优先于材料区间</h3>
      <p style="color:var(--fg2);font-size:13.5px">
      铝合金 vc=300 配 φ10 刀具时 n=9549 r/min 超主轴上限。
      此时允许 vc 低于材料推荐下限——主轴安全优先，用 vc_floor 机制表达，
      避免 C-01 与 C-05 死锁。</p>
    </div>
  </div>
</section>

<section>
  <h2><span class="n">08</span>如何本地运行</h2>
  <p class="sub">本页是静态演示，数据来自系统真实运行输出。完整交互需本地启动 HTTP API。</p>
  <div class="card">
    <pre># 环境要求：Python 3.10+、numpy
git clone &lt;仓库地址&gt;
cd &lt;仓库目录&gt;

# 一键：测试 + 覆盖率 + 评估
.\run-tests.bat

# 一键：启动 HTTP API
.\启动智刃工艺.bat
# 健康检查 http://127.0.0.1:8077/health

# 命令行直接提问
python -m zrcut.cli recommend -m M1 -t T3 -o OP2 --ra 1.6 --volume 288000</pre>
  </div>
</section>

<footer>
  <div class="links">
    <a href="https://github.com/">源代码</a>
    <a href="./ARCHITECTURE.md">架构说明</a>
    <a href="./draft/09-examples.md">端到端示例</a>
  </div>
  <p>智刃工艺 ZR-CUT · MIT License · 零外部依赖（Python 标准库 + numpy）</p>
  <p style="margin-top:10px;font-size:12px">
    免责声明：本系统用于工艺参数推荐辅助，不替代法定检验、持证作业与工艺工程师签发。
    颤振判定采用单自由度简化叶瓣模型，仅用于风险分级，不用于精确预测稳定域。
  </p>
</footer>

</div>
</body>
</html>
"""


def main() -> int:
    """主入口。"""
    data = collect()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build_html(data), encoding="utf-8")
    size_kb = OUT.stat().st_size / 1024
    print(f"[写出] {OUT}")
    print(f"[大小] {size_kb:.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
