# 4.1 意图路由与槽位抽取 ZRCNC-RTR-001

## 4.1.1 设计意图

**设计意图**：解决两个失败模式——槽位不全时模型自行假设参数并直接给数，以及意图误判导致"越限复核""安全咨询"被当成普通参数推荐处理、跳过校验器放行。

路由层是提示词体系的入口闸门。它不产生任何切削参数，只产出三样东西：意图标签、槽位字典、下一步动作。下游的 ZRCNC-PAR-001、ZRCNC-PRD-001、ZRCNC-CST-001、ZRCNC-CRD-001、ZRCNC-CLS-001、ZRCNC-SEC-001 全部以路由输出为输入，因此路由的两类错误都会沿链路放大：漏抽一个必填槽位，下游会拿空值去调工具或凭经验补值；错分一个意图，下游会用错工具、跳过安全拦截。

**表 4-1 路由层职责边界（无单位）**

| 职责 | 做什么 | 不做什么 | 越界判定 |
| --- | --- | --- | --- |
| 意图分类 | 把用户话语归入八类意图之一 | 不判断参数是否合理 | 输出解释性段落即越界 |
| 槽位抽取 | 按词面抽取，域外不映射 | 不推测、不做最接近匹配、不改写用户数值 | slot 出现用户未说的值即越界 |
| 缺失判定 | 标记必填槽位缺失项 | 不用缺省值静默补齐 | 缺失槽位被填默认值即越界 |
| 分派 | 给 dispatchTo 与 nextAction | 不调用参数工具、不生成参数表 | 输出含 n、vf、Pc 数值即越界 |

## 4.1.2 八类意图

意图判定以用户话语中的动作词与对象词为依据，不看句长，不看礼貌用语。

**表 4-2 八类意图定义与分派（无单位）**

| 意图码 | 意图名 | 判定信号（用户表述模式） | 必需槽位 | 分派目标 | 误判后果 |
| --- | --- | --- | --- | --- | --- |
| PARAM_REC | 参数推荐 | "给参数""怎么切""转速多少""用什么进给""效率优先/质量优先" | material、tool、operation、ra_req | ZRCNC-PAR-001 | 误判为 QA 则只给道理不给参数 |
| PREDICT | 结果预测 | "能到多少 Ra""刀具能切多久""要加工多久""功率够不够" | material、tool、operation | ZRCNC-PRD-001 | 误判为 PARAM_REC 则重复出参数、浪费校验配额 |
| RECHECK | 越限复核 | 用户自报 n、vf、ap、ae 数值并要求"行不行""能不能上" | material、tool，且用户自报值已给出 | ZRCNC-CST-001 | 误判为 PARAM_REC 则自报越限值被覆盖，越限隐患消失 |
| CARD | 工艺卡生成 | "出工艺卡""打印工艺卡""导给车间""扫码看" | 全部 14 个工艺卡字段 | ZRCNC-CRD-001 | 误判为 PARAM_REC 则缺卡号与签发状态 |
| CLOSURE | 回填比对 | "实测 Ra 多少""实际用了多久""这把刀磨损了""对不上" | 试切记录号 TR、实测值 | ZRCNC-CLS-001 | 误判则实测数据不入库，模型不迭代 |
| QA | 工艺问答 | "为什么""有什么区别""手册怎么规定""45 钢和 304 差在哪" | 无（材料或刀具可选） | ZRCNC-RAG-QRY-001 | 误判为 PARAM_REC 则给出无需求场景的参数 |
| SAFETY | 安全咨询 | "能不能不夹""手扶着切""开门行不行""干切钛合金""学生自己换刀" | 无（角色必取） | ZRCNC-SEC-001 | 误判为 PARAM_REC 则红线不被拦截，直接放行 |
| OUT_OF_SCOPE | 闲聊越界 | 与切削无关的写作、聊天、检索；或指定非 VMC-850 设备 | 无 | 无（拒答模板） | 误判为 QA 则消耗检索配额并给出域外结论 |

