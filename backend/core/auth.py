"""
认证内核：密码哈希、会话签发与校验、以及挂在路由上的三个依赖项。

设计取向（按「够用且不引入新依赖」选）：

- **密码**用标准库的 `hashlib.pbkdf2_hmac`，不引 passlib/bcrypt。存的是
  `pbkdf2_sha256$轮数$盐$摘要` 这种自带参数的字符串，以后换算法时旧记录仍能校验。
- **会话**存 `user_sessions` 表，不用 JWT。这个系统要「退出即失效」——
  JWT 的常见做法是退出只清客户端，旧 token 在被盗后依然有效。
- **Cookie** 用 httpOnly + SameSite=Lax。前端与后端同源（Vite 把 /api 代理到 8000），
  所以不需要 CORS 那一套，也不给 JS 读 token 的机会。

归属规则（贯穿全项目的两个概念）：

- **公共数据**：`user_id IS NULL`。存量数据迁移后全是这种，人人可读、不可改。
- **私有数据**：`user_id` 等于当前用户。
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from .database import Case, KnowledgeEntry, User, UserSession, get_db

COOKIE_NAME = "softip_session"
SESSION_DAYS = 30
_PBKDF2_ROUNDS = 200_000
_ALGO = "pbkdf2_sha256"


# ---------------------------------------------------------------- 密码

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), _PBKDF2_ROUNDS
    ).hex()
    return f"{_ALGO}${_PBKDF2_ROUNDS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    """常数时间比较。

    轮数与盐都从 stored 里读，所以调高轮数后老密码仍可校验。
    格式不认识时返回 False 而不是抛错——认证失败不该让请求 500。
    """
    try:
        algo, rounds, salt, digest = stored.split("$")
        if algo != _ALGO:
            return False
        calc = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), int(rounds)
        ).hex()
    except (ValueError, TypeError, AttributeError):
        # AttributeError：stored 是 None 之类没有 split 的东西。
        # 库里若真存了这种值，也只是「这条记录校验不过」，不该把请求打成 500。
        return False
    return hmac.compare_digest(calc, digest)


# ---------------------------------------------------------------- 会话

def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(UserSession(
        token=token,
        user_id=user.id,
        expires_at=datetime.now() + timedelta(days=SESSION_DAYS),
    ))
    db.commit()
    return token


def revoke_session(db: Session, token: str) -> None:
    db.query(UserSession).filter(UserSession.token == token).delete()
    db.commit()


def resolve_session(db: Session, token: str) -> Optional[User]:
    if not token:
        return None
    row = db.query(UserSession).filter(UserSession.token == token).first()
    if not row:
        return None
    if row.expires_at and row.expires_at < datetime.now():
        # 过期的行顺手清掉，免得表无限长大
        db.delete(row)
        db.commit()
        return None
    return db.query(User).filter(User.id == row.user_id).first()


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


# ---------------------------------------------------------------- 依赖项

def current_user_optional(
    request: Request, db: Session = Depends(get_db)
) -> Optional[User]:
    """没登录返回 None，不抛错——浏览类接口要允许匿名。"""
    return resolve_session(db, request.cookies.get(COOKIE_NAME, ""))


def require_user(user: Optional[User] = Depends(current_user_optional)) -> User:
    """写操作的门槛：未登录一律 401，前端据此弹登录框。"""
    if user is None:
        raise HTTPException(401, "请先登录")
    return user


def is_public(case: Case) -> bool:
    return case.user_id is None


def case_readable(
    case_id: str,
    user: Optional[User] = Depends(current_user_optional),
    db: Session = Depends(get_db),
) -> Case:
    """读类接口：公共案件或自己的案件。

    不区分「不存在」与「是别人的」都回 404——回 403 等于告诉对方
    「这个 id 真实存在，只是不属于你」，是可以被用来枚举的。
    """
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    if case.user_id is not None and (user is None or case.user_id != user.id):
        raise HTTPException(404, "案件不存在")
    return case


def case_owned(
    case_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Case:
    """写类接口：只能动自己的案件。

    公共案件回 403 而不是 404：用户在列表里明明看得见它，
    回「不存在」会让人以为界面出了 bug。这里要的是一句说得通的拒绝。
    """
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(404, "案件不存在")
    if case.user_id is None:
        raise HTTPException(403, "这是公共示例数据，只读。如需修改请新建自己的案件。")
    if case.user_id != user.id:
        raise HTTPException(404, "案件不存在")
    return case


def entry_owned(
    entry_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> KnowledgeEntry:
    """知识条目的写权限（删除）。

    归属有两条路径，别混：
    - **案件材料**（scope=case）跟着案件走 —— 能改那个案件就能删它的材料；
    - **全局经验**看条目自己的 user_id。

    公共全局条目回 403：它在列表里看得见、能检索到，回「不存在」说不通；
    但也确实不能让某个登录用户把共享经验删掉。
    """
    entry = db.query(KnowledgeEntry).filter(KnowledgeEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(404, "条目不存在")

    if entry.scope == "case" and entry.case_id:
        case_owned(case_id=entry.case_id, user=user, db=db)
        return entry

    if entry.user_id is None:
        raise HTTPException(403, "这是公共经验，只读。如需修改请在自己的账号下新建。")
    if entry.user_id != user.id:
        raise HTTPException(404, "条目不存在")
    return entry
