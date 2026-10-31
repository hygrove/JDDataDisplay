# -*- coding: utf-8 -*-
"""AI 报告导出：markdown → HTML（内嵌样式完整文档）与 Word（真 docx）。

ADR-0018 的落地：
- HTML 侧零新依赖（markdown-it-py 已在 freeze），与聊天显示共用同一渲染内核；
  但导出渲染器**单独开 html=True 通道**——报告允许内联样式 CSS 条形图
  （Q11B 决策），聊天显示侧仍然 html=False（v-html 安全底线），两者互不影响。
- Word 侧 python-docx 结构映射（标题/段落/列表/表格/加粗），CSS 条形图
  **降级为剥标签后的纯文本**（DrawingML 绘图成本远超收益，不做）。
- 两份导出与聊天显示同源（同一份 markdown 输入），内容永远一致。

安全说明：导出端点接收的 markdown 来自前端，公网任何人都能 POST——但产物
只是**下载到请求者自己电脑的附件**（Content-Disposition: attachment），
不回显给其它用户，所以注入面仅限"自己下载自己的恶意文件"。即便如此仍做
基础净化（剥 <script> 与 on* 事件属性），防误用不防攻击者。
"""
from __future__ import annotations

import html as _html
import io
import re
from datetime import datetime
from typing import Any, Optional

from markdown_it import MarkdownIt
from markdown_it.token import Token

# 导出渲染器：html=True（CSS 条形图需要内联样式通过）+ 表格 / 删除线。
# 与 chat_service._make_md（html=False，聊天显示）是**两个刻意不同的实例**，
# 别为了"统一"合成一个——那会要么丢条形图、要么开聊天侧注入面。
_MD_EXPORT = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])