两条优先级规则写死在路由层，不得由模型自行权衡：

1. **安全带优先**：同一句话同时命中 SAFETY 与任一业务意图时，intent 必须取 SAFETY。典型句"学生能不能自己把转速开到 12000 转"同时命中 PARAM_REC 与 SAFETY，须按 SAFETY 分派，先拦截再谈参数。
2. **复核优先于推荐**：用户自报数值并要求判定时，必须取 RECHECK，不得改写为 PARAM_REC 后用自己的推荐值覆盖用户输入。自报值经规范 3.6 节 vc = π × D × n / 1000 折合后再与规范 3.5 节区间比对，比对由 ZRCNC-CST-001 完成，路由层只登记该值。

## 4.1.3 槽位定义

槽位只做词面到代号的映射，映射表即规范 3.2 至 3.4 节的材料库、刀具库与加工方式三张表。域外表述不映射、不做最接近匹配，一律置 null 并在 missingSlots 中标注 unknown_value。

**表 4-3 槽位定义（取值域按规范 3.2–3.5 节，数值单位见表内）**

| 槽位名 | 类型 | 取值域 | 必填意图 | 缺失处置 |
| --- | --- | --- | --- | --- |
| material | 枚举 | M1、M2、M3、M4、M5（对应 45 正火、6061-T6、304、QT600-3、TC4） | PARAM_REC、PREDICT、RECHECK | 追问，优先级 1 |
| tool | 枚举 | T1–T6（φ10/φ12/φ16/φ20 立铣、R5 球头、φ80 面铣） | PARAM_REC、PREDICT、RECHECK | 追问，优先级 2 |
| operation | 枚举 | OP1 端面铣、OP2 侧铣、OP3 型腔粗加工、OP4 曲面精加工 | PARAM_REC、PREDICT | 追问，优先级 3 |
| ra_req | 数值（μm） | 正实数，须由用户明示，系统不得赋默认值 | PARAM_REC | 追问，优先级 4 |
| blank_size | 三元组（mm） | 长 × 宽 × 高，单边不超过 VMC-850 行程 850/500/550 | CARD | 不追问，置 null，下游在风险段声明"尺寸未确认" |
| fixture | 枚举 | 虎钳、压板、真空吸盘、专用夹具 | 条件必填（壁厚 <3 mm 或悬伸件必填） | 置 null 并由前端带出默认项请求确认，不静默补齐 |
| strategy | 枚举 | efficiency（效率优先）、quality（质量优先）、life（寿命优先） | 非必填 | 缺省按 quality，理由：fz 取下限，Ra 余量最大 |
| machine | 常量 | 恒为 VMC-850（EQ-A01-0037） | 全部意图 | 用户指定其他设备时置 OUT_OF_SCOPE，不追问 |

machine 槽位是常量而非输入项：全篇只有 VMC-850 一台锚点机床，路由不得接受第二台设备，也不得把"其他机床"映射为 VMC-850 的近似型号。用户明确要求其他设备时，按 OUT_OF_SCOPE 分派并说明数值链仅覆盖 VMC-850。

## 4.1.4 澄清策略

澄清的目标是用最少轮次补齐必填槽位，且不重复打扰用户。四条规则如下。

**表 4-4 澄清规则（无单位）**

| 编号 | 规则 | 判定与实现 |
| --- | --- | --- |
| Q-01 | 一次只问一个槽位 | 追问句只含一个疑问点，不得出现"材料、刀具分别是什么"这类合问 |
| Q-02 | 按固定优先级追问 | material → tool → operation → ra_req，顺序不可跳；每轮补齐一个即停止 |
| Q-03 | 已确认槽位不重复追问 | 会话状态中 slots 已有的非 null 值不得再次询问，包括用户修正后的值 |
| Q-04 | 追问上限 3 轮 | 第 3 轮结束仍缺必填槽位时，nextAction 置 handoff，dispatchTo 置 teacher 或 process_engineer，不再追问 |

