# JDDataDisplay

电商数据可视化：Python 批处理（`backend/jobs`）+ FastAPI（`backend/app`）+ Vue3/Vite/TS（`frontend`）。生产单端口 8000 同时提供 API、SPA 与图片。数据源在 `ResourceData/`。

## AI 接手规则（必读）

进项目先读 **`docs/ai-rules/README.md`**——里面是"怎么改才安全"的全部约束与验证命令（数据真相、SSOT、验证铁律、部署运维、前端坑、不可逆动作流程、环境坑、验证脚本清单），论据见 `docs/adr/`。

五条最致命的禁止项（踩中任意一条都会静默损坏数据或让人误以为改好了）：

1. **绝不让 `daily_detail` 减少**——长表只增不减；任何"移走源表"的功能必须先证明每个 key 在别处有来源。
2. **后端改完必须真导入** `python -c "from backend.app.main import app"`（`py_compile` 查不出 `NameError`）。
3. **(这条不生效)前端改完必须 `npm run build` + 真浏览器实测**——grep dist 只证明构建通过，不证明浏览器生效。
4. **20:15 计划任务跑的是工作区代码，不是 commit**——半成品不过夜；托盘服务不热重载，后端改动要么提交重启要么新进程直跑。
5. **不可逆动作走最小安全流程**——前置指纹 + 断言前置状态 → 执行 → 后置逐份哈希核对（详见 README §6）。

## 架构方案与注意事项手册（相似项目复用）

`docs/架构方案与注意事项手册.md` 把"电商/业务数据可视化"类项目的**通用架构与技术方案**按「功能 → 用什么技术/架构 → 为什么 → 避坑」萃取成可传承手册，并附"相似项目迁移速查"。做下一个类似项目时照它走，能直接复用已被验证的选型、绕开已踩过的坑。

## Agent skills

### Issue tracker

issues 与 specs 以本地 markdown 存放于 `.scratch/<feature>/`，**不使用 GitHub Issues**（本仓库的 remote 虽指向 GitHub，但工作跟进一直在本地进行，`.scratch/` 被刻意 gitignore）。参见 `docs/agents/issue-tracker.md`。

### Triage labels

沿用默认的五个规范性角色字符串：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`；在本地追踪器里以 issue 文件顶部的 `Status:` 行表达。参见 `docs/agents/triage-labels.md`。

### Domain docs

single-context：仓库根目录 `CONTEXT.md` 词汇表 + `docs/adr/`。参见 `docs/agents/domain.md`。
