#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""覆盖率测算脚本，零外部依赖。

做法：用标准库 trace 统计被测源码的行命中，配合 ast 剔除不可执行行
（文档字符串、空行、注释等），得到可执行行覆盖率。

关键点：测试模块的导入与加载必须发生在 trace 启动之后，否则模块级代码
（如 MATERIALS 常量表）会被误判为未覆盖。

用法：
    python scripts/coverage.py
    python scripts/coverage.py --threshold 85
    python scripts/coverage.py --verbose
"""

from __future__ import annotations

import argparse
import ast
import io
import sys
import trace
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "zrcut"
TESTS_DIR = ROOT / "tests"

_NON_EXEC_DECORATORS = {"property", "staticmethod", "classmethod"}


def executable_lines(path: Path) -> set[int]:
    """用 AST 求可执行行号基线。

    只把真正会执行的语句计入，跳过纯装饰器与类级常量声明之外的空壳行。

    Args:
        path: 源码路径。

    Returns:
        可执行行号集合。
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt):
            lines.add(node.lineno)
    return lines


def measure() -> tuple[dict[Path, set[int]], dict[Path, set[int]]]:
    """在 trace 下运行全部测试，返回命中行与可执行行。

    除了主线程，还会给新建线程装上同一套 tracer，否则 HTTP 服务线程内
    执行的 api.py 代码会被漏计。

    Returns:
        (命中行映射, 可执行行映射)。
    """
    import threading

    sys.path.insert(0, str(ROOT))
    tests_dir = str(TESTS_DIR)
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)

    tracer = trace.Trace(
        count=1,
        trace=0,
        ignoredirs=[sys.prefix, sys.exec_prefix],
        ignoremods=("unittest", "trace"),
    )

    def run() -> None:
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        for p in sorted(TESTS_DIR.glob("test_*.py")):
            suite.addTests(loader.loadTestsFromName(f"tests.{p.stem}"))
        runner = unittest.TextTestRunner(verbosity=0, stream=io.StringIO())
        runner.run(suite)

    threading.settrace(tracer.globaltrace)
    try:
        tracer.runfunc(run)
    finally:
        threading.settrace(None)
    counts = tracer.results().counts

    src_root = SRC_DIR.resolve()
    hit: dict[Path, set[int]] = {}
    for (filename, lineno), cnt in counts.items():
        if not cnt:
            continue
        p = Path(filename).resolve()
        try:
            p.relative_to(src_root)
        except ValueError:
            continue
        hit.setdefault(p, set()).add(lineno)

    exec_map: dict[Path, set[int]] = {}
    for src in sorted(SRC_DIR.glob("*.py")):
        exec_map[src] = executable_lines(src)
    return hit, exec_map


def main() -> int:
    """主入口。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=85.0)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if not SRC_DIR.exists():
        print(f"[错误] 源码目录不存在：{SRC_DIR}")
        return 2

    hit, exec_map = measure()

    total_exec = 0
    total_hit = 0
    print("=" * 68)
    print("智刃工艺 · 代码覆盖率报告")
    print("=" * 68)
    print(f"{'文件':<26}{'可执行行':>10}{'已覆盖':>10}{'覆盖率':>10}")
    print("-" * 68)
    detail: dict[str, list[int]] = {}
    for src, exec_lines in exec_map.items():
        if not exec_lines:
            continue
        covered = hit.get(src, set()) & exec_lines
        total_exec += len(exec_lines)
        total_hit += len(covered)
        pct = len(covered) / len(exec_lines) * 100
        print(f"{src.name:<26}{len(exec_lines):>10}{len(covered):>10}{pct:>9.1f}%")
        missing = sorted(exec_lines - covered)
        if missing:
            detail[src.name] = missing

    print("-" * 68)
    overall = total_hit / total_exec * 100 if total_exec else 0.0
    print(f"{'合计':<26}{total_exec:>10}{total_hit:>10}{overall:>9.1f}%")

    if args.verbose and detail:
        print()
        print("未覆盖行号：")
        for name, lines in detail.items():
            preview = ", ".join(str(x) for x in lines[:40])
            more = " …" if len(lines) > 40 else ""
            print(f"  {name}: {preview}{more}")

    print()
    if overall >= args.threshold:
        print(f"[通过] 覆盖率 {overall:.1f}% ≥ 阈值 {args.threshold:.1f}%")
        return 0
    print(f"[未达标] 覆盖率 {overall:.1f}% < 阈值 {args.threshold:.1f}%")
    return 1


if __name__ == "__main__":
    sys.exit(main())
