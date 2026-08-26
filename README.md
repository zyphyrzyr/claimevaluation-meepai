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

真实模式需配置 `.env`：DEEPSEEK_API_KEY / QCC_API_TOKEN / PKULAW_API_TOKEN。

## 免责声明

本系统为 AI 辅助评估工具，结果仅供内部决策参考，不构成正式法律意见。
