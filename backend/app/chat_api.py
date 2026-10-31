# -*- coding: utf-8 -*-
"""AI 对话路由：配置双通道（ADR-0017）+ SSE 流式对话（工单 01 范围：无工具）。

端点：
- ``GET  /api/chat/config``     → 掩码配置视图（**永不返回明文 key**）
- ``PUT  /api/chat/config``     → 保存 key / base_url / 模型到 ai_config.json
- ``POST /api/chat/completions`` → SSE 流式对话（delta / done / error 帧）

只读铁律：本模块与 chat_service 不写任何业务表（daily_detail / inventory_*
等）；AI 功能对数据库只读（spec Implementation Decisions）。
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from . import chat_export
from . import chat_service as svc

router = APIRouter(prefix="/api/chat", tags=["chat"])

# docx 的官方 MIME（Word 打开无格式警告的前提：内容是真 docx，ADR-0018）
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class ChatModelInfo(BaseModel):
    """模型目录条目（驱动前端切换器与降级提示，事实来源 chat_service.MODEL_CATALOG）。"""

    id: str
    label: str
    tools_supported: bool


class ChatConfigOut(BaseModel):
    """配置视图：key 只出掩码（ADR-0017 安全底线）。"""

    configured: bool
    key_mask: Optional[str] = None
    base_url: str
    model: str
    models: list[ChatModelInfo] = Field(default_factory=list)


class ChatConfigIn(BaseModel):
    """配置保存请求。约定：None = 不改该键；空串 = 删除该键（回退环境变量）。"""

    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None


class ChatMessage(BaseModel):
    """对话消息：客户端只允许 user / assistant 两种角色。

    system 由后端统一注入（chat_service.SYSTEM_PROMPT），前端传入的
    任何 system 都会被剥掉——提示词主权在后端，防止公网用户注入
    冒充系统指令。
    """

    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=20000)


class ChatRequest(BaseModel):
    """对话请求：messages 为当前对话的最近若干轮（前端无状态截断）。"""

    messages: list[ChatMessage] = Field(min_length=1, max_length=24)
    model: Optional[str] = None


class ChatExportRequest(BaseModel):
    """报告导出请求：markdown 为 AI 回答原文（每条回答都可导出，不做
    「是否报告」的启发式判断——报告只是长回答的自然形态，spec Q11）。"""

    markdown: str = Field(min_length=1, max_length=100_000)
    title: str = Field(default="AI 报告", max_length=100)


def _attachment_headers(filename: str) -> dict[str, str]:
    """构造附件下载头：中文文件名走 RFC 5987（filename*），带 ASCII 兜底。

    Args:
        filename: 完整文件名（含扩展名，可含中文）。

    Returns:
        dict[str, str]: Content-Disposition 头。
    """
    ascii_name = quote(filename)  # 非 ASCII 全部百分号编码作为兜底
    return {
        "Content-Disposition": (
            f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
        )
    }


def _config_view(cfg: dict[str, str]) -> ChatConfigOut:
    """把合并后的配置转成对外视图（掩码 + 白名单归一 + 模型目录）。"""
    model = cfg["model"] if cfg["model"] in svc.ALLOWED_MODELS else svc.DEFAULT_MODEL
    return ChatConfigOut(
        configured=bool(cfg["api_key"]),
        key_mask=svc.mask_key(cfg["api_key"]) if cfg["api_key"] else None,
        base_url=cfg["base_url"],
        model=model,
        models=[ChatModelInfo(**m) for m in svc.MODEL_CATALOG],
    )


@router.get("/config", response_model=ChatConfigOut)
def get_chat_config() -> ChatConfigOut:
    """查看 AI 配置（掩码）。

    Returns:
        ChatConfigOut: configured / key_mask / base_url / model。
        ⚠️ 永不包含明文 key（ADR-0017）。
    """
    return _config_view(svc.load_ai_config())


@router.put("/config", response_model=ChatConfigOut)
def put_chat_config(body: ChatConfigIn) -> ChatConfigOut:
    """保存面板提交的配置（key / base_url / 模型），返回掩码视图。

    Args:
        body: 只更新给出的键；空串表示清除该键回退环境变量。

    Returns:
        ChatConfigOut: 保存后的生效配置（掩码视图）。
    """
    return _config_view(svc.save_ai_config(body.model_dump()))


@router.post("/completions")
def chat_completions(body: ChatRequest) -> StreamingResponse:
    """SSE 流式对话：delta（增量）→ done（markdown + 渲染 HTML）。

    Args:
        body: 对话消息（最近若干轮）+ 可选模型。

    Raises:
        HTTPException:
            - 400：尚未配置 API Key（流开始前直接引导，比流中 error 帧友好）；
            - 422：模型不在白名单。

    Returns:
        StreamingResponse: ``text/event-stream``；响应头禁缓存 +
        ``X-Accel-Buffering: no``（nginx 等反代默认缓冲会攒住 SSE，
        云端部署必须显式关掉——spec 迁移性要求）。
    """
    if body.model is not None and body.model not in svc.ALLOWED_MODELS:
        raise HTTPException(
            status_code=422,
            detail=f"不支持的模型：{body.model}（可选：{' / '.join(svc.ALLOWED_MODELS)}）",
        )
    if not svc.load_ai_config()["api_key"]:
        raise HTTPException(
            status_code=400,
            detail="尚未配置 DeepSeek API Key：点击面板右上角设置，粘贴 Key 后保存。",
        )
    messages = [{"role": m.role, "content": m.content} for m in body.messages]
    # 工具开关跟随模型目录：不支持的模型自动剥离 tools（工单 05 降级路径，
    # 目录里 tools_supported=False 时生效；当前两个模型实测均支持）
    requested = body.model or svc.load_ai_config()["model"]
    entry = next((m for m in svc.MODEL_CATALOG if m["id"] == requested), None)
    use_tools = bool(entry["tools_supported"]) if entry else True
    return StreamingResponse(
        svc.generate_chat_frames(messages, model=body.model, use_tools=use_tools),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/export/html")
def export_chat_html(body: ChatExportRequest) -> Response:
    """把一条 AI 回答导出为内嵌样式的完整 HTML 报告（双击浏览器可开）。

    Args:
        body: markdown 原文 + 标题（标题用于文档标题与下载文件名）。

    Returns:
        Response: text/html 附件；内嵌平台配色样式，支持 CSS 条形图
        （ADR-0018），已做 script / on* 属性净化。
    """
    html = chat_export.render_report_html(body.markdown, body.title)
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers=_attachment_headers(f"{body.title}.html"),
    )


@router.post("/export/docx")
def export_chat_docx(body: ChatExportRequest) -> Response:
    """把一条 AI 回答导出为真 docx 文档（Word 打开无格式警告，ADR-0018）。

    Args:
        body: markdown 原文 + 标题。

    Returns:
        Response: docx 附件；标题/段落/列表/表格结构映射，CSS 条形图
        降级为纯文本。
    """
    data = chat_export.markdown_to_docx(body.markdown, body.title)
    return Response(
        content=data,
        media_type=DOCX_MEDIA_TYPE,
        headers=_attachment_headers(f"{body.title}.docx"),
    )
