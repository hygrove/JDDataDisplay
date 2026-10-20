# 数据上云 · 可照抄传输清单

> 配套主教程：`DOCKER_DEPLOY_GUIDE.md` 第 5 节。本文是「把本地项目 + 真实业务数据上传到腾讯云轻量服务器」的**独立、可直接照抄**步骤清单，可脱离正文单独使用。

---

## 0. 这套流程解决什么

- 项目里 `.git` 历史、`node_modules`、`frontend/dist` 等体量巨大且是海量小文件，**直接整目录上传会超时**。
- `ResourceData/`（真实业务数据）被 `.gitignore` 忽略，`git clone` 拿不到，**必须单独随项目传输**。
- 目标：只传「源码 + Docker 配置 + ResourceData」，到服务器后由 `docker build` 重新生成依赖与构建产物。

---

## 1. 前置确认

- 已在 CodeBuddy 连接**腾讯云 Lighthouse 集成**，且能看到实例 `lhins-ca3q5aa5`（广州 `ap-guangzhou`）。
- 本地 `e:/JDDataDisplay/ResourceData/` 存在且为真实数据。
- 本地已装 PowerShell（Windows 自带）。

---

## 2. 步骤一：本地生成「精简部署副本」（PowerShell）

打开 PowerShell，执行下面整段（只改第一行两个路径即可）：

```powershell
# ① 生成排除依赖/历史/生成物的精简副本 JDDeploy
robocopy e:/JDDataDisplay e:/JDDeploy /E `
  /XD .git node_modules __pycache__ .venv .scratch data dist `
  /XF *.pyc *.pyo *.tsbuildinfo

# ② 核对副本体积（预期约 80~90MB，其中 ResourceData 占约 31MB）
$s = (Get-ChildItem e:/JDDeploy -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
'JDDeploy size MB: ' + [math]::Round($s/1MB,1)

# ③ 确认关键文件都在
Get-ChildItem e:/JDDeploy -Force | Select-Object Name
```

**排除项说明（照抄即可，无需改）：**

| 排除 | 原因 |
|---|---|
| `.git` | 版本历史，部署不需要 |
| `node_modules` / `frontend/dist` | 前端依赖与产物，镜像内 `npm ci` + `vite build` 会重建 |
| `__pycache__` / `.venv` / `*.pyc` / `*.pyo` | Python 运行时产物 |
| `.scratch` | 本地 issue tracker（已被 gitignore） |
| `data` | 本地生成数据，云端 `run_batch` 重建 + 卷持久化 |

**保留（关键）：** `ResourceData/`、`Dockerfile`、`docker/`、`docker-compose.yml`、`.env`、前后端源码。

> 若你的项目路径不同，只改第①行 `e:/JDDataDisplay` 和 `e:/JDDeploy` 两个路径。

---

## 3. 步骤二：上传到云服务器

通过 CodeBuddy 的 **腾讯云 Lighthouse 集成 → `deploy_project_preparation`** 执行（这是集成工具调用，不是终端命令，在对话里给集成传参）：

```
deploy_project_preparation(
  FolderPath  = "e:/JDDeploy",
  InstanceId  = "lhins-ca3q5aa5",
  Region      = "ap-guangzhou",
  ProjectName = "JDDataDisplay"
)
```

- 上传的是**精简副本** `e:/JDDeploy`，几秒完成。
- 服务器落地路径（带时间戳，每次不同）：`/root/JDDeploy_<YYYYMMDDHHMMSS>/`

> **纯命令行备选**（脱离 IDE 也能传，需服务器 SSH 22 已开，默认开）：
> ```bash
> scp -r /e/JDDeploy root@1.12.221.101:/root/JDDeploy
> ```
> 或用腾讯云 COS + `coscmd` 中转。集成上传最省心，推荐前者。

---

## 4. 步骤三：服务器侧验证上传完整性

在服务器（通过 Lighthouse `execute_command`）执行：

```bash
cd /root/JDDeploy_*        # 用 Tab 补全实际时间戳目录
echo "== 顶层 =="      && ls -la
echo "== 关键部署文件 ==" && ls -la Dockerfile docker-compose.yml .env docker/
echo "== 数据目录 =="  && ls ResourceData && du -sh ResourceData
```

**预期：** 能看到 `Dockerfile`、`docker/`、`ResourceData`（约 31MB）齐全。

---

## 5. 常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 上传超时 / 一直转圈 | 直接传了 `.git` + 依赖 + 海量小文件 | 先用 robocopy 生成精简副本再传 |
| 服务器没有数据 | 以为 `ResourceData` 随 git 走 | 它已被 `.gitignore` 忽略，必须单独传（本清单已保留） |
| 上传后目录名带时间戳找不到 | 正常行为 | `cd /root/JDDeploy_*` 或 `ls /root/ \| grep JDDeploy` 定位 |
| 服务器 Docker 拉基础镜像慢 | 未配镜像加速器 | 腾讯云轻量已内置 `mirror.ccs.tencentyun.com`，无需手动配 |

---

## 6. 一页速查（TL;DR）

```
本地:   robocopy 项目 → JDDeploy(精简) → 核对 ~80MB
上传:   Lighthouse deploy_project_preparation(FolderPath=JDDeploy)
服务器: cd /root/JDDeploy_* → ls 确认 Dockerfile/docker/ResourceData 在
```

照抄上面 3 个步骤即可完成数据上云。
