# Word 报告导出用 python-docx 生成真 docx

AI 生成的分析报告要求可导出 Word 文档。项目依赖是完整 freeze（为异地复现，ai-rules §3），引入新依赖要过秤：

- **HTML 伪 .doc**（把 HTML 内容存成 .doc 扩展名）：零依赖，但 Word 打开时弹「格式与扩展名不匹配」黄色警告，发给别人时尤其掉信任感；
- **python-docx 生成真 .docx（选定）**：新增两个 freeze 依赖（`python-docx` + `lxml`；lxml 是 C 扩展，但 Windows CPython 3.13 有预编译 wheel，不触发本地编译），换来打开无警告、样式可控的正经文档。

HTML 导出侧零新依赖：`markdown-it-py` 已在 freeze（Rich 的传递依赖），聊天显示与 HTML 报告共用后端**同一份** markdown→HTML 渲染，两处样式天然一致（Q13C 决策：后端统一渲染，前端零新渲染依赖）。

报告里的图表做**非对称**处理：HTML 报告允许内联样式 CSS 条形图（模型写简单 div 条形图可靠、零依赖）；Word 版退化为文字 + 表格——python-docx 画图表要手写 DrawingML，成本远超收益，**不要**为了「Word 也有图」去补这条腿。

## Considered Options

- **HTML 伪 .doc**：零依赖；黄色警告 + 非 Word 原生格式，发给合作方的观感差。
- **第一版只做 HTML，Word 二期**：少两个依赖，但用户明确要 Word 文档，二期大概率还是这条路。
- **python-docx 真 docx（选定）**：+2 freeze 依赖，换正经文档。

## Consequences

- freeze 新增 `python-docx`、`lxml` 两行；异地复现需重跑 `pip install -r requirements.txt`。
- markdown→docx 的结构映射（标题/段落/表格/列表）在后端实现一份，输入是模型输出的 markdown——与 HTML 导出同源，两份导出内容永远一致。
- CSS 条形图在 docx 转换时**降级为表格或省略**，不做 DrawingML 绘图。
