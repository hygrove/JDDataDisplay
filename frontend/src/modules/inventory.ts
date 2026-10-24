// 库存视图（矩阵表）与补货工作台共用的展示判定纯函数。
//
// 这里是「商品图怎么取」「缺值怎么显示」「预计可售天数怎么格式化」的唯一事实来源：
// 两个视图共用同一份实现，避免各写一套规则导致口径漂移。
// 纯函数：不碰网络、不碰 DOM，可直接用 node 断言（frontend/verify_inventory_display.mjs）。

/** 缩略图尺寸（像素）。与矩阵表一致：商品图是 1:1 正方形，缩略图按 1:1 铺满展示框。 */
export const THUMB_SIZE = 120;

/** 缺值占位符：数据缺失（没有这个值）。 */
export const EMPTY = "—";

/** 未登记占位符：业务状态（该去登记），刻意与数据缺失长得不一样。 */
export const NOT_REGISTERED = "--";

/**
 * 由原图路径推导缩略图路径。
 *
 * @param pic - 原图访问路径（形如 `/inv-pics/jpzy/100080186911.jpg`）；空值返回空串。
 * @param size - 缩略图尺寸（像素）。
 * @param fmt - 缩略图格式。
 * @returns 缩略图路径；路径层级不足时返回空串。
 */
export function invThumb(
  pic: string | null | undefined,
  size: number,
  fmt: "avif" | "webp",
): string {
  if (!pic) return "";
  const parts = pic.split("/").filter(Boolean);
  if (parts.length < 3) return "";
  const sub = parts[parts.length - 2];
  const sku = (parts[parts.length - 1] ?? "").replace(/\.[^.]+$/, "");
  if (!sub || !sku) return "";
  return `/thumbs/inv/${sub}/${size}/${sku}.${fmt}`;
}

/**
 * 商品图的降级链：avif → webp → 原图。
 *
 * @param pic - 原图访问路径；空值返回空数组。
 * @param size - 缩略图尺寸（像素）。
 * @returns 依次尝试的图片地址；全部失败则由调用方显示占位。
 */
export function invThumbChain(pic: string | null | undefined, size: number): string[] {
  if (!pic) return [];
  const avif = invThumb(pic, size, "avif");
  const webp = invThumb(pic, size, "webp");
  const chain: string[] = [];
  if (avif) chain.push(avif);
  if (webp) chain.push(webp);
  chain.push(pic);
  return chain;
}

/**
 * 商品图的 srcset（同一张图两种格式，让浏览器自选）。
 *
 * @param pic - 原图访问路径；空值返回空串。
 * @returns srcset 文本；无图时返回空串。
 */
export function invThumbSrcSet(pic: string | null | undefined): string {
  if (!pic) return "";
  const avif = invThumb(pic, THUMB_SIZE, "avif");
  const webp = invThumb(pic, THUMB_SIZE, "webp");
  if (!avif || !webp) return "";
  return `${avif} ${THUMB_SIZE}w, ${webp} ${THUMB_SIZE}w`;
}

/**
 * ERP 编码的显示值：未登记显示「--」（不是 0——0 会被下游当真值用）。
 *
 * @param code - ERP 编码；空值视为未登记。
 * @returns 编码文本或占位符。
 */
export function erpText(code: string | null | undefined): string {
  const text = (code ?? "").trim();
  return text ? text : NOT_REGISTERED;
}

/**
 * 文本缺省值：缺失显示「—」。
 *
 * @param value - 原始文本。
 * @returns 原文本或占位符。
 */
export function orEmpty(value: string | null | undefined): string {
  const text = (value ?? "").trim();
  return text ? text : EMPTY;
}

/**
 * 预计可售天数的显示值：保留一位小数；缺失显示「—」。
 *
 * @remarks
 * 0 是有效值（今天就没得卖了），不能当成缺值。
 *
 * @param value - 天数（可为小数）。
 * @returns 形如 `11.2` 的文本，或占位符。
 */
export function sellableDaysText(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return EMPTY;
  return value.toFixed(1);
}

/**
 * 上下柜状态的显示值：缺失显示「—」。
 *
 * @param value - 上下柜状态文本。
 * @returns 原文本或占位符。
 */
export function shelfText(value: string | null | undefined): string {
  return orEmpty(value);
}
