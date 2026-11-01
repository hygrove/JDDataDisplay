<script setup lang="ts">
// AI 对话悬浮面板（工单 01：无对话持久化 / 无溯源 / 无导出的最小版）。
//
// 入口形态：视口**右上角**气泡按钮（用户指定），点开为悬浮面板，
// 不跳转、不打断当前数据页——「边看数据边问」。
// 消息渲染（spec Q13C）：流式期间显示原始文本，done 帧到达后替换为
// 后端渲染好的 HTML（v-html 安全：后端 markdown-it html=False 已转义）。
// 工单 02 加溯源与查询态；工单 03 加对话列表；工单 04 加导出按钮。
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import {
  exportChatReport,
  fetchChatConfig,
  putChatConfig,
  streamChat,
  type ChatConfig,
} from "../../api";
import {
  appendMessage,
  buildContextMessages,
  emptyHistory,
  loadHistory,
  newConversation,
  normalizeModel,
  MODEL_PREF_KEY,
  type ChatHistory,
  type StoredMessage,
} from "../../modules/chat";

const open = ref(false);
const settingsOpen = ref(false);
/** 会话历史（localStorage 持久化，最多 5 个对话） */
const history = ref<ChatHistory>(emptyHistory());
/** 对话列表视图开关（点 header 的历史按钮切换） */
const listOpen = ref(false);
const input = ref("");
const streaming = ref(false);
/** 流式中的原始文本（done 后清空，内容转入当前对话） */
const streamText = ref("");
/** 工具接地状态行：模型正在查数时显示"正在查询：xxx"（tool_call 帧） */
const toolStatus = ref("");
const error = ref("");
const cfg = ref<ChatConfig | null>(null);
const keyInput = ref("");
const savingKey = ref(false);
const listEl = ref<HTMLElement | null>(null);

/** localStorage 键名（沿用项目 `jd.*.v1` 命名惯例）。 */
const STORAGE_KEY = "jd.chat-history.v1";

/** 当前模型偏好（localStorage 持久化，随每个请求带给后端）。 */
const model = ref(normalizeModel(localStorage.getItem(MODEL_PREF_KEY)));

/** 切换器选项：优先用后端目录（含 tools_supported 降级提示），配置未拉到时兜底。 */
const modelOptions = computed(
  () =>
    cfg.value?.models ?? [
      { id: "deepseek-chat", label: "标准", tools_supported: true },
      { id: "deepseek-reasoner", label: "深度思考", tools_supported: true },
    ],
);

/** 当前选中模型的目录信息（用于"不查库"提示）。 */
const activeModelInfo = computed(
  () => modelOptions.value.find((m) => m.id === model.value) ?? null,
);

/** 切换模型并持久化偏好。 */
function setModel(id: string): void {
  model.value = id;
  try {
    localStorage.setItem(MODEL_PREF_KEY, id);
  } catch {
    /* 存不进 localStorage 只是丢偏好，不影响本次会话 */
  }
}

/** 当前对话（activeId 命中；无则为 null）。 */
const activeConv = computed(
  () => history.value.conversations.find((c) => c.id === history.value.activeId) ?? null,
);

/** 消息流渲染源 = 当前对话的消息。 */
const bubbles = computed<StoredMessage[]>(() => activeConv.value?.messages ?? []);

onMounted(() => {
  // 恢复历史对话（容错：损坏自动回空）+ 预拉配置
  history.value = loadHistory(localStorage.getItem(STORAGE_KEY));
  loadConfig();
  window.addEventListener("resize", onBubbleResize);
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", onBubbleResize);
});

/** 历史写盘（localStorage 满配额 / 隐私模式失败不致命，静默降级为不持久化）。 */
function persist(): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(history.value));
  } catch {
    /* 私有模式 / 配额超限：本次会话仅内存态 */
  }
}

/** 拉取配置（掩码视图）。 */
async function loadConfig(): Promise<void> {
  try {
    cfg.value = await fetchChatConfig();
  } catch (e) {
    // 配置拉失败不阻塞聊天，设置弹层里会显示「未配置」
    cfg.value = null;
  }
}

/** 展开 / 收起面板。 */
function toggle(): void {
  open.value = !open.value;
  if (open.value) void scrollBottom();
}