**表 4-5 追问话术模板（无单位）**

| 缺失槽位 | 追问句 | 可接受回答示例 | 不接受并如何处置 |
| --- | --- | --- | --- |
| material | 请给出材料代号：M1 中碳钢、M2 铝合金、M3 不锈钢、M4 球墨铸铁、M5 钛合金 | "45 钢正火"→M1 | "普通钢"→置 null 并标注 unknown_value |
| tool | 请给出刀具代号 T1–T6 或直径与类型 | "φ16 立铣"→T3 | "随便哪把"→置 null，转 process_engineer |
| operation | 请确认工序：OP1 端面铣、OP2 侧铣、OP3 型腔粗加工、OP4 曲面精加工 | "轮廓侧铣"→OP2 | "铣一下"→置 null 并追问一次 |
| ra_req | 请给表面粗糙度 Ra 要求，单位 μm | "Ra 1.6"→1.6 | "光一点"→置 null，按 Q-04 处理 |

Q-02 的优先级不是随意排列：material 决定 vc 区间与 K_ra，tool 决定 D 与 z，二者都缺时先问 material 可以把后续追问收窄到该材料适用的刀具；ra_req 放最后，因为它只影响 fz 上限的反解，不影响 n 与 vc 的落点。

## 4.1.5 ZRCNC-RTR-001 提示词全文

**设计意图**：把"分类 + 取槽 + 判缺 + 分派"压成单一 JSON 输出，消除模型在缺槽时凭经验补值、以及把安全类请求吸进参数推荐链路的两个高频缺陷。

**表 4-6 变量清单（无单位）**

| 变量名 | 类型 | 来源 | 缺失处理 |
| --- | --- | --- | --- |
| {{session_id}} | 字符串 | 会话服务 | 必填，缺失则拒绝启动 |
| {{role}} | 枚举 | 登录态，六角色之一 | 缺省按 student |
| {{material_domain}} | 列表 | 规范 3.2 节 M1–M5 | 缺失则只接受代号，不做中文映射 |
| {{tool_domain}} | 列表 | 规范 3.3 节 T1–T6 | 同上 |
| {{op_domain}} | 列表 | 规范 3.4 节 OP1–OP4 | 缺省列全四种 |
| {{machine_code}} | 常量 | 设备台账 EQ-A01-0037 | 缺失则整链拒答 |
| {{turn_count}} | 整数 | 会话状态 | 缺省为 0，用尽 3 轮即转人工 |
| {{slot_state}} | 对象 | 上一轮 slots | 缺省为空对象 |

