// AI 对话的纯函数层：SSE 帧解析 + 会话历史管理。
// 约定（ai-rules §3「判定与渲染分离」）：可断言的逻辑放本文件（node 断言见
// verify_chat.mjs），.vue 只负责渲染与交互。
//
// 会话历史的语义（CONTEXT.md「对话」「追问」）：
// - **对话** = 从打开话题到点「新建对话」之间的全部来回；localStorage 最多
//   保留 5 个，超出淘汰最旧（updatedAt 最小）。
// - **追问** = 同一对话内的后续提问，归入当前记录，不产生新对话。
// - 上下文 = 当前对话**最近 12 条**消息随请求发给后端（后端无状态）。

import type { ProvenanceCall } from "../api";

// ---------------------------------------------------------------------------
// 会话历史（工单 03）
// ---------------------------------------------------------------------------

/** localStorage 保存的一条消息：assistant 额外带渲染 HTML 与溯源。 */
export interface StoredMessage {
  role: "user" | "assistant";
  /** markdown 原文（user 为输入原文） */
  content: string;
  /** 后端渲染后的 HTML（仅 assistant） */
  html?: string;
  /** 溯源（仅 assistant） */
  provenance?: ProvenanceCall[];
}

/** 一个对话：连续追问的完整来回，直到用户点「新建对话」。 */
export interface StoredConversation {
  id: string;
  /** 首条用户消息截断生成的标题 */
  title: string;
  messages: StoredMessage[];
  /** 最后活动时间戳（ms）——排序与淘汰依据 */
  updatedAt: number;
}

/** 全量历史（localStorage 单键存的 JSON 结构）。 */
export interface ChatHistory {
  /** 最近使用在前（updatedAt 降序） */
  conversations: StoredConversation[];
  /** 当前对话 id；空历史为 null */
  activeId: string | null;
}

/** 最多保留的对话数（超出淘汰最旧）。 */
export const MAX_CONVERSATIONS = 5;
/** 单次请求携带的上下文消息上限（当前对话的最近 N 条）。 */
export const CONTEXT_MESSAGE_LIMIT = 12;
/** 模型偏好 localStorage 键（工单 05）。 */
export const MODEL_PREF_KEY = "jd.chat-model.v1";
/** 对话标题截断长度。 */
const TITLE_MAX = 20;

/**
 * 把任意存储值规整成合法模型 id（工单 05：模型偏好容错读取）。
 *
 * @remarks
 * 只认 "deepseek-reasoner"，其余一切值（null / 旧版本残留 / 手改脏数据）
 * 一律回默认 "deepseek-chat"——模型清单的事实来源在后端目录，前端只做
 * "这个值还认不认"的最小判断。
 *
 * @param {unknown} value localStorage 里的原始值。
 * @returns {string} 合法模型 id。
 */
export function normalizeModel(value: unknown): string {
  return value === "deepseek-reasoner" ? "deepseek-reasoner" : "deepseek-chat";
}

/**
 * 空历史（没有任何对话；首条消息发送时自动开对话）。
 */
export function emptyHistory(): ChatHistory {
  return { conversations: [], activeId: null };
}

/**
 * 生成对话标题：首条用户消息去多余空白后截断。
 *
 * @param {string} text 首条用户消息原文。
 * @returns {string} ≤20 字的标题；空消息返回「新对话」。
 */
export function conversationTitle(text: string): string {
  const t = text.replace(/\s+/g, " ").trim();
  if (!t) return "新对话";
  return t.length > TITLE_MAX ? t.slice(0, TITLE_MAX) + "…" : t;
}

/**
 * 校验并规整单条 localStorage 里的对话记录（容错：坏行丢弃，不炸整库）。
 */
