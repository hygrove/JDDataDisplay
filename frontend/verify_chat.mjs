// AI 对话纯函数断言：SSE 帧解析 + 会话历史管理（工单 03）。
// 纯静态断言，不需要 8000 在跑（与 verify:tokens 同级）。
import {
  splitSseFrames,
  parseSseFrame,
  appendMessage,
  buildContextMessages,
  conversationTitle,
  deleteConversation,
  emptyHistory,
  loadHistory,
  newConversation,
  normalizeModel,
  stoppedContent,
  STOPPED_MARKER,
  CONTEXT_MESSAGE_LIMIT,
  MAX_CONVERSATIONS,
} from "./src/modules/chat.ts";

let pass = 0;
let fail = 0;
function eq(a, e, l) {
  const A = JSON.stringify(a);
  const E = JSON.stringify(e);
  if (A === E) pass++;
  else {
    fail++;
    console.log(`FAIL ${l}\n  期望 ${E}\n  实际 ${A}`);
  }
}
function ok(cond, l) {
  if (cond) pass++;
  else {
    fail++;
    console.log(`FAIL ${l}`);
  }
}

// ---------- splitSseFrames：帧边界与不完整尾部 ----------

// 完整帧 + 不完整尾部（网络分块把下一条帧劈开的最常见形态）
const r1 = splitSseFrames('data: {"type":"delta","text":"你"}\n\ndata: {"type"');
eq(r1.frames.length, 1, "split: 只出完整帧");
eq(r1.frames[0], 'data: {"type":"delta","text":"你"}', "split: 帧内容");
eq(r1.rest, 'data: {"type"', "split: 尾部保留待拼接");

// 尾部拼接后再切：跨段帧能拼回一条完整帧
const r2 = splitSseFrames(r1.rest + ':"done"}\n\n');
eq(r2.frames.length, 1, "split: 跨段拼接成帧");
eq(r2.frames[0], 'data: {"type":"done"}', "split: 拼接帧内容");
eq(r2.rest, "", "split: 拼接后尾部清空");
eq(parseSseFrame(r2.frames[0]).type, "done", "parse: 跨段帧可解析");

// 没有任何边界：全部是尾部（首包很小 / 长帧被多次分块）
const r3 = splitSseFrames("data: {");
eq(r3.frames, [], "split: 无边界不出帧");
eq(r3.rest, "data: {", "split: 无边界全留尾部");

// 连续多帧一次到达
const r4 = splitSseFrames('data: a\n\ndata: b\n\ndata: c\n\n');
eq(r4.frames.length, 3, "split: 多帧一次切出");
eq(r4.rest, "", "split: 多帧后无尾部");

// \r\n 变体（反代可能改写行尾）：先归一再切，边界不丢
const r5 = splitSseFrames('data: {"type":"delta"}\r\n\r\ndata: {"type":"done"}\r\n\r\n');
eq(r5.frames.length, 2, "split: CRLF 边界可切");
eq(parseSseFrame(r5.frames[0]).type, "delta", "parse: CRLF 帧可解析");

// CRLF 被网络分块劈开：\r 在上一段末尾、\n 在下一段开头，仍能拼出边界
const r6a = splitSseFrames('data: {"type":"delta"}\r');
const r6b = splitSseFrames(r6a.rest + '\n\r\n');
eq(r6b.frames.length, 1, "split: 跨段 CRLF 拼出边界");
eq(parseSseFrame(r6b.frames[0]).type, "delta", "parse: 跨段 CRLF 帧可解析");

// ---------- parseSseFrame：载荷解析与容错 ----------

// 正常 delta / done 帧
eq(
  parseSseFrame('data: {"type":"delta","text":"你好"}'),
  { type: "delta", text: "你好" },
  "parse: delta 帧",
);
const done = parseSseFrame('data: {"type":"done","markdown":"# 报告","html":"<h1>报告</h1>"}');
eq(done.type, "done", "parse: done 帧 type");
eq(done.markdown, "# 报告", "parse: done 帧 markdown");
eq(done.html, "<h1>报告</h1>", "parse: done 帧 html");

