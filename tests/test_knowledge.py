"""知识库检索测试。

覆盖中文二元切分、BM25 打分、三因子综合分、权威度门槛、
空结果兜底、冲突检测与知识条目字段校验。
"""

from __future__ import annotations

from datetime import date, timedelta

from tests import ZrCutTestCase
from zrcut.domain import Confidence
from zrcut.knowledge import (
    AUTHORITY,
    KnowledgeBase,
    KnowledgeError,
    KnowledgeItem,
    SCORE_THRESHOLD,
    SOURCES,
    W_AUTHORITY,
    W_FRESHNESS,
    W_RELEVANCE,
    default_knowledge_base,
    parse_valid_until,
    tokenize,
)


def _item(**kw) -> KnowledgeItem:
    """构造默认知识条目，便于逐字段覆盖。"""
    defaults = dict(
        kb_id="KB-CNC-9999",
        title="测试条目",
        source_type="S2",
        source_ref="测试来源",
        scope="测试范围",
        metrics={"vc": "100-200 m/min"},
        confidence=Confidence.MEDIUM,
        valid_until=date.today() + timedelta(days=365),
        reviewers=["甲", "乙"],
        body="这是一条用于测试的知识条目正文，包含切削速度与每齿进给等内容。",
    )
    defaults.update(kw)
    return KnowledgeItem(**defaults)


class TestTokenize(ZrCutTestCase):
    """中文二元切分与西文分词。"""

    def test_chinese_bigrams(self) -> None:
        """连续中文应切成二元组。"""
        tokens = tokenize("侧铣加工")
        self.assertIn("侧铣", tokens)
        self.assertIn("铣加", tokens)

    def test_latin_and_numbers(self) -> None:
        """西文与数字按词切分。"""
        tokens = tokenize("vc=180 m/min")
        self.assertIn("vc", tokens)
        self.assertIn("180", tokens)

    def test_mixed_content(self) -> None:
        """中英混排都应被切出。"""
        tokens = tokenize("45钢 vc=180")
        self.assertIn("45", tokens)
        self.assertIn("180", tokens)
        self.assertIn("钢", "".join(tokens))

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(tokenize(""), [])

    def test_single_char(self) -> None:
        """单字应保留。"""
        self.assertEqual(tokenize("钢"), ["钢"])


class TestKnowledgeItemValidation(ZrCutTestCase):
    """知识条目字段校验。"""

    def test_accepts_valid_id(self) -> None:
        item = _item(kb_id="KB-CNC-0042")
        self.assertEqual(item.kb_id, "KB-CNC-0042")

    def test_rejects_bad_id_format(self) -> None:
        """编号格式不合规必须拒绝。"""
        for bad in ("KB-0042", "kb-cnc-0042", "KB-XX-0042", "KB-CNC-42"):
            with self.subTest(kb_id=bad):
                with self.assertRaises(KnowledgeError):
                    _item(kb_id=bad)

    def test_rejects_unknown_source(self) -> None:
        """未知来源类型必须拒绝。"""
        with self.assertRaises(KnowledgeError):
            _item(source_type="S9")

    def test_authority_reflects_source(self) -> None:
        """权威度应由来源类型决定。"""
        for st, expected in AUTHORITY.items():
            with self.subTest(source=st):
                self.assertAlmostEqualRel(_item(source_type=st).authority, expected, 1e-9)

    def test_all_sources_defined(self) -> None:
        self.assertEqual(len(SOURCES), 7)


class TestFreshness(ZrCutTestCase):
    """时效性计算。"""

    def test_far_future_full_score(self) -> None:
        item = _item(valid_until=date.today() + timedelta(days=400))
        self.assertAlmostEqualRel(item.freshness(), 1.0, 1e-9)

    def test_expired_low_score(self) -> None:
        item = _item(valid_until=date.today() - timedelta(days=10))
        self.assertAlmostEqualRel(item.freshness(), 0.2, 1e-9)
        self.assertTrue(item.is_expired())

    def test_no_expiry_full_score(self) -> None:
        self.assertAlmostEqualRel(_item(valid_until=None).freshness(), 1.0, 1e-9)

    def test_monotonic_decay(self) -> None:
        """临近过期时分数单调下降。"""
        a = _item(valid_until=date.today() + timedelta(days=170)).freshness()
        b = _item(valid_until=date.today() + timedelta(days=30)).freshness()
        self.assertGreater(a, b)


