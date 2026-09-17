"""
认证路由：注册 / 登录 / 登出 / 当前用户。

会话走 httpOnly cookie（见 core/auth.py 的取向说明），前端不持有 token，
所以这四个端点之外没有任何「带 token 的接口」——鉴权全部由依赖项在服务端做。
"""

import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth import (
    COOKIE_NAME, clear_session_cookie, create_session, current_user_optional,
    hash_password, resolve_session, revoke_session, set_session_cookie,
    verify_password,
)
from core.database import KnowledgeEntry, User, Case, get_db

router = APIRouter()

MIN_PASSWORD = 6
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Credentials(BaseModel):
    email: str
    password: str
    display_name: str = ""


def _normalize_email(email: str) -> str:
    """统一小写去空格。

    不做这一步的话，`A@x.com` 与 `a@x.com` 会变成两个账号——
    用户在手机上自动首字母大写一下就登不进自己刚注册的号。
    """
    return (email or "").strip().lower()


def _validate(email: str, password: str) -> None:
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "邮箱格式不正确")
    if len(password) < MIN_PASSWORD:
        raise HTTPException(400, f"密码至少 {MIN_PASSWORD} 位")


def _stats(db: Session, user: User) -> dict:
    """侧边栏那行「N 个案件 · M 条经验」。

    只数自己的——公共数据不该算进「我的」。
    """
    cases = db.query(Case).filter(Case.user_id == user.id).count()
    entries = (
        db.query(KnowledgeEntry)
        .filter(KnowledgeEntry.user_id == user.id, KnowledgeEntry.scope == "global")
        .count()
    )
    return {"cases": cases, "entries": entries}


def _me_payload(db: Session, user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name or user.email.split("@")[0],
        "stats": _stats(db, user),
    }


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    """当前登录用户；未登录返回 user=null（不是 401）。

    前端首屏就要调它，未登录是**正常状态**而不是错误——
    回 401 会让 401 拦截器在每次打开页面时弹一次登录框。
    """
    user = resolve_session(db, request.cookies.get(COOKIE_NAME, ""))
    return {"user": _me_payload(db, user) if user else None}


@router.post("/register")
def register(payload: Credentials, response: Response, db: Session = Depends(get_db)):
    email = _normalize_email(payload.email)
    _validate(email, payload.password)

    if db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "该邮箱已注册，直接登录即可")

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=(payload.display_name or "").strip() or email.split("@")[0],
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # 注册即登录：让用户少填一次表单
    set_session_cookie(response, create_session(db, user))
    return {"user": _me_payload(db, user)}


@router.post("/login")
def login(payload: Credentials, response: Response, db: Session = Depends(get_db)):
    email = _normalize_email(payload.email)
    user = db.query(User).filter(User.email == email).first()
    # 不区分「邮箱不存在」和「密码错」——分开提示等于给了撞库的探针
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "邮箱或密码不正确")

    set_session_cookie(response, create_session(db, user))
    return {"user": _me_payload(db, user)}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME, "")
    if token:
        revoke_session(db, token)
    clear_session_cookie(response)
    return {"ok": True}
