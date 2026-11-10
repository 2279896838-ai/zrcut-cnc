# 智刃工艺 ZR-CUT

CNC 切削工艺参数智能推荐系统。零外部依赖（Python 标准库 + numpy），可直接部署到实训室工控机。

**在线演示**：`docs/index.html` 是单文件静态应用，浏览器内真实执行计算，可部署到 GitHub Pages。

## 这是什么

输入材料、刀具、机床与质量要求，输出：

- **可执行的切削参数**：主轴转速 n、进给速度 vf、每齿进给 fz、轴向切深 ap、径向切宽 ae
- **三项预测与置信区间**：表面粗糙度 Ra、刀具寿命 T、加工时间 Tm
- **十条硬约束校验结果**：C-01 至 C-10 逐条 pass/fail，越限时给出收敛后的替代参数
- **可签发的工艺卡**：14 个必填字段 + 内容哈希 + 扫码载荷，签发后锁定
- **回填闭环**：实测数据回流，三档偏差判定，触发模型复标或组合冻结

## 两套实现，同一套算法

| | Python 端 | 浏览器端 |
| --- | --- | --- |
| 位置 | `zrcut/` | `web/static/engine.js` |
| 用途 | HTTP API、CLI、离线批量评估 | 静态页面在线演示 |
| 依赖 | 标准库 + numpy | 无 |
| 一致性 | 基准实现 | 移植版，2263 项测试比对 |

浏览器端不是"播放预录结果"——它把计算引擎完整移植到 JavaScript，
页面上的每次点击都在真实执行公式计算。两边的数值一致性由
`web/test-engine.mjs` 用 Python 生成的 150+ 组金标准向量逐项校验。

重建演示页：

```powershell
python scripts/gen-golden-vectors.py   # 生成金标准（改动 Python 算法后需重跑）
node web/test-engine.mjs               # 校验 JS 引擎与 Python 一致
node web/build-pages.mjs               # 打包成 docs/index.html
node web/test-page.mjs                 # 页面无头冒烟测试
```

## 快速开始


### 环境要求

- Python 3.10 及以上（开发验证于 3.12）
- numpy（唯一外部依赖，仅用于小样本回归）

```powershell
# 检查环境
python --version
python -c "import numpy; print(numpy.__version__)"
```

### 一键启动

```powershell
# 启动 HTTP API（默认 http://127.0.0.1:8077）
.\启动智刃工艺.bat

# 运行全部测试
.\run-tests.bat

# 测算覆盖率
.\run-coverage.bat
```

### 命令行用法

```powershell
# 参数推荐
python -m zrcut.cli recommend -m M1 -t T3 -o OP2 --ra 1.6 --volume 288000

# 硬约束校验（自动收敛）
python -m zrcut.cli validate -m M1 -t T4 --vc 180 --fz 0.12 --ap 20 --ae 14 --ra 1.6

# 三项预测
python -m zrcut.cli predict -m M1 -t T3 --vc 168 --fz 0.12 --ap 8 --volume 288000

# 生成并签发工艺卡
python -m zrcut.cli card --part 底板 --blank 100x60x20 -m M1 -t T3 -o OP2 --issue 工艺-张

# 知识检索
python -m zrcut.cli search "304 不锈钢 内冷"

# 元数据
python -m zrcut.cli meta

# 运行 400 条评估集
python -m zrcut.cli eval
```

## HTTP API

启动后访问 `http://127.0.0.1:8077`，接口如下：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| GET | `/api/v1/meta` | 材料、刀具、工序、机床元数据 |
| POST | `/api/v1/recommend` | 参数推荐 |
| POST | `/api/v1/validate` | 硬约束校验与收敛 |
| POST | `/api/v1/predict` | 三项预测与区间 |
| POST | `/api/v1/cards` | 生成工艺卡 |
| GET | `/api/v1/cards/{id}` | 读取工艺卡 |
| POST | `/api/v1/cards/{id}/issue` | 签发锁定 |
| POST | `/api/v1/closures` | 写入回填 |
| GET | `/api/v1/closures/stats` | 回填统计 |
| GET | `/api/v1/search?q=` | 知识检索 |

统一响应信封：

```json
{"ok": true, "data": {...}}
{"ok": false, "error": {"code": "E1003", "message": "...", "detail": {}}}
```

示例：

```powershell
curl -X POST http://127.0.0.1:8077/api/v1/recommend `
  -H "Content-Type: application/json" `
  -d '{\"material\":\"M1\",\"tool\":\"T3\",\"operation\":\"OP2\",\"raReq\":1.6}'
```

## 核心设计

### 数值可信链

系统与「通用大模型问答」的根本区别：**大模型不允许自己算数**。

```
工具返回 → 约束校验 → 模型组织 → Schema 校验 → 前端渲染
```

- `zrcut/calc.py` 是全系统唯一产生切削数值的地方，纯函数，输入相同则输出必然相同
- LLM 只负责选择、串联、解释工具返回值
- 输出中的每个数字必须能在工具返回值中找到同值（±2% 容差），否则降级为模板回答

### 十条硬约束

