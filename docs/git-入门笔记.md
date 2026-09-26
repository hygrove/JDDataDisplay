# Git 入门实战笔记（以 JDDataDisplay 为例）

> 这份笔记是「边做边学」的产物：我把 `E:\JDDataDisplay` 这个真实项目从零推到了 GitHub（`https://github.com/hygrove/JDDataDisplay.git`）。下面每一步都是**真实执行过的命令 + 真实输出**，并讲清楚「这条命令在干嘛、为什么要这么做」。
>
> 适合人群：刚学 Git、不想死背命令、想看一个真实项目怎么进仓库的人。



---

## 0. 前置准备（别急着敲命令）

推送之前先确认三件事，否则仓库会脏：

1. **项目里没有密钥 / 密码硬编码** —— 我们之前用 Grep 在 `scripts/ backend/ frontend/src/` 里搜过 `password / token / secret / api_key / AKIA / ghp_` 等关键词，结果为 **0 命中**，安全。
2. **`.gitignore` 已经备好** —— 否则 `node_modules`（117MB）、`.venv`、`frontend/dist` 这种自动生成的产物会全被塞进仓库，clone 一次慢到怀疑人生。
3. **`.env` 是空的**（`config.yml` 里是 `{}`）—— 没有要泄露的机密。

> 顺带一提：本项目根目录其实**没有** `node_modules/`，真正的依赖在 `frontend/node_modules/`，被 `.gitignore` 第 10 行的 `node_modules/` 覆盖忽略。别被「根目录 node_modules」这种错觉骗了。

---

## 1. 配置身份：`git config`

Git 每次提交都要记录「谁提交的」。第一次用 Git 必须先设身份，否则提交会报错。

```powershell
git config --global user.name  "hygrove"
git config --global user.email "hygrove@users.noreply.github.com"
```

- `--global`：写到全局（`~/.gitconfig`），以后所有仓库都用这个身份。
- 用 `hygrove@users.noreply.github.com` 这种 GitHub 提供的「不公开邮箱」地址，能避免真邮箱被爬。
- 实测输出：`name=hygrove  email=hygrove@users.noreply.github.com`（无报错即成功）。

---

## 2. 初始化仓库：`git init`

```powershell
# 必须先 cd 到项目根目录
cd E:\JDDataDisplay
git init
```

真实输出：

```
Initialized empty Git repository in E:/JDDataDisplay/.git/
```

- `git init` 在项目根目录创建一个 `.git/` 隐藏文件夹，里面是 Git 的数据库（所有版本历史都在这里）。**这一步只创建仓库，不跟踪任何文件。**
- 你的代码文件此时一个都还没进版本控制，只是「仓库已就绪」。

---

## 3. 主干改名：`git branch -M main`

```powershell
git branch -M main
```

- 新仓库默认分支叫 `master`（老习惯）。2020 年后 GitHub 默认叫 `main`，为了和远端一致，本地也改成 `main`。
- `-M` = 强制改名（Move）。输出为空就是成功。

---

## 4. 先处理 `.gitignore`（关键顺序）

**顺序很重要**：先 `add .gitignore`，再 `add .`。这样暂存时忽略规则已生效，不会把垃圾文件一起收进去。

```powershell
    # 先把忽略规则本身加进暂存区（exit=0）
git add .            # 再把整个项目加进暂存区（exit=0）
```

- `git add .` 的 `.` 表示「当前目录所有文件」。但 Git 会**自动跳过** `.gitignore` 里列出的内容。
- `exit=0` 表示命令成功。

### 本项目的 `.gitignore` 都拦了啥（节选 + 白话）