# 报告 HTML 模板：独立完整文档（双击浏览器可开），配色取自平台设计令牌的
# 同款值（#1e293b 主文字 / #e1251b 品牌红），不依赖任何外部资源。
_REPORT_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{TITLE}</title>
<style>
  body { font-family: "Segoe UI", "Microsoft YaHei", sans-serif; max-width: 860px;
         margin: 40px auto; padding: 0 24px; color: #1e293b; line-height: 1.75; }
  h1.report-title { font-size: 22px; border-bottom: 2px solid #e1251b;
                    padding-bottom: 10px; margin-bottom: 6px; }
  .report-meta { color: #64748b; font-size: 12px; margin: 0 0 28px; }
  h1, h2, h3, h4 { color: #0f172a; margin: 1.4em 0 0.5em; }
  h1 { font-size: 20px; } h2 { font-size: 17px; } h3 { font-size: 15px; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13px; }
  th, td { border: 1px solid #e5e7eb; padding: 6px 10px; text-align: left; }
  th { background: #fef2f2; color: #b91c1c; }
  tr:nth-child(even) td { background: #fafbfc; }
  code { background: #f8fafc; padding: 1px 5px; border-radius: 3px; font-size: 0.9em; }
  pre { background: #f8fafc; padding: 10px 12px; border-radius: 6px; overflow-x: auto; }
  pre code { background: none; padding: 0; }
  blockquote { margin: 12px 0; padding: 6px 14px; border-left: 3px solid #e1251b;
               color: #475569; background: #fafbfc; }
  ul, ol { padding-left: 22px; }
  hr { border: none; border-top: 1px solid #e5e7eb; margin: 20px 0; }
  /* CSS 条形图容器（模型输出的内联样式 div）在文档流里自然渲染 */
</style>
</head>
<body>
<h1 class="report-title">{TITLE}</h1>
<p class="report-meta">生成时间 {NOW} · JD 数据平台 AI 助手</p>
{BODY}
</body>
</html>
"""

_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_ONATTR_RE = re.compile(r"\son\w+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)


def _sanitize_report_html(html: str) -> str:
    """报告 HTML 的基础净化：剥 <script> 块与 on* 事件属性。

    Args:
        html: 渲染后的 HTML 片段。

    Returns:
        str: 净化后的 HTML（script 与事件钩子已移除，其余原样保留——
        CSS 条形图的内联 style 不受影响）。
    """
    html = _SCRIPT_RE.sub("", html)
    return _ONATTR_RE.sub("", html)


def render_report_html(markdown: str, title: str) -> str:
    """把报告 markdown 渲染成内嵌样式的完整 HTML 文档。

    Args:
        markdown: 模型输出的报告原文。
        title: 文档标题（同时用于 <title> 与正文大标题）。

    Returns:
        str: 完整 HTML 文档（含 <style>，双击浏览器可开，零外部依赖）。
    """
    body = _sanitize_report_html(_MD_EXPORT.render(markdown))
    safe_title = _html.escape(title, quote=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        _REPORT_HTML_TEMPLATE
        .replace("{TITLE}", safe_title)
        .replace("{NOW}", now)
        .replace("{BODY}", body)
    )


# ---------------------------------------------------------------------------
# markdown → docx（python-docx 结构映射）
# ---------------------------------------------------------------------------

# 纯文本抽取：表格单元格与 html_block 降级用
_TAG_RE = re.compile(r"<[^>]+>")


def _inline_plain_text(inline: Token) -> str:
    """把 inline token 的子节点抽成纯文本（丢格式，保内容）。"""
    parts: list[str] = []
    for child in inline.children or []:
        if child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type == "html_inline":
            text = _TAG_RE.sub("", child.content)
            if text:
                parts.append(text)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
        # strong/em/link 的开闭标签没有文本内容，跳过（其内部 text 子节点会被遍历到）
    return "".join(parts)


def _styled_run(par: Any, text: str, bold: bool, italic: bool) -> Any:
    """加一个带格式的 run 到段落，返回该 run。"""
    run = par.add_run(text)
    if bold:
        run.bold = True
    if italic:
        run.italic = True
    return run


def _add_inline_runs(par: Any, children: list[Token]) -> None:
    """把 inline 子节点映射为段落的 runs（加粗 / 斜体 / 行内代码 / 换行）。"""
    bold = False
    italic = False
    for tok in children or []:
        t = tok.type
        if t == "text":
            _styled_run(par, tok.content, bold, italic)
        elif t == "strong_open":
            bold = True
        elif t == "strong_close":
            bold = False
        elif t == "em_open":
            italic = True
        elif t == "em_close":
            italic = False
        elif t == "code_inline":
            run = _styled_run(par, tok.content, bold, italic)
            run.font.name = "Consolas"
        elif t in ("softbreak", "hardbreak"):
            par.add_run("\n")
        elif t == "html_inline":
            # CSS 条形图降级：剥标签留文本（ADR-0018）
            text = _TAG_RE.sub("", tok.content)
            if text:
                _styled_run(par, text, bold, italic)
        # link_open/close 不特殊处理：链接文字作为 text 子节点已遍历，URL 不进 docx


def _emit_docx_table(doc: Any, rows: list[list[str]]) -> None:
    """把收集好的表格行写成 docx 表格（首行加粗当表头）。"""
    rows = [r for r in rows if r]
    if not rows:
        return
    cols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=cols)
    try:
        table.style = "Table Grid"
    except KeyError:  # 模板缺失该样式时裸表也比丢数据强
        pass
    for ri, row in enumerate(rows):
        for ci in range(cols):
            text = row[ci] if ci < len(row) else ""
            cell = table.cell(ri, ci)
            cell.text = text
            if ri == 0:
                for run in cell.paragraphs[0].runs:
                    run.bold = True


def markdown_to_docx(markdown: str, title: str) -> bytes:
    """把报告 markdown 生成真 docx 文档字节流。

    Args:
        markdown: 模型输出的报告原文。
        title: 文档标题（一级大标题）。

    Returns:
        bytes: .docx 文件内容（可直接作为 HTTP 响应体）。

    Note:
        结构映射：h1-h6 → Heading、段落 → 段落（含加粗/斜体/行内代码）、
        无序列表 → List Bullet、有序列表 → 手动编号（避免 python-docx
        List Number 样式跨列表连续计数的缺陷）、表格 → Table Grid 首行加粗、
        代码块 → Consolas 小字段落、引用 → Intense Quote、CSS 条形图 →
        剥标签纯文本。
    """
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    doc.add_heading(title, 0)
    meta = doc.add_paragraph()
    meta_run = meta.add_run(f"生成时间 {datetime.now():%Y-%m-%d %H:%M} · JD 数据平台 AI 助手")
    meta_run.italic = True
    meta_run.font.size = Pt(9)

    tokens = _MD_EXPORT.parse(markdown)
    n = len(tokens)
    i = 0
    # 遍历状态
    current_par: Optional[Any] = None
    in_quote = False
    in_ordered = False
    ordered_no = 0
    in_table = False
    table_rows: list[list[str]] = []
    current_row: Optional[list[str]] = None
    cell_buf: Optional[list[str]] = None

    while i < n:
        tok = tokens[i]
        t = tok.type
        if t == "heading_open":
            level = min(6, max(1, int(tok.tag[1:]) or 1))
            par = doc.add_heading("", level=level)
            if i + 1 < n and tokens[i + 1].type == "inline":
                _add_inline_runs(par, tokens[i + 1].children)
            i += 3  # heading_open + inline + heading_close
            continue
        if t == "paragraph_open":
            if current_par is None:
                if in_quote:
                    try:
                        current_par = doc.add_paragraph(style="Intense Quote")
                    except KeyError:
                        current_par = doc.add_paragraph()
                else:
                    current_par = doc.add_paragraph()
            i += 1
            continue
        if t == "inline":
            if cell_buf is not None:
                cell_buf.append(_inline_plain_text(tok))
            elif current_par is not None:
                _add_inline_runs(current_par, tok.children)
            i += 1
            continue
        if t == "paragraph_close":
            current_par = None
            i += 1
            continue
        if t == "bullet_list_open" or t == "bullet_list_close":
            i += 1
            continue
        if t == "ordered_list_open":
            in_ordered = True
            ordered_no = 0
            i += 1
            continue
        if t == "ordered_list_close":
            in_ordered = False
            i += 1
            continue
        if t == "list_item_open":
            ordered_no += 1
            if in_ordered:
                # 手动编号：python-docx 的 List Number 样式在多份文档/多个列表间
                # 会连续计数（1,2,3...7,8），手动前缀最可预测
                current_par = doc.add_paragraph()
                current_par.add_run(f"{ordered_no}. ")
            else:
                try:
                    current_par = doc.add_paragraph(style="List Bullet")
                except KeyError:
                    current_par = doc.add_paragraph()
            i += 1
            continue
        if t == "list_item_close":
            current_par = None
            i += 1
            continue
        if t == "table_open":
            in_table = True
            table_rows = []
            i += 1
            continue
        if t == "table_close":
            _emit_docx_table(doc, table_rows)
            in_table = False
            table_rows = []
            i += 1
            continue
        if t in ("thead_open", "thead_close", "tbody_open", "tbody_close"):
            i += 1
            continue
        if t == "tr_open":
            current_row = []
            i += 1
            continue
        if t == "tr_close":
            if current_row is not None:
                table_rows.append(current_row)
            current_row = None
            i += 1
            continue
        if t in ("th_open", "td_open"):
            cell_buf = []
            i += 1
            continue
        if t in ("th_close", "td_close"):
            if current_row is not None and cell_buf is not None:
                current_row.append("".join(cell_buf).strip())
            cell_buf = None
            i += 1
            continue
        if t in ("fence", "code_block"):
            par = doc.add_paragraph()
            run = par.add_run(tok.content.rstrip("\n"))
            run.font.name = "Consolas"
            run.font.size = Pt(9)
            i += 1
            continue
        if t == "html_block":
            # CSS 条形图降级：剥标签留文本（ADR-0018）；纯样式块剥完为空则跳过
            text = _TAG_RE.sub(" ", tok.content).strip()
            if text:
                doc.add_paragraph(text)
            i += 1
            continue
        if t == "blockquote_open":
            in_quote = True
            i += 1
            continue
        if t == "blockquote_close":
            in_quote = False
            current_par = None
            i += 1
            continue
        # hr / 其它未识别 token：跳过（docx 侧分隔线省略）
        i += 1

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
