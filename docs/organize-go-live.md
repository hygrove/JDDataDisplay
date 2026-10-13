# 源表整理器（organize）· 首次上线与运维手册

> 适用对象：负责跑批 / 维护 `ResourceData` 数据源的负责人。
> 目标：让你在**不改任何代码**的前提下，安全完成「首次归档」并处理日常运维。
> 设计依据：`docs/adr/0001..0007`、`CONTEXT.md`、`.scratch/organize/spec.md`。

---

## 0. 一句话认知：整理只改文件位置，不改数据

整理器在每次批处理**入库之前**跑一次：把**冗余**源文件（其数据 key 都能在同店同类的「保留集」里找到来源）
搬进**归档区**；把**形态异常**文件搬进**隔离区**；被隔离文件若持有独有 key 则发 **ERROR 哨兵告警**（只报警、不阻断）。
之后批处理照常跑，`daily_detail` 的每一格都必须**不变**（验收闸门 `verify_organize_acceptance.py` 逐格比对）。

- 归档区：`ResourceData/_archive/{YYYY-MM-DD}/`（源表目录**之外**，可回滚，满 60 天进系统回收站）
- 隔离区：`ResourceData/_quarantine/`（源表目录**之外**，等**人**处理，可回滚，⛔ 永不参与 60 天清理）
- 报告：`logs/organize/report-{YYYY-MM-DD}.md`（人读）+ `.json`（机读）

---

## 1. 🔴 安全默认：不设环境变量 = 什么都不搬

整理模式 `JD_ORGANIZE` 的默认值是 **`dry-run`**：只出清单与报告，**不搬文件、不写清单、不动源表目录**。
真实目录上「什么都不搬」是默认行为——首次归档是整个设计里规模最大的一次不可逆动作，
必须先由人工过目（ADR-0002 / Q13）。

| 模式 | 取值 | 行为 |
|---|---|---|
| 关闭 | `JD_ORGANIZE=off` | 整段跳过（连决策都不算） |
| 预览（默认） | 不设 / `JD_ORGANIZE=dry-run` | 只算决策 + 哨兵 + 断档 + 清理候选，**零搬动、零清单**，产报告 |
| 真搬 | `JD_ORGANIZE=on` | 把 archive 决策的文件搬进 `_archive/`、quarantine 决策的搬进 `_quarantine/` |

⛔ 非法值一律回退到 `dry-run`（安全侧），拼错模式不会误触发搬移。

---

## 2. 首次上线：三步走（dry-run → 人工过目 → 转自动）

### 步骤 1 · 出 dry-run 清单

在**当前数据**上重跑（数字每天会漂移，⛔ 别拿旧数字核）：

```bash
# 只读：打印每个文件的决策 + 落一份机读清单。⛔ 不搬任何文件。
.venv/Scripts/python.exe -m backend.jobs.organize --source ResourceData \
    --out logs/organize/decisions-<今天>.json
```

- **输出落点**：stdout（逐文件决策 + 统计行）+ `logs/organize/decisions-<今天>.json`（机读）。
- 想要**五段人读报告**：跑一次批处理即可（默认 `dry-run` 也会产报告，见 §4）：
  `logs/organize/report-<今天>.md`。

最终一行是决策统计，形如：

```
--- 决策统计：keep 16 / archive 93 / quarantine 0 / skip 4（共 113 个文件）---
```

### 步骤 2 · 人工过目要核对什么

逐项确认下面 4 件事，任一项存疑就先别转自动：

1. **归档份数是否都是冗余**：`archive` 的文件，其每个 `(店铺, 日期, SPU)` 都能在**保留集**里找到来源
   （清单里 `covered_by` 给出样例来源文件）。看一眼是否都是滚动窗里被新旧文件盖住的那批。
2. **隔离份数是否合理**：`quarantine` 应为 0（当前真实数据如此）。若 >0，读报告「隔离」段点名了哪些文件、
   为什么（列不符 / 日期窗解析不出 / 跨月怪象）——这些是**等人判断**的，不是被清理的。
3. **哨兵是否有告警**：报告「哨兵告警」段非空 = 某份被隔离文件持有保留集里找不到来源的独有 key
   （那批数据会变成历史缺口或静默陈旧）。有告警必须先处理，别转自动。
4. **断档是否是已知的下载缺失**：报告「断档」段列出日期轴上没人覆盖的区间（只报告、零动作）。
   当前已知 3 段，均为**卡求旗舰店 / promo** 各 1 天（`2026-09-10`、`2026-09-26`、`2026-09-29`）。
   只要是你已知的「那几天没下载」，就属正常；不是已知缺失才需要追查。

