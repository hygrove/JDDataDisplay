# -*- coding: utf-8 -*-
"""AI 报告导出测试：HTML（内嵌样式/净化/条形图）与 docx（结构映射/降级）。

运行：仓库根 `python -m unittest backend.tests.test_chat_export -v`

验收对应（工单 04）：
- HTML：内嵌样式完整文档、Content-Disposition 文件名、CSS 条形图通过、
  script / on* 净化
- docx：content-type / 文件名 / 重新打开可读且标题-段落-列表-表格都在、
  条形图降级为纯文本
"""
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.main import app  # noqa: E402

_CLIENT = TestClient(app)

# 一份覆盖主要结构的样例报告（标题/段落/加粗/有序无序列表/表格/引用/条形图）
SAMPLE_MD = """# 周销售分析

## 整体表现

本周成交金额 **591,075.60 元**，环比上升。

- 钻芯旗舰店：320,000 元
- 卡求旗舰店：45,000 元

1. 补货优先级最高
2. 关注北京库存

| 店铺 | 成交金额 | 访客 |
|---|---|---|
| 钻芯旗舰店 | 591,075.60 | 12,340 |
| 卡求旗舰店 | 39,578.28 | 2,106 |

> 数据口径：先求和再相除。

<div style="width:60%;background:#e1251b;height:12px;"></div><p>钻芯占 78%</p>
"""


class TestHtmlExport(unittest.TestCase):
    """HTML 导出：完整文档 + 样式 + 净化 + 条形图。"""

    def test_basic_document(self):
        r = _CLIENT.post(
            "/api/chat/export/html",
            json={"markdown": "# 标题\n\n**销售额** 100 元", "title": "销售周报"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/html"))
        # 附件下载头：文件名含标题与 .html（RFC 5987 编码）
        self.assertIn(".html", r.headers["content-disposition"])
        self.assertIn("%E9%94%80%E5%94%AE%E5%91%A8%E6%8A%A5", r.headers["content-disposition"])
        body = r.text
        self.assertIn("<style>", body)  # 内嵌样式（零外部依赖）
        self.assertIn("销售周报", body)  # 文档标题
        self.assertIn("<h1>标题</h1>", body)  # markdown 渲染
        self.assertIn("<strong>销售额</strong>", body)
        self.assertIn("JD 数据平台 AI 助手", body)  # 元信息行

    def test_bar_chart_passes_and_script_stripped(self):
        md = (
            '<div style="width:60%;background:#e1251b;height:12px;"></div>\n\n'
            "<script>alert(1)</script>\n\n"
            '<p onclick="evil()">正常文字</p>'
        )
        r = _CLIENT.post("/api/chat/export/html", json={"markdown": md, "title": "t"})
        self.assertEqual(r.status_code, 200)
        body = r.text
        # CSS 条形图：内联样式通过（html=True 导出通道）
        self.assertIn('style="width:60%;background:#e1251b', body)
        # 净化：script 块与 on* 事件属性被剥
        self.assertNotIn("<script", body)
        self.assertNotIn("onclick", body)
        # 正文文本保留
        self.assertIn("正常文字", body)

    def test_empty_markdown_422(self):
        r = _CLIENT.post("/api/chat/export/html", json={"markdown": "", "title": "t"})
        self.assertEqual(r.status_code, 422)


class TestDocxExport(unittest.TestCase):
    """docx 导出：结构映射 + 可重新打开 + 条形图降级。"""

    def _export(self, markdown: str, title: str = "周销售分析"):
        r = _CLIENT.post(
            "/api/chat/export/docx", json={"markdown": markdown, "title": title}
        )
        self.assertEqual(r.status_code, 200)
        return r

    def test_content_type_and_filename(self):
        r = self._export("# t\n\n正文")
        self.assertEqual(
            r.headers["content-type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertIn(".docx", r.headers["content-disposition"])

    def test_full_structure_reopenable(self):
        from docx import Document

        r = self._export(SAMPLE_MD)
        doc = Document(io.BytesIO(r.content))
        texts = [p.text for p in doc.paragraphs]
        all_text = "\n".join(texts)
        # 大标题（请求 title）+ meta 行
        self.assertEqual(doc.paragraphs[0].text, "周销售分析")
        self.assertIn("生成时间", texts[1])
        # markdown 各结构都在
        self.assertIn("周销售分析", all_text)  # h1
        self.assertIn("整体表现", all_text)  # h2
        self.assertIn("591,075.60 元", all_text)  # 段落（含加粗内容）
        self.assertIn("钻芯旗舰店：320,000 元", all_text)  # 无序列表项
        self.assertIn("补货优先级最高", all_text)  # 有序列表项（手动编号）
        # 引用
        self.assertTrue(any("先求和再相除" in t for t in texts))
        # 表格：2 行 3 列，表头加粗
        self.assertEqual(len(doc.tables), 1)
        table = doc.tables[0]
        self.assertEqual(len(table.rows), 3)
        self.assertEqual(table.cell(0, 0).text, "店铺")
        self.assertEqual(table.cell(1, 1).text, "591,075.60")
        self.assertTrue(all(run.bold for run in table.rows[0].cells[0].paragraphs[0].runs))
        # 加粗映射：段落里存在 bold 的 run
        bold_runs = [run for p in doc.paragraphs for run in p.runs if run.bold]
        self.assertTrue(any("591,075.60 元" in run.text for run in bold_runs))
        # 条形图降级：内层文本保留、样式丢弃
        self.assertTrue(any("钻芯占 78%" in t for t in texts))

    def test_bar_chart_degrades_to_plain_text(self):
        from docx import Document

        md = '<div style="width:60%;background:#e1251b;">缺货损失 15.6 件</div>'
        r = self._export(md, title="补货提示")
        doc = Document(io.BytesIO(r.content))
        texts = [p.text for p in doc.paragraphs]
        self.assertTrue(any("缺货损失 15.6 件" in t for t in texts))

    def test_ordered_list_numbering_manual(self):
        from docx import Document

        md = "1. 第一项\n2. 第二项\n\n另起一段\n\n1. 重新计数"
        r = self._export(md, title="编号")
        doc = Document(io.BytesIO(r.content))
        texts = [p.text for p in doc.paragraphs]
        # 手动编号：两个列表各自从 1 开始（List Number 样式会连续计数，这是选手动的原因）
        self.assertIn("1. 第一项", texts)
        self.assertIn("2. 第二项", texts)
        self.assertIn("1. 重新计数", texts)


if __name__ == "__main__":
    unittest.main()