/* ---------- 气泡沿右边缘上下拖动 ---------- */
/** 落点持久化键（沿用 `jd.*.v1` 惯例）。 */
const BUBBLE_TOP_KEY = "jd.chat-bubble-top.v1";
/** 气泡尺寸与上下留白（px）。 */
const BUBBLE_SIZE = 44;
const BUBBLE_EDGE = 6;

/** 把 top 夹在可视范围内（窗口变矮时也不会跑出屏幕）。 */
function clampBubbleTop(v: number): number {
  const max = Math.max(BUBBLE_EDGE, window.innerHeight - BUBBLE_SIZE - BUBBLE_EDGE);
  return Math.max(BUBBLE_EDGE, Math.min(v, max));
}

/** 气泡纵向位置（初始取上次落点，缺省贴顶 12px）。 */
const bubbleTop = ref(clampBubbleTop(Number(localStorage.getItem(BUBBLE_TOP_KEY)) || 12));

// 拖动过程态无需响应式，用普通变量
let dragging = false;
let dragMoved = false;
let dragStartY = 0;
let dragStartTop = 0;

function onBubblePointerDown(e: PointerEvent): void {
  dragging = true;
  dragMoved = false;
  dragStartY = e.clientY;
  dragStartTop = bubbleTop.value;
  try {
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  } catch {
    /* 个别环境不支持指针捕获，退化为无捕获拖动 */
  }
}
function onBubblePointerMove(e: PointerEvent): void {
  if (!dragging) return;
  const dy = e.clientY - dragStartY;
  if (Math.abs(dy) > 4) dragMoved = true;
  if (dragMoved) bubbleTop.value = clampBubbleTop(dragStartTop + dy);
}
function onBubblePointerUp(e: PointerEvent): void {
  if (!dragging) return;
  dragging = false;
  try {
    (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
  } catch {
    /* 指针已释放，忽略 */
  }
  if (dragMoved) {
    try {
      localStorage.setItem(BUBBLE_TOP_KEY, String(bubbleTop.value));
    } catch {
      /* 存不进只是丢位置偏好 */
    }
  } else {
    toggle(); // 未移动 = 点击，开合面板
  }
  dragMoved = false;
}
function onBubblePointerCancel(): void {
  dragging = false;
  dragMoved = false;
}
/** 窗口尺寸变化时重新夹取位置。 */
function onBubbleResize(): void {
  bubbleTop.value = clampBubbleTop(bubbleTop.value);
}

/**
 * 消息区滚到底部（发送 / 每段增量后调用，跟住输出）。
 */
async function scrollBottom(): Promise<void> {
  await nextTick();
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight;
}

/**
 * 保存 API Key（设置弹层）。
 *
 * @remarks 保存成功后掩码回显立即更新；失败把后端 detail 显示在错误条。
 */
async function saveKey(): Promise<void> {
  const key = keyInput.value.trim();
  if (!key || savingKey.value) return;
  savingKey.value = true;
  error.value = "";
  try {
    cfg.value = await putChatConfig({ api_key: key });
    keyInput.value = "";
    settingsOpen.value = false;
  } catch (e) {
    error.value = String(e);
  } finally {
    savingKey.value = false;
  }
}

/** 新建对话（流式回答中禁用，防止当前回答写错对话）。 */
function startNewChat(): void {
  if (streaming.value) return;
  history.value = newConversation(history.value, `c-${Date.now()}`, Date.now());
  persist();
  listOpen.value = false;
  error.value = "";
  streamText.value = "";
  toolStatus.value = "";
  void scrollBottom();
}

/** 切换到历史对话（流式回答中禁用，理由同上）。 */
function switchConversation(id: string): void {
  if (streaming.value) return;
  history.value = { ...history.value, activeId: id };
  persist();
  listOpen.value = false;
  error.value = "";
  streamText.value = "";
  toolStatus.value = "";
  void scrollBottom();
}

/**
 * 对话列表用的相对时间（刚刚 / N 分钟前 / 今天 HH:mm / M-DD）。
 */
function fmtTime(ts: number): string {
  if (!ts) return "";
  const d = new Date(ts);
  const now = new Date();
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  if (d.toDateString() === now.toDateString()) {
    const diffMin = Math.floor((now.getTime() - ts) / 60000);
    if (diffMin < 1) return "刚刚";
    if (diffMin < 60) return `${diffMin} 分钟前`;
    return `${hh}:${mm}`;
  }
  return `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, "0")}`;
}

/**
 * 发送当前输入：追加用户消息到当前对话 → SSE 流式 → 增量进 streamText →
 * done 转 assistant 消息（渲染 HTML + 溯源）；上下文取当前对话最近 12 条。
 */
async function send(): Promise<void> {
  const text = input.value.trim();
  if (!text || streaming.value) return;
  input.value = "";
  error.value = "";
  // 追问归入当前对话；没有活动对话时 appendMessage 自动新建
  history.value = appendMessage(history.value, { role: "user", content: text }, Date.now());
  persist();
  streaming.value = true;
  streamText.value = "";
  toolStatus.value = "";
  await scrollBottom();
  const context = buildContextMessages(bubbles.value);
  try {
    await streamChat({ messages: context, model: model.value }, {
      onToolCall: (name) => {
        toolStatus.value = name;
        void scrollBottom();
      },
      onDelta: (t) => {
        streamText.value += t;
        void scrollBottom();
      },
      onDone: (markdown, html, provenance) => {
        history.value = appendMessage(
          history.value,
          { role: "assistant", content: markdown, html, provenance },
          Date.now(),
        );
        persist();
        streamText.value = "";
        toolStatus.value = "";
      },
      onError: (message) => {
        error.value = message;
        streamText.value = "";
        toolStatus.value = "";
      },
    });
  } finally {
    streaming.value = false;
    toolStatus.value = "";
    void scrollBottom();
  }
}

/** 溯源参数的紧凑展示（对象转单行 k=v）。 */
function fmtArgs(args: unknown): string {
  if (args === null || args === undefined) return "";
  if (typeof args !== "object") return String(args);
  const entries = Object.entries(args as Record<string, unknown>)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k}=${String(v)}`);
  return entries.join(" ");
}

/** 导出进行中（任一按钮点击后短暂禁用全部导出按钮）。 */
const exporting = ref(false);

/** 标题转合法文件名（Windows 非法字符替换为下划线）。 */
function safeFilename(t: string): string {
  return t.replace(/[\\/:*?"<>|]/g, "_").trim() || "AI 报告";
}

/**
 * 导出一条 AI 回答为报告文件并触发浏览器下载（与现有 xlsx 导出同款交互）。
 *
 * @param {string} markdown 回答的 markdown 原文。
 * @param {"html" | "docx"} format 目标格式：HTML 内嵌样式 / Word 真 docx。
 */
async function exportReport(markdown: string, format: "html" | "docx"): Promise<void> {
  if (exporting.value || !markdown) return;
  exporting.value = true;
  error.value = "";
  try {
    const title = activeConv.value?.title || "AI 报告";
    const blob = await exportChatReport(format, markdown, title);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${safeFilename(title)}.${format}`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    error.value = `导出失败：${String(e)}`;
  } finally {
    exporting.value = false;
  }
}
</script>

