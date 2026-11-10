# 第7章 输出 Schema 与工具接口

本章把五段式输出（规范 4.1 节）与统一公式（规范 3.6 节）固化为机器可校验的结构，是数值可信链的最后一道闸门：工具返回的数值经大模型组织后，必须通过本章 Schema 校验才能进入前端渲染与工艺卡签发。字段设计原则有三条：凡 3.6 节出现的物理量必须有同名字段并带单位；凡 4.1 节五段必须有对应字段；凡无法从工具获得的量一律不设字段，避免模型自填。

## 7.1 六类输出 Schema

### 7.1.1 五段式与字段对应关系

| 4.1 节段位 | 对应 Schema 字段 | 说明 |
| --- | --- | --- |
| 结论 | `conclusion.text`、`conclusion.confidence` | 置信度取 HIGH/MEDIUM/LOW，口径见 4.2 节 |
| 依据 | `basis[]` | 每项含 formulaId（对齐 3.6 节公式名）与 kbId |
| 参数 | `params` / `prediction` / `checks[]` 等按类型展开 | 数值必须来自工具返回 |
| 风险 | `risks[]` | 越限项、颤振等级、首件要求 |
| 来源 | `sources[]` | 形如 KB-CNC-0042、T3、M1 |

### 7.1.2 ParameterRecommendation（参数推荐）

| 字段名 | 类型 | 必填 | 取值范围 | 说明 |
| --- | --- | --- | --- | --- |
| schemaVersion | string | 是 | 固定 `"1.0"` | 版本号 |
| cardId | string | 是 | `PC-YYYYMMDD-NNNN` | 关联卡号，未签发时为空串 |
| sessionId | string | 是 | `SESS-<8位>` | 会话号 |
| materialCode | enum | 是 | M1–M5 | 材料代号 |
| toolCode | enum | 是 | T1–T6 | 刀具代号 |
| machineCode | string | 是 | 固定 `VMC-850` | 机床名称 |
| machineAssetId | string | 是 | 固定 `EQ-A01-0037` | 设备编码 |
| opCode | enum | 是 | OP1–OP4 | 工序代号 |
| strategy | enum | 是 | efficiency / quality / life | 效率优先 / 质量优先 / 寿命优先 |
| params | object | 是 | 见下表 | 主方案参数 |
| alternatives | array | 是 | 长度 2 | 两套备选，策略互斥 |
| basis | array | 是 | ≥2 项 | 依据与公式锚点 |
| risks | array | 是 | 0–5 项 | 风险条目 |
| sources | array | 是 | ≥1 项 | 来源编号 |
| conclusion | object | 是 | `{text, confidence}` | 结论段 |
| createdAt | string | 是 | ISO 8601 | 生成时间 |

`params` 子对象字段（与 3.6 节公式一一对应）：

| 字段名 | 类型 | 单位 | 必填 | 约束 |
| --- | --- | --- | --- | --- |
| n | number | r/min | 是 | 1–8,000（C-01） |
| vc | number | m/min | 是 | 落在 3.5 节区间 |
| fz | number | mm/z | 是 | 落在 3.5 节区间 |
| vf | number | mm/min | 是 | 1–10,000（C-02） |
| ap | number | mm | 是 | ≤ap/D 上限 ×D |
| ae | number | mm | 是 | ≤ae/D 上限 ×D |
| Pc | number | kW | 是 | ≤11（C-03） |
| RaPred | number | μm | 是 | ≤Ra_req（C-08） |
| toolLife | number | min | 是 | Taylor 计算值 |
| mrr | number | mm³/min | 否 | 材料去除率 |

完整示例（M1 + T3 + OP2，效率优先）：

