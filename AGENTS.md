# JDDataDisplay

电商数据可视化：Python 批处理（`backend/jobs`）+ FastAPI（`backend/app`）+ Vue3/Vite/TS（`frontend`）。生产单端口 8000 同时提供 API、SPA 与图片。数据源在 `ResourceData/`。

## Agent skills

### Issue tracker

issues 与 specs 以本地 markdown 存放于 `.scratch/<feature>/`，**不使用 GitHub Issues**（本仓库的 remote 虽指向 GitHub，但工作跟进一直在本地进行，`.scratch/` 被刻意 gitignore）。参见 `docs/agents/issue-tracker.md`。

### Triage labels

沿用默认的五个规范性角色字符串：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`；在本地追踪器里以 issue 文件顶部的 `Status:` 行表达。参见 `docs/agents/triage-labels.md`。

### Domain docs

single-context：仓库根目录 `CONTEXT.md` 词汇表 + `docs/adr/`。参见 `docs/agents/domain.md`。