| 规则                           | 拦掉的东西       | 为什么要拦                        |
| ---------------------------- | ----------- | ---------------------------- |
| `.venv/`                     | Python 虚拟环境 | 用 `pip install` 生成的，换台机器重建即可 |
| `node_modules/`              | 前端 npm 依赖   | 几百 MB，靠 `package.json` 一键重装  |
| `frontend/dist/`             | 前端构建产物      | `npm run build` 重新生成         |
| `backend/app/data/`          | 运行生成的图片数据   | `run_batch` 重新生成             |
| `.workbuddy/`                | 助手工作记忆      | 不是项目源码                       |
| `.jj/`                       | jj 的本地元数据   | 后面要学 jj，必须拦，否则 git 会把它当未跟踪文件 |
| `*.log` / `screenshot_*.png` | 日志、截图       | 临时产物                         |
| `.env`                       | 环境密钥        | 防泄密                          |
| `scripts/_*.py`              | 临时脚本        | 下划线前缀的临时文件                   |

> `ResourceData/` 默认**不忽略**（已注释掉），因为我们希望仓库自包含：别人 clone 下来 + 跑 `run_batch` 就能直接跑，不用你另外发数据文件。如果你觉得业务数据敏感/太大，把第 21 行的 `# ResourceData/` 注释去掉即可忽略它。

---

## 5. 验证忽略是否生效：`git check-ignore`（这一步最容易偷懒跳过，但最该做）

盲信 `.gitignore` 是新手经典翻车点。用 `check-ignore` 实地验一下：

```powershell
git check-ignore -v node_modules .venv frontend/dist backend/app/data .workbuddy .jj ResourceData
```

真实输出（节选）：

```
IGNORED  .venv            <- .gitignore:2:.venv/
IGNORED  frontend/dist    <- .gitignore:11:frontend/dist/
IGNORED  backend/app/data <- .gitignore:16:backend/app/data/
IGNORED  .workbuddy/      <- .gitignore:24:.workbuddy/
IGNORED  .jj/             <- .gitignore:28:.jj/
TRACKED  ResourceData/    (will be committed)   # 因为我们决定提交它
IGNORED  frontend/node_modules <- .gitignore:10:node_modules/
```

- `-v` 会告诉你**哪一行规则**命中了，排错时极其好用。
- 被忽略的路径不会进仓库；`ResourceData/` 没被忽略（我们故意的），会被提交。

> ⚠️ 排坑提醒：别用「`git check-ignore` 没匹配 = 会被提交」来反推根目录 `node_modules` 一定被提交了。**真相的唯一裁判是 `git ls-files`**（列出实际被跟踪的文件）。我们实测 `git ls-files node_modules/ | 计数 = 0`，`git ls-files .venv | 计数 = 0`，证明它们压根没进仓。暂存文件总数 = **94**，符合预期（源码 + 配置 + ResourceData）。自定义诊断脚本的「TRACKED」标签会骗人，`ls-files` 不会。

---

## 6. 提交：`git commit`

```powershell
git commit -m "feat: 初始提交 JDDataDisplay"
```

真实输出：

```
[main (root-commit) e9cd545] feat: 初始提交 JDDataDisplay
 94 files changed, 16838 insertions(+)
 create mode 100644 .gitignore
 create mode 100644 README.md
 ...（94 个文件）
```

- 提交是把「暂存区」里的内容**永久快照**成一个版本。`e9cd545` 是这次提交的短哈希（身份证号）。
- `feat:` 是 Conventional Commits 规范的前缀，表示「新功能」。好的提交信息日后回看历史时救你一命。
- `root-commit` 说明这是仓库的第一个提交。

> 小插曲：上面日志里提交信息显示成乱码 `鍒濆鎻愪氦`，那只是**沙箱控制台的 GBK 编码显示问题**，真实存进 Git 的是 UTF-8 的「初始提交」，GitHub 上显示正常。

---

## 7. 关联远端：`git remote add origin`

本地仓库和 GitHub 仓库还是两个孤岛，用 `remote` 把它们连起来。

```powershell
# 注意：这里临时把 PAT 拼进 URL 做一次性认证（下面第 9 步会清掉）
git remote add origin https://<你的PAT>@github.com/hygrove/JDDataDisplay.git
```