```json
{
  "schemaVersion": "1.0",
  "cardId": "PC-20261109-0007",
  "sessionId": "SESS-3f7a21c4",
  "materialCode": "M1",
  "toolCode": "T3",
  "machineCode": "VMC-850",
  "machineAssetId": "EQ-A01-0037",
  "opCode": "OP2",
  "strategy": "efficiency",
  "params": {
    "n": 2984,
    "vc": 150,
    "fz": 0.06,
    "vf": 716,
    "ap": 4.0,
    "ae": 4.0,
    "Pc": 0.382,
    "RaPred": 0.18,
    "toolLife": 96.3,
    "mrr": 11456
  },
  "alternatives": [
    {
      "strategy": "quality",
      "params": {"n": 2984, "vc": 150, "fz": 0.04, "vf": 477, "ap": 3.0, "ae": 3.2, "Pc": 0.153, "RaPred": 0.10, "toolLife": 96.3},
      "tradeoff": "MRR 降至主方案 66%，Ra 余量提升至 0.10 μm 以下"
    },
    {
      "strategy": "life",
      "params": {"n": 2387, "vc": 120, "fz": 0.05, "vf": 477, "ap": 4.0, "ae": 4.0, "Pc": 0.254, "RaPred": 0.20, "toolLife": 235.0},
      "tradeoff": "寿命提升约 2.4 倍，MRR 为 7632 mm³/min 降至主方案 66%"
    }
  ],
  "basis": [
    {"formulaId": "F-N", "statement": "n = 1000×vc/(π×D)，vc 取 150 m/min，D = 16 mm", "kbId": "KB-CNC-0042"},
    {"formulaId": "F-VF", "statement": "vf = fz×z×n = 0.06×4×2984", "kbId": "KB-CNC-0042"},
    {"formulaId": "F-PC", "statement": "Pc = ap×ae×vf×Kc/(60×10⁶)，Kc = 2000 N/mm²", "kbId": "KB-SST-0004"},
    {"formulaId": "F-RA", "statement": "Ra_th = fz²/(32×rε)×1000，rε = 0.8 mm，K_ra = 1.6", "kbId": "KB-CNC-0042"}
  ],
  "risks": [
    {"type": "chatter", "level": "LOW", "detail": "n = 2984 r/min 不在 z=4 的稳定带（1600/2400/4800 r/min）内，但 ae/D = 0.25 ≤ 0.3 且 ap = 0.25D ≤ 0.5D，判 MEDIUM"},
    {"type": "firstPiece", "level": "INFO", "detail": "首件须检验 Ra 与刀具磨损 VB"}
  ],
  "sources": ["[KB-CNC-0042]", "[KB-SST-0004]", "[T3]", "[M1]", "[OP2]"],
  "conclusion": {"text": "M1+T3 侧铣效率优先方案：vc 150 m/min、n 2984 r/min、vf 716 mm/min，约束全通过", "confidence": "HIGH"},
  "createdAt": "2026-11-09T10:12:00+08:00"
}
```

示例数值验算（供读者复核，不作为运行时计算依据，全部按 3.6 节公式）：n = 1000×150/(π×16) = 2984 r/min；vf = 0.06×4×2984 = 716 mm/min；MRR = 4.0×4.0×716 = 11456 mm³/min；Pc = 4.0×4.0×716×2000/(60×10⁶) = 0.382 kW；Ra_th = 0.06²/(32×0.8)×1000 = 0.14 μm，Ra_pred = 0.14×1.6 = 0.22 μm；T = (350/150)^(1/0.25) = 96.3 min。备选 quality 方案 ap 3.0、ae 3.2、vf 477：Pc = 3.0×3.2×477×2000/(60×10⁶) = 0.153 kW，Ra_th = 0.04²/(32×0.8)×1000 = 0.06 μm，Ra_pred = 0.10 μm。备选 life 方案 vc 120、fz 0.05、n 2387、vf 477：Pc = 4.0×4.0×477×2000/(60×10⁶) = 0.254 kW，T = (350/120)^(1/0.25) = 235.0 min。

### 7.1.3 PredictionExplanation（预测解释）

| 字段名 | 类型 | 必填 | 取值范围 | 说明 |
| --- | --- | --- | --- | --- |
| schemaVersion | string | 是 | `"1.0"` | 版本号 |
| predictionId | string | 是 | `PRD-<8位>` | 预测标识 |
| modelVersion | string | 是 | 如 `xgb-2.3.1` | 预测器版本 |
| inputs | object | 是 | 8 维特征 | 见下表 |
| prediction | object | 是 | 见下表 | 预测值与区间 |
| theoryAnchor | object | 是 | 含 RaTh、toolLifeTheo | 3.6 节公式对照值 |
| deviation | object | 是 | `{metric, pct, direction, warning}` | 偏离率与告警 |
| explanation | array | 是 | 3 段 | 公式锚点 / 模型偏差 / 可信度 |
| confidence | enum | 是 | HIGH / MEDIUM / LOW | 置信度 |

`inputs` 八维特征：`hardnessHB`、`Kc`、`D`、`z`、`rEps`、`vc`、`fz`、`ap`。`prediction` 含 `raPred`、`raCI90`、`toolLifePred`、`toolLifeCI90`、`tmPred`、`pcPred`。

