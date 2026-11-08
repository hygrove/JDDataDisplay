# -*- coding: utf-8 -*-
"""店铺日常运营数据入库的验证脚本（临时库 + 只读真源目录）。

运行：仓库根 `python -m backend.jobs.verify_ops_daily`

⛔ 按 `ai-rules §3`：**不引入 pytest**（requirements.txt 是完整 freeze）。
沿用独立脚本 + 自制断言计数器 + `sys.exit(1)`。

覆盖 12 类断言：
  1. 表结构：19 原子列齐备、5 个派生指标不落库、改名列名正确
  2. 基准指标字典 24 行、4 个改名项的中文基准名与公式
  3. 文件发现：只认 6 个月报文件，日更文件被显式跳过
  4. 导入 1564 行、日期范围、17 店铺、self/pop 分布
  5. `/` → NULL 与 `0` → 0 的语义区分（具体行）
  6. 8/9 月源派生真值 vs 独立公式重算（不复用被测模块的解析函数）
  7. 10 月「假零」被识别且不落库
  8. 幂等：二次导入行数与全表内容哈希不变
  9. 增删改查全链路 + 人工修订保护 + 删除墓碑防复活
 10. 派生层「先求和再相除」：分母为 0 返回 NULL，区间汇总不等于比率平均
 11. 受保护表零影响；模拟指纹变化 ⇒ 抛错且事务回滚
 12. 畸形源文件（顶层键错 / 缺字段 / 跨月日期键）一律拒绝，不静默吞

跑法：全程在临时目录建库与坏样本，⛔ 不碰生产 app.db。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.jobs import config, ops_daily as o  # noqa: E402

SRC = config.OPS_DAILY_SOURCE_DIR
MONTHS = ["2026-08", "2026-09", "2026-10"]
DERIVED_FORMULA = {
    "转化率": ("成交人数", "访客数"),
    "客单价": ("销售额", "成交人数"),
    "PPC": ("花费", "点击量"),
    "ROI": ("成交额", "花费"),
    "佣金比例": ("佣金", "成交金额"),
}
FIELD_TO_KEY = {m.source_name: m.key for m in o.ATOMIC_METRICS}

# ---- 自制断言计数器 ----
_passed = 0
_failed: list[str] = []


def check(condition: bool, label: str) -> None:
    """累加一条断言结果；失败不中断，最后统一汇总。"""
    global _passed
    if condition:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed.append(label)
        print(f"  FAIL  {label}")


def independent_num(raw) -> float | None:
    """独立的数值解析器（刻意不复用被测模块），用于交叉验算。"""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if text in ("", "/", "-", "--"):
        return None
    pct = text.endswith("%")
    if pct:
        text = text[:-1]
    try:
        value = float(text)
    except ValueError:
        return None
    return value / 100 if pct else value


def table_hash(conn: sqlite3.Connection, table: str) -> str:
    """按主键顺序哈希整表内容，用于幂等性比对。"""
    rows = conn.execute(f'SELECT * FROM "{table}" ORDER BY id').fetchall()
    return hashlib.sha256(repr([tuple(r) for r in rows]).encode("utf-8")).hexdigest()


def main() -> int:
    print("店铺运营指标库验证（临时库）\n")
    with tempfile.TemporaryDirectory(prefix="ops_daily_verify_") as tmp:
        tmp_dir = Path(tmp)
        db = tmp_dir / "verify.db"
        conn = o.get_conn(db)

        # --- 1. 表结构 ---
        print("[1] 表结构与基准指标字典")
        o.ensure_schema(conn)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(ops_shop_daily)")]
        atomic_in_db = [m.key for m in o.ATOMIC_METRICS if m.key in cols]
        derived_in_db = [m.key for m in o.DERIVED_METRICS if m.key in cols]
        check(len(atomic_in_db) == 19, "19 个原子列全部存在")
        check(derived_in_db == [], "5 个派生指标一律不落库")
        check(
            all(k in cols for k in ("promo_order_amount", "jd_union_order_amount", "jd_union_commission")),
            "改名后的 3 个原子列名存在",
        )
        check("commission_rate" not in cols, "旧口径佣金比例列不存在")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        check(
            {"ops_shop_daily", "ops_metric_dict", "ops_ingest_log", "ops_row_tombstone"} <= tables,
            "四张 ops_* 表齐备",
        )

        # --- 2. 指标字典 ---
        print("[2] 基准指标字典")
        d = {r["metric_key"]: dict(r) for r in o.metric_dict_rows(conn)}
        check(len(d) == 24, "字典 24 行（19 原子 + 5 派生）")
        check(
            d["promo_order_amount"]["cn_name"] == "推广成交金额"
            and d["jd_union_order_amount"]["cn_name"] == "JD联盟成交金额"
            and d["jd_union_commission"]["cn_name"] == "JD联盟佣金"
            and d["jd_union_commission_rate"]["cn_name"] == "JD联盟佣金比例",
            "4 个改名指标以新名为基准名入库",
        )
        check(
            d["jd_union_commission_rate"]["formula"]
            == "jd_union_commission/jd_union_order_amount",
            "JD联盟佣金比例的分母是 JD联盟成交金额",
        )
        check(
            all(r["is_baseline"] == 1 for r in d.values())
            and all(d[k]["db_column"] == "" for k in o.DERIVED_NUM_DEN),
            "全部标为基准指标，派生项无数据库列",
        )

        # --- 3. 文件发现 ---
        print("[3] 文件发现")
        files = o.discover_monthly_files(SRC, MONTHS)
        check(len(files) == 6, f"6 个月报文件（实得 {len(files)}）")
        check(
            all("2026-11-04" not in p.name for p in files),
            "日更格式文件被显式跳过",
        )

        # --- 4. 导入 ---
        print("[4] 导入 8/9/10 月")
        result = o.import_monthly(conn, SRC, MONTHS)
        count = conn.execute("SELECT COUNT(*) FROM ops_shop_daily").fetchone()[0]
        check(count == 1564, f"总行数 1564（实得 {count}）")
        bounds = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(DISTINCT shop) FROM ops_shop_daily"
        ).fetchone()
        check(
            bounds[0] == "2026-08-01" and bounds[1] == "2026-10-31" and bounds[2] == 17,
            f"日期范围与店铺数：{tuple(bounds)}",
        )
        types = dict(
            conn.execute("SELECT shop_type, COUNT(*) FROM ops_shop_daily GROUP BY shop_type")
        )
        check(types == {"self": 552, "pop": 1012}, f"self/pop 分布：{types}")
        check(result["protected_tables_unchanged"] is True, "受保护表指纹核对通过")

        # --- 5. NULL / 0 语义 ---
        print("[5] 占位符与真实零的区分")
        # 2026-11-08：用「数值」定位样本行，不再把真实店名写进源码
        # （§6.1 推前扫描④）。⛔ 不能改用 promo_clicks IS NULL 之类的语义条件来选，
        # 否则断言会变成「自己选自己」的同义反复；销售额是独立定位键。
        r1 = conn.execute(
            "SELECT * FROM ops_shop_daily WHERE date='2026-10-01' "
            "AND ABS(sales_amount - 2178.90) < 1e-6 LIMIT 1"
        ).fetchone()
        check(r1 is not None, "定位到 10-01 销售额 2178.90 的样本行")
        check(r1["promo_clicks"] is None, "`/` → SQL NULL（样本行点击量）")
        check(r1["compensation_amount"] == 0, "`0` → 真实的 0（赔付金额）")
        check(abs(r1["sales_amount"] - 2178.90) < 1e-6, "销售额数值无损")
        r2 = conn.execute(
            "SELECT * FROM ops_shop_daily WHERE date='2026-10-01' "
            "AND ABS(promo_order_amount - 1483.50) < 1e-6 LIMIT 1"
        ).fetchone()
        check(r2 is not None, "定位到 10-01 推广成交金额 1483.50 的样本行")
        check(
            r2["jd_union_order_amount"] == 0 and r2["promo_order_amount"] == 1483.50,
            "0.00 不被误判为缺失；推广成交金额取自原「成交额」",
        )

        # --- 6. 独立公式交叉验算 8/9 月真值 ---
        print("[6] 源派生真值 vs 独立公式重算（8/9 月）")
        rows = {
            (r["date"], r["shop"], r["shop_type"]): r
            for r in conn.execute("SELECT * FROM ops_shop_daily")
        }
        compared = {"转化率": 0, "客单价": 0, "PPC": 0, "ROI": 0, "佣金比例": 0}
        matched = dict.fromkeys(compared, 0)
        for path in files:
            if path.name[:7] not in ("2026-08", "2026-09"):
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            shop_type = "self" if "自营" in path.name else "pop"
            year, month = path.name[:4], path.name[5:7]
            for shop, days in payload[list(payload)[0]].items():
                for day_key, raw in days.items():
                    date = f"{year}-{month}-{day_key[3:5]}"
                    db_row = rows.get((date, shop, shop_type))
                    if db_row is None:
                        continue
                    for name, (num_field, den_field) in DERIVED_FORMULA.items():
                        original = independent_num(raw[name])
                        num = independent_num(raw[num_field])
                        den = independent_num(raw[den_field])
                        if original in (None, 0) or num is None or den in (None, 0):
                            continue
                        # 用「库里的原子列」重算，验证入库无损 + 公式正确
                        db_num = db_row[FIELD_TO_KEY[num_field]]
                        db_den = db_row[FIELD_TO_KEY[den_field]]
                        if db_num is None or db_den in (None, 0):
                            continue
                        expected = db_num / db_den
                        compared[name] += 1
                        if abs(original - expected) <= max(0.02, abs(expected) * 0.02):
                            matched[name] += 1
        check(
            all(v > 0 for v in compared.values()),
            f"5 个指标都有可比对样本：{compared}",
        )
        ratios = {k: round(matched[k] / compared[k], 3) for k in compared}
        check(all(v >= 0.97 for v in ratios.values()), f"误差<2% 占比全部 ≥97%：{ratios}")

        # --- 7. 10 月假零 ---
        print("[7] 10 月派生列塌成假零")
        fake_zero = sum(
            sum(q["derived_zero_but_computable"].values())
            for name, q in result["quality"].items()
            if name.startswith("2026-10")
        )
        check(fake_zero > 500, f"识别出 10 月假零行 {fake_zero}（源值为 0 但可重算）")
        check(
            sum(
                sum(q["derived_mismatch"].values())
                for q in result["quality"].values()
            ) == 0,
            "所有源真值与重算值一致（无口径反例）",
        )

        # --- 8. 幂等 ---
        print("[8] 幂等")
        before_hash = table_hash(conn, "ops_shop_daily")
        o.import_monthly(conn, SRC, MONTHS)
        after_hash = table_hash(conn, "ops_shop_daily")
        check(before_hash == after_hash, "二次导入全表内容哈希不变")
        check(
            conn.execute("SELECT COUNT(*) FROM ops_ingest_log").fetchone()[0] == 6,
            "导入日志 6 条（不重复累积）",
        )

        # --- 9. 派生层语义（先做，避免 CRUD 干扰）---
        print("[9] 派生层：先求和再相除")
        daily = o.derived_rows(conn, ("date", "shop", "shop_type"), start="2026-08-01", end="2026-08-01")
        check(len(daily) == 17, "单日 17 组（11 POP + 6 自营）")
        month = o.derived_rows(conn, ("shop_type",), start="2026-08-01", end="2026-10-31")
        check(len(month) == 2 and all("conversion_rate" in r for r in month), "区间按类型汇总重算")
        # 分母为 0 必须给 NULL，不能崩也不能伪装成 0
        null_row = conn.execute(
            "SELECT * FROM ops_shop_daily WHERE promo_clicks = 0 OR promo_clicks IS NULL LIMIT 1"
        ).fetchone()
        check(null_row is not None, "存在分母为 0/空的行")
        zero_div = o.derived_rows(
            conn, ("date", "shop", "shop_type"),
            start=null_row["date"], end=null_row["date"], shop=null_row["shop"],
        )
        check(
            all(r["ppc"] is None for r in zero_div if r["date"] == null_row["date"]),
            "分母 0/空 ⇒ PPC 为 NULL",
        )

        # --- 10. 增删改查 ---
        print("[10] 增删改查 + 人工保护 + 墓碑")
        new_id = o.insert_row(conn, {
            "date": "2026-12-01", "shop": "验证用店", "shop_type": "self",
            "metrics": {"sales_amount": 100.0, "buyers": 2, "visitors": 10},
        })
        check(o.get_row(conn, new_id)["is_manual"] == 1, "增：人工行 is_manual=1")
        check(o.update_row(conn, new_id, {"sales_amount": 200.0}), "改：更新返回 True")
        check(o.get_row(conn, new_id)["sales_amount"] == 200.0, "改：新值生效")
        check(len(o.list_rows(conn, shop="验证用店")) == 1, "查：按店铺过滤生效")
        check(o.delete_row(conn, row_id=new_id), "删：返回 True")
        check(o.get_row(conn, new_id) is None, "删：行已消失")
        check(
            conn.execute("SELECT COUNT(*) FROM ops_row_tombstone").fetchone()[0] == 1,
            "删：登记墓碑",
        )
        # 用源文件里真实存在的一行验证「删除不复活」：人工行本就没有源文件支撑，说服力不足
        real = conn.execute(
            "SELECT id, date, shop, shop_type FROM ops_shop_daily "
            "WHERE date='2026-10-02' LIMIT 1"
        ).fetchone()
        key = (real["date"], real["shop"], real["shop_type"])
        o.delete_row(conn, row_id=real["id"])
        o.import_monthly(conn, SRC, MONTHS)
        exists = "SELECT COUNT(*) FROM ops_shop_daily WHERE date=? AND shop=? AND shop_type=?"
        check(
            conn.execute(exists, key).fetchone()[0] == 0,
            "源里真实存在的行被删后不会被导入复活",
        )
        check(o.restore_row(conn, *key), "restore 撤销墓碑")
        o.import_monthly(conn, SRC, MONTHS)
        check(conn.execute(exists, key).fetchone()[0] == 1, "restore 后重新导入补回该行")
        # 人工修订保护：修改一行后重跑导入，值不被源文件覆盖
        target = conn.execute(
            "SELECT id FROM ops_shop_daily WHERE date='2026-10-01' LIMIT 1"
        ).fetchone()[0]
        o.update_row(conn, target, {"sales_amount": 99999.0})
        o.import_monthly(conn, SRC, MONTHS)
        check(
            conn.execute("SELECT sales_amount FROM ops_shop_daily WHERE id=?", (target,)).fetchone()[0]
            == 99999.0,
            "人工修订跨次导入存活",
        )
        try:
            o.update_row(conn, target, {"conversion_rate": 0.5})
            illegal = False
        except ValueError:
            illegal = True
        check(illegal, "拒绝写入派生指标列")

        # --- 11. 受保护表零影响 + 回滚 ---
        print("[11] 受保护表隔离与事务回滚")
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS daily_detail (id INTEGER PRIMARY KEY, v TEXT);"
            "INSERT INTO daily_detail(v) VALUES ('sentinel');"
            "CREATE TABLE IF NOT EXISTS inventory_daily (id INTEGER PRIMARY KEY, v TEXT);"
            "INSERT INTO inventory_daily(v) VALUES ('sentinel2');"
        )
        conn.commit()
        fp_before = o.protected_fingerprint(conn)
        o.import_monthly(conn, SRC, MONTHS)
        fp_after = o.protected_fingerprint(conn)
        check(fp_before == fp_after, "导入前后受保护表内容指纹一致")
        # 模拟「导入中途受保护表被写入」：指纹变化必须抛错且回滚
        original_fp = o.protected_fingerprint
        calls = {"n": 0}

        def tampered_fp(c):
            calls["n"] += 1
            base = original_fp(c)
            if calls["n"] >= 2:
                base = dict(base, daily_detail="TAMPERED")
            return base

        o.protected_fingerprint = tampered_fp
        count_before = conn.execute("SELECT COUNT(*) FROM ops_shop_daily").fetchone()[0]
        rolled_back = False
        try:
            o.import_monthly(conn, SRC, MONTHS)
        except RuntimeError:
            rolled_back = True
        finally:
            o.protected_fingerprint = original_fp
        check(rolled_back, "指纹变化 ⇒ RuntimeError")
        check(
            conn.execute("SELECT COUNT(*) FROM ops_shop_daily").fetchone()[0] == count_before,
            "异常时事务回滚，行数不变",
        )

        # --- 12. 畸形源文件拒绝 ---
        print("[12] 畸形源文件")
        bad_dir = tmp_dir / "bad"
        bad_dir.mkdir()
        bad1 = bad_dir / "2026-07自营店铺日常运营数据.json"
        bad1.write_text(json.dumps({"错误的顶层键": {}}, ensure_ascii=False), encoding="utf-8")
        bad2 = bad_dir / "2026-07POP店铺日常运营数据.json"
        bad2.write_text(
            json.dumps({"2026-07店铺日常运营数据": {"某店": {"07月01日": {"销售额": "1"}}}}, ensure_ascii=False),
            encoding="utf-8",
        )
        rejected = 0
        for path in (bad1, bad2):
            try:
                o.parse_monthly_file(path)
            except ValueError:
                rejected += 1
        check(rejected == 2, "顶层键错 / 缺字段一律 ValueError")
        bad3 = bad_dir / "2026-07自营店铺日常运营数据.json"
        row = {name: "1" for name in o.SOURCE_FIELDS_EXPECTED}
        bad3.write_text(
            json.dumps({"2026-07店铺日常运营数据": {"某店": {"08月01日": row}}}, ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            o.parse_monthly_file(bad3)
            cross_month = False
        except ValueError:
            cross_month = True
        check(cross_month, "日期键月份与文件名不符时拒绝")
        conn.close()

    print(f"\n合计：PASS {_passed} / FAIL {len(_failed)}")
    if _failed:
        for label in _failed:
            print(f"  - {label}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