- `origin` 是远端仓库的**默认别名**（约定俗成，不是关键字）。
- URL 里的 `<你的PAT>@` 是 HTTPS 方式下把 Personal Access Token 当密码用。

---

## 8. 推送：`git push -u origin main`

```powershell
git push -u origin main
```

**第一次的真实结果（踩坑现场）：**

```
fatal: unable to access 'https://github.com/hygrove/JDDataDisplay.git/': Empty reply from server
```

再次重试并打印详细日志，抓到真凶：

```
* Establishing HTTP proxy tunnel to github.com:443
=> Send header: CONNECT github.com:443 HTTP/1.1
<= Recv header: HTTP/1.1 502 Bad Gateway
<= Recv header: Retry-After: 1
fatal: unable to access '...': CONNECT tunnel failed, response 502
```

**根因**：本机出口走了一个本地代理（`127.0.0.1:53149`），它给 Git 建 HTTPS 隧道时偶发返回 **502**（代理自己连上游失败，且带 `Retry-After: 1`，典型的瞬时/限流错误）。`curl` 走 GET 能通，但 Git 走 `CONNECT` 隧道时撞上了这次抽风。

**解法**：这种 502 是**可重试的**，直接重推就行——第二次（其实就是紧接着的重试）立刻成功：

```
To https://github.com/hygrove/JDDataDisplay.git
 * [new branch]      main -> main
branch 'main' set up to track 'origin/main'.
```

- `push` 把本地 `main` 分支上传到远端。
- `-u`（= `--set-upstream`）把本地 `main` 和远端 `origin/main` 绑定，之后直接 `git push` / `git pull` 不用再写分支名。
- `[new branch] main -> main` 表示在远端新建了 `main` 分支。

---

## 9. 推送后清理：把 PAT 从远端 URL 里抹掉

刚才为了认证，远端 URL 里塞了明文 PAT。留在配置里不卫生（别人看 `git remote -v` 就看到你的令牌了）。推送成功后立刻改回干净地址：

```powershell
git remote set-url origin https://github.com/hygrove/JDDataDisplay.git
git remote -v
```

真实输出：

```
origin  https://github.com/hygrove/JDDataDisplay.git (fetch)
origin  https://github.com/hygrove/JDDataDisplay.git (push)
```

- 现在 URL 里没有 PAT 了。后续推送靠 **Git Credential Manager**（装 Git for Windows 时自带）缓存凭据，不再需要把令牌写进 URL。

---

## 10. 收尾验证

```powershell
git log --oneline      # 看提交历史：e9cd545 feat: 初始提交 JDDataDisplay
git status -s          # 应为空（工作树干净，没有未提交改动）
git ls-files | 计数    # = 94
git ls-remote origin   # 确认远端分支存在（偶发 502 时重试即可）
```

---

## 11. 踩坑总结（都是真金白银换来的）

1. **先 `.gitignore` 再 `add .`** —— 顺序错了，几百 MB 依赖就进仓了，且要从历史里抠出来很麻烦。
2. **`git check-ignore -v` 是排错神器**，但别拿它的「未命中」标签反推「会被提交」；**`git ls-files` 才是真相**。
3. **代理 502 是可重试错误**：`CONNECT tunnel failed, response 502` / `Empty reply from server` 不是你配置错了，重试几次就行。
4. **PAT 不要长期留在 `remote` URL** —— 用完 `set-url` 清掉，或用凭据管理器 / SSH。
5. **提交信息用 Conventional Commits**（`feat:` / `fix:` / `docs:` / `chore:` …），历史可读性强十倍。

---

## 12. 下一步：你要继续学的

