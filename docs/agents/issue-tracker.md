# 问题追踪器：本地 Markdown（Issue tracker: Local Markdown）

本仓库的 issues 与 specs 以 markdown 文件形式存在于 `.scratch/`。**不使用 GitHub Issues**——尽管本仓库的 remote 指向 GitHub，实际的工作跟进一直在本地进行（`.scratch/` 被刻意列进 `.gitignore`）。

## 约定（Conventions）

- 每个功能一个目录：`.scratch/<feature-slug>/`
- 规格说明是 `.scratch/<feature-slug>/spec.md`
- 实现 issue 每张工单一个文件，位于 `.scratch/<feature-slug>/issues/<NN>-<slug>.md`，从 `01` 起编号，绝不用单个合并的 tickets 文件
- 分诊状态记录为每个 issue 文件顶部附近的 `Status:` 一行（角色字符串见 `triage-labels.md`）
- 评论与对话历史追加到文件底部、一个 `## Comments` 标题之下

## 当技能说"发布到问题追踪器"（publish to the issue tracker）

在 `.scratch/<feature-slug>/` 下创建一个新文件（必要时创建目录）。

## 当技能说"抓取相关工单"（fetch the relevant ticket）

读取被引用路径处的文件。用户通常会直接传路径或 issue 编号。

## 寻路操作（Wayfinding operations）

由 `/wayfinder-zh` 使用。**地图（map）** 是一个文件，每个工单一个**子**文件。

- **地图（Map）**：`.scratch/<effort>/map.md`（Notes / Decisions-so-far / Fog 正文）。
- **子工单（Child ticket）**：`.scratch/<effort>/issues/NN-<slug>.md`，从 `01` 起编号，问题在正文里。一个 `Type:` 行记录工单类型（`research-zh`/`prototype-zh`/`grilling-zh`/`task`）；一个 `Status:` 行记录 `claimed`/`resolved`。
- **阻塞（Blocking）**：顶部附近的一行 `Blocked by: NN, NN`。一张工单在其列出的每个文件都 `resolved` 时即解除阻塞。
- **前沿（Frontier）**：扫描 `.scratch/<effort>/issues/` 找那些开放、未阻塞、未认领的文件；按编号第一个胜出。
- **认领（Claim）**：在任何工作之前设置 `Status: claimed` 并保存。
- **解决（Resolve）**：在 `## Answer` 标题下追加答案，设置 `Status: resolved`，然后向 `map.md` 中的地图 Decisions-so-far 追加一个上下文指针（梗概 + 链接）。
