#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""合稿与质量校验脚本。

用途：
  1. 按固定顺序合并 docs/draft/*.md 为单一 Markdown；
  2. 统计中文字符数；
  3. 检查统一设定的一致性（禁用词、编号引用、单位规范）。

用法：
  python scripts/assemble.py            # 只校验，不写文件
  python scripts/assemble.py --write    # 校验并写出 deliverables/规范-Markdown版.md
"""

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRAFT = ROOT / "docs" / "draft"
OUT_DIR = ROOT / "deliverables"

# 合稿顺序：章节号 -> 文件名
ORDER = [
    "01-overview-architecture.md",
    "03-system-prompt.md",
    "04-01-router.md",
    "04-02-param-recommend.md",
    "04-03-prediction.md",
    "04-04-constraint.md",
    "04-05-process-card.md",
    "04-06-closure.md",
    "05-rag.md",
    "06-safety.md",
    "07-schema.md",
    "08-eval-fix.md",
    "09-examples.md",
]

# 禁用词：营销腔与 AI 腔
BANNED = ["赋能", "抓手", "生态", "闭环生态", "我认为", "建议您", "如上文所述", "综上所述"]

# 合法的提示词资产编号
ASSET_RE = re.compile(r"ZRCNC-(SYS|RTR|PAR|PRD|CST|CRD|CLS|RAG|SEC|FIX|EVL|DEMO)-\d{3}")
LEGAL_ASSETS = {
    "ZRCNC-SYS-001", "ZRCNC-RTR-001", "ZRCNC-PAR-001", "ZRCNC-PRD-001",
    "ZRCNC-CST-001", "ZRCNC-CRD-001", "ZRCNC-CLS-001", "ZRCNC-RAG-QRY-001",
    "ZRCNC-RAG-RNK-001", "ZRCNC-SEC-001", "ZRCNC-FIX-001", "ZRCNC-EVL-001",
    "ZRCNC-DEMO-001",
}

CN_RE = re.compile(r"[\u4e00-\u9fa5]")


def count_cn(text: str) -> int:
    return len(CN_RE.findall(text))


def strip_code_blocks(text: str) -> str:
    """去掉代码块后再统计，用于给出去代码块的中文字符数。"""
    return re.sub(r"```.*?```", "", text, flags=re.S)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="写出合稿文件")
    args = parser.parse_args()

    total_cn = 0
    total_cn_nocode = 0
    missing = []
    problems = []

    print("=" * 72)
    print("分章统计")
    print("=" * 72)
    print(f"{'文件':<34}{'中文':>8}{'去代码块':>10}{'表数':>6}{'资产数':>8}")

    parts = []
    for name in ORDER:
        path = DRAFT / name
        if not path.exists():
            missing.append(name)
            print(f"{name:<34}{'缺失':>8}")
            continue
        text = path.read_text(encoding="utf-8")
        cn = count_cn(text)
        cn_nocode = count_cn(strip_code_blocks(text))
        tables = text.count("\n| --- |") + text.count("\n|:---")
        assets = len(set(ASSET_RE.findall(text)))
        total_cn += cn
        total_cn_nocode += cn_nocode
        print(f"{name:<34}{cn:>8}{cn_nocode:>10}{tables:>6}{assets:>8}")
        parts.append(text)

        # 禁用词检查
        for word in BANNED:
            if word in text:
                problems.append(f"{name}: 出现禁用词「{word}」")

        # 资产编号合法性
        for m in ASSET_RE.finditer(text):
            code = m.group(0)
            if code not in LEGAL_ASSETS and code != "ZRCNC-DEMO-001":
                problems.append(f"{name}: 出现未定义的资产编号 {code}")

    print("-" * 72)
    print(f"{'合计':<34}{total_cn:>8}{total_cn_nocode:>10}")

    print()
    print("=" * 72)
    print("结论")
    print("=" * 72)

    if missing:
        print(f"[缺失] {len(missing)} 个章节文件未生成：{', '.join(missing)}")
    else:
        print("[完整] 全部章节文件已生成")

    target = 10000
    if total_cn >= target:
        print(f"[字数] 中文字符 {total_cn} ≥ {target}，达标")
    else:
        print(f"[字数] 中文字符 {total_cn} < {target}，缺 {target - total_cn}")

    if problems:
        print(f"[问题] 共 {len(problems)} 项：")
        for p in problems:
            print("  - " + p)
    else:
        print("[一致性] 未发现禁用词与非法编号")

    if args.write and not missing:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        merged = "\n\n".join(parts)
        out = OUT_DIR / "智刃工艺-提示词工程规范-v1.0.md"
        out.write_text(merged, encoding="utf-8")
        print(f"\n[写出] {out}")
        print(f"[写出] 中文字符 {count_cn(merged)}")

    return 0 if (not missing and total_cn >= target) else 1


if __name__ == "__main__":
    sys.exit(main())
