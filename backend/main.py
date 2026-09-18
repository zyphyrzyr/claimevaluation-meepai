"""
Soft IP 主诉评估系统 v4 - FastAPI 入口
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import APP_TITLE, APP_VERSION
from core.database import init_db
from routers import cases, evaluation, moot, report, knowledge, advisor, settings, auth

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
# 认证挂在 /api/auth 下。放在最后不影响路由匹配（前缀互不重叠），
# 但读代码时它是「跨切面」而不是某个业务模块，单独一行更醒目。
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])


@app.on_event("startup")
def startup():
    init_db()
    # 启动自愈：多用户隔离上线前入库的向量块没有 user_id 元数据，
    # 不补的话新的「自己的 + 公共的」过滤会把它们全挡掉——老经验库一夜之间
    # 搜不到任何东西，而接口照常返回空列表。
    try:
        from core.knowledge import ensure_vector_user_id
        ensure_vector_user_id()
    except Exception as e:
        print(f"[startup] 向量块 user_id 回填跳过：{e}")
    # 启动自愈：进程刚起来时不可能有任何运行中的评估，凡是 case.status 还停在
    # evaluating 的都是上一轮被重启/异常打断留下的僵尸状态，按实际产出修正。
    # 不修的话案件会永远显示「评估中」，且 run-state 会据此谎报 running、点暂停报 404。
    try:
        from core.database import SessionLocal
        db = SessionLocal()
        try:
            fixed = evaluation.heal_stuck_evaluations(db)
        finally:
            db.close()
        if fixed:
            print(f"[startup] 修正 {len(fixed)} 个残留的 evaluating 僵尸状态：")
            for it in fixed:
                print(f"  · {it['case_id']}  {it['name']}  {it['from']} → {it['to']}")
    except Exception as e:
        # 自愈失败不该挡住服务启动
        print(f"[startup] 僵尸状态自愈跳过：{e}")

    # 启动自愈：后台证据解析在进程重启时可能被中断，把残留 pending 的文件重新排队解析，
    # 否则这些文件会永远停在「解析中」。
    try:
        from core.database import SessionLocal
        db = SessionLocal()
        try:
            from routers.cases import _resume_pending_evidence
            _resume_pending_evidence(db)
        finally:
            db.close()
    except Exception as e:
        print(f"[startup] 证据解析自愈跳过：{e}")


@app.get("/api/health")
def health():
    return {"status": "ok", "version": APP_VERSION}