<template>
  <!-- 右上角气泡入口（可沿右边缘上下拖动，落点记忆；面板打开时隐藏，由面板内 ✕ 收起）。
       T1 科技风：脉冲光圈 + 旋转微光环 + 蓝青渐变「AI」圆钮 + 琥珀角标。 -->
  <div
    v-if="!open"
    class="chat-bubble-wrap"
    :style="{ top: bubbleTop + 'px' }"
    @pointerdown="onBubblePointerDown"
    @pointermove="onBubblePointerMove"
    @pointerup="onBubblePointerUp"
    @pointercancel="onBubblePointerCancel"
  >
    <div class="chat-bubble-ring" aria-hidden="true"></div>
    <div class="chat-bubble-halo" aria-hidden="true"></div>
    <button
      class="chat-bubble"
      data-testid="chat-bubble"
      title="AI 助手（可上下拖动）"
      aria-label="打开 AI 助手"
      @keydown.enter.prevent="toggle"
      @keydown.space.prevent="toggle"
    >
      AI
    </button>
    <div class="chat-bubble-dot" aria-hidden="true"></div>
  </div>

  <!-- 悬浮面板 -->
  <div v-else class="chat-panel" data-testid="chat-panel">
    <header class="chat-head">
      <span class="chat-title">AI 助手</span>
      <span class="chat-head-actions">
        <button
          class="icon-btn"
          :class="{ 'icon-btn-active': listOpen }"
          data-testid="chat-history-btn"
          title="历史对话"
          aria-label="历史对话"
          @click="listOpen = !listOpen"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
        </button>
        <button
          class="icon-btn"
          data-testid="chat-settings-btn"
          title="设置"
          aria-label="设置"
          @click="settingsOpen = !settingsOpen"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>
        <button
          class="icon-btn"
          data-testid="chat-close"
          title="收起"
          aria-label="收起面板"
          @click="toggle"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </span>
    </header>

    <!-- 设置弹层：key 输入 + 掩码回显（明文 key 永不回前端，ADR-0017） -->
    <div v-if="settingsOpen" class="chat-settings" data-testid="chat-settings">
      <div class="set-label">DeepSeek API Key</div>
      <div v-if="cfg?.key_mask" class="set-mask" data-testid="chat-key-mask">
        当前：{{ cfg.key_mask }}
      </div>
      <div v-else class="set-mask" :class="{ warn: true }">未配置</div>
      <div class="set-row">
        <input
          v-model="keyInput"
          class="set-input"
          type="password"
          placeholder="粘贴 API Key"
          autocomplete="off"
          data-testid="chat-key-input"
          @keyup.enter="saveKey"
        />
        <button
          class="set-save"
          :disabled="!keyInput.trim() || savingKey"
          data-testid="chat-key-save"
          @click="saveKey"
        >
          {{ savingKey ? "保存中…" : "保存" }}
        </button>
      </div>
      <div class="set-hint">
        Key 只保存在服务端数据目录，不进代码仓库；也可用环境变量
        DEEPSEEK_API_KEY 配置。输入新值直接覆盖旧值。
      </div>
    </div>

    <!-- 消息流（两种视图：历史对话列表 / 当前对话消息） -->
    <div ref="listEl" class="chat-msgs" data-testid="chat-messages">
      <!-- 对话列表视图：最多 5 项，点击恢复，当前项高亮 -->
      <div v-if="listOpen" class="chat-conv-list" data-testid="chat-conv-list">
        <button
          class="conv-new"
          data-testid="chat-new-conv"
          :disabled="streaming"
          @click="startNewChat"
        >
          ＋ 新建对话
        </button>
        <button
          v-for="c in history.conversations"
          :key="c.id"
          class="conv-item"
          :class="{ active: c.id === history.activeId }"
          :data-testid="`chat-conv-item-${c.id}`"
          :disabled="streaming"
          @click="switchConversation(c.id)"
        >
          <span class="conv-title">{{ c.title || "新对话" }}</span>
          <span class="conv-meta">{{ c.messages.length }} 条 · {{ fmtTime(c.updatedAt) }}</span>
        </button>
        <div v-if="!history.conversations.length" class="chat-empty">
          还没有历史对话<br />直接提问会自动开始一个
        </div>
      </div>

      <!-- 当前对话视图 -->
      <template v-else>
      <div v-if="!bubbles.length && !streaming" class="chat-empty">
        有任何数据问题直接问，例如：<br />「本周哪家店铺销售额最高？」
      </div>
      <div
        v-for="(b, i) in bubbles"
        :key="i"
        class="msg"
        :class="b.role"
        :data-testid="`chat-msg-${b.role}`"
      >
        <!-- assistant 用后端渲染的 HTML；user 纯文本 -->
        <div v-if="b.role === 'assistant' && b.html" class="chat-md" v-html="b.html"></div>
        <template v-else>{{ b.content }}</template>
        <!-- 导出按钮：每条 AI 回答都可导出（spec Q11），与 xlsx 导出同款下载交互 -->
        <div v-if="b.role === 'assistant' && b.content" class="msg-actions">
          <button
            class="act-btn"
            :disabled="exporting"
            data-testid="chat-export-html"
            title="导出为 HTML 报告（双击可开，支持条形图）"
            @click="exportReport(b.content, 'html')"
          >
            导出 HTML
          </button>
          <button
            class="act-btn"
            :disabled="exporting"
            data-testid="chat-export-docx"
            title="导出为 Word 文档（真 docx，无格式警告）"
            @click="exportReport(b.content, 'docx')"
          >
            导出 Word
          </button>
        </div>
        <!-- 溯源：本条回答查了什么数据（CONTEXT.md「溯源」），默认折叠 -->
        <details v-if="b.role === 'assistant' && b.provenance?.length" class="chat-prov" data-testid="chat-provenance">
          <summary>溯源 · {{ b.provenance.length }} 次查询</summary>
          <div v-for="(p, j) in b.provenance" :key="j" class="prov-item">
            <div class="prov-head">{{ p.tool }}<span v-if="fmtArgs(p.args)" class="prov-args">{{ fmtArgs(p.args) }}</span></div>
            <pre class="prov-preview">{{ p.result_preview }}</pre>
          </div>
        </details>
      </div>
      <!-- 流式中的实时气泡：先显示"正在查询"，出字后显示正文 -->
      <div v-if="streaming" class="msg assistant" data-testid="chat-msg-streaming">
        <div v-if="toolStatus" class="tool-status" data-testid="chat-tool-status">
          <span class="tool-spin"></span>正在查询：{{ toolStatus }}
        </div>
        <template v-if="streamText">{{ streamText }}</template>
        <span v-else-if="!toolStatus" class="chat-dots" data-testid="chat-thinking"><i></i><i></i><i></i></span>
      </div>
      </template>
    </div>

    <!-- 模型切换行：选项来自后端目录；不支持工具的模型显示"不查库"提示 -->
    <div v-if="!listOpen" class="model-row" data-testid="chat-model-row">
      <button
        v-for="m in modelOptions"
        :key="m.id"
        class="model-chip"
        :class="{ active: model === m.id }"
        :data-testid="`chat-model-${m.id}`"
        :title="m.id"
        @click="setModel(m.id)"
      >
        {{ m.label }}
      </button>
      <span
        v-if="activeModelInfo && !activeModelInfo.tools_supported"
        class="model-hint"
        data-testid="chat-model-hint"
      >
        该模型暂不支持查数，仅基于对话上下文分析
      </span>
    </div>

    <!-- 错误条（配置引导 / 上游错误 / 网络中断统一入口） -->
    <div v-if="error" class="chat-error" data-testid="chat-error">{{ error }}</div>

    <footer class="chat-input-row">
      <textarea
        v-model="input"
        class="chat-input"
        rows="2"
        placeholder="输入问题，Enter 发送（Shift+Enter 换行）"
        data-testid="chat-input"
        :disabled="streaming"
        @keydown.enter.exact.prevent="send"
      ></textarea>
      <button
        class="chat-send"
        :disabled="!input.trim() || streaming"
        data-testid="chat-send"
        @click="send"
      >
        发送
      </button>
    </footer>
  </div>