> ⚠️ 当前（2026-10-12 会话时点）dry-run 结果：`keep 16 / archive 93 / quarantine 0 / skip 4`，
> 断档 3 段、哨兵 0 条。**数字会随每日新文件漂移**，过目时以当场重跑为准。

### 步骤 3 · 确认后，同一份代码转自动

整理**不需要手动执行**：它挂在 `run_batch.run_once()` 的入库步骤之前，每次跑批自动跑一次。
转自动只是**改一个开关**：

```bat
:: Windows cmd（当前进程/计划任务的环境变量）
set JD_ORGANIZE=on
python -m backend.jobs.run_batch
```

```powershell
# PowerShell
$env:JD_ORGANIZE = "on"
python -m backend.jobs.run_batch
```

生产里由计划任务 / cron 调用 `run_batch`，所以**上线只需在调度环境里持久化设好 `JD_ORGANIZE=on`**，
之后每天自动整理，无需人工介入。若要**单次**手动真搬（例如首次上线后手动补一次），用同一份代码：

```bash
.venv/Scripts/python.exe -m backend.jobs.organize --source ResourceData --mode on
```

> ⚠️ 首次真搬后，批处理会**多出一次整表重算**（门控被触发）——这是**预期行为**（ADR-0007），
> 不要去为它加豁免。整理不改变任何指标值，只是让摄入少读若干冗余文件。

---

## 3. 怎么撤回（回滚）

回滚是**跨目录**操作：把归档区 `_archive/` **或隔离区** `_quarantine/` 里的文件搬回源表目录的原位。
**同一条命令同时覆盖两条通道**（归档区 + 隔离区），按批次或按单个文件都行：

```bash
# 按批次回滚归档（批次目录名 = 归档日期，如 2026-10-12；隔离区无批次概念，不参与）
.venv/Scripts/python.exe -m backend.jobs.organize --source ResourceData --rollback 2026-10-12

# 按文件回滚（给文件名或相对路径，跨「归档全部批次 + 隔离清单」匹配）
.venv/Scripts/python.exe -m backend.jobs.organize --source ResourceData \
    --rollback "钻芯旗舰店_推广数据_2026-09-10_2026-10-10.csv"

# 回滚归档全部批次 + 隔离清单里还没回滚的条目（空串）
.venv/Scripts/python.exe -m backend.jobs.organize --source ResourceData --rollback ""
```

- 回滚**不覆盖**原位已有文件：若原位与归档 / 隔离副本**同时存在**，记为 `conflicts`（数据两侧都在，不丢），
  需要你手工决定留哪份。
- 归档 / 隔离副本已不在（`failed` / 尚未搬 / 归档已被 60 天清理）记为 `missing`。
- 回滚会打印**来源拆分**（归档区搬回几份 / 隔离区搬回几份），每条结果也标了来源是 `archive` 还是 `quarantine`。
- 两份清单**互不干扰**：回滚隔离文件只改 `_quarantine/manifest.json`，归档清单**一字不动**（反之亦然）。
  两份清单是各自唯一的追溯依据，不会被对方悄悄改写。

### 隔离区也能回滚（对称）

隔离区原本**没有** `--rollback` 通道——误隔离的文件只能人工从 `ResourceData/_quarantine/` 拖回
源表目录。这曾是一条**不对称**的局限：隔离恰恰是最可能误判的一类（形态判定偏保守、哨兵探针刻意宽松），
越容易误判的一侧反而越难撤回，而且人工搬回**没有清单状态记录、事后无法审计**。
现已修正：`rollback()` 同时遍历归档清单与隔离清单，误隔离的文件用**同一条命令**即可精确撤回，
隔离清单条目状态同步改为 `rolled_back`。据此，§7 的「某文件误隔离」一行也已从「人工搬回」改为
`--rollback <文件名>`。

---

## 4. 回滚依赖的清单（⛔ 别删）

- 归档清单：`ResourceData/_archive/{批次}/manifest.json` —— 那批归档**唯一**的追溯与回滚依据。
  每条含 `{source_rel 原相对路径, archive_rel 归档后相对路径, reason 判定理由, batch_time 批次时间, status}`，
  `status`：`planned → done`（搬成功）/ `failed`（搬失败）/ `rolled_back`（已回滚）。
- 隔离清单：`ResourceData/_quarantine/manifest.json` —— 隔离区**唯一**的追溯与回滚依据（平铺、不分批次）。
  条目形状与归档清单**同构**，只是目的地字段是 `quarantine_rel`、`status` 同样含 `rolled_back`。
- 清理账本：`ResourceData/_archive/cleanup.json` —— 60 天清理的 write-ahead 账本（与归档清单**分开**）。
- 🔴 **清单缺失或损坏 ⇒ 那批归档 / 那批隔离等同于不可回滚**（ADR-0002 / 0003）。
  ⛔ 不要删、不要覆盖 `manifest.json` / `cleanup.json`；备份 `ResourceData` 时把它们一起带上。

