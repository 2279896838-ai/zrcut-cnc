# 智刃工艺 · 架构说明

## 模块依赖图

依赖方向严格单向，不存在循环依赖。下层不知道上层存在。

```
                     ┌─────────────┐
                     │   api.py    │  HTTP 层
                     │   cli.py    │  命令行层
                     └──────┬──────┘
                            │
                     ┌──────▼──────┐
                     │ service.py  │  业务门面（唯一入口）
                     └──────┬──────┘
                            │
        ┌───────────┬───────┼───────┬───────────┐
        ▼           ▼       ▼       ▼           ▼
  recommender   knowledge  card  closure    evaluate
        │           │       │       │           │
        ▼           ▼       ▼       ▼           │
  constraints   domain  domain  domain          │
        │                                       │
        ▼                                       │
      calc ◄──────────────────────────────────────┘
        │
        ▼
     domain.py         （常量层，无内部依赖）
```

| 模块 | 依赖 | 被谁依赖 |
| --- | --- | --- |
| `domain.py` | 无 | 几乎所有模块 |
| `calc.py` | domain | constraints, recommender, service, evaluate |
| `constraints.py` | domain, calc | recommender, service, evaluate |
| `recommender.py` | domain, calc, constraints | service, card, evaluate |
| `predictor.py` | domain | service |
| `knowledge.py` | domain | service |
| `card.py` | domain, recommender, errors | service |
| `closure.py` | domain, errors | service |
| `llm.py` | domain, errors | service |
| `schema.py` | errors | service |
| `errors.py` | 无 | 全部上层 |
| `service.py` | 以上全部 | api, cli, evaluate |
| `api.py` | service, domain, errors | （对外） |
| `cli.py` | service, evaluate | （对外） |
| `evaluate.py` | service, constraints, calc, domain | cli |

## 数值可信链的实现

这是系统最核心的架构约束，五道关卡逐层收窄。

### 关卡一：工具返回

`calc.py` 是所有切削数值的唯一产地。它只包含纯函数：

```python
def spindle_speed(vc: float, diameter: float) -> float:
    if vc <= 0: raise CalculationError(...)
    if diameter <= 0: raise CalculationError(...)
    return 1000.0 * vc / (math.pi * diameter)
```

不读全局状态、不读时间、不读网络、不用随机数。同样的入参永远得到同样的结果。

### 关卡二：约束校验

`constraints.py` 逐条判定 C-01 至 C-10。判定失败不是终点——`converge()` 会按
固定优先级迭代收敛：先降 ap → 再降 ae → 再降 vc → 最后降 fz。

关键设计是 `_diagnose()`：它判断的不是「哪条约束失败」，而是「哪个自变量超限」。
因为 C-06 失败可能由 ap/D 或 ae/D 引起，两者的收敛动作完全不同。早期版本混淆了
这一点，导致 C-06 因 ae 越限时反复降 ap，6 轮无效迭代。

### 关卡三：模型组织

`llm.py` 的 `check_numeric_discipline()` 做机器化检查：输出文本中的每个数字必须
能在本轮工具返回值中找到同值（±2% 容差）。找不到就判定为大模型自算，整段回答
作废并退回模板。

```python
violations = check_numeric_discipline(llm_text, allowed_values)
if violations:
    return fallback_template, "template-numeric-violation"
```

### 关卡四：Schema 校验

`schema.py` 是零依赖的 JSON Schema 子集实现。`ServiceConfig.strict_schema=True`
时，任何输出字段不合规都会抛异常而非静默放行。

这个关卡在开发中抓到了真实缺陷：`Plan.to_dict()` 输出 `ra_th` 而下层 Schema
期望 `raTh`，属于 API 契约破裂。

### 关卡五：前端渲染

参数以表格呈现，数值带单位。字段缺失时置灰显示「待确认」，不显示 0。

## 关键算法

### 参数推荐的八步顺序

```
① 由材料+刀具查 vc 区间
② 算 n 并受 C-01 截断
③ 由 Ra_req 反解 fz 上限
④ 由 ap/D、ae/D 上限定切深切宽（含悬伸与薄壁折减）
⑤ 算 vf
⑥ 算 Pc 并受 C-03 校验
⑦ 算颤振等级
⑧ 输出主方案 + 两套备选
```

模型只做「选择与串联」，每个数值都由工具给出。

### 收敛算法的诊断逻辑

```python
def _diagnose(material, tool, *, vc, fz, ap, ae, ctx) -> list[str]:
    need = set()
    ap_d, ae_d = ap / tool.diameter, ae / tool.diameter
    ap_lim = material.ap_d_max            # 计入悬伸折减与薄壁折减
    if res.pc > MACHINE.power_continuous_kw:
        need.add("ap")                     # 功率只能靠降 ap 或 ae
    if ap_d > ap_lim:  need.add("ap")
    if ae_d > ae_lim:  need.add("ae")      # 与上一条严格分离
    ...
    return [k for k in ("ap", "ae", "vc", "fz") if k in need]
```