</template>

<style scoped>
/* ---------- 气泡入口：视口右上角（用户指定）· T1 科技风 ---------- */
.chat-bubble-wrap {
  position: fixed;
  right: 14px;
  z-index: 230;
  width: 44px;
  height: 44px;
  touch-action: none; /* 交给指针事件处理纵向拖动，避免被浏览器滚动/手势抢走 */
  user-select: none;
}
.chat-bubble {
  position: relative;
  z-index: 2;
  width: 44px;
  height: 44px;
  border-radius: var(--radius-circle);
  border: 1px solid rgba(103, 232, 249, 0.5);
  background: linear-gradient(135deg, #1e3a8a, #0e7490);
  color: #a5f3fc;
  font-size: calc(14px * var(--font-scale));
  font-weight: 800;
  font-style: italic;
  letter-spacing: 0.5px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: 0 0 16px rgba(34, 211, 238, 0.35);
  transition: transform 0.18s ease, box-shadow 0.18s ease, background 0.18s ease, color 0.18s ease;
}
.chat-bubble:hover {
  background: linear-gradient(135deg, #0891b2, #1d4ed8);
  color: #fff;
  transform: scale(1.08);
  box-shadow: 0 0 26px rgba(34, 211, 238, 0.7);
}
/* 脉冲光圈：向外扩散的青色环（两层错峰） */
.chat-bubble-ring {
  position: absolute;
  inset: 0;
  border-radius: var(--radius-circle);
  background: rgba(34, 211, 238, 0.28);
  z-index: 0;
  animation: bubble-pulse 1.9s ease-out infinite;
}
/* 旋转微光环：蓝青 conic 渐变，径向遮罩把中心打透成环 */
.chat-bubble-halo {
  position: absolute;
  inset: -3px;
  border-radius: var(--radius-circle);
  z-index: 1;
  background: conic-gradient(from 0deg, transparent, #22d3ee, transparent 40%, transparent, #3b82f6, transparent 85%);
  -webkit-mask: radial-gradient(closest-side, transparent 62%, #000 64%);
  mask: radial-gradient(closest-side, transparent 62%, #000 64%);
  animation: bubble-rot 3.6s linear infinite;
}
/* 琥珀角标 */
.chat-bubble-dot {
  position: absolute;
  top: -2px;
  right: -2px;
  width: 12px;
  height: 12px;
  border-radius: var(--radius-circle);
  background: #fbbf24;
  border: 2px solid var(--color-surface);
  z-index: 3;
}
@keyframes bubble-pulse {
  0% { transform: scale(0.95); opacity: 0.7; }
  100% { transform: scale(1.75); opacity: 0; }
}
@keyframes bubble-rot {
  to { transform: rotate(360deg); }
}
@media (prefers-reduced-motion: reduce) {
  .chat-bubble-ring,
  .chat-bubble-halo {
    animation: none;
  }
}

/* ---------- 悬浮面板 ---------- */
.chat-panel {
  position: fixed;
  top: 14px;
  right: 16px;
  z-index: 230;
  width: 400px;
  max-width: calc(100vw - 32px);
  height: min(600px, calc(100vh - 28px));
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: 0 8px 32px rgba(15, 23, 42, 0.16);
  overflow: hidden;
}
.chat-head {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border);
}
.chat-title {
  font-size: calc(15px * var(--font-scale));
  font-weight: 600;
  color: var(--color-text-strong);
}
.chat-head-actions {
  display: flex;
  gap: 4px;
}
.icon-btn {
  width: 28px;
  height: 28px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-3);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.icon-btn:hover {
  background: var(--color-surface-2);
  color: var(--color-text);
}
.icon-btn.icon-btn-active {
  background: var(--color-brand-tint);
  color: var(--color-brand);
}
.icon-btn svg {
  width: 16px;
  height: 16px;
}

/* ---------- 对话列表视图 ---------- */
.chat-conv-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: auto 0;
}
.conv-new {
  padding: 9px 12px;
  border: 1px dashed var(--color-divider-strong);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text-2);
  font-size: calc(13px * var(--font-scale));
  cursor: pointer;
  text-align: center;
}
.conv-new:hover:not(:disabled) {
  border-color: var(--color-brand);
  color: var(--color-brand);
  background: var(--color-brand-tint-3);
}
.conv-new:disabled,
.conv-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.conv-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
  padding: 9px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  cursor: pointer;
  text-align: left;
}
.conv-item:hover:not(:disabled) {
  background: var(--color-surface-2);
  border-color: var(--color-border-3);
}
.conv-item.active {
  border-color: var(--color-brand-border);
  background: var(--color-brand-tint-3);
  box-shadow: inset 3px 0 0 var(--color-brand);
}
.conv-title {
  font-size: calc(13px * var(--font-scale));
  color: var(--color-text);
  font-weight: 600;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conv-item.active .conv-title {
  color: var(--color-brand-dark);
}
.conv-meta {
  font-size: calc(12px * var(--font-scale));
  color: var(--color-text-5);
}

/* ---------- 设置弹层 ---------- */
.chat-settings {
  flex: none;
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface-3);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.set-label {
  font-size: calc(12px * var(--font-scale));
  color: var(--color-text-5);
}
.set-mask {
  font-size: calc(13px * var(--font-scale));
  color: var(--color-text-2);
  font-family: Consolas, monospace;
}
/* 未配置态：用品牌色提示该去设置，而非中性灰 */
.set-mask.warn {
  color: var(--color-brand);
  font-family: inherit;
}
.set-row {
  display: flex;
  gap: 6px;
}
.set-input {
  flex: 1;
  min-width: 0;
  padding: 6px 8px;
  font-size: calc(13px * var(--font-scale));
  border: 1px solid var(--color-border-3);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text);
  box-sizing: border-box;
}
.set-input:focus {
  outline: none;
  border-color: var(--color-brand);
}
.set-save {
  flex: none;
  padding: 6px 14px;
  font-size: calc(13px * var(--font-scale));
  border: none;
  border-radius: var(--radius-sm);
  background: var(--color-brand);
  color: var(--color-surface);
  cursor: pointer;
}
.set-save:disabled {
  background: var(--color-brand-border);
  cursor: not-allowed;
}
.set-hint {
  font-size: calc(12px * var(--font-scale));
  color: var(--color-text-5);
  line-height: 1.5;
}

/* ---------- 消息流 ---------- */
.chat-msgs {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.chat-empty {
  margin: auto 0;
  text-align: center;
  font-size: calc(13px * var(--font-scale));
  color: var(--color-text-5);
  line-height: 1.8;
}
.msg {
  max-width: 86%;
  padding: 8px 10px;
  border-radius: var(--radius-md);
  font-size: calc(13px * var(--font-scale));
  line-height: 1.65;
  word-break: break-word;
}
.msg.user {
  align-self: flex-end;
  background: var(--color-brand-tint);
  color: var(--color-text);
  white-space: pre-wrap;
}
.msg.assistant {
  align-self: flex-start;
  background: var(--color-surface-2);
  color: var(--color-text);
  white-space: pre-wrap;
}
/* 流式结束后 assistant 气泡换成渲染 HTML，交回标准排版 */
.msg.assistant:has(.chat-md) {
  white-space: normal;
}

/* 后端渲染的 markdown（v-html）样式：scoped 不穿透 v-html，用 :deep */
.chat-md :deep(h1),
.chat-md :deep(h2),
.chat-md :deep(h3),
.chat-md :deep(h4) {
  margin: 8px 0 4px;
  line-height: 1.4;
  color: var(--color-text-strong);
}
.chat-md :deep(h1) { font-size: calc(15px * var(--font-scale)); }
.chat-md :deep(h2) { font-size: calc(14px * var(--font-scale)); }
.chat-md :deep(h3),
.chat-md :deep(h4) { font-size: calc(13px * var(--font-scale)); }
.chat-md :deep(p) {
  margin: 4px 0;
}
.chat-md :deep(ul),
.chat-md :deep(ol) {
  margin: 4px 0;
  padding-left: 18px;
}
.chat-md :deep(table) {
  border-collapse: collapse;
  margin: 6px 0;
  font-size: calc(12px * var(--font-scale));
}
.chat-md :deep(th),
.chat-md :deep(td) {
  border: 1px solid var(--color-border);
  padding: 4px 8px;
  text-align: left;
}
.chat-md :deep(th) {
  background: var(--color-surface-3);
  color: var(--color-text-2);
}
.chat-md :deep(code) {
  background: var(--color-surface-3);
  border-radius: var(--radius-xs);
  padding: 1px 4px;
  font-size: calc(12px * var(--font-scale));
}
.chat-md :deep(blockquote) {
  margin: 6px 0;
  padding: 2px 10px;
  border-left: 3px solid var(--color-divider-strong);
  color: var(--color-text-3);
}

/* ---------- 消息操作区（导出按钮） ---------- */
.msg-actions {
  display: flex;
  gap: 6px;
  margin-top: 6px;
}
.act-btn {
  padding: 2px 8px;
  font-size: calc(11px * var(--font-scale));
  border: 1px solid var(--color-divider);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text-4);
  cursor: pointer;
}
.act-btn:hover:not(:disabled) {
  border-color: var(--color-brand-border);
  color: var(--color-brand);
  background: var(--color-brand-tint-3);
}
.act-btn:disabled {
  color: var(--color-text-6);
  cursor: not-allowed;
}

/* ---------- 溯源折叠区（assistant 气泡底部） ---------- */
.chat-prov {
  margin-top: 8px;
  border-top: 1px dashed var(--color-divider);
  padding-top: 6px;
  font-size: calc(12px * var(--font-scale));
}
.chat-prov summary {
  cursor: pointer;
  color: var(--color-text-5);
  user-select: none;
}
.chat-prov summary:hover {
  color: var(--color-text-3);
}
.prov-item {
  margin-top: 6px;
}
.prov-head {
  color: var(--color-text-2);
  font-weight: 600;
  word-break: break-all;
}
.prov-args {
  font-weight: 400;
  color: var(--color-text-5);
  margin-left: 6px;
}
.prov-preview {
  margin: 4px 0 0;
  padding: 6px 8px;
  background: var(--color-surface-3);
  border-radius: var(--radius-sm);
  color: var(--color-text-3);
  font-size: calc(11px * var(--font-scale));
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 120px;
  overflow-y: auto;
}

/* ---------- 工具查询状态行（流式期间） ---------- */
.tool-status {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--color-text-5);
  font-size: calc(12px * var(--font-scale));
  margin-bottom: 4px;
}
.tool-spin {
  width: 10px;
  height: 10px;
  flex: none;
  border: 2px solid var(--color-divider-strong);
  border-top-color: var(--color-brand);
  border-radius: var(--radius-circle);
  animation: chat-tool-spin 0.8s linear infinite;
}
@keyframes chat-tool-spin {
  to { transform: rotate(360deg); }
}

