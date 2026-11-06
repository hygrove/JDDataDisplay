# -*- coding: utf-8 -*-
"""``/api/ops-daily/*`` 接口验证（独立脚本 + 自制断言，⛔ 不引入 pytest）。

按 ai-rules §3：后端改完必须真导入 + 真发请求验路由（``app.routes`` 里挂的是
``_IncludedRouter`` 条目，只数注册数会误判）。本脚本用 ``TestClient`` 真发 HTTP 请求，
并用**独立公式**（不复用接口 SQL）重算派生值，防止「自己验自己」。

只读保证：前后对 6 张受保护表 + 4 张 ops 表逐表拍全行 sha256，不一致即失败。

运行：``./.venv/Scripts/python.exe -m backend.jobs.verify_ops_daily_api``
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.jobs import config as job_config
from backend.jobs import ops_daily

_passed = 0
_failed: list[str] = []


def check(condition: bool, label: str) -> None:
    """记录一条断言结果。"""
    global _passed
    if condition:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed.append(label)
        print(f"  FAIL  {label}")


def table_hash(conn: sqlite3.Connection, table: str) -> str:
    """对一张表按主键顺序取全行内容的 sha256（用于证明接口零写入）。"""
    h = hashlib.sha256()
    for row in conn.execute(f"SELECT * FROM {table}"):
        h.update(repr(tuple(row)).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def all_hashes() -> dict[str, str]:
    """对 6 张受保护表 + 4 张 ops 表拍指纹。"""
    tables = list(ops_daily.PROTECTED_TABLES) + [
        "ops_shop_daily",
        "ops_metric_dict",
        "ops_ingest_log",
        "ops_row_tombstone",
    ]
    with closing(ops_daily.get_conn()) as conn:
        return {t: table_hash(conn, t) for t in tables}


def independent_derived(
    values: dict[str, Any], pairs: dict[str, tuple[str, str]]
) -> dict[str, float | None]:
    """用 Python 独立重算派生值：分母 > 0 且分子非 None 才除，否则 None。"""
    out: dict[str, float | None] = {}
    for key, (num, den) in pairs.items():
        n, d = values.get(num), values.get(den)
        if d is None or d <= 0 or n is None:
            out[key] = None
        else:
            out[key] = n / d
    return out


def main() -> int:
    """执行全部断言，返回进程退出码（0 全绿 / 1 有失败）。"""
    client = TestClient(app)
    before = all_hashes()

    print("\n[1] meta 端点与字典驱动")
    r_meta = client.get("/api/ops-daily/meta")
    check(r_meta.status_code == 200, "GET /meta 返回 200（未被 SPA 回退吞掉）")
    check(
        "application/json" in r_meta.headers.get("content-type", ""),
        "响应是 JSON 而非 index.html",
    )
    meta = r_meta.json()
    metrics = meta.get("metrics", [])
    check(len(metrics) == 24, f"字典 24 项（实为 {len(metrics)}）")
    check(sum(m["kind"] == "atomic" for m in metrics) == 19, "19 个原子指标")
    check(sum(m["kind"] == "derived" for m in metrics) == 5, "5 个派生指标")
    required = {
        "metric_key", "cn_name", "source_name", "kind", "db_column",
        "formula", "unit", "is_baseline", "sort_order", "precision",
    }
    check(all(required <= set(m) for m in metrics), "每项含 10 个契约字段（含 precision）")
    orders = [m["sort_order"] for m in metrics]
    check(orders == sorted(orders), "sort_order 严格递增，前端列序可依赖")
    check(
        all(m["db_column"] == "" and m["formula"] for m in metrics if m["kind"] == "derived"),
        "派生项 db_column 为空且公式非空",
    )
    check(meta.get("data_through") == "2026-10-31", "数据截至 2026-10-31")
    check(meta.get("data_from") == "2026-08-01", "数据起始 2026-08-01（供前端「全区间」按钮）")
    check(len(meta.get("shops", [])) == 17, "筛选项 17 个店铺")
    check(meta.get("total_rows") == 1564, "meta.total_rows = 1564")

    print("\n[2] 参数校验与分页")
    check(client.get("/api/ops-daily/data").status_code == 422, "缺 from/to → 422")
    check(
        client.get("/api/ops-daily/data", params={"from": "2026/08/01", "to": "2026-10-31"}).status_code == 400,
        "日期格式非法 → 400",
    )
    check(
        client.get("/api/ops-daily/data", params={"from": "2026-10-31", "to": "2026-08-01"}).status_code == 400,
        "from 晚于 to → 400",
    )
    check(
        client.get("/api/ops-daily/data", params={"from": "2026-08-01", "to": "2026-10-31", "shop_type": "xxx"}).status_code == 400,
        "shop_type 非法 → 400",
    )
    base = {"from": "2026-08-01", "to": "2026-10-31"}
    r = client.get("/api/ops-daily/data", params=base)
    check(r.status_code == 200, "GET /data 返回 200")
    data = r.json()
    check(data.get("total") == 1564, f"total = 1564（实为 {data.get('total')}）")
    check(len(data.get("rows", [])) == 200, "默认每页 200 行")
    keys = {m["metric_key"] for m in metrics}
    check(
        all(set(row["values"]) == keys for row in data["rows"]),
        "每行 values 键集合 == 字典 24 个 metric_key",
    )
    page2 = client.get("/api/ops-daily/data", params={**base, "page": 2}).json()
    first_ids = {(x["date"], x["shop"], x["shop_type"]) for x in data["rows"]}
    second_ids = {(x["date"], x["shop"], x["shop_type"]) for x in page2["rows"]}
    check(not (first_ids & second_ids), "第 1 / 2 页无重复行")

    print("\n[3] 三条件筛选与 DB 直查一致")
    # 必须挑一个确实有 pop 行的店铺：若取 shops[0] 恰好是自营店，
    # 「0 == 0」也能通过断言，却完全没验到筛选逻辑（自检时抓到过这个弱断言）。
    shop = next(
        (x["shop"] for x in data["rows"] if x["shop_type"] == "pop"),
        meta["shops"][0],
    )
    filt = {**base, "shop": shop, "shop_type": "pop", "from": "2026-09-01", "to": "2026-09-30"}
    fr = client.get("/api/ops-daily/data", params=filt).json()
    with closing(ops_daily.get_conn()) as conn:
        expected = conn.execute(
            "SELECT COUNT(*) FROM ops_shop_daily "
            "WHERE date >= ? AND date <= ? AND shop = ? AND shop_type = ?",
            ("2026-09-01", "2026-09-30", shop, "pop"),
        ).fetchone()[0]
    check(expected > 0, f"筛选样本非空（{shop} 在 9 月有 {expected} 行），断言不空转")
    check(fr["total"] == expected, f"筛选 total 与 DB 直查一致（{fr['total']} == {expected}）")
    check(
        all(x["shop"] == shop and x["shop_type"] == "pop" for x in fr["rows"]),
        "返回的每一行都符合店铺 + 类型条件",
    )

    print("\n[4] 派生值独立重算（SUM/SUM 与单行粒度）")
    with closing(ops_daily.get_conn()) as conn:
        dict_rows = [dict(x) for x in ops_daily.metric_dict_rows(conn)]
    pairs: dict[str, tuple[str, str]] = {}
    for m in dict_rows:
        if m["kind"] == "derived":
            a, b = m["formula"].split("/")
            pairs[m["metric_key"]] = (a, b)
    row_mismatches = 0
    for row in data["rows"]:
        expect = independent_derived(row["values"], pairs)
        for key, val in expect.items():
            got = row["values"][key]
            if val is None:
                if got is not None:
                    row_mismatches += 1
            elif got is None or abs(got - val) > 1e-9:
                row_mismatches += 1
    check(row_mismatches == 0, f"200 行 × 5 派生与独立公式零误差（不一致 {row_mismatches} 处）")

    # summary = 筛选范围整体先求和再相除，用 DB 直查独立聚合
    with closing(ops_daily.get_conn()) as conn:
        cols = sorted({c for pair in pairs.values() for c in pair})
        sums = dict(
            conn.execute(
                f"SELECT {', '.join(f'SUM({c}) AS {c}' for c in cols)} FROM ops_shop_daily"
            ).fetchone()
        )
    expect_summary = independent_derived(sums, pairs)
    sum_mismatches = 0
    for key, val in expect_summary.items():
        got = data["summary"].get(key)
        if val is None:
            if got is not None:
                sum_mismatches += 1
        elif got is None or abs(got - val) > 1e-9:
            sum_mismatches += 1
    check(sum_mismatches == 0, "summary 整体 SUM/SUM 与独立聚合零误差")
    check(
        data["summary"].get("conversion_rate") is not None,
        "KPI 卡转化率有值（首屏必须出真数据，非空态）",
    )

    print("\n[5] NULL 与 0 分离")
    full = client.get("/api/ops-daily/data", params={**base, "all": "true"}).json()
    null_rows = [x for x in full["rows"] if x["values"]["visitors"] is None]
    check(len(null_rows) > 0, f"存在访客数 NULL 的行（{len(null_rows)} 行）")
    check(
        all(x["values"]["conversion_rate"] is None for x in null_rows),
        "分母 NULL ⇒ 转化率 None（前端渲染 --）",
    )
    zero_rows = [x for x in full["rows"] if x["values"]["sales_amount"] == 0.0]
    check(len(zero_rows) > 0, f"存在销售额真实 0 的行（{len(zero_rows)} 行）")
    check(
        all(x["values"]["sales_amount"] is not None for x in zero_rows),
        "真实 0 返回 0.0 而非 None / --",
    )

    print("\n[6] 导出全量请求")
    check(len(full["rows"]) == full["total"] == 1564, "all=true 返回筛选全量 1564 行")
    check(full["page_size"] == 1564 and full["page"] == 1, "全量模式 page_size 回填真实行数")
    check(
        all(set(x["values"]) == keys for x in full["rows"]),
        "全量行键集合仍与字典一致（导出列序可依赖 meta）",
    )

    print("\n[7] response_model 与 OpenAPI")
    spec = app.openapi()
    check("/api/ops-daily/meta" in spec["paths"], "OpenAPI 含 /meta 路径")
    check("/api/ops-daily/data" in spec["paths"], "OpenAPI 含 /data 路径")
    schemas = spec.get("components", {}).get("schemas", {})
    check("OpsMetricMeta" in schemas and "OpsDailyData" in schemas, "两个响应模型已注册（/docs 响应体不空白）")
    check(
        spec["paths"]["/api/ops-daily/meta"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        .endswith("OpsMetricMeta"),
        "/meta 200 响应绑定 OpsMetricMeta",
    )
    check("OpsMonthlyPoint" in schemas, "月度趋势点模型已注册（方案 C 响应体不空白）")

    print("\n[7.5] 月度趋势点（方案 C 数据源：仍只有两个 GET）")
    monthly = data.get("monthly", [])
    check(len(monthly) == 3, f"全区间 monthly = 3 个月（实为 {len(monthly)}）")
    check(
        [m["month"] for m in monthly] == ["2026-08", "2026-09", "2026-10"],
        "月份升序且格式 YYYY-MM",
    )
    # 独立 SQL：用 LIKE 而不是 substr，避免「接口自己验自己」
    mismatches = 0
    with closing(ops_daily.get_conn()) as conn:
        for m in monthly:
            row = conn.execute(
                "SELECT SUM(sales_amount) AS s, SUM(visitors) AS v "
                "FROM ops_shop_daily WHERE date LIKE ?",
                (m["month"] + "-%",),
            ).fetchone()
            if m["sales_amount"] is None or abs(m["sales_amount"] - row["s"]) > 1e-6:
                mismatches += 1
            if m["visitors"] is None or abs(m["visitors"] - row["v"]) > 1e-6:
                mismatches += 1
            for t, amount in m["by_type"].items():
                one = conn.execute(
                    "SELECT SUM(sales_amount) AS s FROM ops_shop_daily "
                    "WHERE date LIKE ? AND shop_type = ?",
                    (m["month"] + "-%", t),
                ).fetchone()["s"]
                if amount is None or abs(amount - one) > 1e-6:
                    mismatches += 1
            if abs(sum(x or 0 for x in m["by_type"].values()) - (m["sales_amount"] or 0)) > 1e-6:
                mismatches += 1
    check(
        mismatches == 0,
        f"3 个月 ×（合计 / 访客 / 分类型 / 拆分求和）与独立 SQL 零误差（不一致 {mismatches} 处）",
    )
    check(
        all(set(m["by_type"]) == {"pop", "self"} for m in monthly),
        "每月 by_type 键集合为库内全部类型 {pop, self}，前端双折线不错位",
    )
    # 有意口径：类型筛选不收窄图表，否则方案 C 会退化成方案 A
    typed = client.get(
        "/api/ops-daily/data", params={**base, "shop_type": "pop", "page_size": 1}
    ).json()
    check(
        all(set(m["by_type"]) == {"pop", "self"} for m in typed["monthly"]),
        "shop_type=pop 时 monthly 仍含 self 键（结构对比不被筛选破坏）",
    )
    check(page2.get("monthly") == monthly, "翻到第 2 页 monthly 不变（不受分页影响）")
    shop_monthly = client.get(
        "/api/ops-daily/data", params={**base, "shop": shop, "page_size": 1}
    ).json()["monthly"]
    check(
        bool(shop_monthly)
        and sum(m["sales_amount"] or 0 for m in shop_monthly)
        < sum(m["sales_amount"] or 0 for m in monthly),
        "按店铺筛选时 monthly 随之收窄（与 KPI 卡同批数据）",
    )
    # 首屏场景：默认近 30 天只落在 10 月，monthly 仍必须给全库 3 个月，
    # 否则图表只有 1 个点、算不出月份间环比、首屏变空态。
    single_month = client.get(
        "/api/ops-daily/data", params={"from": "2026-10-02", "to": "2026-10-31"}
    ).json()
    check(
        [m["month"] for m in single_month["monthly"]]
        == ["2026-08", "2026-09", "2026-10"],
        "默认 30 天区间 monthly 仍含全库 3 个月（首屏能算环比，不空态）",
    )
    empty = client.get(
        "/api/ops-daily/data", params={"from": "2027-01-01", "to": "2027-01-31"}
    ).json()
    check(
        empty["monthly"] == monthly,
        "无数据的日期区间 monthly 仍为全库 3 行（图表是历史走势，不随日期收窄）",
    )

    print("\n[8] 字典驱动：临时库新增 1 项派生指标，接口 0 改码自动出列")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_db = Path(tmp) / "ops_api_test.db"
        original = job_config.DB_PATH
        try:
            with closing(ops_daily.get_conn(tmp_db)) as conn:
                ops_daily.ensure_schema(conn)
                ops_daily.insert_row(
                    conn,
                    {
                        "date": "2026-09-01",
                        "shop": "验证用店",
                        "shop_type": "pop",
                        "metrics": {"visitors": 100.0, "buyers": 5.0, "sales_amount": 500.0},
                    },
                )
                conn.execute(
                    "INSERT INTO ops_metric_dict "
                    "(metric_key, cn_name, source_name, kind, db_column, formula, unit, is_baseline, sort_order) "
                    "VALUES ('test_ratio', '测试比率', '测试比率', 'derived', '', 'buyers/visitors', '%', 1, 99)"
                )
                conn.commit()
            job_config.DB_PATH = tmp_db
            t_meta = client.get("/api/ops-daily/meta").json()
            t_data = client.get(
                "/api/ops-daily/data",
                params={"from": "2026-09-01", "to": "2026-09-01"},
            ).json()
        finally:
            job_config.DB_PATH = original
        check(len(t_meta["metrics"]) == 25, "字典新增 1 项 ⇒ meta 自动 25 项")
        check(
            t_data["rows"] and abs(t_data["rows"][0]["values"].get("test_ratio", -1) - 0.05) < 1e-9,
            "新派生项自动出列且值 = 5/100 = 0.05（0 改码）",
        )
        check(t_data["summary"].get("test_ratio") is not None, "summary 也自动含新派生项")

    print("\n[9] 只读隔离")
    after = all_hashes()
    check(before == after, "10 张表（6 受保护 + 4 ops）内容指纹零变化，接口未写入")

    print(f"\n{'=' * 60}")
    print(f"PASS {_passed} / FAIL {len(_failed)}")
    if _failed:
        for label in _failed:
            print(f"  ✗ {label}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
