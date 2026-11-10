#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""代码块围栏配对检查：每个 ``` 围栏必须带语言标识开启、以裸 ``` 关闭。"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRAFT = ROOT / "docs" / "draft"

FENCE = re.compile(r"^```(.*)$")


def check(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    problems = []
    blocks = []
    state = None  # None=外部, dict=区块内
    for idx, line in enumerate(lines, 1):
        m = FENCE.match(line.rstrip())
        if m is None:
            continue
        lang = m.group(1).strip()
        if state is None:
            if not lang:
                problems.append(f"第 {idx} 行：代码块以裸 ``` 开启，缺少语言标识")
                state = {"start": idx, "lang": "(none)"}
            else:
                state = {"start": idx, "lang": lang}
        else:
            if lang:
                problems.append(
                    f"第 {idx} 行：区块开启于第 {state['start']} 行，关闭时带了语言标识 '{lang}'"
                )
            state["end"] = idx
            blocks.append(state)
            state = None
    if state is not None:
        problems.append(f"第 {state['start']} 行：代码块未闭合（语言 {state['lang']}）截止文件末尾")
    return blocks, problems


def main() -> int:
    total_blocks = 0
    all_problems = []
    print(f"{'文件':<34}{'区块数':>8}  语言分布")
    print("-" * 72)
    for path in sorted(DRAFT.glob("*.md")):
        blocks, problems = check(path)
        total_blocks += len(blocks)
        langs = {}
        for b in blocks:
            langs[b["lang"]] = langs.get(b["lang"], 0) + 1
        desc = ", ".join(f"{k}×{v}" for k, v in sorted(langs.items()))
        print(f"{path.name:<34}{len(blocks):>8}  {desc}")
        for p in problems:
            all_problems.append(f"{path.name}: {p}")

    print("-" * 72)
    print(f"代码块合计：{total_blocks}")
    if all_problems:
        print(f"\n[围栏问题] {len(all_problems)} 项：")
        for p in all_problems:
            print("  - " + p)
        return 1
    print("\n[围栏] 全部配对正确，无未闭合区块")
    return 0


if __name__ == "__main__":
    sys.exit(main())
