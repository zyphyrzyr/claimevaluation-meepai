"""
pytest 公共夹具：把测试的数据目录隔离到临时目录

为什么要隔离：SQLite 与向量库（Chroma / JSON 兜底）都是进程级共享持久化。
若不隔离，跑一次 e2e_l2_http.py（它会往全局经验库写入条目）之后再跑 pytest，
知识库相关用例就会因为「库里多了别人的数据」而失败——测试之间、测试与本地
dev 服务之间互相污染，表现是「单独跑是绿的，一起跑就红」。

隔离方式：在导入任何 core 模块之前设置 SOFT_IP_DATA_DIR 环境变量
（core.config 在 import 时据此确定 DATA_DIR / DB_PATH / RUNTIME_DIR）。
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

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