```text
你是「智刃工艺」（代号 ZR-CUT）的意图路由与槽位抽取器。会话 {{session_id}}，当前角色 {{role}}，本次为第 {{turn_count}} 轮，已确认槽位 {{slot_state}}。你只做分类、取槽、判缺、分派四件事，不生成任何切削参数。

一、数值纪律
一切数值只能来自工具返回，不得自行计算或估算。你不得输出 n、vf、fz、vc、ap、ae、Pc、Ra、T 中的任何一个数值，只把用户原话中的数值原样搬进槽位，例如"Ra 1.6"记为 ra_req=1.6，不改写、不换算、不取整。

二、输出契约
每次只输出一个 JSON 对象，字段固定为 intent、slots、missingSlots、nextAction、dispatchTo、confidence。intent 取 PARAM_REC、PREDICT、RECHECK、CARD、CLOSURE、QA、SAFETY、OUT_OF_SCOPE 之一。slots 的键只能是 material、tool、operation、ra_req、blank_size、fixture、strategy、machine。nextAction 取 dispatch、ask、handoff 之一。

三、取槽规则
槽位缺失时追问，不得推测补齐。material 只认 {{material_domain}} 的 M1 至 M5，tool 只认 {{tool_domain}} 的 T1 至 T6，operation 只认 {{op_domain}} 的 OP1 至 OP4，machine 恒为 {{machine_code}}。用户未说的槽位填 null 并计入 missingSlots；域外表述填 null 并标注 unknown_value，不做最接近匹配。用户指定非 {{machine_code}} 的设备时，intent 置 OUT_OF_SCOPE。

四、澄清规则
追问按 material、tool、operation、ra_req 顺序，一次只问一个槽位，已确认槽位不得重复追问，单次会话不超过 3 轮。第 3 轮仍缺必填槽位时，nextAction 置 handoff，dispatchTo 置 teacher 或 process_engineer，并说明缺哪一项。

五、优先级
命中安全类表述（超机床极限、危险装夹、无防护旋转、M3 或 M5 干切、学生独立危险操作）时 intent 取 SAFETY，优先于其他意图，不得被 PARAM_REC 吸收。用户自报参数数值并要求判定时 intent 取 RECHECK，保留原值待 ZRCNC-CST-001 复核。

六、上下文继承
{{slot_state}} 中已确认的槽位直接继承，不得重复追问，也不得被本轮推测覆盖；用户明确更正时以更正值为准，并重新计算 missingSlots。单位一律按规范 3.11 节术语表书写。

七、禁止
不得在 slots 写入用户未说的值，不得用缺省值静默补齐，不得自行判定约束是否通过，不得输出解释性段落。只输出 JSON。
```

## 4.1.6 路由示例

### 示例一：槽位齐全，直接分派

用户输入：`45钢，φ16立铣刀侧铣轮廓，Ra 1.6，效率优先，给参数。`

抽取过程：`45钢` → material=M1（规范 3.2 节 M1 为 45 正火）；`φ16立铣刀` → tool=T3（规范 3.3 节 T3，D=16 mm、z=4）；`侧铣轮廓` → operation=OP2（规范 3.4 节）；`Ra 1.6` → ra_req=1.6；`效率优先` → strategy=efficiency；machine 恒为 EQ-A01-0037。四个必填槽位齐全，missingSlots 为空。

```json
{
  "intent": "PARAM_REC",
  "slots": {
    "material": "M1",
    "tool": "T3",
    "operation": "OP2",
    "ra_req": 1.6,
    "strategy": "efficiency",
    "machine": "EQ-A01-0037",
    "blank_size": null,
    "fixture": null
  },
  "missingSlots": [],
  "nextAction": "dispatch",
  "dispatchTo": "ZRCNC-PAR-001",
  "confidence": "HIGH"
}
```

blank_size 与 fixture 为 null 但不进入 missingSlots，因为二者对 PARAM_REC 非必填；下游在风险段声明"毛坯尺寸与装夹方式未确认"。该请求携带的静态约束由下游登记：T3 悬伸比 LD = L / D = 55 / 16 = 3.44 ≤ 4.0，落入 C-04 的通行区间，不触发 ap 上限折减。

### 示例二：缺 tool 与 ra_req，连续追问两轮

用户输入：`45钢的曲面精加工，光一点就行。`

抽取过程：`45钢` → material=M1；`曲面精加工` → operation=OP4（规范 3.4 节，典型刀具 T5）；`光一点就行` 不含 Ra 数值 → ra_req=null，不得按经验填 0.8 或 1.6；未提刀具 → tool=null，虽 OP4 通常用 T5，仍不得推断。

第 1 轮输出：

```json
{
  "intent": "PARAM_REC",
  "slots": {
    "material": "M1",
    "tool": null,
    "operation": "OP4",
    "ra_req": null,
    "strategy": "quality",
    "machine": "EQ-A01-0037",
    "blank_size": null,
    "fixture": null
  },
  "missingSlots": ["tool", "ra_req"],
  "nextAction": "ask",
  "dispatchTo": null,
  "confidence": "MEDIUM"
}
```

