#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""推广分析端点（/api/promo/analysis）的接口层回归断言。

与 verify_promo_greedy.py 的分工：
  - verify_promo_greedy.py 验**算法**（贪心 / 波动 / 分档 / 文案），纯函数、不碰 IO；
  - 本脚本验**端点**（取数 → 汇总 → 组装 → 空态 → OpenAPI Schema），用 TestClient 走真实路由。

为什么用 TestClient 而不是起服务 + curl：
  起服务依赖端口空闲（8000 常被占）、依赖手动重启、失败时只看到一段 uvicorn 栈；
  TestClient 直接调 ASGI 应用，能断言 HTTP 状态码与 JSON 体，且能 monkeypatch 数据做边界场景。

不引入 pytest 的理由见 verify_promo_greedy.py 顶部（requirements.txt 是完整 freeze）。
运行：.venv\\Scripts\\python backend\\verify_promo_api.py
"""

import sys
from pathlib import Path

from fastapi.testclient import TestClient

# 允许直接 ``python backend/verify_promo_api.py`` 运行（把项目根塞进 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app import api as api_mod  # noqa: E402
from backend.app.main import app  # noqa: E402

# ---------------------------------------------------------------- 断言计数器

_pass = 0
_fail = 0


def ok(cond, label):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print(f"FAIL {label}")


def close(a, e, label, eps=0.01):
    ok(isinstance(a, (int, float)) and abs(a - e) <= eps,
       f"{label}（期望 {e}，实际 {a}）")


def test_real_data_shape():
    """真实数据：结构完整 + 零和守恒 + 不超集中度上限 + 占比 ∈ [0,1]。"""
    c = TestClient(app)
    r = c.get("/api/promo/analysis", params={"shop": "钻芯旗舰店"})
    ok(r.status_code == 200, "真实店铺返回 200")
    d = r.json()

    # 三数字齐备（看板模式的核心卡片）
    for k in ("ideal_total_amount", "current_total_amount", "gap", "conservative_total"):
        ok(k in d and isinstance(d[k], (int, float)), f"三数字字段 {k} 存在且为数值")
    close(d["gap"], d["ideal_total_amount"] - d["current_total_amount"],
          "gap == ideal − current")
    close(d["conservative_total"], d["ideal_total_amount"] * 0.70,
          "conservative == ideal × BACKTEST_DISCOUNT")

    # 零和守恒：⚠️ 判据是「Σsuggested + unallocated == total_budget」，
    #    不是「Σsuggested == total_budget」——cap 约束下品数 < ceil(1/cap)=4 时必然剩钱。
    s = sum(x["suggested_cost"] for x in d["spus"])
    close(s + d["unallocated"], d["total_cost"], "Σsuggested + unallocated == total_cost")

    # 集中度上限：任一品建议分配额不得超过 cap
    ok(all(x["suggested_cost"] <= d["cap"] + 0.01 for x in d["spus"]),
       f"无 SPU 超单品预算上限 cap={d['cap']:.2f}")

    # 占比 ∈ [0,1]：日均占比除过天数，若忘除会是 14.75 这种「天数倍」量纲
    for x in d["spus"]:
        ok(x["cost_share"] is None or 0.0 <= x["cost_share"] <= 1.0,
           f"cost_share ∈ [0,1]（{x['spu']} = {x['cost_share']}）")
        ok(x["amount_share"] is None or 0.0 <= x["amount_share"] <= 1.0,
           f"amount_share ∈ [0,1]（{x['spu']} = {x['amount_share']}）")

    # 派生字段自洽：profit == amount − cost；delta == suggested − current
    for x in d["spus"]:
        close(x["profit"], x["current_amount"] - x["current_cost"],
              f"profit == amount − cost（{x['spu']}）")
        close(x["delta"], x["suggested_cost"] - x["current_cost"],
              f"delta == suggested − current（{x['spu']}）")

    # 投产比「先求和再相除」：roi == amount / cost
    for x in d["spus"]:
        if x["current_cost"] > 0:
            close(x["roi"], x["current_amount"] / x["current_cost"],
                  f"roi == Σamount ÷ Σcost（{x['spu']}）", eps=1e-6)

    # 波动标记与 CV 的一致性：unstable=True 必须有 CV 值（None 表示算不出，不该被标不稳定）
    for x in d["spus"]:
        if x["unstable"]:
            ok(x["roi_cv"] is not None,
               f"unstable=True 时 roi_cv 非 None（{x['spu']}）")

    # 建议文案非空（前端直接展示，勿在前端另写一份）
    ok(all(isinstance(x["advice"], str) and x["advice"] for x in d["spus"]),
       "每个 SPU 都有非空 advice 文案")

    # 排序：按建议分配额降序（与贪心顺序一致，前端直接渲染不需再排）
    sugs = [x["suggested_cost"] for x in d["spus"]]
    ok(sugs == sorted(sugs, reverse=True), "spus 按 suggested_cost 降序")
    return d


def test_date_range_and_no_shop():
    """日期区间生效 + 不指定店铺时取第一家（不 400）。"""
    c = TestClient(app)
    r = c.get("/api/promo/analysis",
              params={"shop": "钻芯旗舰店", "start": "2026-09-01", "end": "2026-09-30"})
    ok(r.status_code == 200, "指定区间返回 200")
    d = r.json()
    ok(d["start"] == "2026-09-01" and d["end"] == "2026-09-30",
       f"start/end 回显请求区间（实际 {d['start']}..{d['end']}）")
    # 区间子集的总花费必须小于全区间
    full = TestClient(app).get("/api/promo/analysis",
                               params={"shop": "钻芯旗舰店"}).json()
    ok(d["total_cost"] < full["total_cost"], "子区间总花费 < 全区间总花费")

    # 不指定 shop：取第一家店，仍返回 200 而非 400
    r2 = c.get("/api/promo/analysis")
    ok(r2.status_code == 200, "不指定 shop 仍返回 200")
    ok(r2.json()["shop"] != "", "不指定 shop 时回填了具体店名")


def test_empty_reason_no_data():
    """空态 1/3：no_data —— 区间内该店无任何推广数据。"""
    c = TestClient(app)
    r = c.get("/api/promo/analysis",
              params={"shop": "钻芯旗舰店", "start": "2020-01-01", "end": "2020-01-31"})
    ok(r.status_code == 200, "无数据时仍返回 200（空态是业务状态，不是错误）")
    d = r.json()
    ok(d["empty_reason"] == "no_data", f"empty_reason == no_data（实际 {d['empty_reason']}）")
    ok(d["spus"] == [], "no_data 时 spus 为空")
    ok(isinstance(d["disclaimers"], list) and len(d["disclaimers"]) == 3,
       "空态也带恰好 3 条免责文案（口径说明不因无数据而消失）")


def test_empty_reason_too_few_spu():
    """空态 2/3：too_few_spus —— SPU 数 < 4。

    ⚠️ 这不是致命错误：贪心仍可执行（1 个 SPU 时结果就是它自己），
       响应必须照常带全部数字，只由前端弱化四象限。
    """
    c = TestClient(app)
    r = c.get("/api/promo/analysis", params={"shop": "卡求旗舰店"})  # 真实数据 1 个 SPU
    ok(r.status_code == 200, "SPU 数不足时返回 200")
    d = r.json()
    ok(d["empty_reason"] == "too_few_spus",
       f"empty_reason == too_few_spus（实际 {d['empty_reason']}）")
    ok(d["quadrant_reliable"] is False, "quadrant_reliable == False（前端据此弱化四象限）")
    ok(len(d["spus"]) >= 1, "too_few_spus 仍返回 SPU 明细（部分可用，不是不可用）")
    ok(d["total_cost"] > 0, "too_few_spus 仍返回总花费")
    # 1 个品时预算必然填不满：unallocated > 0 是数学必然，不是 bug
    ok(d["unallocated"] > 0,
       f"单 SPU 时 unallocated > 0（数学必然，实际 {d['unallocated']:.2f}）")


def test_empty_reason_all_zero(monkey_rows):
    """空态 3/3：all_zero —— 有 SPU 行但全部推广花费为 0。

    真实数据触发不到（至少有花费），故注入合成行。
    """
    c = TestClient(app)
    orig = api_mod.flatten_module
    api_mod.flatten_module = lambda module_id: monkey_rows
    try:
        r = c.get("/api/promo/analysis", params={"shop": "零花费测试店"})
        ok(r.status_code == 200, "all_zero 时返回 200")
        d = r.json()
        ok(d["empty_reason"] == "all_zero",
           f"empty_reason == all_zero（实际 {d['empty_reason']}）")
        ok(len(d["spus"]) >= 1, "all_zero 仍返回 SPU 行（让前端能显示「有品但没花钱」）")
        ok(all(x["roi"] is None for x in d["spus"]),
           "花费为 0 时 roi 为 None 而非 0（0 会被当成「投产比真的是 0」）")
        # 达成率分母为 0 → None，不得为 0 或除零
        ok(d["achievement_rate"] is None,
           f"ideal 为 0 时 achievement_rate 为 None（实际 {d['achievement_rate']}）")
    finally:
        api_mod.flatten_module = orig


def test_openapi_schema():
    """验收重点：/docs 里的响应 Schema 必须非空。

    项目里现有 7 个路由有 3 个漏写 response_model=，导致 OpenAPI 里响应体空白。
    """
    c = TestClient(app)
    spec = c.get("/openapi.json").json()
    path = spec["paths"]["/api/promo/analysis"]["get"]
    ref = path["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    name = ref.rsplit("/", 1)[-1]
    schema = spec["components"]["schemas"][name]
    props = schema.get("properties", {})
    ok(len(props) >= 15, f"响应 Schema 非空且字段完整（{name} 有 {len(props)} 个字段）")
    for f in ("shop", "total_cost", "ideal_total_amount", "gap", "spus", "empty_reason"):
        ok(f in props, f"Schema 含字段 {f}")
    # 嵌套的 SPU 模型也要在 components 里（否则前端拿不到明细结构）
    spu_ref = props["spus"]["items"]["$ref"].rsplit("/", 1)[-1]
    spu_props = spec["components"]["schemas"][spu_ref].get("properties", {})
    ok(len(spu_props) >= 15, f"SPU 明细 Schema 非空（{spu_ref} 有 {len(spu_props)} 个字段）")
    # 查询参数也要进文档
    names = {p["name"] for p in path.get("parameters", [])}
    ok({"shop", "start", "end"} <= names, f"查询参数进 OpenAPI（实际 {sorted(names)}）")


def test_over_concentrated_semantics():
    """现状违反单品集中度上限时的语义一致性（本轮 CDP 实测发现的真实场景）。

    背景：真实数据 3 店中 **2 店**出现「达成率 > 100%、gap 为负」。
    这不是 bug —— 现状把大部分预算压在少数品上，30% 上限要求把钱挪走。
    但页面若照字面渲染「可优化空间 −1,735 元」运营会以为算法算错，
    故必须有 `over_concentrated` 标志让前端改文案。
    """
    c = TestClient(app)
    hits, misses = [], []
    for shop in ("钻芯旗舰店", "钻芯官方旗舰店", "卡求旗舰店"):
        d = c.get("/api/promo/analysis", params={"shop": shop}).json()
        (hits if d["over_concentrated"] else misses).append((shop, d))

    ok(len(hits) > 0, f"真实数据中至少一个店触发 over_concentrated（{len(hits)}/3 命中）")
    for shop, d in hits:
        ok(d["gap"] < 0, f"{shop}: 触发超限时 gap 为负（{d['gap']:.0f}）")
        ok(d["achievement_rate"] is not None and d["achievement_rate"] > 1.0,
           f"{shop}: 触发超限时达成率 > 1（{d['achievement_rate']:.3f}）")
        # 超限的 SPU 标识必须真实存在，且其花费确实超过 cap
        ids = [x.strip() for x in d["over_concentrated"].split("、") if x.strip()]
        spus = {x["spu"]: x for x in d["spus"]}
        ok(len(ids) > 0, f"{shop}: 超限标识非空")
        for sid in ids:
            ok(sid in spus, f"{shop}: 超限 SPU {sid} 存在于返回明细中")
            if sid in spus:
                ok(spus[sid]["current_cost"] > d["cap"],
                   f"{shop}: SPU {sid} 花费 {spus[sid]['current_cost']:.0f} 确实超 cap {d['cap']:.0f}")
    for shop, d in misses:
        ok(d["gap"] >= -0.01, f"{shop}: 未触发超限时 gap 不为负（{d['gap']:.0f}）")
        if d["achievement_rate"] is not None:
            ok(d["achievement_rate"] <= 1.0 + 1e-9,
               f"{shop}: 未触发超限时达成率 <= 1（{d['achievement_rate']:.3f}）")


def test_quadrant_thresholds_exposed():
    """分割线阈值必须由端点透传，且与 quadrant 字段完全自洽（工单 06 验收项）。

    背景：前端四象限图要画两条 P50 分割线。若让前端从 spus 自己重算分位数，
    插值约定一旦有差就会���现「点被染成高效档却在分割线下方」的自相矛盾。
    端点透传 roi_threshold / cost_threshold 是把口径钉在唯一事实来源上。
    """
    c = TestClient(app)
    for shop in ("钻芯旗舰店", "钻芯官方旗舰店", "卡求旗舰店"):
        d = c.get("/api/promo/analysis", params={"shop": shop}).json()
        rt, ct = d["roi_threshold"], d["cost_threshold"]
        plottable = [x for x in d["spus"] if x["roi"] is not None]
        if not plottable:
            ok(rt is None and ct is None, f"{shop}: 无可分档样本时阈值为 None")
            continue
        ok(rt is not None and ct is not None, f"{shop}: 有可分档样本时阈值非空")
        for x in plottable:
            # 分档 = 「点在线的哪一侧」，用返回的阈值反推必须得到同一个 quadrant
            hi_roi = x["roi"] >= rt
            hi_cost = x["current_cost"] >= ct
            expect = ("core" if hi_roi and hi_cost
                      else "potential" if hi_roi
                      else "loser" if hi_cost else "trial")
            ok(x["quadrant"] == expect,
               f"{shop}/{x['spu']}: quadrant 与阈值位置自洽"
               f"（实际 {x['quadrant']}，阈值反推 {expect}）")
        # roi=None 的品绝不能被算进阈值（否则阈值被拉向 0，全落进高档）
        none_items = [x for x in d["spus"] if x["roi"] is None]
        if none_items:
            ok(all(x["quadrant"] is None for x in none_items),
               f"{shop}: roi=None 的品没有 quadrant")


def test_disclaimers_exactly_three():
    """spec §8 三处免责：恰好 3 条，且每条各自说清自己的要点（工单 08 硬要求）。

    ⚠️ 为什么必须结构化成 3 条而不是单串：单串拼接后，「三处缺一不可」无法被断言——
       删掉第 2 条的文字，前两条与第三条的关键词仍能 grep 到，断言照样绿。
       结构化后，「恰好 3 条」+「每条含自己的要点词」才真正锁住这条硬要求。

    要点词的选择依据（各自是这条文案**不可替代**的信息，换个说法就讲不清了）：
      ① 线性假设：必提「线性」+「可达」（说明不是可达目标）
      ② 保本线：必提「毛利」（数据源无此字段，必须声明未计入）
      ③ 大促风险：必提「大促」+「照搬」
    """
    from backend.app.api import DISCLAIMER_KINDS, PROMO_DISCLAIMERS

    c = TestClient(app)
    # 真实数据与空态都必须带齐三条 —— 空态是「无数据」，不是「无需免责」
    for shop, label in (("钻芯旗舰店", "真实数据"), ("卡求旗舰店", "SPU 不足")):
        d = c.get("/api/promo/analysis", params={"shop": shop}).json()
        ds = d["disclaimers"]
        ok(isinstance(ds, list), f"{label}: disclaimers 是数组")
        ok(len(ds) == 3, f"{label}: 恰好 3 条（实际 {len(ds)}）")
        ok(all(isinstance(x, str) and x.strip() for x in ds),
           f"{label}: 每条都是非空字符串")
        ok(list(ds) == list(PROMO_DISCLAIMERS),
           f"{label}: 与常量 PROMO_DISCLAIMERS 完全一致（前端不得改写文案）")
        ok(len(DISCLAIMER_KINDS) == len(PROMO_DISCLAIMERS),
           "DISCLAIMER_KINDS 与文案条数一一对应")

    # ⚠️ 下面按索引取ds[i]：条数不足时会 IndexError 崩掉整个脚本，
    #    后续用例全被跳过 —— 崩溃比红更糟（看起来"跑完了"，其实后面没跑）。
    #    故先判条数再取，不足时直接 return。
    ds = list(PROMO_DISCLAIMERS)
    if len(ds) != 3:
        ok(False, f"PROMO_DISCLAIMERS 应恰好 3 条（实际 {len(ds)}），跳过逐条要点校验")
        return
    ok("线性" in ds[0] and "可达" in ds[0],
       f"第 1 条讲线性假设与非可达目标（实际 {ds[0]}）")
    ok("毛利" in ds[1],
       f"第 2 条声明未计入毛利（实际 {ds[1]}）")
    ok("大促" in ds[2] and "照搬" in ds[2],
       f"第 3 条警示大促期不可照搬（实际 {ds[2]}）")
    # 顺序不可换：前端按数组顺序逐条渲染，顺序即展示顺序
    ok("大促" not in ds[0] and "大促" not in ds[1],
       "大促风险只出现在第 3 条")
    ok("毛利" not in ds[0] and "毛利" not in ds[2],
       "毛利声明只出现在第 2 条")

    # ⛔ 三条文案内部也不得含禁用词（test_terminology 只扫整个响应体，
    #    这里额外锁一次常量本身 —— 常量是所有文案的源头，源头出事三条全中）
    for banned in ("边际", "亏损", "预测", "预计达成"):
        ok(all(banned not in x for x in ds), f"免责文案常量不含禁用词「{banned}」")


def test_terminology():
    """术语纪律：⛔ 响应里不得出现「边际」「亏损」「预测」。"""
    c = TestClient(app)
    r = c.get("/api/promo/analysis", params={"shop": "钻芯旗舰店"})
    body = r.text
    for banned in ("边际", "亏损", "预测", "预计达成"):
        ok(banned not in body, f"响应体不含禁用词「{banned}」")

    # 超限提示文案也不得出现「边际」——它是「现状过度集中」的意思，不是「边际」


def main():
    test_real_data_shape()
    test_date_range_and_no_shop()
    test_empty_reason_no_data()
    test_empty_reason_too_few_spu()
    test_empty_reason_all_zero(_zero_cost_rows())
    test_over_concentrated_semantics()
    test_quadrant_thresholds_exposed()
    test_disclaimers_exactly_three()
    test_openapi_schema()
    test_terminology()

    print(f"\n{'=' * 46}")
    print(f"通过 {_pass} / 失败 {_fail}")
    if _fail:
        sys.exit(1)
    print("全部通过")


def _zero_cost_rows():
    """构造「有 SPU 但推广花费与成交全为 0」的合成扁平行（触发 all_zero）。"""
    from backend.app.api import RowOut
    from backend.jobs.models import MetricRecord

    rows = []
    for spu in ("ZERO001", "ZERO002"):
        for day in ("2026-09-01", "2026-09-02"):
            rows.append(RowOut(
                shop="零花费测试店", spu=spu, spu_name=f"零花费品{spu}",
                category="测试", image=None, date=day,
                metrics=MetricRecord(promotion_cost=0.0, promotion_amount=0.0),
            ))
    return rows


if __name__ == "__main__":
    main()
