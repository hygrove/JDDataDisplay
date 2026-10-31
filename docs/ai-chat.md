# AI 对话助手 · 部署与使用说明

内嵌的 DeepSeek AI 助手：右上角气泡入口，基于查询工具接地到 POP 销售 + 库存真实数据（与页面同口径，ADR-0016），流式回答 + 溯源 + 报告导出（HTML / Word，ADR-0018）。设计决策见 `docs/adr/0016~0018`；词汇表见 `CONTEXT.md`「AI 助手」节。

## 配置（三选一起步，一条就能跑）

| 方式 | 操作 | 适用 |
|---|---|---|
| 环境变量（推荐） | 设 `DEEPSEEK_API_KEY` 后启动服务 | 本地托盘 / Docker / 云平台 |
| 面板配置 | 打开聊天面板 → ⚙ 设置 → 粘贴 Key 保存 | 临时换 Key / 不想动环境变量 |
| 两者并存 | 文件优先于环境变量；删除文件即回退 | — |

- 可选环境变量：`DEEPSEEK_BASE_URL`（默认 `https://api.deepseek.com`，可指向自建网关）、`DEEPSEEK_MODEL`（默认 `deepseek-chat`）。
- 面板保存的配置落在 `backend/app/data/ai_config.json`（已被 .gitignore 覆盖，**不进仓库**）；查询接口只回掩码（ADR-0017），明文 Key 永不出服务端。

## 本地部署（Windows 托盘）

1. 装新依赖（本次新增 python-docx + lxml）：
   ```bat
   .venv\Scripts\pip install -r requirements.txt
   ```
2. **重启托盘服务**（服务进程不热重载，旧进程没有 chat 路由；启停权归用户）：任务管理器结束 8000 端口的 uvicorn 进程后重新运行 `serve.bat`。
3. 浏览器强刷（Ctrl+F5），右上角出现 AI 气泡即成功。

## 云端 / Docker 部署

Dockerfile 无需改动（依赖按 `backend/requirements.txt` 安装；lxml 有 cp313 manylinux wheel，已实测）：

```bash
docker build -t jddisplay .
docker run -d -p 8000:8000 \
  -e DEEPSEEK_API_KEY=你的key \
  -v /data/jddisplay:/app/backend/app/data \
  jddisplay
```

- `-e DEEPSEEK_API_KEY`：平台密钥管理 / compose `environment` 同理，零代码改动。
- `-v .../backend/app/data`：挂载数据卷。不挂载也能跑（配置回退环境变量）；挂载则面板里保存的 Key 也随卷持久化。
- SSE 已带 `X-Accel-Buffering: no` + 禁缓存响应头，nginx 等反向代理无需额外配置（但别在反代层开启响应缓冲）。

## 使用要点

- **问数据**：直接问（"近 7 天哪家店铺销售额最高""哪些 SKU 该补货"）。AI 通过查询工具取数，回答下方「溯源」可展开核对依据；数字与页面同口径。
- **模型切换**：面板底部「标准 / 深度思考」。deepseek-reasoner 支持 function calling（2026-10-31 实测，探测脚本 `.scratch/ai-chat/probe_reasoner_tools.py`），两模型都能查数；reasoner 思考更久更深。
- **对话历史**：浏览器本地保留最近 5 个对话（连续追问算一个），刷新不丢；「历史」按钮查看切换。
- **报告导出**：每条 AI 回答下方「导出 HTML / 导出 Word」。HTML 双击可开（支持 CSS 条形图）；Word 为真 docx（文字+表格）。
- **成本**：按 DeepSeek token 计费。工具返回已做压缩（明细小页、数值取整）；工具循环上限 5 轮防跑飞。

## 故障排查

| 现象 | 原因与处理 |
|---|---|
| 发送即提示"尚未配置 API Key" | 环境变量没设且面板未保存；按上面任一方式配置 |
| 提示"API Key 无效或已失效" | Key 被轮换/删除；面板设置里更新 |
| 提示"余额不足" | DeepSeek 账户充值 |
| 回答"数据尚未摄入" | 库是空的（没跑过批处理）；先跑 `python -m backend.jobs.run_batch` |
| 服务重启后面板 404 | 旧进程还在 8000 上跑旧代码；确认已重启服务 |
| 公网部署担心 Key 被偷 | 掩码底线保证 Key 取不走（最坏被覆盖，重新保存即恢复）；如需限流见 spec Out of Scope 的演进说明 |