/* 思考点动画（等待首个 token） */
.chat-dots {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}
.chat-dots i {
  width: 5px;
  height: 5px;
  border-radius: var(--radius-circle);
  background: var(--color-text-5);
  animation: chat-dot-bounce 1.2s infinite ease-in-out;
}
.chat-dots i:nth-child(2) { animation-delay: 0.15s; }
.chat-dots i:nth-child(3) { animation-delay: 0.3s; }
@keyframes chat-dot-bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.5; }
  30% { transform: translateY(-4px); opacity: 1; }
}

/* ---------- 模型切换行 ---------- */
.model-row {
  flex: none;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-top: 1px solid var(--color-border);
}
.model-chip {
  padding: 3px 12px;
  font-size: calc(12px * var(--font-scale));
  border: 1px solid var(--color-border-3);
  border-radius: var(--radius-pill);
  background: var(--color-surface);
  color: var(--color-text-3);
  cursor: pointer;
}
.model-chip:hover {
  border-color: var(--color-brand-border);
  color: var(--color-brand);
}
.model-chip.active {
  border-color: var(--color-brand);
  background: var(--color-brand-tint);
  color: var(--color-brand-dark);
  font-weight: 600;
}
.model-hint {
  font-size: calc(11px * var(--font-scale));
  color: var(--color-text-5);
  margin-left: 2px;
}