function _validConversation(raw: unknown): StoredConversation | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  if (typeof o.id !== "string" || !o.id) return null;
  if (!Array.isArray(o.messages)) return null;
  const messages: StoredMessage[] = [];
  for (const m of o.messages) {
    if (!m || typeof m !== "object") continue;
    const mm = m as Record<string, unknown>;
    if (
      (mm.role === "user" || mm.role === "assistant") &&
      typeof mm.content === "string" &&
      mm.content
    ) {
      messages.push({
        role: mm.role,
        content: mm.content,
        html: typeof mm.html === "string" ? mm.html : undefined,
        provenance: Array.isArray(mm.provenance)
          ? (mm.provenance as ProvenanceCall[])
          : undefined,
      });
    }
  }
  return {
    id: o.id,
    title: typeof o.title === "string" ? o.title : "",
    messages,
    updatedAt: typeof o.updatedAt === "number" ? o.updatedAt : 0,
  };
}

/**
 * 从 localStorage 原始字符串恢复历史（容错加载）。
 *
 * @remarks
 * 任何损坏（非 JSON / 结构不对 / 字段缺失）都降级而不是抛错——历史是
 * 增强体验，不是功能正确性的依赖；坏了顶多丢历史，不能让面板打不开。
 * 超过 5 个的旧数据按 updatedAt 淘汰到 5 个。
 *
 * @param {string | null} raw localStorage 里的原始字符串。
 * @returns {ChatHistory} 规整后的历史（最近使用在前）。
 */
export function loadHistory(raw: string | null): ChatHistory {
  if (!raw) return emptyHistory();
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return emptyHistory();
  }
  if (!parsed || typeof parsed !== "object") return emptyHistory();
  const obj = parsed as { conversations?: unknown; activeId?: unknown };
  if (!Array.isArray(obj.conversations)) return emptyHistory();
  const valid = obj.conversations
    .map(_validConversation)
    .filter((c): c is StoredConversation => c !== null)
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, MAX_CONVERSATIONS);
  const activeId =
    typeof obj.activeId === "string" && valid.some((c) => c.id === obj.activeId)
      ? obj.activeId
      : (valid[0]?.id ?? null);
  return { conversations: valid, activeId };
}

/**
 * 内部：裁剪到上限（淘汰 updatedAt 最旧的），并保证 activeId 仍在列表内。
 */
function _prune(h: ChatHistory): ChatHistory {
  const kept = [...h.conversations]
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, MAX_CONVERSATIONS);
  return {
    conversations: kept,
    activeId: kept.some((c) => c.id === h.activeId)
      ? h.activeId
      : (kept[0]?.id ?? null),
  };
}

/**
 * 新建对话（用户显式点「新建对话」）。
 *
 * @remarks
 * 当前对话已是空的（0 条消息）时直接复用——连点「新建」不该堆出一排
 * 空记录。新对话置顶并成为当前对话；超出 5 个淘汰最旧。
 *
 * @param {ChatHistory} h 当前历史。
 * @param {string} id 新对话的唯一 id（调用方生成，保证可测）。
 * @param {number} now 当前时间戳（ms，注入便于测试）。
 * @returns {ChatHistory} 新历史。
 */
export function newConversation(h: ChatHistory, id: string, now: number): ChatHistory {
  const active = h.conversations.find((c) => c.id === h.activeId);
  if (active && active.messages.length === 0) return h;
  const conv: StoredConversation = { id, title: "", messages: [], updatedAt: now };
  return _prune({ conversations: [conv, ...h.conversations], activeId: id });
}

/**
 * 追加一条消息到当前对话（追问归入当前记录，不产生新对话）。
 *
 * @remarks
 * 没有活动对话时（用户没点新建直接开聊）自动创建一个。首条 user 消息
 * 定题（conversationTitle）。该对话随即置顶（最近使用在前）并刷新
 * updatedAt；超出 5 个淘汰最旧。
 *
 * @param {ChatHistory} h 当前历史。
 * @param {StoredMessage} msg 新消息。
 * @param {number} now 当前时间戳（ms）。
 * @param {string} [newId] 自动新建对话时用的 id（缺省 `c-{now}`）。
 * @returns {ChatHistory} 新历史。
 */
