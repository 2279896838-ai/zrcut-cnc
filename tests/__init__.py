"""测试公共夹具与工具。"""

from __future__ import annotations

import unittest
from typing import Any


class ZrCutTestCase(unittest.TestCase):
    """所有测试用例的基类，提供近似断言等辅助方法。"""

    def assertAlmostEqualRel(
        self, actual: float, expected: float, rel: float = 0.02, msg: str = ""
    ) -> None:
        """断言相对误差在容差内。

        Args:
            actual: 实际值。
            expected: 期望值。
            rel: 相对容差。
            msg: 断言消息。
        """
        if expected == 0:
            self.assertAlmostEqual(actual, expected, places=9, msg=msg)
            return
        diff = abs(actual - expected) / abs(expected)
        self.assertLessEqual(
            diff,
            rel,
            msg or f"相对误差 {diff:.4%} 超过 {rel:.2%}：actual={actual}, expected={expected}",
        )
