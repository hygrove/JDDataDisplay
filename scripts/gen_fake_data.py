# -*- coding: utf-8 -*-
"""生成真实量级假数据（默认 10 万行）用于压测。

不经过 Excel，直接生成统一长表 -> 走与正式批处理相同的 transforms/pipeline 导出 JSON，
产物覆盖 backend/app/data/ 下的模块文件（模块 id 为 fake_stress，不影响真实模块）。

用法（仓库根目录，venv 环境）：
  python scripts/gen_fake_data.py              # 10 万行
  python scripts/gen_fake_data.py --rows 500000
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import polars as pl  # noqa: E402

from backend.jobs import config, transforms  # noqa: E402
from backend.jobs.models import Manifest, ModuleManifest  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

SHOPS = ["官方旗舰店", "钻芯旗舰店", "自营专卖店", "企业购店铺"]
CATEGORIES = ["净水器", "生活电器配件", "饮水机", "前置过滤器"]
NAMES = ["钻芯 净水器家用直饮RO反渗透纯水机", "钻芯 通用滤芯10寸PP棉活性炭",
         "钻芯 制冰机前置过滤器商用奶茶店", "钻芯 家用直饮加热一体机"]


def gen_long_df(rows: int, seed: int = 42) -> pl.DataFrame:
    rng = random.Random(seed)
    start = date(2026, 8, 14)
    days = [str(start + timedelta(days=i)) for i in range(31)]
    n_spu = max(50, rows // (len(SHOPS) * len(days)))  # 每店铺每天若干 SPU
    spus = [str(10_000_000_000_000 + i) for i in range(n_spu)]

    data = {
        "shop": [], "date": [], "spu": [], "spu_name": [], "category": [],
        "visitors": [], "buyers": [], "items": [], "amount": [],
        "promotion_cost": [], "promotion_amount": [],
    }
    for _ in range(rows):
        shop = rng.choice(SHOPS)
        spu = rng.choice(spus)
        visitors = rng.randint(0, 2000)
        buyers = rng.randint(0, max(1, visitors // 5)) if visitors else 0
        items = buyers + rng.randint(0, 3)
        amount = round(items * rng.uniform(20, 800), 2)
        # 约 30% 的行有推广数据，其余 None（验证 -- 展示）
        if rng.random() < 0.3:
            cost = round(amount * rng.uniform(0.05, 0.4), 2)
            p_amount = round(cost * rng.uniform(1.0, 8.0), 2)
        else:
            cost = p_amount = None
        data["shop"].append(shop)
        data["date"].append(rng.choice(days))
        data["spu"].append(spu)
        data["spu_name"].append(rng.choice(NAMES) + f" {spu[-4:]}款")
        data["category"].append(rng.choice(CATEGORIES))
        data["visitors"].append(float(visitors))
        data["buyers"].append(float(buyers))
        data["items"].append(float(items))
        data["amount"].append(amount)
        data["promotion_cost"].append(cost)
        data["promotion_amount"].append(p_amount)
    return pl.DataFrame(data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=100_000)
    args = parser.parse_args()

    module_id = "fake_stress"
    print(f"生成 {args.rows:,} 行假数据…")
    df = gen_long_df(args.rows)

    module_data = transforms.build_module_data(module_id, df, {})
    summary = transforms.build_summary(module_id, df, {}, top_n=10)

    config.MODULES_DIR.mkdir(parents=True, exist_ok=True)
    (config.MODULES_DIR / f"{module_id}.json").write_text(module_data.model_dump_json(), encoding="utf-8")
    (config.MODULES_DIR / f"{module_id}.summary.json").write_text(summary.model_dump_json(), encoding="utf-8")

    # 合并进 manifest（保留已有模块）
    manifest_path = config.DATA_DIR / "manifest.json"
    if manifest_path.exists():
        manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        manifest.modules = [m for m in manifest.modules if m.module_id != module_id]
    else:
        manifest = Manifest(generated_at=datetime.now(timezone.utc), modules=[])
    manifest.modules.append(ModuleManifest(
        module_id=module_id,
        title="压测假数据",
        description="gen_fake_data.py 生成的 10 万行量级假数据，用于验证虚拟滚动与分页性能。",
        shops=sorted(df["shop"].unique().to_list()),
        date_range=module_data.date_range,
        spu_count=df.select(["shop", "spu"]).unique().height,
        row_count=df.height,
        updated_at=module_data.updated_at,
    ))
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    print(f"完成：{module_id} 模块 {df.height:,} 行 / "
          f"{df.select(['shop','spu']).unique().height:,} 个 SPU，已写入 manifest。")


if __name__ == "__main__":
    main()
