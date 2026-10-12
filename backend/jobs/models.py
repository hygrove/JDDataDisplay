# -*- coding: utf-8 -*-
"""Pydantic v2 数据模型：批处理产出 JSON 的 schema 定义。

前端 TS 类型在 frontend/src/types.ts 中与本文件**手工对齐**；
如需自动生成可引入 datamodel-code-generator，见 README「类型对齐」一节。
⚠️ 改这里的字段时务必同步 frontend/src/types.ts，否则前端会静默拿到 undefined。

字段通用约定：
  数值字段大量使用 Optional[float] = None，None 表示该指标**缺失**（前端展示 --），
  与「值为 0」语义不同——0 是真实统计到 0，None 是压根没这个数据，二者不可混用。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


# ---------------- 指标 ----------------
class MetricRecord(BaseModel):
    """单日单 SPU 的指标集合。缺失指标为 None，前端展示 `--`。

    口径说明（比率类一律按「先求和再相除」派生，不落源表值，保证任意聚合层级一致）：
      conversion_rate    = 成交客户数 / 商品访客数
      avg_price（客单价） = 成交金额 / 成交客户数
      roi                = 推广成交金额 / 推广花费
      promotion_ratio    = 推广花费 / 成交金额
      search_click_rate  = 搜索点击次数 / 搜索曝光次数

    Fields:
        visitors: 商品访客数
        buyers: 成交客户数
        orders: 成交单量
        items: 成交商品件数
        amount: 成交金额
        avg_price: 客单价 = 成交金额 / 成交客户数
        search_impressions: 搜索曝光次数
        search_clicks: 搜索点击次数（仅用于重算点击率的分子，不单独展示）
        search_click_rate: 搜索点击率 = 搜索点击次数 / 搜索曝光次数
        promotion_cost: 推广花费（推广数据表「花费」列）
        promotion_amount: 推广成交金额（推广数据表「总订单金额」列）
        conversion_rate: 成交转化率 = 成交客户数 / 商品访客数
        roi: ROI = 推广成交金额 / 推广花费
        promotion_ratio: 推广占比 = 推广花费 / 成交金额
        refund_orders: 取消及售后退款单量
        refund_amount: 取消及售后退款金额

    Raises:
        pydantic.ValidationError: 传入字段类型不匹配（如给 float 字段传了字符串）时抛出。

    Example:
        >>> m = MetricRecord(visitors=100, buyers=10, orders=12, amount=999.5)
        >>> m.conversion_rate is None
        True
    """

    visitors: Optional[float] = None            # 商品访客数
    buyers: Optional[float] = None              # 成交客户数
    orders: Optional[float] = None              # 成交单量
    items: Optional[float] = None               # 成交商品件数
    amount: Optional[float] = None              # 成交金额
    avg_price: Optional[float] = None           # 客单价 = 成交金额 / 成交客户数
    search_impressions: Optional[float] = None  # 搜索曝光次数
    search_clicks: Optional[float] = None       # 搜索点击次数（用于重算点击率，不单独展示）
    search_click_rate: Optional[float] = None   # 搜索点击率 = 搜索点击次数 / 搜索曝光次数
    promotion_cost: Optional[float] = None      # 推广花费（推广数据表「花费」）
    promotion_amount: Optional[float] = None    # 推广成交金额（推广数据表「总订单金额」）
    conversion_rate: Optional[float] = None     # 成交转化率 = 成交客户数 / 商品访客数
    roi: Optional[float] = None                 # ROI = 推广成交金额 / 推广花费
    promotion_ratio: Optional[float] = None     # 推广占比 = 推广花费 / 成交金额
    refund_orders: Optional[float] = None       # 取消及售后退款单量
    refund_amount: Optional[float] = None       # 取消及售后退款金额


# ---------------- 层级结构：店铺 -> spu -> 日期 -> 指标 ----------------
class SpuNode(BaseModel):
    """单个 SPU 节点：挂在某个店铺下，持有该 SPU 逐日的指标。

    Fields:
        spu: SPU 编号（全局唯一标识）。
        spu_name: 商品名称；数据源映射表缺失时为 None，前端降级显示 SPU 号。
        category: 三级类目；可为空。
        image: 图片访问路径，如 "/images/100xxxx.png"；无图时为 None。
        dates: 日期 -> MetricRecord 的映射，key 为 "YYYY-MM-DD"。

    Raises:
        pydantic.ValidationError: dates 中的值不是合法 MetricRecord 时抛出。

    Example:
        >>> node = SpuNode(spu="100123", spu_name="钻芯保温杯", dates={"2026-09-24": MetricRecord(amount=99)})
        >>> node.spu
        '100123'
    """

    spu: str
    spu_name: Optional[str] = None
    category: Optional[str] = None      # 三级类目
    image: Optional[str] = None         # 图片相对路径，如 /images/100xxxx.png
    dates: dict[str, MetricRecord] = Field(default_factory=dict)  # key: YYYY-MM-DD


class ShopNode(BaseModel):
    """单个店铺节点：持有该店铺下所有 SPU。

    Fields:
        shop: 店铺名。
        spus: SPU 编号 -> SpuNode 的映射。

    Raises:
        pydantic.ValidationError: spus 中值类型不合法时抛出。

    Example:
        >>> shop = ShopNode(shop="钻芯旗舰店", spus={"100123": SpuNode(spu="100123")})
        >>> shop.shop
        '钻芯旗舰店'
    """

    shop: str
    spus: dict[str, SpuNode] = Field(default_factory=dict)  # key: spu


class ModuleData(BaseModel):
    """模块明细 JSON（如 pop_spu_detail.json）的根结构。

    Fields:
        module_id: 模块标识。
        updated_at: 本批数据生成时间（UTC）。
        date_range: 数据覆盖的日期区间 [最早日, 最晚日]；无数据时为空列表。
        shops: 店铺名 -> ShopNode 的映射，构成「店铺->SPU->日期->指标」四层结构。

    Raises:
        pydantic.ValidationError: 字段缺失或类型错误时抛出（如 updated_at 不是 datetime）。

    Example:
        >>> data = ModuleData(module_id="pop_spu_detail", updated_at=datetime.now())
        >>> data.shops
        {}
    """

    module_id: str
    updated_at: datetime
    date_range: list[str] = Field(default_factory=list)  # [min_date, max_date]
    shops: dict[str, ShopNode] = Field(default_factory=dict)  # key: 店铺名


# ---------------- 汇总（图表用，Python 端预聚合）----------------
class DailySummary(BaseModel):
    """按「店铺 + 日期」预聚合的汇总行，供图表直接消费（避免前端全量累加）。

    Fields:
        date: 日期 "YYYY-MM-DD"。
        visitors / buyers / orders / items / amount: 可累加指标，默认 0（无数据按 0 计入）。
        avg_price / search_click_rate / conversion_rate / roi / promotion_ratio:
            派生比率，无分母时为 None（不参与图表，避免画出 0 造成误读）。
        search_impressions / search_clicks: 搜索曝光 / 点击次数，默认 0。
        promotion_cost / promotion_amount: 推广花费 / 推广成交金额。
        refund_orders / refund_amount: 退款单量 / 退款金额，默认 0。

    Raises:
        pydantic.ValidationError: 字段类型不匹配时抛出。

    Example:
        >>> d = DailySummary(date="2026-09-24", amount=1200.0, orders=5)
        >>> d.roi is None
        True
    """

    date: str
    visitors: float = 0
    buyers: float = 0
    orders: float = 0
    items: float = 0
    amount: float = 0
    avg_price: Optional[float] = None
    search_impressions: float = 0
    search_clicks: float = 0
    search_click_rate: Optional[float] = None
    promotion_cost: Optional[float] = None
    promotion_amount: Optional[float] = None
    conversion_rate: Optional[float] = None
    roi: Optional[float] = None
    promotion_ratio: Optional[float] = None
    refund_orders: float = 0
    refund_amount: float = 0


class TopSpu(BaseModel):
    """TOP 榜单里的一条 SPU 记录（按成交金额排序取前 N）。

    Fields:
        spu: SPU 编号。
        spu_name: 商品名称，可为空。
        shop: 所属店铺名。
        image: 图片访问路径，可为空。
        amount / visitors / buyers / items: 区间累计值，默认 0。

    Raises:
        pydantic.ValidationError: 字段类型错误时抛出。

    Example:
        >>> t = TopSpu(spu="100123", shop="钻芯旗舰店", amount=8888.0)
        >>> t.amount
        8888.0
    """

    spu: str
    spu_name: Optional[str] = None
    shop: str
    image: Optional[str] = None
    amount: float = 0
    visitors: float = 0
    buyers: float = 0
    items: float = 0


class ShopSummary(BaseModel):
    """单个店铺的区间汇总（供饼图等展示店铺占比）。

    Fields:
        shop: 店铺名。
        amount / visitors / buyers / items: 该店铺区间累计值，默认 0。

    Raises:
        pydantic.ValidationError: 字段类型错误时抛出。

    Example:
        >>> s = ShopSummary(shop="钻芯旗舰店", amount=10000.0)
        >>> s.shop
        '钻芯旗舰店'
    """

    shop: str
    amount: float = 0
    visitors: float = 0
    buyers: float = 0
    items: float = 0


class ModuleSummary(BaseModel):
    """模块汇总 JSON（如 pop_spu_detail.summary.json）的根结构。

    Fields:
        module_id: 模块标识。
        updated_at: 生成时间。
        daily: 按「店铺+日期」的逐日汇总列表。
        top_spus: TOP SPU 榜单列表。
        shop_totals: 各店铺区间汇总列表（饼图用）。

    Raises:
        pydantic.ValidationError: 字段类型错误时抛出。

    Example:
        >>> s = ModuleSummary(module_id="pop_spu_detail", updated_at=datetime.now())
        >>> s.top_spus
        []
    """

    module_id: str
    updated_at: datetime
    daily: list[DailySummary] = Field(default_factory=list)       # 按店铺+日期
    top_spus: list[TopSpu] = Field(default_factory=list)          # 按店铺
    shop_totals: list[ShopSummary] = Field(default_factory=list)  # 店铺汇总（饼图）


# ---------------- 清单与状态 ----------------
class ModuleManifest(BaseModel):
    """manifest 中的单个模块条目：描述该模块有哪些店铺、覆盖哪些日期、多少 SPU。

    Fields:
        module_id: 模块标识（前端路由参数）。
        title: 模块中文名。
        description: 模块说明，可为空字符串。
        shops: 店铺名列表。
        date_range: [最早日, 最晚日]；无数据时为空列表。
        spu_count: SPU 数量（按 shop+spu 去重）。
        row_count: 明细行数。
        updated_at: 该模块数据更新时间，可为空。

    Raises:
        pydantic.ValidationError: 字段类型错误时抛出。

    Example:
        >>> m = ModuleManifest(module_id="pop_spu_detail", title="POP 单品明细", spu_count=10)
        >>> m.spu_count
        10
    """

    module_id: str
    title: str
    description: str = ""
    shops: list[str] = Field(default_factory=list)
    date_range: list[str] = Field(default_factory=list)
    spu_count: int = 0
    row_count: int = 0
    updated_at: Optional[datetime] = None


class Manifest(BaseModel):
    """全局清单 manifest.json 的根结构：前端首屏第一个请求拿到的东西。

    Fields:
        generated_at: manifest 生成时间（UTC）。
        modules: 各模块条目列表。

    Raises:
        pydantic.ValidationError: 字段类型错误时抛出。

    Example:
        >>> mf = Manifest(generated_at=datetime.now(), modules=[])
        >>> mf.modules
        []
    """

    generated_at: datetime
    modules: list[ModuleManifest] = Field(default_factory=list)


class BatchStatus(BaseModel):
    """批处理运行状态（写入 status.json，供前端展示「上次更新 / 是否成功」）。

    Fields:
        last_run_at: 上次运行开始时间，可为空（从未运行过）。
        success: 是否成功，默认 False（未运行时按失败处理，避免误报「正常」）。
        attempts: 实际执行次数（含首次），默认 1。
        duration_seconds: 总耗时秒数。
        message: 结果说明；失败时含最后一次异常信息。
        source_dir: 本次使用的数据源目录，便于排查「读错目录」类问题。
        modules: 成功时产出的模块 id 列表。

    Raises:
        pydantic.ValidationError: 字段类型错误时抛出。

    Example:
        >>> st = BatchStatus(success=True, message="成功：1 个模块")
        >>> st.success
        True
    """

    last_run_at: Optional[datetime] = None
    success: bool = False
    attempts: int = 1
    duration_seconds: float = 0
    message: str = ""
    source_dir: Optional[str] = None
    modules: list[str] = Field(default_factory=list)
