# -*- coding: utf-8 -*-
"""AI 对话服务层：DeepSeek 调用、配置双通道解析、SSE 帧生成、markdown 渲染。

分层定位（ADR-0016 / 0017）：
- 配置解析 ``load_ai_config``：``ai_config.json``（DATA_DIR，gitignore 覆盖）优先于
  环境变量 ``DEEPSEEK_API_KEY`` / ``DEEPSEEK_BASE_URL`` / ``DEEPSEEK_MODEL``；
  明文 key 只在本模块内部流转，对外仅 ``mask_key`` 掩码（ADR-0017 底线）。
- DeepSeek 调用 ``stream_chat_completion``：httpx 直连 OpenAI 兼容端点
  （chat/completions，SSE 流式），⛔ 不引入 openai SDK——依赖是完整 freeze，
  加 SDK 会拖进整棵依赖树，httpx 已在 freeze 中。
- SSE 帧生成 ``generate_chat_frames``：delta / done / error 三种帧
  （工单 02 会扩展 tool_call / provenance），帧格式 ``data: {json}\\n\\n``。

迁移性（spec「部署与迁移性」节）：
- 云端 / Docker：三个环境变量即可完成配置；ai_config.json 落 DATA_DIR，
  挂卷持久化、不挂卷自动回退环境变量，两种形态互不破坏。
- 本模块零平台专属调用：纯 httpx + 标准库，Windows / Linux 行为一致。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator, Optional

import httpx

from ..jobs import config

# ---- DeepSeek 端点与模型 ----
# DEFAULT_BASE_URL：DeepSeek 官方 OpenAI 兼容端点；换自建网关 / 其它兼容服务时
# 用 DEEPSEEK_BASE_URL 或面板设置覆盖（spec 迁移性要求）。
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
# 模型目录（id / 展示名 / 是否支持查询工具）：前端切换器与降级逻辑的唯一事实来源。
# reasoner 的 tools_supported=True 由 2026-10-31 实测确认（探测脚本
# .scratch/ai-chat/probe_reasoner_tools.py：非流式返回 tool_calls、流式分片含
# tool_calls 增量、参数完整）。若日后换到不支持的模型，只需把该标记改
# False——路由层会自动剥离 tools（工单 02 预留的 use_tools 降级开关），
# 前端切换器也会从本目录读到标记并显示"不查库"提示，两处无需改代码。
MODEL_CATALOG: list[dict[str, Any]] = [
    {"id": "deepseek-chat", "label": "标准", "tools_supported": True},
    {"id": "deepseek-reasoner", "label": "深度思考", "tools_supported": True},
]
# 请求可指定的模型白名单：从目录派生（单一事实来源，别再手写一份清单）。
ALLOWED_MODELS = tuple(m["id"] for m in MODEL_CATALOG)

# 系统提示词模板：角色 + 数据边界铁律 + 动态数据概览（build_system_prompt 现场填充）。
# 数据边界是 ADR-0016 的落地：AI 只对查询工具返回的数据作答——宁可说"查不到"，
# 绝不编数字；比率口径由工具保证（先求和再相除），模型不得自行推算。
SYSTEM_PROMPT_TEMPLATE = (
    "你是「JD 数据平台」内嵌的 AI 数据分析助手，服务对象是本店运营人员。"
    "回答用简体中文，简洁直接，结论先行、数字优先；金额注明单位（元 / 万元）。\n\n"
    "【数据边界（铁律）】\n"
    "1. 只能基于查询工具返回的数据回答；工具没查到的信息就说「查不到」，绝不编造数字。\n"
    "2. 工具返回的指标已经是正确口径（比率类先求和再相除），不要自己用平均值推算比率。\n"
    "3. 数据库里没有的信息（如复购率、用户画像、竞品、利润）直接说明无法提供。\n"
    "4. 工具返回 error 时，如实向用户转述原因（如数据尚未摄入），不要猜。\n\n"
    "【当前数据概览】\n{overview}\n\n"
    "【工具使用建议】\n"
    "- 销售 / 流量 / 推广 / 退款问题 → pop_module_summary（整体）或 pop_rows（明细排行）\n"
    "- 某个 SPU 的深度表现 → pop_spu_analysis\n"
    "- 库存总量 / 滞销 → inventory_summary 或 inventory_rows\n"
    "- 哪些货该补、各城市补多少 → inventory_restock\n"
    "- 哪个城市库存告急 → inventory_alerts\n"
    "多轮工具调用是允许的：先看汇总再下钻明细，直到能回答为止。"
)

# 工具循环上限：防模型陷入"查询→再查询"死循环烧钱；正常问答 1~2 轮足够
MAX_TOOL_ROUNDS = 5


class ChatUpstreamError(RuntimeError):
    """DeepSeek 上游错误（网络 / 鉴权 / 余额 / 限流），message 面向用户可读。"""


# ---------------------------------------------------------------------------
# 配置：双通道（环境变量为底，ai_config.json 覆盖）
# ---------------------------------------------------------------------------


def _config_file() -> Path:
    """返回 ai_config.json 的路径。

    Returns:
        Path: DATA_DIR 下的 ai_config.json（该目录已被 .gitignore 覆盖）。

    Note:
        每次调用现取（不缓存到模块级），测试可 monkeypatch 本函数
        把配置指到临时目录，互不污染真实运行数据。
    """
    return config.DATA_DIR / "ai_config.json"


def _read_file_config() -> dict[str, str]:
    """读取文件通道配置。

    Returns:
        dict[str, str]: 只含 ``api_key`` / ``base_url`` / ``model`` 中有值的键；
        文件缺失 / JSON 损坏 / 类型不对一律返回空 dict——文件坏了不该炸掉
        整个聊天功能（回退环境变量即可），但也不静默吞掉配置。
    """
    try:
        raw = json.loads(_config_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in ("api_key", "base_url", "model"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    return out


def load_ai_config() -> dict[str, str]:
    """合并双通道配置：环境变量为底，ai_config.json 覆盖（ADR-0017 文件优先）。

    Returns:
        dict[str, str]: 必含三键 ``api_key`` / ``base_url`` / ``model``；
        api_key 可能为空串（= 未配置，由调用方引导用户去设置）。
    """
    merged = {
        "api_key": os.getenv("DEEPSEEK_API_KEY", "").strip(),
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "").strip() or DEFAULT_BASE_URL,
        "model": os.getenv("DEEPSEEK_MODEL", "").strip() or DEFAULT_MODEL,
    }
    merged.update(_read_file_config())
    return merged


def save_ai_config(patch: dict[str, Optional[str]]) -> dict[str, str]:
    """把面板提交的配置写入 ai_config.json，返回合并后的生效配置。

    Args:
        patch: 待更新键值。约定：``None`` = 不改动该键；空串 = 删除该键
            （回退到环境变量 / 默认值）；非空串 = 覆盖。只接受
            ``api_key`` / ``base_url`` / ``model`` 三个键，其余忽略。

    Returns:
        dict[str, str]: 写入后的生效配置（同 ``load_ai_config`` 结构）。
    """
    current = _read_file_config()
    for key in ("api_key", "base_url", "model"):
        if key not in patch:
            continue
        val = patch[key]
        if val is None:
            continue
        val = val.strip()
        if val:
            current[key] = val
        else:
            current.pop(key, None)
    # 先写临时文件再原子替换：写一半断电 / 崩溃不会留下半个 JSON，
    # _read_file_config 虽能容错，但配置静默回退环境变量更难排查。
    target = _config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    return load_ai_config()


def mask_key(key: str) -> str:
    """把明文 key 转成掩码（ADR-0017：明文永不离开服务端）。

    Args:
        key: 明文 API key。

    Returns:
        str: 前 6 后 4 中间省略号；key 过短时进一步收缩明文段——
        宁可看不出，不能漏全量。
    """
    if len(key) >= 12:
        return f"{key[:6]}…{key[-4:]}"
    if len(key) >= 4:
        return f"{key[:2]}…{key[-2:]}"
    return "…"


# ---------------------------------------------------------------------------
# markdown 渲染：聊天显示与（工单 04 的）HTML 报告导出共用同一份
# ---------------------------------------------------------------------------

from markdown_it import MarkdownIt  # noqa: E402 （freeze 已有，Rich 的传递依赖）


def _make_md() -> MarkdownIt:
    """构造 markdown 渲染器：commonmark 预设 + 表格 / 删除线扩展。

    commonmark 预设默认 ``html=True``（CommonMark 规范本身允许内联 HTML），
    这里**必须显式关掉**：聊天显示侧的 HTML 由前端 v-html 挂载，原始 HTML
    透传 = 注入面，转义成纯文本才是安全底线。报告导出需要内联 HTML
    （CSS 条形图，工单 04）时在导出侧用单独的渲染器实例。
    表格 / 删除线是 GFM 扩展、markdown 语法而非内联 HTML，不受影响。
    """
    return MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])


_MD = _make_md()


def render_markdown(text: str) -> str:
    """把 markdown 文本渲染成 HTML 片段（不含 <html> 骨架，样式由前端配）。"""
    return _MD.render(text)


# ---------------------------------------------------------------------------
# DeepSeek 调用（SSE 流式）与帧生成
# ---------------------------------------------------------------------------


def _upstream_error_message(status_code: int, body: str) -> str:
    """把上游 HTTP 错误翻译成用户能行动的提示。

    Args:
        status_code: 上游响应状态码。
        body: 上游响应体（截断展示，防止刷屏）。

    Returns:
        str: 面向用户的中文错误信息。
    """
    hint = {
        401: "API Key 无效或已失效，请在设置里更新 Key。",
        402: "DeepSeek 账户余额不足，请充值后重试。",
        403: "DeepSeek 拒绝了请求（403），请检查 Key 权限。",
        429: "请求过于频繁，请稍等片刻再试。",
    }.get(status_code)
    detail = body.strip()[:200]
    prefix = hint or f"DeepSeek 返回 {status_code}。"
    return f"{prefix}（{detail}）" if detail else prefix


def stream_chat_completion(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """调用 DeepSeek chat/completions（SSE 流式），逐个 yield 解析后的载荷。

    Args:
        payload: OpenAI 兼容请求体（model / messages / stream 等）。

    Yields:
        dict[str, Any]: 上游 SSE 每个 data 帧的 JSON 对象（delta 增量等）。

    Raises:
        ChatUpstreamError: 网络异常 / 非 200 响应。单帧 JSON 损坏只跳过不抛，
            因为丢一帧顶多少一个字，中断整条流代价更大。

    Note:
        测试接缝：单测 monkeypatch 本函数即可编排任意帧序列，零真实网络。
    """
    cfg = load_ai_config()
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    # read 放宽到 120s：deepseek-reasoner 深度思考的首个 token 可能极慢，
    # 连接 / 写入保持常规值，避免死连接长期占用。
    timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    body = resp.read().decode("utf-8", "replace")
                    raise ChatUpstreamError(
                        _upstream_error_message(resp.status_code, body)
                    )
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        yield json.loads(data)
                    except ValueError:
                        continue
    except httpx.HTTPError as e:
        raise ChatUpstreamError(f"无法连接 DeepSeek 服务，请检查网络：{e}") from e


def _frame(obj: dict[str, Any]) -> str:
    """把对象编码成一条 SSE 帧（``data: {json}\\n\\n``）。"""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def build_system_prompt() -> str:
    """组装系统提示词：模板 + 现查的数据概览（chat_tools.build_data_overview）。

    Returns:
        str: 完整 system 提示词；概览查库失败时也会退化为「尚未摄入」文案，
        组装过程本身绝不抛出（提示词坏了 = 整个聊天不可用）。
    """
    from . import chat_tools  # 局部导入避免与模块级 httpx/markdown 段耦合

    try:
        overview = chat_tools.build_data_overview()
    except Exception:  # noqa: BLE001 —— 概览失败不阻塞对话
        overview = "（数据概览暂不可用）"
    return SYSTEM_PROMPT_TEMPLATE.format(overview=overview)


def _accumulate_tool_call(
    acc: dict[int, dict[str, Any]], tc: dict[str, Any]
) -> None:
    """把流式 tool_calls 增量片段累积到 acc（按 index 分桶）。

    OpenAI 兼容流的工具调用是**分片**到达的：首片带 id + name + 空 arguments，
    后续片只有 arguments 字符串片段——必须拼接后才是完整 JSON。
    """
    idx = tc.get("index", 0) or 0
    slot = acc.setdefault(idx, {"id": None, "name": "", "args": ""})
    if tc.get("id"):
        slot["id"] = tc["id"]
    fn = tc.get("function") or {}
    # name 只在首片出现；重复到达时保留首个（防个别网关重复发 name 造成拼接成脏串）
    if fn.get("name") and not slot["name"]:
        slot["name"] = fn["name"]
    if fn.get("arguments"):
        slot["args"] += fn["arguments"]


def generate_chat_frames(
    messages: list[dict[str, str]],
    model: Optional[str] = None,
    use_tools: bool = True,
) -> Iterator[str]:
    """生成一次对话回答的完整 SSE 帧序列（含工具接地循环）。

    Args:
        messages: 对话消息（user / assistant），**不含** system——系统提示词
            由本函数统一注入（含动态数据概览），客户端传来的 system 一律丢弃。
        model: 请求指定的模型；不在白名单时回退到配置默认值（白名单校验
            在路由层做 422，这里兜底防绕过）。
        use_tools: 是否启用查询工具（工单 05 的 reasoner 降级开关）。

    Yields:
        str: SSE 帧。正常序列 ``tool_call* → delta* → done``（tool_call 仅在
        模型决定查数时出现，可多轮）；done 含 markdown 原文、渲染 HTML 与
        溯源（provenance：全部工具调用及结果摘要）。上游异常产出单条
        error 帧后结束。
    """
    from . import chat_tools  # 局部导入：chat_tools 依赖 api/inventory_api，保持本模块头部轻

    # 过滤客户端消息：只认 user / assistant，剥掉一切 system（提示词主权在后端）
    clean = [m for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]
    cfg = load_ai_config()
    if not cfg["api_key"]:
        # 端点层已做 400 前置拦截，这里是并发竞态兜底（配置在流开始后被删）
        yield _frame(
            {
                "type": "error",
                "message": "尚未配置 DeepSeek API Key：点击面板右上角设置，粘贴 Key 后保存。",
            }
        )
        return
    use_model = model if model in ALLOWED_MODELS else (
        cfg["model"] if cfg["model"] in ALLOWED_MODELS else DEFAULT_MODEL
    )
    msg_list: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt()},
        *clean,
    ]
    provenance: list[dict[str, Any]] = []
    parts: list[str] = []

    for round_no in range(MAX_TOOL_ROUNDS + 1):
        payload: dict[str, Any] = {
            "model": use_model,
            "messages": msg_list,
            "stream": True,
        }
        if use_tools:
            payload["tools"] = chat_tools.TOOLS_SCHEMA
            payload["tool_choice"] = "auto"

        parts = []
        tool_calls: dict[int, dict[str, Any]] = {}
        try:
            for chunk in stream_chat_completion(payload):
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                if text:
                    parts.append(text)
                    yield _frame({"type": "delta", "text": text})
                for tc in delta.get("tool_calls") or []:
                    _accumulate_tool_call(tool_calls, tc)
        except ChatUpstreamError as e:
            yield _frame({"type": "error", "message": str(e)})
            return

        if not tool_calls:
            break  # 没有工具调用 = 最终回答已完成

        if round_no == MAX_TOOL_ROUNDS:
            # 连 MAX_TOOL_ROUNDS 轮都在查数还没回答：按超限终止，防止烧钱死循环
            yield _frame(
                {
                    "type": "error",
                    "message": f"查询轮次超过上限（{MAX_TOOL_ROUNDS} 轮），请换个更具体的问题再试。",
                }
            )
            return

        # 按上游要求的格式回填 assistant 工具调用消息，再逐个执行并注入结果
        ordered = [tool_calls[i] for i in sorted(tool_calls)]
        msg_list.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": c["id"] or f"call_{i}",
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c["args"] or "{}"},
                    }
                    for i, c in enumerate(ordered)
                ],
            }
        )
        for i, call in enumerate(ordered):
            name = call["name"] or "(unknown)"
            try:
                args = json.loads(call["args"]) if call["args"] else {}
                if not isinstance(args, dict):
                    args = {}
            except ValueError:
                args = {}  # 模型输出的参数 JSON 损坏：按空参执行，让工具的参数校验兜底
            yield _frame({"type": "tool_call", "name": name, "args": args})
            result = chat_tools.execute_tool(name, args)
            msg_list.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"] or f"call_{i}",
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
            provenance.append(
                {
                    "tool": name,
                    "args": args,
                    "result_preview": json.dumps(result, ensure_ascii=False)[:300],
                }
            )

    markdown = "".join(parts)
    yield _frame(
        {
            "type": "done",
            "markdown": markdown,
            "html": render_markdown(markdown),
            "provenance": provenance,
        }
    )