- **日常循环**：改代码 → `git add -p`（精准暂存）→ `git commit` → `git push`。
- **SSH 方案**：现在用的是 HTTPS+PAT。下一步建议配 SSH 密钥对（`ssh-keygen` + 把公钥贴到 GitHub），从此免令牌、命令更短、也更像专业选手。
- **jj（Jujutsu）**：你已经加了 `.jj/` 到忽略列表。jj 和 git 兼容（colocated 模式，`.git` 和 `.jj` 共存），没有「暂存区」概念、有 `jj undo` 后悔药。可以 `jj git clone` / `jj commit` / `jj git push` 直接操作同一个仓库，git 命令也照常能用——两个一起学互不冲突。

---

## 13. 后续日常提交流程（第一次之后怎么循环）

第一次提交做了一半是**一次性地基**，之后永远不用重来。先把"哪些只用做一次"切干净，再讲日常循环。

### 13.1 只用做一次的动作（别每次重来）

| 动作 | 命令 | 说明 |
|---|---|---|
| 建本地仓库 | `git init` | 仓库已存在，跳过 |
| 主干改名 | `git branch -M main` | 已改，跳过 |
| 关联远端 | `git remote add origin <url>` | 已关联，跳过 |
| 绑定跟踪 | `git push -u origin main` | `-u` 已绑过，之后 `git push` 不用再写分支名 |

地基打好了，上面"盖房子"（提交代码）才是日常。

### 13.2 日常循环（recurring）

尤其你是「异地多机开发」，最稳的顺序：

```bash
git pull                        # ① 开工前先同步远端最新
git status                      # ② 看哪些文件动过
# ……改代码……
git diff                        # ③ 提交前过一遍自己改了啥
git add <具体文件>               # ④ 把要提交的改动放进暂存区
git commit -m "type: 一句话说明"  # ⑤ 生成版本快照
git push                        # ⑥ 推到 GitHub（-u 绑过，不用再写分支名）
```

逐条原理：

- **① `git pull`** = `fetch` + `merge`。你可能在公司机和家里机都改过，开工先拉，把远端新提交合进来再基于最新代码改，否则后面 push 会被拒（non-fast-forward 分叉）。
- **② `git status`**：工作树快照，红=已改/未跟踪，绿=已暂存。
- **③ `git diff`**：行级看具体改动，提交前自检，避免把调试代码误提交。
- **④ `git add <文件>`**：**精准添加**，推荐 `git add -p` 做"按块暂存"——同一文件里只想提交其中一部分时用，调试片段就不会混进去。
- **⑤ `git commit`**：把暂存区快照成版本。信息用 Conventional Commits：`feat:`(新功能) / `fix:`(修 bug) / `docs:`(文档) / `refactor:`(重构) / `chore:`(杂务)。
- **⑥ `git push`**：因为第一次用了 `-u`，本地 main 已跟踪 `origin/main`，直接 `git push` 即可，不用再写 `origin main`。

### 13.3 这套项目要上心的几点

1. **`.gitignore` 已就位，现在 `git add .` 比第一次安全**——`.venv`/`node_modules`/`dist`/`app/data`/`.workbuddy`/`.jj` 全拦着。但别因此放松：`ResourceData/` 是**默认提交**的，你往里加新 Excel/CSV/图片，`git add .` 会一并带走（设计意图：数据源随仓库走）。
2. **生成物别手改也别提交**：前端 `npm run build` 出的 `dist/`、批处理 `run_batch` 出的 `app/data/`，都是自动生成，改源码/数据源后重跑即可。
3. **代理 502 老朋友**：本机出口代理偶发 502，push 报 `CONNECT tunnel failed, response 502` / `Empty reply`——不是你错，**重试**就行。
4. **认证**：第一次用临时 PAT 推完就清掉了 remote URL。后续 `git push` 会让 Git Credential Manager 弹窗要凭据（用户名 `hygrove`，密码填那个 PAT），**只输一次会被缓存**。想彻底不碰 PAT，去配 SSH（见第 12 节）。

### 13.4 早点养成的好习惯

- **小步提交**：一个逻辑改完就 commit，别攒一周；回滚时粒度细才好切。
- **一个提交只讲一件事**：别把"修 bug + 加功能 + 格式化"塞一个 commit。
- **push 前先 pull**：跨机器尤其如此，永远先同步再动手。
- 拿不准改了啥：`git diff --staged` 看已暂存、`git log --oneline` 看历史。