// 非 data 行（SSE 注释 / 事件行）：忽略
eq(parseSseFrame(": keep-alive"), null, "parse: 注释行返回 null");
eq(parseSseFrame("event: ping\ndata: not-json"), null, "parse: 非 JSON 载荷返回 null");

// JSON 损坏：单帧跳过不炸（流不中断）
eq(parseSseFrame("data: {broken"), null, "parse: 损坏 JSON 返回 null");

// 缺 type 字段或 type 非字符串：当无效帧
eq(parseSseFrame('data: {"text":"无类型"}'), null, "parse: 无 type 返回 null");
eq(parseSseFrame('data: {"type":123}'), null, "parse: type 非字符串返回 null");

// 中文与 emoji 内容（多字节 UTF-8 分块的高危场景）
const cn = parseSseFrame('data: {"type":"delta","text":"销售额 ¥12.5 万 📈"}');
eq(cn.text, "销售额 ¥12.5 万 📈", "parse: 中文/emoji 内容不丢");

// ---------- 工单 02：tool_call 帧与溯源 ----------

// tool_call 帧：名称 + 参数对象
const tc = parseSseFrame('data: {"type":"tool_call","name":"pop_rows","args":{"shop":"钻芯旗舰店","sort_by":"amount"}}');
eq(tc.type, "tool_call", "parse: tool_call type");
eq(tc.name, "pop_rows", "parse: tool_call name");
eq(tc.args, { shop: "钻芯旗舰店", sort_by: "amount" }, "parse: tool_call args 对象完整");

// done 帧携带溯源数组（provenance 可能出现在 delta 之后的任意 done 帧）
const doneProv = parseSseFrame('data: {"type":"done","markdown":"结论","html":"<p>结论</p>","provenance":[{"tool":"pop_rows","args":{"shop":"钻芯"},"result_preview":"{\\"total\\":42}"}]}');
eq(doneProv.type, "done", "parse: done type");
eq(Array.isArray(doneProv.provenance), true, "parse: provenance 是数组");
eq(doneProv.provenance[0].tool, "pop_rows", "parse: 溯源工具名");
eq(doneProv.provenance[0].result_preview, '{"total":42}', "parse: 溯源结果预览");

// 无 provenance 的 done 帧（纯闲聊）：不炸、字段缺省
const donePlain = parseSseFrame('data: {"type":"done","markdown":"你好","html":"<p>你好</p>"}');
eq(donePlain.provenance, undefined, "parse: 无溯源 done 帧正常");

// ---------- 会话历史：载入容错 ----------

eq(loadHistory(null), emptyHistory(), "load: null → 空历史");
eq(loadHistory(""), emptyHistory(), "load: 空串 → 空历史");
eq(loadHistory("not-json{{{"), emptyHistory(), "load: 损坏 JSON → 空历史");
eq(loadHistory('{"foo":1}'), emptyHistory(), "load: 无 conversations 键 → 空历史");

// 坏对话行被丢弃、合法行保留；activeId 失效回退到最新
const mixed = loadHistory(JSON.stringify({
  conversations: [
    { id: "a", messages: "not-array" },                                  // 结构坏
    { messages: [{ role: "user", content: "x" }] },                      // 缺 id
    { id: "b", title: "t", updatedAt: 2, messages: [{ role: "user", content: "你好" }, { role: "system", content: "注入" }] },
    { id: "c", title: "t", updatedAt: 1, messages: [{ role: "assistant", content: "答" }] },
  ],
  activeId: "gone",
}));
eq(mixed.conversations.map((c) => c.id), ["b", "c"], "load: 坏行丢弃+按新旧排序");
eq(mixed.activeId, "b", "load: activeId 失效回退最新");
// system 角色消息被过滤（历史只存 user/assistant）
eq(mixed.conversations[0].messages.length, 1, "load: 非法角色消息被过滤");

// 超 5 个裁到 5 个最新的（updatedAt 最小的被淘汰）
const six = Array.from({ length: 6 }, (_, i) => ({
  id: `c${i}`, title: `t${i}`, updatedAt: i + 1, messages: [],
}));
const trimmed = loadHistory(JSON.stringify({ conversations: six, activeId: "c0" }));
eq(trimmed.conversations.length, MAX_CONVERSATIONS, "load: 超上限裁到 5");
eq(trimmed.conversations.map((c) => c.id), ["c5", "c4", "c3", "c2", "c1"], "load: 淘汰最旧");
eq(trimmed.activeId, "c5", "load: activeId 被淘汰后回退最新");