返回列表的首元素即为本轮动作，保证优先级稳定。

### 主轴极限优先于材料区间

铝合金 vc=300 配 φ10 刀具时 n=9,549 r/min，超主轴上限 8,000。此时若强行把 vc
卡在材料下限 300，C-01 永远失败；若降到 300 以下，C-05 又判「低于下限」。

解决方式是在 `ValidationContext` 引入 `vc_floor`：当材料区间下限在该机床上会
导致超速时，有效下限降为由主轴上限反推的 vc 值。

```python
vc_from_spindle = cutting_speed_from_rpm(spindle_max_rpm * 0.98, tool.diameter)
if material.vc_min > vc_from_spindle:
    ctx = ctx._replace(vc_floor=vc_from_spindle)   # 主轴安全优先
```

### 检索的相关度定义

早期实现用「BM25 分 / 池内最大值」作为相关度，导致任何命中一个通用词（如「参数」）
的条目都拿到相关度 1.0，从而绕过阈值返回无关结果——「火星探测器轨道参数」能命中
「球墨铸铁铣削参数」。

改为 **IDF 加权查询覆盖率**：

```
relevance = Σ(idf of matched query terms) / Σ(idf of all query terms)
```

这是绝对量，只命中少数弱词时自然偏低。修复后该查询正确返回空结果。

### 比值建模而非绝对值建模

预测器学习的是「实测 / 理论」的比值，不是直接预测绝对值：

```python
ratio = model.predict(features)
value = theory_value * ratio
ratio = max(ratio, 0.1)      # 防止模型给出非物理的负值或零
```

这保证模型只能围绕物理公式做修正，不会出现数量级偏离。无训练样本时比值退化为
1.0，即直接返回公式值，并标注 LOW 置信度。

## 错误处理约定

所有业务异常继承 `ZrCutError`，携带稳定错误码、中文说明、结构化细节与建议 HTTP 状态码。

```python
raise ZrCutError(
    ErrorCode.COOLANT_REQUIRED,
    "M3（不锈钢）必须使用内冷或高压外冷，当前冷却条件不具备，不得开切",
    {"material": "M3", "outlet": "接入内冷后重新推荐"},
    422,
)
```

错误码分段：

| 区间 | 类别 | 示例 |
| --- | --- | --- |
| E1xxx | 输入错误 | E1003 未知材料 |
| E2xxx | 计算错误 | E2002 无可行方案 |
| E3xxx | 约束错误 | E3004 粗糙度不可达 |
| E4xxx | 知识错误 | E4002 无检索命中 |
| E5xxx | 工艺卡错误 | E5002 已签发锁定 |
| E6xxx | 回填错误 | E6002 回填记录非法 |
| E7xxx | 安全错误 | E7003 必须冷却 |
| E9xxx | 系统错误 | E9001 LLM 不可用 |

底层异常（`CalculationError`、`RecommendationError` 等）在服务层转换为错误码，
不直接把 Python 异常类名暴露给调用方。

## 测试策略

| 层次 | 文件 | 重点 |
| --- | --- | --- |
| 计算 | `test_calc.py` | 每条公式的正算、反算、逆运算、边界 |
| 约束 | `test_constraints.py` | C-01..C-10 逐条正负例、颤振分级、收敛行为 |
| 推荐 | `test_recommender.py` | 八步顺序可验算、策略差异、置信度分化 |
| 预测 | `test_predictor.py` | 岭回归拟合能力、区间性质、无样本退化 |
| 检索 | `test_knowledge.py` | 分词、三因子、空结果兜底、冲突检测 |
| 工艺卡回填 | `test_card_closure.py` | 字段完整性、签发锁定、三档判定 |
| 服务与 API | `test_service_api.py` | 错误码映射、Schema、LLM 降级、HTTP 端到端 |
| CLI 与评估 | `test_cli_eval.py` | 全部命令、400 条评估集、六指标 |

测试用例刻意写成**断言行为而非断言实现细节**。例如约束测试检查「薄壁件应报告
折减后的上限」而不是「上限等于某个硬编码数字」，这样重构不会误伤。

## 已知限制

1. **颤振判据是简化模型**。单自由度叶瓣模型只做风险分级（LOW/MEDIUM/HIGH），
   不用于精确预测稳定域。真实稳定域须以切削试验或锤击模态测试标定。系统中凡
   引用该判据处均附此声明。

2. **小样本预测器在无数据时退化**。没有历史试切样本时预测值等于公式值，
   置信度标 LOW，区间宽度 ±20%。这是刻意的保守设计，不是缺陷。

3. **HTTP 层无鉴权**。当前定位是内网/实训室部署。对外暴露前须加认证与速率限制。

4. **内存态存储**。工艺卡与回填记录存在进程内存中，重启即丢失。生产部署应换
   用 SQLite 或外部数据库——接口已按仓储模式设计，替换不影响上层。