| 编号 | 约束 | 越限动作 |
| --- | --- | --- |
| C-01 | n ≤ 8,000 r/min | 降 vc |
| C-02 | 1 ≤ vf ≤ 10,000 mm/min | 调整 fz |
| C-03 | Pc ≤ 11 kW | 降 ap |
| C-04 | 悬伸比 L/D ≤ 4.0 | 换刀或缩短悬伸 |
| C-05 | vc、fz 落在材料区间内 | 收敛到边界 |
| C-06 | ap/D、ae/D 不超过材料上限 | 降 ap 或降 ae |
| C-07 | 颤振风险不高于 MEDIUM | 收敛稳定带或降 ap |
| C-08 | Ra_pred ≤ Ra_req | 降 fz 或换刀 |
| C-09 | M3/M5 须内冷 | 拒答并给合规路径 |
| C-10 | 薄壁件 ap 折减 0.5 | 降 ap 至折减上限 |

**收敛顺序**：先降 ap → 再降 ae → 再降 vc → 最后降 fz。
理由：ap 对功率是线性影响且不改变 Ra，代价最小；fz 直接决定 Ra 且与颤振耦合，放最后。

收敛不是一次性求解，而是「逐条校验—回代复核」的迭代过程。示例：功率越限降 ap 通过后，颤振复核发现 ae/D 仍超 HIGH 边界，必须再降 ae。

### 主轴极限优先于材料区间

铝合金 vc=300 配 φ10 刀具时 n=9,549 r/min，超主轴上限。此时 vc 允许低于材料推荐下限——主轴安全优先。系统用 `vc_floor` 机制表达这一优先级，避免 C-01 与 C-05 死锁。

### 回填闭环

- 偏差阈值：Ra > 25%、工时 > 30%、寿命 > 40%
- 三档处置：仅入库 / 提示调整 / 冻结组合并转工艺工程师
- 观察到颤振直接判严重档（说明颤振判据需重新标定）
- **禁止在线学习**：回填只入样本池，模型更新必须走离线增量训练 + 灰度发布

## 项目结构

```
zrcut/               核心包（16 个模块）
  domain.py          物理与工艺常量，全系统唯一事实来源
  calc.py            确定性计算引擎，唯一产生数值的地方
  constraints.py     C-01..C-10 校验与收敛算法
  recommender.py     八步参数推荐与四种策略
  predictor.py       小样本岭回归与置信区间
  knowledge.py       BM25 检索与三因子打分
  card.py            工艺卡生成、签发锁定、扫码载荷
  closure.py         回填闭环与偏差判定
  errors.py          稳定错误码体系
  schema.py          轻量 JSON Schema 校验
  llm.py             LLM 编排、数值纪律检查、降级
  service.py         业务门面（HTTP/CLI/评估共用）
  api.py             HTTP API（11 个端点）
  cli.py             命令行（8 个命令）
  evaluate.py        400 条评估集与六指标

tests/               6 个测试模块，254 个测试
scripts/
  coverage.py        覆盖率测算（标准库 trace，零依赖）
  assemble.py        提示词规范合稿与校验
  verify-examples.py 规范第 9 章全部数值复算
prompts/             12 条提示词资产
deliverables/        提示词工程规范（Markdown/Word/PDF）
```

## 质量指标

| 指标 | 当前值 | 目标 |
| --- | --- | --- |
| 单元测试 | 254 个，全部通过 | 全绿 |
| 代码覆盖率 | 86.1% | ≥85% |
| 评估集 | 400 条，0 失败 | 六指标全达标 |
| 越限漏检率 | 0% | 0%（一票否决） |
| Ra 预测 MAE | 0.17 μm | ≤0.35 μm |

## 配置

| 环境变量 | 说明 | 默认值 |
| --- | --- | --- |
| `ZRCUT_LLM_API_KEY` | DeepSeek API Key，不配则走模板回答 | 空 |
| `ZRCUT_LLM_BASE_URL` | API 基址 | `https://api.deepseek.com` |
| `ZRCUT_LLM_MODEL` | 模型名 | `deepseek-chat` |
| `ZRCUT_LLM_TIMEOUT` | 超时秒数 | `30` |

未配置 API Key 时系统完全可用，只是叙述层降级为模板输出，所有数值与校验结果不受影响。

## 术语

| 缩写 | 含义 |
| --- | --- |
| n | 主轴转速，r/min |
| vc | 切削速度，m/min |
| fz | 每齿进给，mm/z |
| vf | 进给速度，mm/min |
| ap | 轴向切深，mm |
| ae | 径向切宽，mm |
| MRR | 材料去除率，mm³/min |
| Pc | 切削功率，kW |
| Ra | 表面粗糙度，μm |
| LD | 悬伸比 L/D |
| C-01..C-10 | 十条硬约束编号 |
| P0/P1/P2 | 主方案与两套备选 |

## 限制与免责

- 颤振判据是**单自由度简化叶瓣模型**，只用于风险分级，不用于精确预测稳定域；真实稳定域须以切削试验或锤击模态测试标定
- 材料与刀具参数来自公开手册与本项目统一定义，量产前须用本厂实测数据校核
- 预测模型的置信区间在无历史试切样本时较宽，属预期行为
- 系统不替代法定检验、持证作业与工艺工程师签发