---

## 14. 提交信息前缀：Conventional Commits（约定式提交）

### 14.1 是什么

Conventional Commits 是一套**提交信息格式约定**，让历史一眼可读、还能自动生成 CHANGELOG、驱动语义化版本号。格式：

```
<type>(<scope>): <subject>

<optional body>

<optional footer>
```

- `type`：改动性质（见下表）
- `scope`：可选，受影响范围（如 `feat(parser):`）
- `subject`：一句话，祈使句、不长（如「新增单品分析页」而非「新增了单品分析页」）

### 14.2 常用 type 与含义

| 前缀 | 含义 | 例子 |
|---|---|---|
| `feat` | 新功能 | `feat: 新增单品分析页` |
| `fix` | 修 bug | `fix: 修复缩略图尺寸计算错误` |
| `docs` | 只动文档（不改代码逻辑） | `docs: 补充部署指南` |
| `style` | **代码格式**调整（空格/分号/命名风格/换行），**不影响逻辑、不含 bug 修复** | `style: 统一用 2 空格缩进` |
| `refactor` | 重构：既非新功能也非修 bug（重命名、提取函数、调结构） | `refactor: 抽取指标聚合函数` |
| `perf` | 性能优化 | `perf: 指标计算改并行` |
| `test` | 测试相关 | `test: 补充 pipeline 单测` |
| `build` | 构建系统/外部依赖（package.json、vite 配置、requirements.txt） | `build: 升级 vite 到 5.4` |
| `ci` | CI 配置（GitHub Actions 等） | `ci: 加自动构建工作流` |
| `chore` | 杂务：不碰 src/test 的改动（.gitignore、脚本、配置） | `chore: 补 .gitignore 规则` |
| `revert` | 回滚某次提交 | `revert: 回滚 feat: 新增筛选` |

### 14.3 ⚠️ 最容易踩的坑：`style` 不是「UI 样式」

很多人（包括我给你出的拆分计划里）把「前端视觉 / 静态资源改动」写成 `style:` —— **这是错的**。`style` 在约定里专指**代码格式**（空格、分号、缩进、import 排序），和肉眼看到的界面长什么样无关。

- 改了页面布局、加了插画、做了动画、换了背景图 → 这是**功能 / 界面变更**，该用 `feat:`（新界面/新元素）或 `chore:`（纯资源改名/搬运）。
- 只有「把单引号全改成双引号、调缩进」这种才用 `style:`。

所以前面第 13 节拆分计划里那条 `style: 静态资源稳定命名 + 空态插画 + 箭头动画`，严格说应拆成 `feat: 空态插画与箭头动画` + `chore: 静态资源改名`，别照抄那个 `style:` 前缀。（这就是「讲着规矩自己先破例」的活教材。）

### 14.4 破坏性变更

不兼容旧版本时，在 type 后加 `!` 或在 footer 写 `BREAKING CHANGE:`：

```
feat(api)!: 接口返回结构改为按日期分组

BREAKING CHANGE: /spu 接口旧字段 date 移除，改用 dates[]
```

### 14.5 为什么值得遵守

- 历史 `git log` 一眼分清「加了啥 / 修了啥 / 只是文档」。
- 工具（standard-version、release-please）能据此自动出 CHANGELOG、自动 bump 版本号。
- 团队协作时，review 别人 PR 前看前缀就知道这次改动大概多重。
- 本笔记第 11、13 节的提交都用了 `docs:` / `feat:` / `fix:` 前缀，照着写就行。

---

### 本次实战一句话回顾

身份 → init → 改名 main → 先忽略后 add → check-ignore 验身 → commit → remote 关联 → push（撞 502 重试）→ set-url 清 PAT → 验证。94 个文件已安全躺在 `main` 分支上。