class TestSearch(ZrCutTestCase):
    """检索行为。"""

    def setUp(self) -> None:
        self.kb = default_knowledge_base()

    def test_finds_relevant_entry(self) -> None:
        """45 钢侧铣应命中 CNC 条目。"""
        hits = self.kb.search("45钢 侧铣 切削速度")
        self.assertTrue(hits)
        self.assertEqual(hits[0].item.kb_id, "KB-CNC-0042")

    def test_finds_titanium(self) -> None:
        hits = self.kb.search("钛合金 TC4 精加工")
        self.assertEqual(hits[0].item.kb_id, "KB-TI-0007")

    def test_finds_stainless_coolant(self) -> None:
        hits = self.kb.search("304 不锈钢 内冷")
        self.assertEqual(hits[0].item.kb_id, "KB-SST-0008")

    def test_empty_for_irrelevant_query(self) -> None:
        """无关查询必须返回空，不得硬凑结果。"""
        self.assertEqual(self.kb.search("火星探测器轨道参数"), [])

    def test_empty_query_returns_empty(self) -> None:
        self.assertEqual(self.kb.search(""), [])

    def test_scores_are_descending(self) -> None:
        """命中必须按综合分降序排列。"""
        hits = self.kb.search("切削速度 每齿进给 刀具")
        scores = [h.score for h in hits]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_all_scores_above_threshold(self) -> None:
        """返回结果必须高于阈值。"""
        for h in self.kb.search("45钢 侧铣 参数"):
            self.assertGreaterEqual(h.score, SCORE_THRESHOLD)

    def test_respects_top_k(self) -> None:
        hits = self.kb.search("切削 刀具 参数", top_k=1)
        self.assertLessEqual(len(hits), 1)

    def test_weights_sum_to_one(self) -> None:
        """三因子权重之和必须为 1。"""
        self.assertAlmostEqualRel(W_RELEVANCE + W_AUTHORITY + W_FRESHNESS, 1.0, 1e-9)

    def test_rejects_pool_without_authoritative_hits(self) -> None:
        """若整池无权威条目，应作废结果避免仅凭低权威来源作答。"""
        kb = KnowledgeBase(
            [
                _item(kb_id="KB-CNC-0001", source_type="S7", title="访谈切削速度",
                      body="侧铣切削速度访谈记录"),
            ]
        )
        self.assertEqual(kb.search("侧铣 切削速度"), [])
        # 关闭门槛后应能命中
        self.assertTrue(
            kb.search("侧铣 切削速度", require_high_authority=False)
        )


class TestConflicts(ZrCutTestCase):
    """知识冲突检测。"""

    def test_detects_conflicting_values(self) -> None:
        """同一指标不同数值应报冲突。"""
        kb = KnowledgeBase(
            [
                _item(kb_id="KB-CNC-0001", source_type="S2", metrics={"vc": "120-180"}),
                _item(kb_id="KB-CNC-0002", source_type="S1", metrics={"vc": "90-140"}),
            ]
        )
        hits = kb.search("切削速度", require_high_authority=False)
        conflicts = kb.detect_conflicts(hits)
        self.assertTrue(conflicts)
        self.assertEqual(conflicts[0]["metric"], "vc")

    def test_no_conflict_when_same_value(self) -> None:
        kb = KnowledgeBase(
            [
                _item(kb_id="KB-CNC-0001", source_type="S2", metrics={"vc": "120-180"}),
                _item(kb_id="KB-CNC-0002", source_type="S1", metrics={"vc": "120-180"}),
            ]
        )
        hits = kb.search("切削速度", require_high_authority=False)
        self.assertEqual(kb.detect_conflicts(hits), [])

    def test_conflict_includes_resolution(self) -> None:
        """冲突描述必须给出处置方式。"""
        kb = KnowledgeBase(
            [
                _item(kb_id="KB-CNC-0001", source_type="S2", metrics={"vc": "120-180"}),
                _item(kb_id="KB-CNC-0002", source_type="S1", metrics={"vc": "90-140"}),
            ]
        )
        hits = kb.search("切削速度", require_high_authority=False)
        c = kb.detect_conflicts(hits)[0]
        self.assertIn("resolution", c)
        self.assertTrue(c["kbIds"])


class TestKnowledgeBaseAdmin(ZrCutTestCase):
    """知识库增删与内置库完整性。"""

    def test_len_matches_items(self) -> None:
        kb = default_knowledge_base()
        self.assertGreaterEqual(len(kb), 10)

    def test_add_increases_size(self) -> None:
        kb = KnowledgeBase([_item(kb_id="KB-CNC-0001")])
        before = len(kb)
        kb.add(_item(kb_id="KB-CNC-0002", title="新增条目"))
        self.assertEqual(len(kb), before + 1)

    def test_default_kb_has_all_sources_represented(self) -> None:
        """内置知识库应覆盖多种来源类型，体现知识多样性。"""
        kb = default_knowledge_base()
        types = {it.source_type for it in kb.index.items}
        self.assertGreaterEqual(len(types), 4)

    def test_default_kb_ids_unique(self) -> None:
        kb = default_knowledge_base()
        ids = [it.kb_id for it in kb.index.items]
        self.assertEqual(len(ids), len(set(ids)))

    def test_default_kb_all_have_two_reviewers(self) -> None:
        """双人复核是知识入库的硬要求。"""
        for it in default_knowledge_base().index.items:
            with self.subTest(kb_id=it.kb_id):
                self.assertGreaterEqual(len(it.reviewers), 2)


class TestParseDate(ZrCutTestCase):
    """有效期解析。"""

    def test_parses_iso(self) -> None:
        self.assertEqual(parse_valid_until("2027-01-31"), date(2027, 1, 31))

    def test_returns_none_for_empty(self) -> None:
        self.assertIsNone(parse_valid_until(""))

    def test_raises_for_bad_format(self) -> None:
        with self.assertRaises(KnowledgeError):
            parse_valid_until("二〇二七年")