---

## 5. 60 天后的行为：进**系统回收站**，不是硬删

- 保留期从**归档日期**起算（批次目录名 `YYYY-MM-DD`）。口径：`已存天数 >= 60` 即清理
  （第 60 天到 = 满 60 天；59 天保留）。
- 清理**逐个**把超期批次里的文件送进 **Windows 系统回收站**（stdlib `ctypes` 调 `SHFileOperationW` + `FOF_ALLOWUNDO`），
  **可还原**。⛔ 不是硬删除。
- **降级**：回收站不可用（网络卷 / 被禁用）时，本次只在报告里列出（`deferred`），⛔ 不硬删、不中断批处理。
- ⚠️ **回收站容量驱逐风险（必须知情）**：回收站有容量上限，超出后 Windows 会**永久删除回收站里最旧的条目**。
  批量清理可能把更早的、与本功能无关的文件一并挤出——这是本功能**唯一**波及归档区之外的副作用，
  报告「回收站清理」段每次都带上这句提醒。

---

## 6. 报告在哪看

| 产物 | 路径 | 内容 |
|---|---|---|
| 五段人读报告 | `logs/organize/report-{批次}.md` | 归档 / 隔离 / 断档 / 哨兵告警 / 回收站清理 |
| 五段机读报告 | `logs/organize/report-{批次}.json` | 与 md **内容一致**（`md == render_report(json)` 可逐字节断言） |
| dry-run 决策清单 | `logs/organize/decisions-<日期>.json` | 每份文件的决策 + 理由 + 覆盖来源样例 |

报告与批处理日志同类位置（`logs/` 已被 `.gitignore` 忽略），不必去两个地方找。五段：

1. **归档**：计划 / 搬动 / 失败份数 + 每条 `原路径 → 归档后路径`
2. **隔离**：同上（隔离区永不参与 60 天清理）
3. **断档**：未被覆盖的日期区间（只报告、零动作）
4. **哨兵告警**：被隔离文件持有独有 key 时的 ERROR 清单
5. **回收站清理**：超期批次 / 待清理 / 已入回收站 / 降级说明 / 容量风险提醒

---

## 7. 常见情况处置

| 现象 | 含义 | 处置 |
|---|---|---|
| 报告写「整理失败（已跳过，不影响入库）」 | 整理自身异常被 `_run_organize` 吞掉 | 入库照常；查 `backend/app/data/status.json` 的 message 定位，修完下一批自动恢复 |
| 报告有 `[failed]` 条目 | 单个文件被占用 / 权限不足，已跳过 | 关掉占用它的程序（Excel 等），下一批会自动重试该文件 |
| 哨兵 ERROR | 被隔离文件持有独有 key | 人工判断：从 `_quarantine/` 搬回，或确认丢弃并接受历史缺口 |
| 某文件误归档 | 误判 | `--rollback <文件名>` 搬回；或按批次 `--rollback <批次>` |
| 某文件误隔离 | 误判 | `--rollback <文件名>` 搬回（同时覆盖归档区与隔离区，§3） |
| 想临时停掉整理 | —— | 设 `JD_ORGANIZE=off`（本批不整理，不动文件） |

---

## 8. 上线前/后自检（可重复执行的验收闸门）

```bash
# 自证闸门有牙齿（防假阳性 + 防假阴性；在临时目录跑，不碰真实目录）
.venv/Scripts/python.exe backend/jobs/verify_organize_acceptance.py selftest

# 全量端到端验收（A=全量源根副本 / B=整理后源根副本，各自新建库逐格比对；真实目录只读）
.venv/Scripts/python.exe backend/jobs/verify_organize_acceptance.py accept ResourceData
```

`accept` 会打印：A/B 两库行数、11 指标列差异数、`processed_file` 前后条数、搬走/保留/隔离/断档/耗时增量。
**只要「daily_detail 逐格不变」与「processed_file 一一对应」两条通过，就可以放心上线。**

---

## 9. 诚实说明：整理**不**是性能优化

- 整理让**摄入步骤本身**变快（少读冗余文件），但整理**自己要先读全部文件**算 key，两者相抵，
  **整批净耗时基本持平、甚至略增**（真实 113 份实测：无整理 ≈ 2.7s，有整理 ≈ 3.8s）。
- 它**真实的价值是目录可读性**：源表目录从上百份瘦身到保留集，人一眼能看清「哪些表还在参与计算」。
- 正确性不受影响（行级去重早已由 `keep="last"` + `UNIQUE` 兜住）。
- ⛔ **不要**用性能向任何人推销整理器。