```json
{
  "schemaVersion": "1.0",
  "predictionId": "PRD-5a11c0e2",
  "modelVersion": "xgb-2.3.1",
  "inputs": {"hardnessHB": 95, "Kc": 700, "D": 10, "z": 2, "rEps": 5.0, "vc": 400, "fz": 0.02, "ap": 0.3},
  "prediction": {
    "raPred": 0.030,
    "raCI90": [0.024, 0.038],
    "toolLifePred": 46.0,
    "toolLifeCI90": [38.0, 55.0],
    "tmPred": 3.4,
    "pcPred": 0.056
  },
  "theoryAnchor": {"raTh": 0.010, "toolLifeTheo": 45.0, "formulaIds": ["F-RA-BALL", "F-T"]},
  "deviation": {"metric": "Ra", "pct": 66.7, "direction": "predict_higher", "warning": true},
  "explanation": [
    {"section": "formulaAnchor", "text": "Ra_th = fz²/(8×R)×1000 = 0.02²/(8×5)×1000 = 0.010 μm"},
    {"section": "modelDeviation", "text": "Ra_pred 0.030 μm 高于理论值 200%，主因球头行距与铝屑粘附，偏离 66.7% 超 40% 阈值，须告警"},
    {"section": "credibility", "text": "同材料同刀具历史样本 2 条，低于 3 条，置信度置 LOW，须试切"}
  ],
  "confidence": "LOW"
}
```

### 7.1.4 ConstraintCheckResult（约束校验结果）

| 字段名 | 类型 | 必填 | 取值范围 | 说明 |
| --- | --- | --- | --- | --- |
| schemaVersion | string | 是 | `"1.0"` | 版本号 |
| checkRunId | string | 是 | `CHK-<8位>` | 本次校验批次号 |
| overall | enum | 是 | pass / fail | 全通过才为 pass |
| items | array | 是 | 10 项 | C-01–C-10 逐项 |
| failedIds | array | 是 | `["C-03"]` 形式 | 未通过项编号 |
| convergence | object | 否 | `{applied, rounds, order}` | 收敛记录 |
| blockedReason | string | 否 | ≤80 字 | fail 时的收敛失败原因 |

`items` 每项字段对齐 4.4 节：`{checkId, constraint, passed, actual, limit, action, detail}`。

```json
{
  "schemaVersion": "1.0",
  "checkRunId": "CHK-8c31ab70",
  "overall": "fail",
  "items": [
    {"checkId": "C-01", "constraint": "n ≤ 8000 r/min", "passed": true, "actual": 2984, "limit": 8000, "action": "none", "detail": "units r/min"},
    {"checkId": "C-03", "constraint": "Pc ≤ 11 kW", "passed": false, "actual": 12.4, "limit": 11.0, "action": "reduce ap", "detail": "units kW, Kc = 1500 N/mm2"},
    {"checkId": "C-04", "constraint": "LD ≤ 4.0", "passed": true, "actual": 1.0, "limit": 4.0, "action": "none", "detail": "dimensionless, L = 80 mm, D = 80 mm"}
  ],
  "failedIds": ["C-03"],
  "convergence": {"applied": true, "rounds": 2, "order": ["ap", "ae", "vc", "fz"]},
  "blockedReason": ""
}
```

### 7.1.5 ProcessCard（工艺卡）

14 个必填字段加 4 个附注字段，字段名与 4.5 节一致：

| 字段名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| cardId | string | 是 | `PC-YYYYMMDD-NNNN` |
| partName | string | 是 | 零件名 |
| materialCode | enum | 是 | M1–M5 |
| blankSize | object | 是 | `{x, y, z}`，单位 mm |
| opNo | enum | 是 | OP1–OP4 |
| opName | string | 是 | 工序名 |
| machineAssetId | string | 是 | EQ-A01-0037 |
| toolCode | enum | 是 | T1–T6 |
| n | number | 是 | r/min |
| vf | number | 是 | mm/min |
| fz | number | 是 | mm/z |
| ap | number | 是 | mm |
| ae | number | 是 | mm |
| coolant | enum | 是 | flood / through / highPressure |
| estTime | number | 否 | 附注：预计工时 min |
| estRa | number | 否 | 附注：预计 Ra μm |
| chatterLevel | enum | 否 | 附注：LOW / MEDIUM / HIGH |
| firstArticle | string | 否 | 附注：首件检验要求 |
| signedBy | string | 是 | 签发角色代号 |
| locked | boolean | 是 | 签发后置 true |

