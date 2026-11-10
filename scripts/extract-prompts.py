#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从合稿中抽取提示词资产到 prompts/ 目录，形成可直接投产的资产文件。

每个资产文件结构：
  # ZRCNC-XXX-001 名称
  - 来源：合稿章节
  - 版本：v1.0.0
  ## 设计意图
  ## 提示词正文
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MERGED = ROOT / "deliverables" / "智刃工艺-提示词工程规范-v1.0.md"

# 资产编号 -> (名称, 输出子目录)
ASSETS = {
    "ZRCNC-SYS-001": ("全局系统提示词", "sys"),
    "ZRCNC-RTR-001": ("意图路由与槽位抽取", "router"),
    "ZRCNC-PAR-001": ("参数推荐", "scenario"),
    "ZRCNC-PRD-001": ("预测解释", "scenario"),
    "ZRCNC-CRD-001": ("工艺卡生成", "scenario"),
    "ZRCNC-CLS-001": ("回填闭环与偏差判定", "scenario"),
    "ZRCNC-CST-001": ("约束校验编排", "constraint"),
    "ZRCNC-RAG-QRY-001": ("检索查询改写", "rag"),
    "ZRCNC-RAG-RNK-001": ("检索片段重排与引用", "rag"),
    "ZRCNC-SEC-001": ("安全红线拦截", "safety"),
    "ZRCNC-FIX-001": ("提示词自修复", "fix"),
    "ZRCNC-EVL-001": ("离线评估与打分", "eval"),
}

FENCE = re.compile(r"^```(\w*)\s*$")


def extract_blocks(lines):
    """返回 [(起始行号, 语言, [内容行])]"""
    blocks = []
    i = 0
    while i < len(lines):
        m = FENCE.match(lines[i].rstrip())
        if m and m.group(1):
            lang = m.group(1)
            start = i
            body = []
            i += 1
            while i < len(lines) and not FENCE.match(lines[i].rstrip()):
                body.append(lines[i])
                i += 1
            blocks.append((start, lang, body))
        i += 1
    return blocks


def main() -> int:
    if not MERGED.exists():
        print(f"[错误] 合稿不存在：{MERGED}")
        return 1

    text = MERGED.read_text(encoding="utf-8")
    lines = text.splitlines()
    blocks = extract_blocks(lines)

    # 精确定位：找到「提示词正文」小节标题，其上最近的资产章节标题即为归属
    found = {}
    for idx, line in enumerate(lines):
        if "提示词正文" not in line:
            continue
        # 向上找最近的章节标题，其中含资产编号
        code = None
        for j in range(idx, -1, -1):
            l2 = lines[j]
            m2 = re.search(r"(ZRCNC-[A-Z]+(?:-[A-Z]+)?-\d{3})", l2)
            if m2 and m2.group(1) in ASSETS and l2.lstrip().startswith("#"):
                code = m2.group(1)
                break
        if not code or code in found:
            continue
        # 向下找第一个 text 块
        for k in range(idx, min(idx + 40, len(lines))):
            m = FENCE.match(lines[k].rstrip())
            if m and m.group(1) == "text":
                body = []
                for line2 in lines[k + 1:]:
                    if FENCE.match(line2.rstrip()):
                        break
                    body.append(line2)
                found[code] = (k, body)
                break

    # 兜底：按资产首次出现位置向后找第一个 text 块（仅当上面未命中时）
    for code in ASSETS:
        if code in found:
            continue
        idx = text.find(code)
        if idx < 0:
            continue
        tail_lines = text[idx:].splitlines()
        for k, line in enumerate(tail_lines):
            m = FENCE.match(line.rstrip())
            if m and m.group(1) == "text":
                body = []
                for line2 in tail_lines[k + 1:]:
                    if FENCE.match(line2.rstrip()):
                        break
                    body.append(line2)
                found[code] = (None, body)
                break

    written = 0
    for code, (name, subdir) in ASSETS.items():
        if code not in found:
            print(f"[缺失] {code} {name}：未在合稿中找到提示词正文")
            continue
        _, body = found[code]
        cn = sum(len(re.findall(r"[\u4e00-\u9fa5]", l)) for l in body)
        out_dir = ROOT / "prompts" / subdir
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{code}.md"
        content = (
            f"# {code} {name}\n\n"
            f"- 来源：deliverables/智刃工艺-提示词工程规范-v1.0.md\n"
            f"- 版本：v1.0.0\n"
            f"- 正文字数（中文）：{cn}\n"
            f"- 生成方式：python scripts/extract-prompts.py（修改请改源稿后重新生成）\n\n"
            f"## 提示词正文\n\n"
            f"```text\n" + "\n".join(body) + "\n```\n"
        )
        out.write_text(content, encoding="utf-8")
        written += 1
        print(f"[写出] {code:<20} {name:<16} {cn:>5} 字  -> prompts/{subdir}/")

    print(f"\n共写出 {written}/{len(ASSETS)} 个提示词资产")
    return 0 if written == len(ASSETS) else 1


if __name__ == "__main__":
    sys.exit(main())
