"""
Soft IP 主诉评估系统 v4 - FastAPI 入口
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import APP_TITLE, APP_VERSION
from core.database import init_db
from routers import cases, evaluation, moot, report, knowledge, advisor, settings

app = FastAPI(title=APP_TITLE, version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    # 5173 被占时 Vite 会自动漂到 5174/5175，写死一个端口会让前端某天
    # 突然所有请求都变成 CORS 错误，而报错信息里完全看不出是端口的锅。
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:5174", "http://127.0.0.1:5174",
        "http://localhost:5175", "http://127.0.0.1:5175",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cases.router, prefix="/api/cases", tags=["cases"])
app.include_router(evaluation.router, prefix="/api/evaluation", tags=["evaluation"])
app.include_router(moot.router, prefix="/api/moot", tags=["moot"])
app.include_router(report.router, prefix="/api/report", tags=["report"])
app.include_router(knowledge.router, prefix="/api/knowledge", tags=["knowledge"])
app.include_router(advisor.router, prefix="/api/advisor", tags=["advisor"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])


@app.on_event("startup")
def startup():
    init_db()


@app.get("/api/health")
def health():
    return {"status": "ok", "version": APP_VERSION}
