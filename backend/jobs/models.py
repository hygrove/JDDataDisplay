# -*- coding: utf-8 -*-
"""Pydantic v2 数据模型。

前端 TS 类型在 frontend/src/types.ts 中与本文件手工对齐；
如需自动生成可引入 datamodel-code-generator，见 README「类型对齐」一节。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


# ---------------- 指标 ----------------
class MetricRecord(BaseModel):
    """单日单 SPU 的指标。缺失指标为 None，前端展示 --。

    口径说明（比率类一律按「先求和再相除」派生，不落源表值，保证任意聚合层级一致）：
      conversion_rate    = 成交客户数 / 商品访客数
      avg_price（客单价） = 成交金额 / 成交客户数
      roi                = 推广成交金额 / 推广花费
      promotion_ratio    = 推广花费 / 成交金额
      search_click_rate  = 搜索点击次数 / 搜索曝光次数
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
    spu: str
    spu_name: Optional[str] = None
    category: Optional[str] = None      # 三级类目
    image: Optional[str] = None         # 图片相对路径，如 /images/100xxxx.png
    dates: dict[str, MetricRecord] = Field(default_factory=dict)  # key: YYYY-MM-DD


class ShopNode(BaseModel):
    shop: str
    spus: dict[str, SpuNode] = Field(default_factory=dict)  # key: spu


class ModuleData(BaseModel):
    """模块明细 JSON（pop_spu_detail.json）的根结构。"""

    module_id: str
    updated_at: datetime
    date_range: list[str] = Field(default_factory=list)  # [min_date, max_date]
    shops: dict[str, ShopNode] = Field(default_factory=dict)  # key: 店铺名


# ---------------- 汇总（图表用，Python 端预聚合）----------------
class DailySummary(BaseModel):
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
    spu: str
    spu_name: Optional[str] = None
    shop: str
    image: Optional[str] = None
    amount: float = 0
    visitors: float = 0
    buyers: float = 0
    items: float = 0


class ShopSummary(BaseModel):
    shop: str
    amount: float = 0
    visitors: float = 0
    buyers: float = 0
    items: float = 0


class ModuleSummary(BaseModel):
    module_id: str
    updated_at: datetime
    daily: list[DailySummary] = Field(default_factory=list)       # 按店铺+日期
    top_spus: list[TopSpu] = Field(default_factory=list)          # 按店铺
    shop_totals: list[ShopSummary] = Field(default_factory=list)  # 店铺汇总（饼图）


# ---------------- 清单与状态 ----------------
class ModuleManifest(BaseModel):
    module_id: str
    title: str
    description: str = ""
    shops: list[str] = Field(default_factory=list)
    date_range: list[str] = Field(default_factory=list)
    spu_count: int = 0
    row_count: int = 0
    updated_at: Optional[datetime] = None


class Manifest(BaseModel):
    generated_at: datetime
    modules: list[ModuleManifest] = Field(default_factory=list)


class BatchStatus(BaseModel):
    last_run_at: Optional[datetime] = None
    success: bool = False
    attempts: int = 1
    duration_seconds: float = 0
    message: str = ""
    source_dir: Optional[str] = None
    modules: list[str] = Field(default_factory=list)
