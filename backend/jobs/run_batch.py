# -*- coding: utf-8 -*-
"""批处理入口脚本：由 cron / Windows 计划任务 / 定时调度调用，每天跑一次。

用法：
  python -m backend.jobs.run_batch          # 在仓库根目录执行（推荐）
  python backend/jobs/run_batch.py          # 或直接跑脚本

重试策略：
  指数退避，最多 config.RETRY_MAX_ATTEMPTS 次。功能保留、默认关闭——
  即代码支持重试，但只有设置环境变量 BATCH_RETRY_ENABLED=true 才真正启用。
  默认关闭的理由：上游源数据本身出错时，重试几乎必然再次失败，
  不如让失败立刻暴露出来由人排查，而不是静默重试几次后才知道。

产物：
  每次运行结束都把结果写入 status.json（供前端「上次更新时间 / 是否成功」展示）。
  进程退出码：成功 0，失败 1，便于计划任务或 shell 判断是否报警。
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# 把仓库根目录塞进 sys.path，这样直接 `python backend/jobs/run_batch.py` 也能导入 backend 包；
# 若不加这行，直接跑脚本时会因「顶层包 backend 不在路径中」报 ModuleNotFoundError
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.jobs import config, pipeline  # noqa: E402
from backend.jobs import db as jobdb  # noqa: E402
from backend.jobs.models import BatchStatus  # noqa: E402


def _write_status(status: BatchStatus) -> None:
    """把批处理运行状态写入 status.json（供前端状态展示读取）。

    Args:
        status: 已构造好的 BatchStatus 对象，包含是否成功、耗时、重试次数、模块列表等。

    Returns:
        None

    Side Effects:
        在 config.DATA_DIR 下写 status.json；目录不存在时自动创建。

    Example:
        >>> _write_status(BatchStatus(success=True, last_run_at=datetime.now(timezone.utc)))
    """
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "status.json").write_text(
        status.model_dump_json(), encoding="utf-8"
    )


def _run_organize() -> tuple[dict | None, float, str]:
    """在入库步骤**之前**跑一次源表整理；⛔ 任何异常都只记录、不向外抛。

    为什么整理失败不能拖垮批处理（ADR-0002）：批处理侧本身有行级去重与唯一约束兜底，
    少归档几个文件只是浪费磁盘，不会污染指标；而无人值守的调度只看 status.json，
    整理的小毛病不该让整天的数据更新失败。容错粒度是**单个文件**，见 `organize.organize_files`。

    默认模式是 `dry-run`（`JD_ORGANIZE` 未设时）：真实目录上什么都不搬，
    要真归档需显式 `JD_ORGANIZE=on`（ADR-0002 / Q13 的「先预览、过目后转自动」）。

    Returns:
        tuple[dict | None, float, str]: (整理报告, 整理耗时秒, 错误信息)。
            失败时报告为 None、错误信息非空；成功时错误信息为空串。

    Example:
        >>> report, seconds, err = _run_organize()   # doctest: +SKIP
        >>> err
        ''
    """
    from backend.jobs import organize  # 延迟导入：避免 import 期就拉起 openpyxl / polars

    t0 = time.time()
    try:
        report = organize.organize_files(config.POP_SOURCE_DIR)
        return report, round(time.time() - t0, 3), ""
    except Exception as e:  # noqa: BLE001 —— 整理自身异常绝不向外抛
        return None, round(time.time() - t0, 3), f"{type(e).__name__}: {e}"


def run_once() -> BatchStatus:
    """执行一次完整批处理，并按配置决定是否重试。

    流程：
      1. 记录起始时间与 attempt 上限（未启用重试时上限为 1）；
      2. 先跑一次源表整理（归档；默认 dry-run），⛔ 整理异常不拖垮本批；
      3. 调用 pipeline.run_all() 跑全部数据源与模块；
      4. 成功则写 status.json 并返回；
      5. 失败则按指数退避等待后重试，全部失败后写失败状态返回。

    Returns:
        BatchStatus: 本次运行结果。success=True 表示批处理完成（message 含模块数量），
            success=False 表示重试耗尽仍失败（message 含最后一次异常信息）。

    Raises:
        本函数**不向外抛异常**：pipeline 的异常被捕获后转成失败状态返回，
        目的是让调度方（计划任务 / cron）通过返回的 success 字段判断，而不是靠异常传播。

    Example:
        >>> status = run_once()
        >>> status.success
        True
    """
    start = time.time()
    started_at = datetime.now(timezone.utc)
    # 未启用重试时强制上限为 1，等价于「只跑一次、失败即返回」
    max_attempts = config.RETRY_MAX_ATTEMPTS if config.RETRY_ENABLED else 1

    last_err = ""
    for attempt in range(1, max_attempts + 1):
        try:
            # 整理（归档）先于入库执行：把冗余源文件搬出源表目录，之后的摄入只读保留集。
            # ⛔ 整理自身异常已被 _run_organize 吞掉——它绝不拖垮这一批（ADR-0002）。
            # 目录布局与 write-ahead 清单见 organize.py；默认 dry-run，不搬任何文件。
            org_report, org_seconds, org_err = _run_organize()
            # ⚠️ 成功路径**不再**把整理摘要拼进 status.message：status.message 会直接显示在
            #    模块页「上次更新：… · <message>」那一行，整理的耗时/搬动份数是运维细节，
            #    对看数据的人只是噪音。它已经由下面那行 print 落进 logs/refresh.log，
            #    运维排查时去日志看即可，不必在 UI 里重复一遍。
            #    失败路径**仍然**拼进去——「整理失败」是用户该知道的异常，
            #    verify_organize_execute 对该提示有断言。
            if org_err:
                print(f"[organize] 整理失败（已跳过，不影响入库）：{org_err}")
                org_summary = f"；整理失败（耗时 {org_seconds}s）：{org_err}"
            else:
                print(f"[organize] mode={org_report['mode']}：计划 {org_report['planned']} 份，"
                      f"搬动 {org_report['moved']} 份，失败 {org_report['failed']} 份；"
                      f"整理耗时 {org_seconds}s（整批耗时增量 ≈ {org_seconds}s）")
                org_summary = ""  # 成功时不进 message（理由见上方注释）

            # 增量摄入：先把 Excel/CSV 清洗后写入 SQLite（daily_detail + processed_file 哈希追踪），
            # 再由 pipeline 从 DB 读取干净长表产出 JSON / summary。两步都在同一事务边界内完成，
            # 保证「库里存的」与「页面看到的」永远一致
            db_result = jobdb.ingest_excel_to_db(config.POP_SOURCE_DIR)
            print(f"[db] 摄入：{db_result}")
            manifest = pipeline.run_all()
            status = BatchStatus(
                last_run_at=started_at,
                success=True,
                attempts=attempt,
                duration_seconds=round(time.time() - start, 2),
                message=f"成功：{len(manifest.modules)} 个模块，DB {db_result['rows']} 行{org_summary}",
                source_dir=str(config.POP_SOURCE_DIR),
                modules=[m.module_id for m in manifest.modules],
            )
            _write_status(status)
            return status
        except Exception as e:  # noqa: BLE001
            # 捕获所有异常：批处理是无人值守的后台任务，任何异常都必须落到 status.json，
            # 否则前端「上次更新状态」会一直停留在上一次的旧结果，误导使用者
            last_err = f"{type(e).__name__}: {e}"
            traceback.print_exc()
            # 还有剩余次数才等待重试；最后一次失败直接跳出循环，避免无谓地 sleep
            if attempt < max_attempts:
                delay = config.RETRY_BASE_DELAY * (2 ** (attempt - 1))  # 指数退避：2s, 4s, 8s...
                print(f"[retry] 第 {attempt} 次失败：{last_err}，{delay:.1f}s 后重试")
                time.sleep(delay)

    status = BatchStatus(
        last_run_at=started_at,
        success=False,
        attempts=max_attempts,
        duration_seconds=round(time.time() - start, 2),
        message=f"失败：{last_err}",
        source_dir=str(config.POP_SOURCE_DIR),
    )
    _write_status(status)
    return status


if __name__ == "__main__":
    result = run_once()
    # 先 model_dump_json 再 json.loads，是为了借助 json.dumps 的 ensure_ascii=False 正确输出中文
    print(json.dumps(json.loads(result.model_dump_json()), ensure_ascii=False, indent=2))
    # 退出码供外部调度判断：0=成功，1=失败
    sys.exit(0 if result.success else 1)