```json
{
  "cardId": "PC-20261109-0007",
  "partName": "基板支架 A",
  "materialCode": "M1",
  "blankSize": {"x": 120, "y": 80, "z": 30},
  "opNo": "OP2",
  "opName": "侧铣（轮廓）",
  "machineAssetId": "EQ-A01-0037",
  "toolCode": "T3",
  "n": 2984,
  "vf": 716,
  "fz": 0.06,
  "ap": 4.0,
  "ae": 4.0,
  "coolant": "flood",
  "estTime": 2.8,
  "estRa": 0.22,
  "chatterLevel": "MEDIUM",
  "firstArticle": "首件检验 Ra ≤ 1.6 μm，刀具磨损 VB ≤ 0.15 mm",
  "signedBy": "process_engineer",
  "locked": true
}
```

### 7.1.6 ClosureRecord（回填闭环记录）

| 字段名 | 类型 | 必填 | 取值范围 | 说明 |
| --- | --- | --- | --- | --- |
| recordId | string | 是 | `TR-YYYYMMDD-NNNN` | 试切记录号 |
| cardId | string | 是 | PC 编号 | 关联工艺卡 |
| measuredRa | number | 是 | μm | 实测 Ra |
| measuredTime | number | 是 | min | 实测工时 |
| vbWear | number | 是 | mm | 后刀面磨损 VB |
| chatterObserved | boolean | 是 | true / false | 是否颤振 |
| anomaly | string | 否 | ≤80 字 | 异常现象 |
| operatorNote | string | 否 | ≤120 字 | 操作工备注 |
| deviation | object | 是 | `{raPct, timePct, lifePct}` | 三项偏差率 |
| disposition | enum | 是 | normal / medium / large | 三档处置 |

```json
{
  "recordId": "TR-20261109-0012",
  "cardId": "PC-20261109-0007",
  "measuredRa": 0.9,
  "measuredTime": 3.1,
  "vbWear": 0.11,
  "chatterObserved": false,
  "anomaly": "切屑颜色偏深，暂无异常振动",
  "operatorNote": "第 3 件开始 Ra 略有上升",
  "deviation": {"raPct": 309.1, "timePct": 10.7, "lifePct": -42.0},
  "disposition": "large"
}
```

Ra 偏差率口径：|实测 Ra − 预测 Ra|/预测 Ra。上例 |0.9 − 0.22|/0.22 = 309.1%，超过 25% 阈值；刀具寿命偏差按 4.6 节 40% 阈值同时命中，判 large。

### 7.1.7 Refusal（拒答）

| 字段名 | 类型 | 必填 | 取值范围 | 说明 |
| --- | --- | --- | --- | --- |
| code | enum | 是 | MACHINE_LIMIT / NO_EVIDENCE / SKIP_TRIAL_CUT / UNSAFE_SETUP / ROLE_RESTRICTED / CARD_LOCKED | 3.6 节枚举 |
| reason | string | 是 | ≤80 字 | 原因 |
| safeAction | string | 是 | ≤80 字 | 当前可执行的安全动作 |
| alternative | string | 是 | ≤80 字 | 替代路径 |
| escalateTo | string | 是 | 角色代号 | 转人工对象 |
| redline | string | 否 | SEC-01…SEC-06 | 命中安全红线时填写 |

```json
{
  "code": "MACHINE_LIMIT",
  "reason": "请求 n = 12000 r/min，超 VMC-850 主轴上限 8000 r/min，命中 C-01",
  "safeAction": "保持转速不超过 8000 r/min，先以建议区间参数做首件试切",
  "alternative": "由工艺工程师复核是否改用更高转速机床；本机台不提供超限参数",
  "escalateTo": "process_engineer",
  "redline": "SEC-01"
}
```

## 7.2 工具接口定义

工具签名遵循 OpenAPI 风格，所有数值的唯一合法来源即这六个工具。大模型只能编排调用，不得在工具返回之外写数。