export function appendMessage(
  h: ChatHistory,
  msg: StoredMessage,
  now: number,
  newId?: string,
): ChatHistory {
  const conv =
    h.conversations.find((c) => c.id === h.activeId) ??
    { id: newId ?? `c-${now}`, title: "", messages: [] as StoredMessage[], updatedAt: now };
  const updated: StoredConversation = {
    ...conv,
    messages: [...conv.messages, msg],
    updatedAt: now,
    // 标题只定一次：首条 user 消息
    title: conv.title || (msg.role === "user" ? conversationTitle(msg.content) : conv.title),
  };
  const rest = h.conversations.filter((c) => c.id !== updated.id);
  return _prune({ conversations: [updated, ...rest], activeId: updated.id });
}

/**
 * 构建随请求发送的上下文消息（当前对话的最近 12 条）。
 *
 * @remarks
 * 截断后若以 assistant 开头（它引用的 user 问题已被截掉、上下文悬空），
 * 丢弃头部 assistant 直到首条为 user。只保留 role / content——html 与
 * 溯源是展示层信息，不进上下文。
 *
 * @param {StoredMessage[]} messages 当前对话的全量消息。
 * @returns {{ role: "user" | "assistant"; content: string }[]} 上下文消息。
 */
export function buildContextMessages(
  messages: StoredMessage[],
): { role: "user" | "assistant"; content: string }[] {
  const tail = messages.slice(-CONTEXT_MESSAGE_LIMIT);
  const start = tail.findIndex((m) => m.role === "user");
  const useful = start === -1 ? [] : tail.slice(start);
  return useful.map((m) => ({ role: m.role, content: m.content }));
}

/**
 * 把 SSE 流的累计缓冲区切成完整帧。
 *
 * @remarks
 * SSE 协议以空行（`\n\n`）作为帧边界；网络分块可能把一条帧劈成两半，
 * 所以只把**最后一个边界之前**的内容当作完整帧，剩余尾部留给下一段数据拼接。
 *
 * @param {string} buffer 累计的原始文本（可能含不完整尾部）。
 * @returns {{ frames: string[]; rest: string }} 完整帧文本数组 + 未完成的尾部。
 * @example
 * ```ts
 * splitSseFrames('data: {"type":"delta"}\n\ndata: {"type"');
 * // { frames: ['data: {"type":"delta"}'], rest: 'data: {"type"' }
 * ```
 */
export function splitSseFrames(buffer: string): { frames: string[]; rest: string } {
  // 行尾归一：部分反代 / 网关会把 \n 改写成 \r\n，若不归一，
  // "\r\n\r\n" 里永远找不到 "\n\n" 边界，流式会整条卡死（云端部署高危点）。
  // 归一化对已是 \n 的内容幂等；尾部孤立的 \r 留待下一段拼接后再处理。
  const normalized = buffer.replace(/\r\n/g, "\n");
  const idx = normalized.lastIndexOf("\n\n");
  if (idx === -1) return { frames: [], rest: normalized };
  const complete = normalized.slice(0, idx);
  const rest = normalized.slice(idx + 2);
  const frames: string[] = [];
  for (const block of complete.split("\n\n")) {
    const b = block.trim();
    if (b) frames.push(b);
  }
  return { frames, rest };
}

/**
 * 解析单条 SSE 帧的 data 载荷。
 *
 * @remarks
 * 只取帧内 `data:` 开头的行（忽略注释行等）；JSON 损坏或缺少字符串型
 * `type` 字段时返回 null——单帧损坏不该中断整条流（与后端逐帧容错对称）。
 *
 * @param {string} frame 单条完整帧文本（如 `data: {"type":"delta","text":"hi"}`）。
 * @returns {{ type: string; [key: string]: unknown } | null} 帧对象；无法解析为 null。
 * @example
 * ```ts
 * parseSseFrame('data: {"type":"delta","text":"你好"}');
 * // { type: 'delta', text: '你好' }
 * parseSseFrame(': keep-alive'); // null
 * ```
 */
export function parseSseFrame(
  frame: string,
): { type: string; [key: string]: unknown } | null {
  const line = frame.split("\n").find((l) => l.startsWith("data:"));
  if (!line) return null;
  try {
    const obj = JSON.parse(line.slice(5).trim());
    if (obj && typeof obj === "object" && typeof obj.type === "string") {
      return obj as { type: string; [key: string]: unknown };
    }
    return null;
  } catch {
    return null;
  }
}
