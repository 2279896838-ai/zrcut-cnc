"""智刃工艺（ZR-CUT）CNC 切削工艺参数智能推荐系统。

零外部依赖：仅使用 Python 标准库与 numpy。

模块地图：
    domain       物理与工艺常量（唯一事实来源）
    calc         确定性计算引擎（唯一产生数值的地方）
    constraints  C-01..C-10 硬约束与收敛算法
    recommender  八步参数推荐与三策略
    predictor    小样本回归预测与置信区间
    knowledge    BM25 检索与知识冲突检测
    card         工艺卡生成、签发锁定、扫码内容
    closure      回填闭环与偏差判定
    llm          LLM 编排与数值纪律检查
    schema       轻量 JSON Schema 校验
    service      业务门面（HTTP/CLI/评估共用入口）
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = [
    "__version__",
    "domain",
    "calc",
    "constraints",
    "recommender",
    "predictor",
    "knowledge",
    "card",
    "closure",
    "llm",
    "schema",
    "errors",
    "service",
]
