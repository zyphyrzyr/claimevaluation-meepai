"""
pytest 公共夹具：把测试的数据目录与 .env 都隔离到临时目录

为什么要隔离：SQLite 与向量库（Chroma / JSON 兜底）都是进程级共享持久化。
若不隔离，跑一次 e2e_l2_http.py（它会往全局经验库写入条目）之后再跑 pytest，
知识库相关用例就会因为「库里多了别人的数据」而失败——测试之间、测试与本地
dev 服务之间互相污染，表现是「单独跑是绿的，一起跑就红」。

隔离方式：
1. 数据目录：在导入任何 core 模块之前设置 SOFT_IP_DATA_DIR 环境变量
   （core.config 在 import 时据此确定 DATA_DIR / DB_PATH / RUNTIME_DIR）。
2. .env：见下方 _isolate_env_file —— SOFT_IP_DATA_DIR 管不到它，必须单独处理。
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 固定路径（而非每次 mkdtemp）：便于排查，且不会在 /tmp 里堆积垃圾目录
TEST_DATA_DIR = Path(tempfile.gettempdir()) / "soft-ip-pytest-data"

if TEST_DATA_DIR.exists():
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)
TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)

os.environ["SOFT_IP_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ.setdefault("USE_MOCK", "True")


# ---------------------------------------------------------- .env 隔离

# 固定路径（而非每次 mkdtemp）：便于排查，且不会在 /tmp 里堆积垃圾目录
_ISOLATED_ENV_PATH = TEST_DATA_DIR / "env" / ".env"


# ---------------------------------------------------------- 默认登录态

DEFAULT_TEST_EMAIL = "tester@softip.local"
# 默认用户的 id 也挂到环境变量上：个别用例要直接往库里种「属于测试用户」的行，
# 而 **不能** 用 `from tests.conftest import ...` 拿常量 —— 导入 conftest 会
# 重新执行本文件顶部的 shutil.rmtree(TEST_DATA_DIR)，把已建好的测试库删掉，
# 表现为一片「attempt to write a readonly database」。
TEST_USER_ID_ENV = "SOFT_IP_TEST_USER_ID"


@pytest.fixture(scope="session", autouse=True)
def _default_logged_in_user(_isolate_env_file):
    """让全部既有用例以「一个已登录用户」的身份运行。

    依赖 _isolate_env_file 是为了保证它在 env 隔离**之后**执行：这个夹具
    会 import main，而 env 隔离必须在任何模块读 .env 之前生效。

    多用户隔离上线后，建案 / 改案 / 删案 / 启评估 / 删经验这些都要求登录。
    既有 400+ 个用例写的时候还没有账号这回事，逐个补登录步骤不现实；
    在依赖层统一注入一个默认用户，语义上正是改造前的隐含假设——
    「有个人正在用这个系统」。

    故意注入在依赖层而不是去改每个用例：这样用例代码保持原样，
    而专门验证鉴权的用例（test_l0_auth / test_l1_multitenant_isolation）
    可以通过再次 override 换成「未登录」或「另一个用户」，互不干扰。
    """
    from main import app
    from core.auth import current_user_optional, require_user
    from core.database import User, SessionLocal, init_db

    init_db()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == DEFAULT_TEST_EMAIL).first()
        if user is None:
            user = User(id="test-user", email=DEFAULT_TEST_EMAIL,
                        password_hash="", display_name="测试用户")
            db.add(user)
            db.commit()
            db.refresh(user)
        holder = {"user": user}
    finally:
        db.close()

    os.environ[TEST_USER_ID_ENV] = user.id
    app.dependency_overrides[require_user] = lambda: holder["user"]
    app.dependency_overrides[current_user_optional] = lambda: holder["user"]
    yield holder["user"]
    app.dependency_overrides.pop(require_user, None)
    app.dependency_overrides.pop(current_user_optional, None)
    os.environ.pop(TEST_USER_ID_ENV, None)


@pytest.fixture(scope="session", autouse=True)
def _isolate_env_file():
    """
    把 config.ENV_PATH 从「项目根目录的真实 .env」重定向到一份临时空 .env。

    SOFT_IP_DATA_DIR 只管数据目录，管不到 ENV_PATH——它是 core.config 的模块级
    常量（PROJECT_DIR / ".env"）。不单独隔离会有两个后果：

    1. 每个用例都在读开发者本地那份真实 .env。TestProviderSwap 的 3 个红灯就是
       这么来的：monkeypatch.setenv 改得了环境变量，改不了 .env 文件，于是按
       preset.key_envs 取 key 时，.env 里的真实 key 永远排在前面赢过测试设的值
       ——看起来像「配置没生效」，其实是测试根本没控住输入。
       同理，本地 .env 里若写着 USE_MOCK=False，漏传 USE_MOCK=True 跑 pytest
       就会真的往外发请求。

    2. settings_store.select_provider() 会真实写入 config.ENV_PATH。目前只有
       test_l0_providers.py 调它且自带隔离 fixture，但那是靠一行 fixture 挡着：
       将来哪个新测试少写这一行，就会把本地 .env 改写掉，而且不报错。

    只重定向路径、不动环境变量：泄漏源只有 .env 文件（进程环境里没有任何
    相关键），顺带也就不会误伤 USE_MOCK——否则 USE_MOCK=False 跑真实路径时会
    被静默改成 mock。需要带内容的 .env 的用例自己写临时文件再覆盖即可
    （参 test_l0_providers.py 的 env_file 夹具）。
    """
    from core import config

    _ISOLATED_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ISOLATED_ENV_PATH.write_text("", encoding="utf-8")

    original = config.ENV_PATH
    config.ENV_PATH = _ISOLATED_ENV_PATH
    yield _ISOLATED_ENV_PATH
    config.ENV_PATH = original
