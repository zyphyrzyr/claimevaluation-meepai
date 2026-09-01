# Soft IP 主诉评估系统 v4

著作权 / 商标 / 不正当竞争（Soft IP）诉讼决策引擎：输入案情和证据，输出"能不能诉、值不值得诉、现在能不能诉"的结构化评估。

- 核心模型：主诉决策分 = 法律可行性 × 业务预期（二维乘法）+ 证据前置盘点 + 独立置信度 + 红线硬门禁
- 技术栈：FastAPI + React/TS + Tailwind + shadcn/ui + ECharts + ChromaDB（RAG）
- 总体方案：见同工作区《v4-重做方案.md》v2.1

## 结构

```
backend/    FastAPI 后端（core/ 为不依赖 Web 框架的业务核心）
frontend/   Vite + React + TS 前端
data/       SQLite + ChromaDB（运行时生成，不入库）
```

## 运行（开发）

```bash
# 后端（Mock 模式，无需 API Key）
cd backend && pip install -r requirements.txt
USE_MOCK=True uvicorn main:app --reload --port 8000

# 前端
cd frontend && npm install && npm run dev
```

真实模式需配置 `.env`（照 `.env.example` 复制）。LLM 供应商可在「高级设置」页切换，见下节。

## 切换 LLM 供应商

支持任何 OpenAI 兼容接口（只需满足：`{base_url}/v1/chat/completions` +
`Authorization: Bearer <key>` + 响应取 `choices[0].message.content`）。

两种方式，选一：

1. **界面切换**——进入「高级设置」页，下拉选择供应商，即刻生效（配置每次调用
   重读，无需重启）。内置 DeepSeek / Kimi / OpenAI 三家预设。
2. **手改 `.env`**——设 `LLM_PROVIDER=<id>`，或直接手写 `LLM_*` 全组参数。

密钥不在界面上填，也不由切换逻辑写入。各家认各家的环境变量槽：

| 供应商 | 密钥环境变量 | 备注 |
| --- | --- | --- |
| DeepSeek | `LLM_API_KEY`（兼容旧 `DEEPSEEK_API_KEY`） | 默认供应商，通用槽即它的槽 |
| Kimi | `KIMI_API_KEY` | 未配专属槽时不允许切换，避免拿别家的 key 吃 401 |
| OpenAI | `OPENAI_API_KEY` | |

未配置专属密钥的供应商在界面上标为「未配置密钥」且不可切换——允许切的话，
401 要等评估跑到一半才炸出来。

**新增一家供应商**：只改 `backend/core/providers.py` 的 `PRESETS`，加一条即可，
前端下拉自动出现，其余代码零改动。

## 免责声明

本系统为 AI 辅助评估工具，结果仅供内部决策参考，不构成正式法律意见。
