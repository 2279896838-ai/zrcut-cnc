#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把合稿 Markdown 转换为排版良好的 Word 文档。

支持：# 标题、## / ### / #### 子标题、表格、代码块、引用块、列表、粗体。
"""

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor, Cm

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "deliverables" / "智刃工艺-提示词工程规范-v1.0.md"
OUT = ROOT / "deliverables" / "智刃工艺-提示词工程规范-v1.0.docx"

FENCE = re.compile(r"^```(\w*)\s*$")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
TABLE_SEP = re.compile(r"^\|[\s:\-|]+\|$")
LIST_ITEM = re.compile(r"^\s*([-*]|\d+\.)\s+(.*)$")


def set_cjk_font(run, name="宋体", size=None, bold=None, color=None):
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), name)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if color is not None:
        run.font.color.rgb = color


def shade(cell, hex_color):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def add_runs_with_bold(paragraph, text, size=None, font="宋体"):
    """处理 **粗体** 与 `行内代码`。"""
    tokens = re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text)
    for tok in tokens:
        if not tok:
            continue
        if tok.startswith("**") and tok.endswith("**") and len(tok) > 4:
            run = paragraph.add_run(tok[2:-2])
            set_cjk_font(run, font, size, bold=True)
        elif tok.startswith("`") and tok.endswith("`") and len(tok) > 2:
            run = paragraph.add_run(tok[1:-1])
            set_cjk_font(run, "Consolas", (size - 0.5) if size else 9)
            run.font.color.rgb = RGBColor(0xC0, 0x39, 0x2B)
        else:
            run = paragraph.add_run(tok)
            set_cjk_font(run, font, size)


def parse_table_row(line):
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def build():
    if not SRC.exists():
        print(f"[错误] 源文件不存在：{SRC}")
        return 1

    lines = SRC.read_text(encoding="utf-8").splitlines()
    doc = Document()

    # 页面设置：A4 + 适中页边距
    sec = doc.sections[0]
    sec.page_width = Cm(21.0)
    sec.page_height = Cm(29.7)
    sec.left_margin = Cm(2.5)
    sec.right_margin = Cm(2.5)
    sec.top_margin = Cm(2.5)
    sec.bottom_margin = Cm(2.2)

    # 正文默认样式
    normal = doc.styles["Normal"]
    normal.font.name = "宋体"
    normal.font.size = Pt(10.5)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    pf = normal.paragraph_format
    pf.space_after = Pt(4)
    pf.line_spacing = 1.45

    i = 0
    n = len(lines)
    title_done = False

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 代码块
        m = FENCE.match(stripped)
        if m:
            lang = m.group(1)
            body = []
            i += 1
            while i < n and not FENCE.match(lines[i].strip()):
                body.append(lines[i])
                i += 1
            i += 1
            # 单格表格模拟代码块底纹
            tbl = doc.add_table(rows=1, cols=1)
            tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
            cell = tbl.cell(0, 0)
            shade(cell, "F2F4F7")
            cell.text = ""
            first = True
            for bline in body:
                p = cell.paragraphs[0] if first else cell.add_paragraph()
                first = False
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.line_spacing = 1.15
                run = p.add_run(bline if bline else " ")
                set_cjk_font(run, "Consolas", 8.5)
            doc.add_paragraph()
            continue

        # 表格
        if stripped.startswith("|") and i + 1 < n and TABLE_SEP.match(lines[i + 1].strip()):
            header = parse_table_row(stripped)
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(parse_table_row(lines[i].strip()))
                i += 1
            ncols = len(header)
            tbl = doc.add_table(rows=1, cols=ncols)
            tbl.style = "Table Grid"
            tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
            hdr = tbl.rows[0].cells
            for c, txt in enumerate(header):
                if c >= ncols:
                    break
                hdr[c].text = ""
                p = hdr[c].paragraphs[0]
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_after = Pt(1)
                add_runs_with_bold(p, txt, size=9)
                for r in p.runs:
                    r.bold = True
                shade(hdr[c], "E7ECF3")
            for row in rows:
                cells = tbl.add_row().cells
                for c in range(ncols):
                    txt = row[c] if c < len(row) else ""
                    cells[c].text = ""
                    p = cells[c].paragraphs[0]
                    p.paragraph_format.space_after = Pt(1)
                    p.paragraph_format.line_spacing = 1.15
                    add_runs_with_bold(p, txt, size=8.5)
            doc.add_paragraph()
            continue

        # 标题
        m = HEADING.match(stripped)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            if level == 1 and not title_done:
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_before = Pt(6)
                p.paragraph_format.space_after = Pt(18)
                run = p.add_run(text)
                set_cjk_font(run, "黑体", 20, bold=True, color=RGBColor(0x1F, 0x3A, 0x5F))
                title_done = True
            else:
                sizes = {1: 16, 2: 14, 3: 12, 4: 11, 5: 10.5, 6: 10.5}
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(12 if level <= 2 else 8)
                p.paragraph_format.space_after = Pt(6)
                p.paragraph_format.keep_with_next = True
                run = p.add_run(text)
                set_cjk_font(run, "黑体", sizes.get(level, 10.5), bold=True,
                             color=RGBColor(0x1F, 0x3A, 0x5F))
            i += 1
            continue

        # 引用块
        if stripped.startswith(">"):
            texts = []
            while i < n and lines[i].strip().startswith(">"):
                t = lines[i].strip().lstrip(">").strip()
                if t:
                    texts.append(t)
                i += 1
            for t in texts:
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Cm(0.7)
                p.paragraph_format.space_after = Pt(3)
                add_runs_with_bold(p, t, size=10)
            continue

        # 列表
        m = LIST_ITEM.match(line)
        if m:
            marker, txt = m.group(1), m.group(2)
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.7)
            p.paragraph_format.space_after = Pt(3)
            bullet = "• " if marker in "-*" else marker + " "
            add_runs_with_bold(p, bullet + txt, size=10.5)
            i += 1
            continue

        # 空行
        if not stripped:
            i += 1
            continue

        # 普通段落
        p = doc.add_paragraph()
        p.paragraph_format.first_line_indent = Cm(0)
        add_runs_with_bold(p, stripped, size=10.5)
        i += 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(OUT))
    print(f"[写出] {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(build())
