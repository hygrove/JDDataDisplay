# -*- coding: utf-8 -*-
"""库存数据手动刷新端点：只重跑**库存**链路，不碰 POP 单品数据。

为什么单独一个文件（而不是塞进 inventory_api.py）：
`inventory_api.py` 是**纯查询**模块——每个端点只读库、拼响应，不做任何写入。
本模块要写库（摄入快照、删旧快照），混进去会让那个模块的职责边界失守。
分开后「哪些端点会写库」一眼可见。

与 `POST /api/refresh` 的区别（用户明确要求「只刷新库存数据，单品数据不管」）：
`/api/refresh` 走 `run_batch.run_once()`，一次做完：源表整理 → POP 明细摄入 →
库存摄入 → ERP 映射 → 缩略图 → 库存快照淘汰 → SPU 图片同步。POP 明细摄入
要解析全部销售明细表，是「慢」的主因，而库存页根本不需要它。
本端点只跑库存那四步，故耗时从数十秒降到秒级。

执行顺序与理由：
    1. plan_keep_set()            只读探测「该保留哪些快照」，不删任何东西
    2. ingest_inventory_to_db()    增量摄入（文件级 sha256 门控：内容没变就跳过）
    3. build_inv_thumbs()          新 SKU 的缩略图（幂等：产物已存在且不比源图旧则跳过）
    4. retention.run()             库存快照淘汰（ADR-0015：每店保留最新一份）

⚠️ **第 4 步是不可逆动作**（删源文件 + 删库里的行）。因此这里**复用项目既有的
`resolve_mode()` 口径**：默认 `dry-run`（只出清单不动手），真删必须显式设置
环境变量 `JD_INVENTORY_RETENTION=on`。理由与 organize 一致（ADR-0002）：
不可逆动作的权限级别不该由一个 UI 按钮悄悄决定，但「淘汰这一步确实跑了」——
dry-run 同样产出保留/待删决策与报告，运维可据此核对。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

# 并发闸门：SQLite 是单写者模型，手动点击与 23:15 计划任务撞车时
# 两个进程同时写会撞 "database is locked"。这里用进程内锁挡住**本进程内**的重复点击；
# 跨进程（计划任务）无法用进程内锁挡住，故真正兜底靠 SQLite 自身的写锁与
# 批处理侧的容错（失败只告警、不中断整批）。
_REFRESH_LOCK = threading.Lock()


class InventoryRefreshResult(BaseModel):
    """库存刷新结果（前端据此渲染状态胶囊）。

    Attributes:
        ok: 是否整体成功。四个步骤里**淘汰失败不影响 ok**（淘汰失败只是旧快照多留几天，
            库存数据仍然正确——与批处理侧同一口径）；摄入失败才算失败。
        reason: 结果短语，供前端直接显示（"已是最新数据" / "源目录无库存文件" 等）。
        changed: 本次是否有文件内容发生变化（False = 源表没动过，秒回）。
        files: 探测到的库存源文件数。
        rows: 本次写入库的行数。
        changed_files: 实际发生变更并重写了库的文件数。
        shop_rows: 每店本次写入行数（{店铺: 行数}）。
        retention_mode: 淘汰步骤实际使用的模式（"dry-run" / "on"）。
        retention_kept: 淘汰决策里保留的文件数。
        retention_dropped: 淘汰决策里待删的文件数。
        thumbs: 本次新生成的缩略图文件数。
        duration_seconds: 本次刷新总耗时（秒）。
        steps: 逐步骤明细（每步的耗时与结果摘要），便于排查卡在哪一步。
        error: 失败原因；成功时为空串。
    """

    ok: bool
    reason: str
    changed: bool
    files: int
    rows: int
    changed_files: int
    shop_rows: dict[str, int]
    retention_mode: str
    retention_kept: int
    retention_dropped: int
    thumbs: int
    duration_seconds: float
    steps: list[dict[str, Any]]
    error: str = ""


def _run_refresh() -> dict[str, Any]:
    """执行库存刷新的四个步骤，返回结果字典（不抛异常）。

    ⛔ **重依赖延迟导入**：openpyxl / polars 只在真正刷新时才加载，
    避免「仅启动 API 服务」就拉起整条批处理依赖链（与 `api.py` 的 /api/refresh 同一约定）。

    ⚠️ **失败分级**：摄入失败 → 整次刷新失败（数据没更新，页面刷新了也是旧的）；
    淘汰失败 → 只告警不影响 ok（与批处理侧同一口径：旧快照多留几天，库存数据仍正确）。

    Returns:
        dict[str, Any]: 结果字典，字段与 InventoryRefreshResult 一致。

    Example:
        >>> _run_refresh()["ok"] in (True, False)
        True
    """
    from ..jobs import inv_pics as inv_pics_mod
    from ..jobs import inventory as inventory_mod
    from ..jobs import inventory_retention as retention_mod

    t_all = time.time()
    steps: list[dict[str, Any]] = []

    # ---- 步骤 1：探测保留集（只读，不删任何东西）----
    t0 = time.time()
    try:
        keep_files = retention_mod.plan_keep_set()
        steps.append({"step": "plan_keep_set", "ok": True,
                      "seconds": round(time.time() - t0, 3),
                      "detail": f"保留集 {len(keep_files)} 份"})
    except Exception as e:  # noqa: BLE001 —— 探测失败则退回全量摄入，不阻断刷新
        keep_files = None
        steps.append({"step": "plan_keep_set", "ok": False,
                      "seconds": round(time.time() - t0, 3),
                      "detail": f"{type(e).__name__}: {e}（退回全量摄入）"})

    # ---- 步骤 2：增量摄入（文件级 sha256 门控：内容没变就跳过）----
    t0 = time.time()
    try:
        inv_result = inventory_mod.ingest_inventory_to_db(only_files=keep_files)
    except Exception as e:  # noqa: BLE001 —— 摄入失败 = 数据没更新，整次刷新算失败
        steps.append({"step": "ingest", "ok": False,
                      "seconds": round(time.time() - t0, 3),
                      "detail": f"{type(e).__name__}: {e}"})
        return {
            "ok": False,
            "reason": "库存数据摄入失败",
            "changed": False,
            "files": len(keep_files) if keep_files is not None else 0,
            "rows": 0,
            "changed_files": 0,
            "shop_rows": {},
            "retention_mode": "skipped",
            "retention_kept": 0,
            "retention_dropped": 0,
            "thumbs": 0,
            "duration_seconds": round(time.time() - t_all, 3),
            "steps": steps,
            "error": f"{type(e).__name__}: {e}",
        }
    steps.append({"step": "ingest", "ok": True,
                  "seconds": round(time.time() - t0, 3),
                  "detail": (f"{inv_result.get('changed_files', 0)} 份变更 / "
                             f"{inv_result.get('files', 0)} 份探测，写入 "
                             f"{inv_result.get('rows', 0)} 行")})

    # ---- 步骤 3：库存 SKU 缩略图（幂等；新 SKU 才有新增）----
    t0 = time.time()
    thumb_count = 0
    try:
        thumb_count = inv_pics_mod.build_inv_thumbs()
        steps.append({"step": "thumbs", "ok": True,
                      "seconds": round(time.time() - t0, 3),
                      "detail": f"新增 {thumb_count} 个"})
    except Exception as e:  # noqa: BLE001 —— 缩略图失败只告警：缺图只是行里回落占位图
        steps.append({"step": "thumbs", "ok": False,
                      "seconds": round(time.time() - t0, 3),
                      "detail": f"{type(e).__name__}: {e}（已跳过）"})

    # ---- 步骤 4：库存快照淘汰（ADR-0015）----
    # ⚠️ 不可逆动作（删源文件 + 删库行）。模式复用 resolve_mode()：
    #    默认 dry-run（只出清单），真删须显式 JD_INVENTORY_RETENTION=on。
    #    失败只告警、不影响 ok —— 淘汰失败只是旧快照多留几天，库存数据仍然正确。
    t0 = time.time()
    ret_mode = "dry-run"
    ret_kept = 0
    ret_dropped = 0
    try:
        ret_result = retention_mod.run()
        ret_mode = str(ret_result.get("mode", "dry-run"))
        # ⚠️ decision 是 dataclass 实例（Decision），**不是 dict** → 用属性访问，
        #    别写 .get()（会抛 AttributeError: 'Decision' object has no attribute 'get'）。
        decision = ret_result.get("decision")
        ret_kept = sum(len(v) for v in decision.keep.values()) if decision else 0
        ret_dropped = len(decision.drop) if decision else 0
        steps.append({"step": "retention", "ok": True,
                      "seconds": round(time.time() - t0, 3),
                      "detail": f"模式 {ret_mode}，保留 {ret_kept} 份，待删 {ret_dropped} 份"})
    except Exception as e:  # noqa: BLE001 —— 淘汰失败绝不拖垮整次刷新
        steps.append({"step": "retention", "ok": False,
                      "seconds": round(time.time() - t0, 3),
                      "detail": f"{type(e).__name__}: {e}（已跳过）"})

    changed = bool(inv_result.get("changed"))
    files = int(inv_result.get("files", 0))
    rows = int(inv_result.get("rows", 0))
    changed_files = int(inv_result.get("changed_files", 0))

    # 结果短语：区分「源目录没文件」「源表没动过」「有更新」三种，前端据此显示不同胶囊文案。
    if files == 0:
        reason = "源目录没有库存文件，请确认已放入库存明细表"
    elif not changed:
        reason = "已是最新数据，源表内容未变化"
    else:
        reason = f"已更新 {changed_files} 个文件 · {rows:,} 行"

    return {
        "ok": True,
        "reason": reason,
        "changed": changed,
        "files": files,
        "rows": rows,
        "changed_files": changed_files,
        "shop_rows": dict(inv_result.get("shop_rows", {}) or {}),
        "retention_mode": ret_mode,
        "retention_kept": ret_kept,
        "retention_dropped": ret_dropped,
        "thumbs": thumb_count,
        "duration_seconds": round(time.time() - t_all, 3),
        "steps": steps,
        "error": "",
    }


@router.post("/refresh", response_model=InventoryRefreshResult)
def refresh_inventory() -> InventoryRefreshResult:
    """手动刷新库存数据：重跑库存摄入 + 缩略图 + 快照淘汰（**不碰 POP 单品数据**）。

    与 `POST /api/refresh` 的区别：后者会重跑整条批处理（含 POP 明细摄入，
    数十秒）；本端点只跑库存那几步，耗时秒级。

    ⛔ **并发保护**：SQLite 单写者模型下，两个写入者撞车会 "database is locked"。
    本进程内用 `_REFRESH_LOCK` + `_refreshing` 标志挡住重复点击——第二次点击
    立刻返回"正在刷新中"，**不排队**（排队会把 HTTP 请求拖成长连接）。
    跨进程（与 23:15 计划任务撞车）挡不住，靠 SQLite 写锁与批处理侧容错兜底。

    Returns:
        InventoryRefreshResult: 刷新结果。`ok=False` 表示摄入失败（数据没更新）；
            淘汰失败不影响 ok（只体现在 steps 里）。

    Raises:
        HTTPException: 503 —— 已有刷新在执行中（前端应提示"正在刷新中"）。

    Example:
        POST /api/inventory/refresh  ->  {"ok": true, "reason": "已是最新数据，源表内容未变化", ...}
    """
    # 非阻塞获取锁：拿不到说明有人在刷，直接回 503，不让请求排成队。
    if not _REFRESH_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="正在刷新中，请稍候")
    try:
        result = _run_refresh()
    finally:
        _REFRESH_LOCK.release()
    return InventoryRefreshResult(**result)
