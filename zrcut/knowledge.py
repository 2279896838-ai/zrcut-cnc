"""BM25 检索与知识库。

工业场景禁止向量库强依赖，因此用纯 Python 实现 Okapi BM25 加中文二元切分，
在几十到几千条知识条目上表现稳定且零外部依赖。

设计要点：
    1. 中文按二元组（bigram）切分，西文按词切分，避免依赖分词库。
    2. 三因子综合分：相关度 R、权威度 A、时效性 T，权重 0.50/0.35/0.15，
       与提示词规范 5.3 节一致。
    3. 强冲突（同一槽位两条 A 级条目数值不同）必须显式暴露，交上层处理。
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Sequence

from .domain import Confidence


class KnowledgeError(ValueError):
    """知识条目不合规时抛出。"""


class SourceType(str):
    """知识来源类型常量。"""


SOURCES: dict[str, str] = {
    "S1": "刀具厂家手册",
    "S2": "材料切削性能手册",
    "S3": "企业受控工艺卡",
    "S4": "历史试切记录",
    "S5": "机床说明书",
    "S6": "安全规程",
    "S7": "专家访谈",
}

# 权威度：企业受控工艺卡与安全规程最高，专家访谈最低
AUTHORITY: dict[str, float] = {
    "S3": 1.00,
    "S6": 0.95,
    "S1": 0.90,
    "S2": 0.85,
    "S5": 0.80,
    "S4": 0.60,
    "S7": 0.50,
}

# 提示词规范 5.3 节：三因子权重
W_RELEVANCE = 0.50
W_AUTHORITY = 0.35
W_FRESHNESS = 0.15

# 保留阈值与条数上限
SCORE_THRESHOLD = 0.65
TOP_K = 5

# 有效期，月
VALID_MONTHS = 24

_CJK = re.compile(r"[\u4e00-\u9fa5]")
_LATIN = re.compile(r"[A-Za-z0-9_.\-]+")


def tokenize(text: str) -> list[str]:
    """中英混合切分：中文取二元组，西文按词。

    Args:
        text: 原始文本。

    Returns:
        词元列表。

    Examples:
        >>> tokenize("45钢 vc=180")[:6]
        ['45', '钢', 'vc', '180']
    """
    if not text:
        return []
    norm = unicodedata.normalize("NFKC", str(text)).lower()
    tokens: list[str] = []
    tokens.extend(_LATIN.findall(norm))
    cjk_seq = _CJK.findall(norm)
    joined = "".join(cjk_seq)
    if len(joined) == 1:
        tokens.append(joined)
    else:
        tokens.extend(joined[i : i + 2] for i in range(len(joined) - 1))
    return tokens


@dataclass(slots=True)
class KnowledgeItem:
    """知识条目，对应规范 5.1.2 节字段。

    Attributes:
        kb_id: 知识条目编号，形如 KB-CNC-0042。
        title: 标题。
        source_type: 来源类型 S1-S7。
        source_ref: 来源出处。
        scope: 适用范围。
        metrics: 结构化指标字典，如 {"vc": "120-180 m/min"}。
        confidence: 条目置信等级。
        valid_until: 有效期截止日。
        reviewers: 复核人列表，须两人。
        body: 正文。
    """

    kb_id: str
    title: str
    source_type: str
    source_ref: str
    scope: str
    metrics: dict[str, str] = field(default_factory=dict)
    confidence: Confidence = Confidence.MEDIUM
    valid_until: date | None = None
    reviewers: list[str] = field(default_factory=list)
    body: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"KB-(CNC|ALU|SST|CAST|TI|TOOL|SAFE|MACH)-\d{4}", self.kb_id):
            raise KnowledgeError(
                f"知识条目编号不合规：{self.kb_id}；须形如 KB-CNC-0042"
            )
        if self.source_type not in SOURCES:
            raise KnowledgeError(
                f"来源类型不合规：{self.source_type}；须为 {', '.join(SOURCES)}"
            )

    @property
    def authority(self) -> float:
        """来源权威度。"""
        return AUTHORITY.get(self.source_type, 0.5)

    @property
    def text(self) -> str:
        """参与检索的全文。"""
        parts = [self.title, self.scope, self.body]
        parts.extend(f"{k} {v}" for k, v in self.metrics.items())
        parts.append(SOURCES.get(self.source_type, ""))
        return " ".join(p for p in parts if p)

    def freshness(self, today: date | None = None) -> float:
        """时效性得分，0-1；过期条目快速衰减。

        Args:
            today: 当前日期，默认取系统日期。

        Returns:
            时效性得分。
        """
        if self.valid_until is None:
            return 1.0
        t = today or date.today()
        days_left = (self.valid_until - t).days
        if days_left >= 180:
            return 1.0
        if days_left <= 0:
            return 0.2
        return 0.2 + 0.8 * (days_left / 180.0)

    def is_expired(self, today: date | None = None) -> bool:
        """是否已过期。"""
        if self.valid_until is None:
            return False
        return (today or date.today()) > self.valid_until

    def to_dict(self) -> dict[str, Any]:
        """转字典。"""
        return {
            "kbId": self.kb_id,
            "title": self.title,
            "sourceType": self.source_type,
            "sourceRef": self.source_ref,
            "scope": self.scope,
            "metrics": dict(self.metrics),
            "confidence": self.confidence.value,
            "validUntil": self.valid_until.isoformat() if self.valid_until else None,
            "reviewers": list(self.reviewers),
        }


@dataclass(frozen=True, slots=True)
class SearchHit:
    """检索命中。

    Attributes:
        item: 命中的知识条目。
        relevance: 相关度，0-1。
        authority: 权威度，0-1。
        freshness: 时效性，0-1。
        score: 三因子综合分。
        snippet: 摘要片段。
    """

    item: KnowledgeItem
    relevance: float
    authority: float
    freshness: float
    score: float
    snippet: str

    def to_dict(self) -> dict[str, Any]:
        """转字典。"""
        return {
            "kbId": self.item.kb_id,
            "title": self.item.title,
            "sourceType": self.item.source_type,
            "relevance": round(self.relevance, 4),
            "authority": round(self.authority, 4),
            "freshness": round(self.freshness, 4),
            "score": round(self.score, 4),
            "snippet": self.snippet,
        }


@dataclass(slots=True)
class Bm25Index:
    """Okapi BM25 索引。

    Attributes:
        k1: 词频饱和参数。
        b: 长度归一化参数。
        items: 条目列表。
        doc_tokens: 每条目的词元列表。
        df: 词元文档频率。
        avgdl: 平均文档长度。
    """

    k1: float = 1.5
    b: float = 0.75
    items: list[KnowledgeItem] = field(default_factory=list)
    doc_tokens: list[list[str]] = field(default_factory=list)
    df: dict[str, int] = field(default_factory=dict)
    avgdl: float = 0.0

    def build(self, items: Iterable[KnowledgeItem]) -> "Bm25Index":
        """构建索引。

        Args:
            items: 知识条目集合。

        Returns:
            self。
        """
        self.items = list(items)
        self.doc_tokens = [tokenize(it.text) for it in self.items]
        df: dict[str, int] = {}
        for toks in self.doc_tokens:
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        self.df = df
        total = sum(len(t) for t in self.doc_tokens)
        self.avgdl = total / len(self.doc_tokens) if self.doc_tokens else 0.0
        return self

    def _idf(self, term: str) -> float:
        """计算逆文档频率。"""
        n = len(self.items)
        df = self.df.get(term, 0)
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    def score(self, query_tokens: Sequence[str], doc_idx: int) -> float:
        """计算单文档的 BM25 得分。

        Args:
            query_tokens: 查询词元。
            doc_idx: 文档下标。

        Returns:
            BM25 得分。
        """
        toks = self.doc_tokens[doc_idx]
        dl = len(toks)
        if dl == 0 or self.avgdl == 0:
            return 0.0
        tf_map: dict[str, int] = {}
        for t in toks:
            tf_map[t] = tf_map.get(t, 0) + 1
        total = 0.0
        for q in query_tokens:
            f = tf_map.get(q, 0)
            if f == 0:
                continue
            denom = f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
            total += self._idf(q) * f * (self.k1 + 1) / denom
        return total


class KnowledgeBase:
    """知识库：索引 + 检索 + 冲突检测。

    Attributes:
        index: BM25 索引。
    """

    def __init__(self, items: Sequence[KnowledgeItem] | None = None) -> None:
        self.index = Bm25Index().build(items or [])

    def __len__(self) -> int:
        """条目数。"""
        return len(self.index.items)

    def add(self, item: KnowledgeItem) -> None:
        """追加条目并重建索引。

        Args:
            item: 知识条目。
        """
        self.index.build(list(self.index.items) + [item])

    def search(
        self,
        query: str,
        *,
        top_k: int = TOP_K,
        threshold: float = SCORE_THRESHOLD,
        today: date | None = None,
        require_high_authority: bool = True,
    ) -> list[SearchHit]:
        """执行三因子检索。

        Args:
            query: 查询文本。
            top_k: 返回条数上限。
            threshold: 综合分阈值。
            today: 当前日期。
            require_high_authority: 若无任何权威度 ≥0.8 的命中则整池作废。

        Returns:
            按综合分降序的命中列表。
        """
        if not self.index.items:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []

        raw: list[tuple[int, float]] = []
        for i in range(len(self.index.items)):
            s = self.index.score(q_tokens, i)
            if s > 0:
                raw.append((i, s))
        if not raw:
            return []

        # 相关度采用「IDF 加权查询覆盖率」而非「除以最大 BM25 分」。
        # 原因：按最大值归一化会让任意命中一个通用词（如「参数」）的条目
        # 都拿到 relevance=1.0，从而绕过阈值返回无关结果。覆盖率是绝对量，
        # 只命中少数弱词时自然偏低。
        idf_map = {q: self.index._idf(q) for q in set(q_tokens)}
        total_idf = sum(idf_map.values())
        if total_idf <= 0:
            return []

        hits: list[SearchHit] = []
        for i, _s in raw:
            doc_tokens = set(self.index.doc_tokens[i])
            matched_idf = sum(v for q, v in idf_map.items() if q in doc_tokens)
            rel = matched_idf / total_idf
            if rel <= 0:
                continue
            item = self.index.items[i]
            auth = item.authority
            fresh = item.freshness(today)
            score = W_RELEVANCE * rel + W_AUTHORITY * auth + W_FRESHNESS * fresh
            hits.append(
                SearchHit(
                    item=item,
                    relevance=rel,
                    authority=auth,
                    freshness=fresh,
                    score=score,
                    snippet=self._snippet(item, q_tokens),
                )
            )

        if not hits:
            return []

        hits.sort(key=lambda h: (h.score, h.relevance), reverse=True)

        if require_high_authority and not any(h.authority >= 0.8 for h in hits):
            return []

        return [h for h in hits if h.score >= threshold][:top_k]

    @staticmethod
    def _snippet(item: KnowledgeItem, q_tokens: Sequence[str], width: int = 90) -> str:
        """生成摘要片段，优先包含查询词。"""
        text = item.body or item.title
        for q in q_tokens:
            pos = text.find(q)
            if pos >= 0:
                start = max(0, pos - width // 3)
                return text[start : start + width].replace("\n", " ")
        return text[:width].replace("\n", " ")

    def detect_conflicts(self, hits: Sequence[SearchHit]) -> list[dict[str, Any]]:
        """检测强冲突：同指标不同数值。

        Args:
            hits: 检索命中列表。

        Returns:
            冲突描述列表，每项含 metric、values、kbIds。
        """
        by_metric: dict[str, list[tuple[str, str, float]]] = {}
        for h in hits:
            for k, v in h.item.metrics.items():
                by_metric.setdefault(k, []).append((h.item.kb_id, v, h.authority))

        conflicts: list[dict[str, Any]] = []
        for metric, entries in by_metric.items():
            if len(entries) < 2:
                continue
            values = {v for _, v, _ in entries}
            if len(values) > 1 and max(a for _, _, a in entries) >= 0.8:
                conflicts.append(
                    {
                        "metric": metric,
                        "values": sorted(values),
                        "kbIds": [e[0] for e in entries],
                        "resolution": "并列双方数值，置信度置 LOW，转工艺工程师裁定",
                    }
                )
        return conflicts


def default_knowledge_base() -> KnowledgeBase:
    """构建内置示例知识库。

    这些条目仅用于演示检索链路，数值与 domain 中的材料表一致，
    不作为独立事实来源。

    Returns:
        已装载示例条目的知识库。
    """
    today = date.today()
    far = date(today.year + 2, today.month, 1)
    items = [
        KnowledgeItem(
            kb_id="KB-CNC-0042",
            title="45 钢侧铣推荐切削速度与每齿进给",
            source_type="S2",
            source_ref="《材料切削性能手册》第 4 章表 4-12",
            scope="中碳钢 45，正火 HB180-220，硬质合金立铣刀侧铣",
            metrics={"vc": "120-180 m/min", "fz": "0.05-0.12 mm/z"},
            confidence=Confidence.HIGH,
            valid_until=far,
            reviewers=["工艺-张", "工艺-李"],
            body="45 钢正火状态侧铣，切削速度建议 120-180 m/min，每齿进给 0.05-0.12 mm/z。"
            "粗加工取上限，精加工取下限。冷却采用乳化液外冷，压力 0.3-0.5 MPa。",
        ),
        KnowledgeItem(
            kb_id="KB-ALU-0011",
            title="6061-T6 铝合金高速铣削参数",
            source_type="S2",
            source_ref="《材料切削性能手册》第 5 章表 5-3",
            scope="铝合金 6061-T6，HB95，硬质合金立铣刀",
            metrics={"vc": "300-500 m/min", "fz": "0.08-0.20 mm/z"},
            confidence=Confidence.HIGH,
            valid_until=far,
            reviewers=["工艺-张", "工艺-王"],
            body="6061-T6 铝合金允许高速切削，vc 可达 300-500 m/min。"
            "需注意积屑瘤，建议使用 TiAlN 涂层刀具并保证充分冷却。",
        ),
        KnowledgeItem(
            kb_id="KB-SST-0008",
            title="304 不锈钢切削参数与加工硬化对策",
            source_type="S2",
            source_ref="《材料切削性能手册》第 6 章表 6-1",
            scope="不锈钢 304，HB185，硬质合金立铣刀",
            metrics={"vc": "60-100 m/min", "fz": "0.04-0.08 mm/z"},
            confidence=Confidence.HIGH,
            valid_until=far,
            reviewers=["工艺-李", "工艺-赵"],
            body="304 不锈钢加工硬化倾向强，切削速度须控制在 60-100 m/min，"
            "每齿进给 0.04-0.08 mm/z，必须使用内冷或高压外冷，避免刀具在硬化层空切。",
        ),
        KnowledgeItem(
            kb_id="KB-CAST-0005",
            title="QT600-3 球墨铸铁铣削参数",
            source_type="S2",
            source_ref="《材料切削性能手册》第 7 章表 7-4",
            scope="球墨铸铁 QT600-3，HB220-270",
            metrics={"vc": "100-150 m/min", "fz": "0.06-0.12 mm/z"},
            confidence=Confidence.MEDIUM,
            valid_until=far,
            reviewers=["工艺-赵", "工艺-张"],
            body="球墨铸铁 QT600-3 铣削 vc 100-150 m/min，fz 0.06-0.12 mm/z。"
            "铸铁粉尘大，须干式或微量润滑并配除尘，注意石墨粉尘对导轨的磨损。",
        ),
        KnowledgeItem(
            kb_id="KB-TI-0007",
            title="TC4 钛合金低速大进给加工",
            source_type="S2",
            source_ref="《材料切削性能手册》第 8 章表 8-2",
            scope="钛合金 TC4，HB320，球头刀曲面精加工",
            metrics={"vc": "30-60 m/min", "fz": "0.03-0.06 mm/z"},
            confidence=Confidence.HIGH,
            valid_until=far,
            reviewers=["工艺-李", "工艺-王"],
            body="TC4 钛合金热导率低，切削热集中于刀刃，vc 必须控制在 30-60 m/min。"
            "严禁干切，须高压内冷；刀具磨损以月牙洼为主，寿命显著低于钢件。",
        ),
        KnowledgeItem(
            kb_id="KB-TOOL-0024",
            title="φ20 四刃立铣刀大切深侧铣能力",
            source_type="S1",
            source_ref="刀具厂家样本 C 系列第 12 页",
            scope="φ20 四刃硬质合金立铣刀 AlCrN 涂层",
            metrics={"apD": "≤1.0", "aeD": "≤0.5"},
            confidence=Confidence.HIGH,
            valid_until=far,
            reviewers=["刀具-陈", "工艺-张"],
            body="φ20 四刃立铣刀在刚性足够时可取 ap/D 达 1.0，"
            "但径向切宽建议不超过 0.5D，否则颤振风险显著上升。功率为主要约束。",
        ),
        KnowledgeItem(
            kb_id="KB-TOOL-0031",
            title="BT40 刀柄悬伸与刚性折减规则",
            source_type="S1",
            source_ref="刀具厂家样本 技术附录 A",
            scope="BT40 刀柄，硬质合金立铣刀",
            metrics={"LD": "安全 ≤4.0，警戒 4.0-6.0"},
            confidence=Confidence.HIGH,
            valid_until=far,
            reviewers=["刀具-陈", "工艺-李"],
            body="悬伸比 L/D ≤4.0 时刚性充足；4.0-6.0 区间切深须折减 30%；"
            "超过 6.0 属刚性严重不足，须改用加粗刀杆或缩短悬伸后重新计算。",
        ),
        KnowledgeItem(
            kb_id="KB-MACH-0001",
            title="VMC-850 主轴功率与转速限制",
            source_type="S5",
            source_ref="VMC-850 使用说明书 第 3 章",
            scope="VMC-850 立式加工中心 EQ-A01-0037",
            metrics={"powerContinuous": "11 kW", "powerPeak": "15 kW", "spindleMax": "8000 r/min"},
            confidence=Confidence.HIGH,
            valid_until=far,
            reviewers=["设备-孙", "工艺-张"],
            body="VMC-850 主轴连续功率 11 kW，30 分钟短时 15 kW。"
            "工艺校核一律按连续功率 11 kW 计算，不得使用短时功率。",
        ),
        KnowledgeItem(
            kb_id="KB-CNC-0091",
            title="45 钢侧铣实测粗糙度与理论值比例",
            source_type="S4",
            source_ref="试切记录 TR-20261015-0003 至 TR-20261030-0011",
            scope="45 钢侧铣，rε=0.8 立铣刀",
            metrics={"kRa": "1.55-1.68"},
            confidence=Confidence.MEDIUM,
            valid_until=far,
            reviewers=["质检-周", "工艺-张"],
            body="45 钢侧铣实测 Ra 约为理论残留高度的 1.55-1.68 倍，"
            "取 K_ra=1.6。偏差主要来自刀具让刀与材料撕裂。",
        ),
        KnowledgeItem(
            kb_id="KB-SAFE-0012",
            title="高速旋转与薄壁件装夹安全要求",
            source_type="S6",
            source_ref="实训中心安全规程 第 5 章",
            scope="所有铣削作业",
            metrics={"thinWall": "<3 mm 须辅助支撑"},
            confidence=Confidence.HIGH,
            valid_until=far,
            reviewers=["安全-钱", "设备-孙"],
            body="主轴高速旋转时严禁手扶工件或开门观察。薄壁件（壁厚<3mm）"
            "须增加辅助支撑并将切深折减 50%。装夹必须校平，垫铁不得悬空。",
        ),
    ]
    return KnowledgeBase(items)


def parse_valid_until(text: str) -> date | None:
    """解析有效期字符串。

    Args:
        text: ISO 日期字符串，可为空。

    Returns:
        日期对象或 None。
    """
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise KnowledgeError(f"无法解析日期：{text}")
