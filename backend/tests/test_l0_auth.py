"""
L0 认证内核：注册 / 登录 / 登出 / 当前用户 / 密码哈希

这些用例走**真实 cookie 链路**（TestClient 各持一份 cookie 罐），
不碰 conftest 注入的默认登录态——认证端点本身不依赖那两个依赖项，
所以这里测到的就是浏览器里真实发生的事。
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from core.auth import hash_password, verify_password
from core.database import SessionLocal, User, UserSession, init_db
from main import app


def _email() -> str:
    return f"auth-{uuid.uuid4().hex[:10]}@example.com"


@pytest.fixture
def client():
    init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def real_auth():
    """摘掉 conftest 注入的默认登录态，让请求走真实的 cookie 鉴权。

    其余用例（以及既有的 400+ 个用例）依赖那个注入的「默认用户」，
    但「我注册了一个号 → 用它建案 → 统计里就该多一件」这种断言，
    只有在鉴权真的按 cookie 走时才成立。
    """
    from core.auth import current_user_optional, require_user

    saved = {}
    for dep in (require_user, current_user_optional):
        if dep in app.dependency_overrides:
            saved[dep] = app.dependency_overrides.pop(dep)
    yield
    app.dependency_overrides.update(saved)


# ============================================================
# 1. 密码哈希
# ============================================================

class TestPasswordHash:

    def test_roundtrip(self):
        stored = hash_password("hello123")
        assert verify_password("hello123", stored) is True
        assert verify_password("hello124", stored) is False

    def test_salt_is_random(self):
        """同样的密码两次哈希结果不同——否则等于明文存盐，撞库一次全中。"""
        assert hash_password("same") != hash_password("same")

    def test_roundtrip_with_old_rounds(self):
        """调高轮数后老记录仍可校验：轮数从 stored 里读，不是硬编码。"""
        old = "pbkdf2_sha256$1000$abcdef$" + __import__("hashlib").pbkdf2_hmac(
            "sha256", b"pw123456", b"abcdef", 1000).hex()
        assert verify_password("pw123456", old) is True

    def test_garbage_returns_false_not_exception(self):
        """格式不认识返回 False：认证失败不该把请求打成 500。"""
        for bad in ("", "not-a-hash", "x$1$2", None):
            assert verify_password("anything", bad) is False


# ============================================================
# 2. 注册
# ============================================================

class TestRegister:

    def test_register_logs_in_immediately(self, client):
        email = _email()
        r = client.post("/api/auth/register",
                        json={"email": email, "password": "abcd1234"})
        assert r.status_code == 200, r.text
        assert r.json()["user"]["email"] == email
        # 注册即登录：cookie 已下发，紧接着 /me 就该认得
        assert client.get("/api/auth/me").json()["user"]["email"] == email

    def test_duplicate_email_rejected(self, client):
        email = _email()
        client.post("/api/auth/register", json={"email": email, "password": "abcd1234"})
        r = client.post("/api/auth/register", json={"email": email, "password": "abcd1234"})
        assert r.status_code == 400
        assert "已注册" in r.json()["detail"]

    def test_email_is_normalized(self, client):
        """A@x.com 与 a@x.com 必须是同一个账号：手机自动首字母大写一下就登不进去是最常见的投诉。"""
        email = _email()
        client.post("/api/auth/register", json={"email": email.upper(), "password": "abcd1234"})
        r = client.post("/api/auth/login", json={"email": "  " + email + " ", "password": "abcd1234"})
        assert r.status_code == 200, r.text

    @pytest.mark.parametrize("email", ["", "no-at-sign", "a@b", "a b@c.com", "@x.com"])
    def test_bad_email_rejected(self, client, email):
        r = client.post("/api/auth/register", json={"email": email, "password": "abcd1234"})
        assert r.status_code == 400

    @pytest.mark.parametrize("pw", ["", "12345", "短"])
    def test_short_password_rejected(self, client, pw):
        r = client.post("/api/auth/register", json={"email": _email(), "password": pw})
        assert r.status_code == 400


# ============================================================
# 3. 登录 / 登出 / me
# ============================================================

class TestLoginLogout:

    def test_me_is_null_when_anonymous(self, client):
        """未登录是**正常状态**而不是错误：回 401 会让前端 401 拦截器每次开页面都弹登录框。"""
        r = client.get("/api/auth/me")
        assert r.status_code == 200
        assert r.json()["user"] is None

    def test_login_sets_cookie(self, client):
        email = _email()
        client.post("/api/auth/register", json={"email": email, "password": "abcd1234"})
        client.post("/api/auth/logout")

        assert client.get("/api/auth/me").json()["user"] is None
        r = client.post("/api/auth/login", json={"email": email, "password": "abcd1234"})
        assert r.status_code == 200, r.text
        assert client.get("/api/auth/me").json()["user"]["email"] == email

    def test_wrong_password_is_401(self, client):
        email = _email()
        client.post("/api/auth/register", json={"email": email, "password": "abcd1234"})
        client.post("/api/auth/logout")
        r = client.post("/api/auth/login", json={"email": email, "password": "wrong-pw"})
        assert r.status_code == 401

    def test_unknown_email_says_the_same_thing(self, client):
        """「邮箱不存在」与「密码错」必须同一句话：分开提示等于送给撞库者一个探针。"""
        r1 = client.post("/api/auth/login", json={"email": _email(), "password": "abcd1234"})
        email = _email()
        client.post("/api/auth/register", json={"email": email, "password": "abcd1234"})
        client.post("/api/auth/logout")
        r2 = client.post("/api/auth/login", json={"email": email, "password": "wrong-pw"})
        assert r1.status_code == r2.status_code == 401
        assert r1.json()["detail"] == r2.json()["detail"]

    def test_logout_revokes_the_session_row(self, client):
        """退出必须真的作废会话，而不只是清 cookie：
        cookie 清了但服务端会话还在，等于给了拿到旧 cookie 的人一张长期门票。"""
        email = _email()
        client.post("/api/auth/register", json={"email": email, "password": "abcd1234"})
        token = client.cookies.get("softip_session")
        assert token

        db = SessionLocal()
        try:
            assert db.query(UserSession).filter(UserSession.token == token).first() is not None
        finally:
            db.close()

        client.post("/api/auth/logout")

        db = SessionLocal()
        try:
            assert db.query(UserSession).filter(UserSession.token == token).first() is None
        finally:
            db.close()

    def test_password_hash_never_leaves_the_server(self, client):
        email = _email()
        r = client.post("/api/auth/register", json={"email": email, "password": "abcd1234"})
        payload = r.json()["user"]
        assert "password" not in payload
        assert "password_hash" not in payload


# ============================================================
# 4. 会话 cookie 的加固项
# ============================================================

class TestCookieHardening:

    def test_cookie_is_http_only_and_same_site(self, client):
        """httpOnly 挡 XSS 取 token；SameSite=Lax 挡 CSRF 带 cookie。"""
        r = client.post("/api/auth/register",
                        json={"email": _email(), "password": "abcd1234"})
        raw = r.headers.get("set-cookie", "")
        assert "softip_session=" in raw
        assert "httponly" in raw.lower()
        assert "samesite=lax" in raw.lower()


# ============================================================
# 5. 统计口径
# ============================================================

class TestStats:

    def test_stats_counts_only_own_data(self, client, real_auth):
        """侧边栏那行「N 个案件 · M 条经验」只数自己的：
        公共数据是大家都能看的示例，算进「我的」会让数字对不上列表。"""
        email = _email()
        client.post("/api/auth/register", json={"email": email, "password": "abcd1234"})
        before = client.get("/api/auth/me").json()["user"]["stats"]

        client.post("/api/cases", json={"name": "我的案子", "draft": True})
        after = client.get("/api/auth/me").json()["user"]["stats"]

        assert after["cases"] == before["cases"] + 1

        db = SessionLocal()
        try:
            db.query(User).filter(User.email == email).delete()
            db.commit()
        finally:
            db.close()