/* ---------- 错误条 ---------- */
.chat-error {
  flex: none;
  margin: 0 12px 8px;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--color-brand-tint);
  border: 1px solid var(--color-brand-border);
  color: var(--color-brand-dark);
  font-size: calc(12px * var(--font-scale));
  line-height: 1.5;
}

/* ---------- 输入区 ---------- */
.chat-input-row {
  flex: none;
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  border-top: 1px solid var(--color-border);
  align-items: flex-end;
}
.chat-input {
  flex: 1;
  min-width: 0;
  resize: none;
  padding: 8px 10px;
  font-size: calc(13px * var(--font-scale));
  line-height: 1.5;
  border: 1px solid var(--color-border-3);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text);
  box-sizing: border-box;
  font-family: inherit;
}
.chat-input::placeholder {
  color: var(--color-text-6);
}
.chat-input:focus {
  outline: none;
  border-color: var(--color-brand);
}
.chat-send {
  flex: none;
  padding: 8px 16px;
  font-size: calc(13px * var(--font-scale));
  border: none;
  border-radius: var(--radius-sm);
  background: var(--color-brand);
  color: var(--color-surface);
  cursor: pointer;
}
.chat-send:disabled {
  background: var(--color-brand-border);
  cursor: not-allowed;
}
</style>