| 工具名 | 入参 | 出参 | 失败码 | 调用方 |
| --- | --- | --- | --- | --- |
| recommend_params | materialCode: enum(M1–M5) 必填；toolCode: enum(T1–T6) 必填；opCode: enum(OP1–OP4) 必填；strategy: enum 必填；raReq: number 必填（μm）；apMax: number 可选（mm）；aeMax: number 可选（mm） | ParameterRecommendation 主体（不含 conclusion、basis） | E_RANGE / E_NO_COVERAGE | ZRCNC-PAR-001 |
| predict_outcome | features: object 8 维必填；模型版本可选 | PredictionExplanation 的 prediction、theoryAnchor 部分 | E_FEATURE_MISSING / E_MODEL_TIMEOUT | ZRCNC-PRD-001 |
| constraint_check | params: object 必填（n/vf/fz/ap/ae）；materialCode 必填；toolCode 必填；machineAssetId 必填 | ConstraintCheckResult 全量 | E_MACHINE_UNKNOWN / E_CONVERGE_EXHAUSTED | ZRCNC-CST-001 |
| make_card | recommendation: object 必填；partName、blankSize 必填；signedBy 必填 | ProcessCard（locked = true） | E_UNSIGNED / E_CARD_EXISTS | ZRCNC-CRD-001 |
| record_closure | cardId 必填；measuredRa、measuredTime、vbWear 必填；chatterObserved 必填 | ClosureRecord（含 deviation、disposition） | E_CARD_NOT_FOUND / E_OUT_OF_RANGE | ZRCNC-CLS-001 |
| kb_search | queries: array 1–5 条必填；filters: array(S1–S7) 可选；topK: integer 1–20 可选，默认 10 | chunks[]（字段同 5.1.2 节条目字段） | E_EMPTY_QUERY / E_INDEX_DOWN | ZRCNC-RAG-QRY-001、ZRCNC-RAG-RNK-001 |

调用纪律四条：一、工具返回即事实，返回为空即无证据，不得以记忆补位；二、同一会话内 constraint_check 必须晚于 recommend_params，make_card 必须晚于 constraint_check 且 overall 为 pass；三、任一工具返回失败码时不得重试超过 2 次，超出即按降级模板输出；四、所有工具的数值字段带单位说明，前端不得做单位换算以外的换算。

## 7.3 Schema 校验失败处理

校验失败分三级，逐级下沉，不允许跳过：

| 级别 | 触发 | 动作 | 输出 |
| --- | --- | --- | --- |
| 一级：一次重试 | JSON 语法错误、必填字段缺失、枚举越界 | 把校验器返回的错误路径原样回传模型，要求重新生成一次，仍使用同一批工具返回值 | 重试后的 JSON |
| 二级：降级模板 | 一级重试仍失败 | 切换到固定填空模板，仅保留工具返回的数值字段，`basis` 与 `risks` 改用固定话术 | 模板化 JSON + 风险段标注"结构化降级" |
| 三级：报错兜底 | 二级仍失败，或数值字段与工具返回不一致 | 停止渲染，输出 Refusal（code 取 NO_EVIDENCE 或对应红线码）并写审计日志 | Refusal JSON + 告警 |

一致性断言：校验器须逐字段比对 `params` 与最近一次工具返回，任一项相对偏差 >0.5% 即判为模型自算，直接进入三级。该断言是"禁止大模型心算"在工程上的落地手段。

## 7.4 JSON Schema 文件