// ---------- 新建与追加 ----------

// 新建：空历史 → 创建并激活
const h1 = newConversation(emptyHistory(), "n1", 100);
eq(h1.conversations.length, 1, "new: 空历史创建对话");
eq(h1.activeId, "n1", "new: 新对话激活");

// 当前对话已空（0 条）时复用，不堆空记录
const h1b = newConversation(h1, "n2", 200);
eq(h1b.conversations.length, 1, "new: 空对话时复用不新建");
eq(h1b.activeId, "n1", "new: 复用时保持原对话");

// 当前有内容时新建 → 新对话激活、旧的保留
const h2 = appendMessage(h1, { role: "user", content: "第一条问题" }, 150);
const h3 = newConversation(h2, "n2", 200);
eq(h3.conversations.length, 2, "new: 有内容时正常新建");
eq(h3.activeId, "n2", "new: 新对话成为当前");

// 追加：无活动对话时自动新建并定题
const ha = appendMessage(emptyHistory(), { role: "user", content: "自动开聊" }, 300, "auto-1");
eq(ha.activeId, "auto-1", "append: 自动新建对话");
eq(ha.conversations[0].title, "自动开聊", "append: 首条 user 消息定题");

// 追问归入当前对话、置顶、刷新时间
const hb = appendMessage(ha, { role: "assistant", content: "答1", html: "<p>答1</p>" }, 310);
const hc = appendMessage(hb, { role: "user", content: "追问" }, 320);
eq(hc.conversations[0].messages.length, 3, "append: 追问归入同一对话");
eq(hc.conversations[0].updatedAt, 320, "append: 刷新 updatedAt");

// 超上限：第 6 个对话出现时淘汰最旧
let hgrow = emptyHistory();
for (let i = 0; i < 7; i++) {
  hgrow = newConversation(hgrow, `g${i}`, i + 1);
  hgrow = appendMessage(hgrow, { role: "user", content: `问题${i}` }, i + 1);
}
eq(hgrow.conversations.length, MAX_CONVERSATIONS, "append/new: 总量守住 5");
eq(hgrow.conversations.map((c) => c.id), ["g6", "g5", "g4", "g3", "g2"], "grow: 最旧的 g0/g1 被淘汰");

// 标题截断（20 字 + …）与空白规整
eq(conversationTitle("  多个   空白\n换行  "), "多个 空白 换行", "title: 空白规整");
eq(conversationTitle(""), "新对话", "title: 空消息默认标题");
eq(conversationTitle("一二三四五六七八九十一二三四五六七八九十一").length, 21, "title: 21 字 = 20 字+省略号");
eq(conversationTitle("一二三四五六七八九十一二三四五六七八九十一"), "一二三四五六七八九十一二三四五六七八九十…", "title: 超长截断");

// ---------- 上下文截断 ----------

// 20 条消息 → 只取最近 12 条
const many = Array.from({ length: 20 }, (_, i) => ({
  role: i % 2 === 0 ? "user" : "assistant",
  content: `m${i}`,
}));
const ctx = buildContextMessages(many);
eq(ctx.length, CONTEXT_MESSAGE_LIMIT, "ctx: 截到最近 12 条");
eq(ctx[0].content, "m8", "ctx: 从 m8（下标 20-12）开始");
eq(ctx[ctx.length - 1].content, "m19", "ctx: 尾部完整");

// 截断后以 assistant 开头 → 丢弃到首条 user
const leading = Array.from({ length: 12 }, (_, i) => ({
  role: i === 0 ? "assistant" : i === 1 ? "assistant" : i % 2 === 0 ? "user" : "assistant",
  content: `m${i}`,
}));
// 构造：前 2 条 assistant，之后 user/assistant 交替 → 截断窗口开头是 assistant
const ctx2 = buildContextMessages(leading);
eq(ctx2[0].role, "user", "ctx: 头部孤立 assistant 被丢弃");
ok(ctx2.every((m) => m.role === "user" || m.role === "assistant"), "ctx: 角色合法");

