# -*- coding: utf-8 -*-
"""批处理入口：每天一次（cron / Windows 计划任务 / APScheduler 调用本脚本）。

用法：
  python -m backend.jobs.run_batch          # 在仓库根目录执行
  python backend/jobs/run_batch.py          # 或直接跑脚本

重试机制：指数退避，最多 N 次。按方案要求功能保留、默认关闭，
通过环境变量 BATCH_RETRY_ENABLED=true 开启。结果写入 status.json。
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# 支持直接 `python backend/jobs/run_batch.py` 运行
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.jobs import config, pipeline  # noqa: E402
from backend.jobs.models import BatchStatus  # noqa: E402


def _write_status(status: BatchStatus) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "status.json").write_text(
        status.model_dump_json(), encoding="utf-8"
    )


def run_once() -> BatchStatus:
    start = time.time()
    started_at = datetime.now(timezone.utc)
    max_attempts = config.RETRY_MAX_ATTEMPTS if config.RETRY_ENABLED else 1

    last_err = ""
    for attempt in range(1, max_attempts + 1):
        try:
            manifest = pipeline.run_all()
            status = BatchStatus(
                last_run_at=started_at,
                success=True,
                attempts=attempt,
                duration_seconds=round(time.time() - start, 2),
                message=f"成功：{len(manifest.modules)} 个模块",
                source_dir=str(config.POP_SOURCE_DIR),
                modules=[m.module_id for m in manifest.modules],
            )
            _write_status(status)
            return status
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            traceback.print_exc()
            if attempt < max_attempts:
                delay = config.RETRY_BASE_DELAY * (2 ** (attempt - 1))  # 指数退避
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
    print(json.dumps(json.loads(result.model_dump_json()), ensure_ascii=False, indent=2))
    sys.exit(0 if result.success else 1)