### 7.4.1 ParameterRecommendation.schema.json

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "schemas/parameter-recommendation.schema.json",
  "title": "ParameterRecommendation",
  "type": "object",
  "required": ["schemaVersion", "materialCode", "toolCode", "machineCode", "opCode", "strategy", "params", "alternatives", "basis", "risks", "sources", "conclusion"],
  "additionalProperties": false,
  "properties": {
    "schemaVersion": {"type": "string", "const": "1.0"},
    "cardId": {"type": "string", "pattern": "^PC-[0-9]{8}-[0-9]{4}$"},
    "sessionId": {"type": "string", "pattern": "^SESS-[0-9a-f]{8}$"},
    "materialCode": {"type": "string", "enum": ["M1", "M2", "M3", "M4", "M5"]},
    "toolCode": {"type": "string", "enum": ["T1", "T2", "T3", "T4", "T5", "T6"]},
    "machineCode": {"type": "string", "const": "VMC-850"},
    "machineAssetId": {"type": "string", "const": "EQ-A01-0037"},
    "opCode": {"type": "string", "enum": ["OP1", "OP2", "OP3", "OP4"]},
    "strategy": {"type": "string", "enum": ["efficiency", "quality", "life"]},
    "params": {
      "type": "object",
      "required": ["n", "vc", "fz", "vf", "ap", "ae", "Pc", "RaPred", "toolLife"],
      "additionalProperties": false,
      "properties": {
        "n": {"type": "number", "minimum": 1, "maximum": 8000},
        "vc": {"type": "number", "exclusiveMinimum": 0},
        "fz": {"type": "number", "exclusiveMinimum": 0},
        "vf": {"type": "number", "minimum": 1, "maximum": 10000},
        "ap": {"type": "number", "exclusiveMinimum": 0},
        "ae": {"type": "number", "exclusiveMinimum": 0},
        "Pc": {"type": "number", "minimum": 0, "maximum": 11},
        "RaPred": {"type": "number", "exclusiveMinimum": 0},
        "toolLife": {"type": "number", "exclusiveMinimum": 0},
        "mrr": {"type": "number", "exclusiveMinimum": 0}
      }
    },
    "alternatives": {
      "type": "array",
      "minItems": 2,
      "maxItems": 2,
      "items": {
        "type": "object",
        "required": ["strategy", "params"],
        "additionalProperties": false,
        "properties": {
          "strategy": {"type": "string", "enum": ["efficiency", "quality", "life"]},
          "params": {"type": "object"},
          "tradeoff": {"type": "string", "maxLength": 80}
        }
      }
    },
    "basis": {
      "type": "array",
      "minItems": 2,
      "items": {
        "type": "object",
        "required": ["formulaId", "statement"],
        "additionalProperties": false,
        "properties": {
          "formulaId": {"type": "string", "enum": ["F-N", "F-VF", "F-MRR", "F-PC", "F-RA", "F-RA-BALL", "F-RAPRED", "F-T", "F-TM", "F-LD"]},
          "statement": {"type": "string", "maxLength": 120},
          "kbId": {"type": "string", "pattern": "^KB-(CNC|ALU|SST|CAST|TI|TOOL|SAFE)-[0-9]{4}$"}
        }
      }
    },
    "risks": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["type", "level", "detail"],
        "additionalProperties": false,
        "properties": {
          "type": {"type": "string", "enum": ["limit", "chatter", "clamp", "coolant", "firstPiece"]},
          "level": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "INFO"]},
          "detail": {"type": "string", "maxLength": 80}
        }
      }
    },
    "sources": {"type": "array", "minItems": 1, "items": {"type": "string", "maxLength": 24}},
    "conclusion": {
      "type": "object",
      "required": ["text", "confidence"],
      "additionalProperties": false,
      "properties": {
        "text": {"type": "string", "maxLength": 50},
        "confidence": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]}
      }
    },
    "createdAt": {"type": "string", "format": "date-time"}
  }
}
```

### 7.4.2 ConstraintCheckResult.schema.json

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "schemas/constraint-check-result.schema.json",
  "title": "ConstraintCheckResult",
  "type": "object",
  "required": ["schemaVersion", "checkRunId", "overall", "items", "failedIds"],
  "additionalProperties": false,
  "properties": {
    "schemaVersion": {"type": "string", "const": "1.0"},
    "checkRunId": {"type": "string", "pattern": "^CHK-[0-9a-f]{8}$"},
    "overall": {"type": "string", "enum": ["pass", "fail"]},
    "items": {
      "type": "array",
      "minItems": 1,
      "maxItems": 10,
      "items": {
        "type": "object",
        "required": ["checkId", "constraint", "passed", "actual", "limit", "action"],
        "additionalProperties": false,
        "properties": {
          "checkId": {"type": "string", "pattern": "^C-(0[1-9]|10)$"},
          "constraint": {"type": "string", "maxLength": 40},
          "passed": {"type": "boolean"},
          "actual": {"type": "number"},
          "limit": {"type": "number"},
          "action": {"type": "string", "enum": ["none", "reduce ap", "reduce ae", "reduce vc", "reduce fz", "adjust cooling", "manual review"]},
          "detail": {"type": "string", "maxLength": 80}
        }
      }
    },
    "failedIds": {
      "type": "array",
      "items": {"type": "string", "pattern": "^C-(0[1-9]|10)$"}
    },
    "convergence": {
      "type": "object",
      "required": ["applied", "rounds"],
      "additionalProperties": false,
      "properties": {
        "applied": {"type": "boolean"},
        "rounds": {"type": "integer", "minimum": 0, "maximum": 8},
        "order": {"type": "array", "items": {"type": "string", "enum": ["ap", "ae", "vc", "fz"]}}
      }
    },
    "blockedReason": {"type": "string", "maxLength": 80}
  }
}
```
