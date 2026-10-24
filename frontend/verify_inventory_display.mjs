// 验证库存/补货视图的展示判定纯函数（frontend/src/modules/inventory.ts）。
// 矩阵表与补货工作台共用同一份实现，这里把行为锁死，防止改了一处忘了另一处。
// 运行：npm run verify:inventory
import {
  THUMB_SIZE,
  erpText,
  invThumb,
  invThumbChain,
  invThumbSrcSet,
  orEmpty,
  sellableDaysText,
  shelfText,
} from "./src/modules/inventory.ts";

let pass = 0;
let fail = 0;

function eq(actual, expected, label) {
  const A = JSON.stringify(actual);
  const E = JSON.stringify(expected);
  if (A === E) pass++;
  else {
    fail++;
    console.log(`FAIL ${label}\n  期望 ${E}\n  实际 ${A}`);
  }
}

const PIC = "/inv-pics/jpzy/100080186911.jpg";

// 商品图 → 缩略图
eq(invThumb(PIC, 120, "avif"), "/thumbs/inv/jpzy/120/100080186911.avif", "缩略图路径");
eq(invThumb("", 120, "avif"), "", "空图路径返回空串");
eq(invThumb(null, 120, "avif"), "", "null 图路径返回空串");
eq(invThumb("/x.jpg", 120, "avif"), "", "层级不足的路径返回空串");

// 降级链：avif → webp → 原图
eq(invThumbChain(PIC, 120).length, 3, "降级链长度");
eq(invThumbChain(PIC, 120)[2], PIC, "降级链末档是原图");
eq(invThumbChain("", 120).length, 0, "无图时降级链为空");

// srcset
eq(
  invThumbSrcSet(PIC),
  `/thumbs/inv/jpzy/${THUMB_SIZE}/100080186911.avif ${THUMB_SIZE}w, /thumbs/inv/jpzy/${THUMB_SIZE}/100080186911.webp ${THUMB_SIZE}w`,
  "srcset 两种格式",
);

// ERP 编码：未登记是「--」，不是 0
eq(erpText("JD-10C-PKCTO"), "JD-10C-PKCTO", "ERP 有值原样");
eq(erpText(null), "--", "ERP 未登记(null)");
eq(erpText(""), "--", "ERP 未登记(空串)");
eq(erpText("0"), "0", "编码真的是 0 时原样显示");

// 缺值占位
eq(orEmpty(null), "—", "缺值占位(null)");
eq(orEmpty("x"), "x", "有值原样");

// 预计可售天数
eq(sellableDaysText(11.2), "11.2", "天数保留一位小数");
eq(sellableDaysText(8), "8.0", "整数也带一位小数");
eq(sellableDaysText(0), "0.0", "0 是有效值");
eq(sellableDaysText(null), "—", "天数缺值(null)");

// 上下柜状态
eq(shelfText("上架"), "上架", "上下柜原样");
eq(shelfText(null), "—", "上下柜缺值");

console.log(`\n通过 ${pass} / 失败 ${fail}`);
if (fail > 0) process.exit(1);