// 展示层字段不进上下文（html / provenance 剥掉）
const withHtml = [
  { role: "user", content: "问" },
  { role: "assistant", content: "答", html: "<p>答</p>", provenance: [{ tool: "t", args: {}, result_preview: "x" }] },
];
const ctx3 = buildContextMessages(withHtml);
eq(Object.keys(ctx3[1]).sort(), ["content", "role"], "ctx: 只保留 role/content");

// ---------- 模型偏好容错（工单 05） ----------

eq(normalizeModel("deepseek-reasoner"), "deepseek-reasoner", "model: 合法 reasoner 透传");
eq(normalizeModel("deepseek-chat"), "deepseek-chat", "model: 合法 chat 透传");
eq(normalizeModel(null), "deepseek-chat", "model: null 回默认");
eq(normalizeModel(""), "deepseek-chat", "model: 空串回默认");
eq(normalizeModel("gpt-4"), "deepseek-chat", "model: 未知值回默认（防手改脏数据）");
eq(normalizeModel(undefined), "deepseek-chat", "model: undefined 回默认");

// ---------- 停止回答：半截内容入库（stoppedContent） ----------

// 有半截文本：末尾追加停止标注，原文保留
eq(stoppedContent("根据数据，销售额"), `根据数据，销售额\n\n${STOPPED_MARKER}`, "stop: 半截文本追加标注");
// 空白文本视同无内容：存占位说明（content 不能为空串，否则 loadHistory 当坏行丢弃）
eq(stoppedContent("   "), "（已停止：未生成内容）", "stop: 空白回占位");
eq(stoppedContent(""), "（已停止：未生成内容）", "stop: 空串回占位");
// 停止标注会随上下文回传：入库后的消息进 buildContextMessages 不被剥掉
const stoppedMsg = [
  { role: "user", content: "问" },
  { role: "assistant", content: stoppedContent("半截") },
];
eq(buildContextMessages(stoppedMsg)[1].content, `半截\n\n${STOPPED_MARKER}`, "stop: 标注随上下文回传");
// 半截消息经 loadHistory 往返不丢（content 非空即合法行）
const reloaded = loadHistory(JSON.stringify({
  conversations: [{ id: "s1", title: "t", updatedAt: 1, messages: stoppedMsg }],
  activeId: "s1",
}));
eq(reloaded.conversations[0].messages[1].content, `半截\n\n${STOPPED_MARKER}`, "stop: 历史往返不丢");

// ---------- 删除对话（deleteConversation） ----------

const delBase = {
  conversations: [
    { id: "x1", title: "a", messages: [{ role: "user", content: "1" }], updatedAt: 300 },
    { id: "x2", title: "b", messages: [{ role: "user", content: "2" }], updatedAt: 200 },
    { id: "x3", title: "c", messages: [{ role: "user", content: "3" }], updatedAt: 100 },
  ],
  activeId: "x1",
};
// 删非当前：目标消失、其余保序，当前对话不变
const d1 = deleteConversation(delBase, "x3");
eq(d1.conversations.map((c) => c.id), ["x1", "x2"], "del: 删非当前，移除目标");
eq(d1.activeId, "x1", "del: 删非当前，activeId 不变");
// 删当前：回退到剩余最近一条（列表已降序，第 0 个最新）
const d2 = deleteConversation(delBase, "x1");
eq(d2.conversations.map((c) => c.id), ["x2", "x3"], "del: 删当前，移除");
eq(d2.activeId, "x2", "del: 删当前回退最近一条");
// 删到空：列表空、activeId null
const d3 = deleteConversation({ conversations: [{ id: "z", title: "", messages: [], updatedAt: 1 }], activeId: "z" }, "z");
eq(d3.conversations, [], "del: 删空列表");
eq(d3.activeId, null, "del: 删空后 activeId null");
// 不存在的 id：幂等，原样返回（防重复点击炸态）
const d4 = deleteConversation(delBase, "nope");
eq(d4.conversations.length, 3, "del: 不存在 id 幂等，列表不变");
eq(d4.activeId, "x1", "del: 不存在 id activeId 不变");

console.log(`\nchat 断言：${pass} 通过，${fail} 失败`);
if (fail > 0) process.exit(1);