追问句（按 Q-02，tool 优先于 ra_req，且只问一项）：`请给出刀具代号 T1–T6 或直径与类型，曲面精加工常用球头刀。`

用户回答：`用 R5 球头刀。` → tool=T5（规范 3.3 节，D=10 mm、R5、z=2、悬伸 L=50 mm）。第 2 轮输出：

```json
{
  "intent": "PARAM_REC",
  "slots": {
    "material": "M1",
    "tool": "T5",
    "operation": "OP4",
    "ra_req": null,
    "strategy": "quality",
    "machine": "EQ-A01-0037",
    "blank_size": null,
    "fixture": null
  },
  "missingSlots": ["ra_req"],
  "nextAction": "ask",
  "dispatchTo": null,
  "confidence": "MEDIUM"
}
```

追问句：`请给表面粗糙度 Ra 要求，单位 μm，例如 Ra 0.8 或 Ra 1.6。`

用户回答：`Ra 0.8。` → ra_req=0.8。第 3 轮输出转为分派，未超 Q-04 的三轮上限：

```json
{
  "intent": "PARAM_REC",
  "slots": {
    "material": "M1",
    "tool": "T5",
    "operation": "OP4",
    "ra_req": 0.8,
    "strategy": "quality",
    "machine": "EQ-A01-0037",
    "blank_size": null,
    "fixture": null
  },
  "missingSlots": [],
  "nextAction": "dispatch",
  "dispatchTo": "ZRCNC-PAR-001",
  "confidence": "MEDIUM"
}
```

**表 4-7 示例二的槽位核对（数值为规范 3.3 节刀具表静态值，路由层不计算切削参数）**

| 核对项 | 依据 | 值 | 对下游的含义 |
| --- | --- | --- | --- |
| T5 悬伸比 LD | LD = L / D = 50 / 10 | 5.00 | 落入 C-04 的 4–6 区间，ap 上限须 ×0.7 |
| T5 齿数 z | 规范 3.3 节刀具库 | 2 | 齿通过频率 f_t = z × n / 60 的分母项 |
| M1 材料修正系数 K_ra | 规范 3.6 节粗糙度修正表 | 1.6 | Ra_pred = Ra_th × 1.6，质量要求更严 |
| OP4 粗糙度公式 | Ra_th = fz² / (8 × R) × 1000，R = 5 mm | 待工具返回 fz | 由 ZRCNC-PAR-001 反解 fz 上限 |
| 置信度降档原因 | 规范 3.5 节 strategy 缺省 | quality | 用户未指定策略，按质量优先执行 |

示例二的 confidence 为 MEDIUM 而非 HIGH，其含义是**槽位定值可信度**：operation 由词面直接映射，strategy 由缺省规则补齐而非用户明示，blank_size 与 fixture 未确认。该字段只描述槽位层；最终回答的置信度由下游在工具返回后按规范 4.2 节重算，二者不互相替代。

## 4.1.7 路由失败的三种形态与处置

**表 4-8 路由缺陷形态（无单位）**

| 形态 | 表现 | 后果 | 处置 |
| --- | --- | --- | --- |
| 过度抽取 | 用户说"不锈钢"即填 material=M3 并继续推荐 | 材料错配导致 vc 区间整体偏移 | 域外表述一律 null，仅 M3 对应"304"词面才映射 |
| 缺槽推断 | tool 为空时按 OP2 经验填 T3 | 参数与实物刀具不符，撞刀风险 | slot 写入未说值判越界，回归测试用例固化 |
| 安全带被吸收 | "学生能不能开 12000 转"分派为 PARAM_REC | 越限参数进入推荐链路 | 优先级规则前置，SAFETY 优先于业务意图 |

三种形态均须进入第 8.4 节失败用例聚类，按归因层判定是 L1 角色层还是 L2 场景层，再出补丁并回归。
