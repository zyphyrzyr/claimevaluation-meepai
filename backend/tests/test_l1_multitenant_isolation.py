"""
L1 多用户隔离：A 的数据，B 既看不见也改不动

这个文件是「每个登录用户看到自己的案件和经验库」这条需求的验收标准。
全部走真实 HTTP + cookie（conftest 注入的默认登录态在此摘除），
因为越权漏洞恰恰长在「依赖项到底有没有真正生效」上——用 mock 用户测不出来。

三条主线：
  1. 案件：B 访问 A 的案件一律 404（不泄露存在性），改/删 403 或 404
  2. 公共数据：人人可读、人人不可改，认领后才归自己
  3. 经验库：含向量库那一层——漏了它，别人的经验会被召回进别人的评估报告
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from core.database import Case, KnowledgeEntry, SessionLocal, init_db
from core.knowledge import search_knowledge
from core.knowledge.service import PUBLIC_USER_ID, add_knowledge, list_entries
from main import app

PW = "isolate-1234"


def _email(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:10]}@example.com"


@pytest.fixture(scope="module", autouse=True)
def _real_auth():
    """摘掉 conftest 注入的「默认登录用户」，改用真实 cookie 鉴权。

    不摘的话 require_user 永远返回同一个默认用户，两个 client 的请求
    在服务端看来是同一个人——越权断言会全部假绿。
    """
    from core.auth import current_user_optional, require_user

    saved = {}
    for dep in (require_user, current_user_optional):
        if dep in app.dependency_overrides:
            saved[dep] = app.dependency_overrides.pop(dep)
    yield
    app.dependency_overrides.update(saved)


def _register(tag: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/register", json={"email": _email(tag), "password": PW})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture(scope="module")
def client_a():
    with _register("isoA") as c:
        yield c


@pytest.fixture(scope="module")
def client_b():
    with _register("isoB") as c:
        yield c


@pytest.fixture(scope="module")
def anon():
    """未登录：公共数据可浏览，写操作一律 401"""
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def case_a(client_a):
    r = client_a.post("/api/cases", json={"name": "A 的私密案件", "draft": True})
    assert r.status_code == 200, r.text
    case_id = r.json()["id"]
    yield case_id
    client_a.delete(f"/api/cases/{case_id}")


@pytest.fixture(scope="module")
def public_case():
    """一条公共案件（user_id 为空）：模拟迁移后的存量数据"""
    init_db()
    db = SessionLocal()
    try:
        cid = "iso-public-case"
        db.query(Case).filter(Case.id == cid).delete(synchronize_session=False)
        db.add(Case(id=cid, name="公共示例案", cause_type="商标侵权",
                    goal_type="要钱", status="draft", user_id=None))
        db.commit()
    finally:
        db.close()
    yield cid
    db = SessionLocal()
    try:
        db.query(Case).filter(Case.id == cid).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


# ============================================================
# 1. 案件：别人的看不见
# ============================================================

class TestCaseIsolation:

    def test_owner_sees_own_case(self, client_a, case_a):
        assert client_a.get(f"/api/cases/{case_a}").status_code == 200

    @pytest.mark.parametrize("method,path", [
        ("get", "/api/cases/{id}"),
        ("put", "/api/cases/{id}"),
        ("delete", "/api/cases/{id}"),
        ("post", "/api/cases/{id}/start-evaluation"),
        ("get", "/api/evaluation/{id}/result"),
        ("get", "/api/evaluation/{id}/run-state"),
        ("get", "/api/evaluation/{id}/audit"),
        ("get", "/api/report/{id}/result.docx"),
        ("get", "/api/report/{id}/transcript.docx"),
        ("get", "/api/moot/{id}"),
        ("get", "/api/advisor/{id}/history"),
    ])
    def test_other_user_gets_404(self, client_b, case_a, method, path):
        """一律 404 而不是 403：回 403 等于告诉对方「这个 id 真实存在」，
        那是可以被用来枚举别人案件数的探针。"""
        r = getattr(client_b, method)(path.format(id=case_a))
        assert r.status_code == 404, f"{method} {path} -> {r.status_code} {r.text[:120]}"

    def test_other_user_cannot_pause_or_stop(self, client_b, case_a):
        """控制端点也要拦：RUNS 是内存里的，漏了这两条就能停掉别人的评估。"""
        assert client_b.post(f"/api/evaluation/{case_a}/pause").status_code == 404
        assert client_b.post(f"/api/evaluation/{case_a}/stop").status_code == 404

    def test_others_case_not_in_my_list(self, client_b, case_a):
        ids = [c["id"] for c in client_b.get("/api/cases?page_size=50").json()["items"]]
        assert case_a not in ids

    def test_anonymous_sees_no_private_cases(self, anon, case_a):
        ids = [c["id"] for c in anon.get("/api/cases?page_size=50").json()["items"]]
        assert case_a not in ids

    def test_anonymous_cannot_create_case(self, anon):
        assert anon.post("/api/cases", json={"name": "匿名案", "draft": True}).status_code == 401

    def test_list_marks_public_rows(self, client_a, public_case):
        """前端要靠 is_public 把行标成「公共·只读」，否则用户点进去做一半才发现改不了。"""
        items = client_a.get("/api/cases?page_size=50").json()["items"]
        row = next(i for i in items if i["id"] == public_case)
        assert row["is_public"] is True
        own = client_a.post("/api/cases", json={"name": "A 的另一案", "draft": True}).json()
        items = client_a.get("/api/cases?page_size=50").json()["items"]
        assert next(i for i in items if i["id"] == own["id"])["is_public"] is False
        client_a.delete(f"/api/cases/{own['id']}")


# ============================================================
# 2. 公共数据：可读、不可改、可认领
# ============================================================

class TestPublicData:

    def test_anyone_can_read(self, client_a, client_b, anon, public_case):
        for c in (client_a, client_b, anon):
            assert c.get(f"/api/cases/{public_case}").status_code == 200

    def test_nobody_can_edit_or_delete_public_case(self, client_a, client_b, public_case):
        """403 而不是 404：用户在列表里明明看得见它，回「不存在」会让人以为界面坏了。"""
        for c in (client_a, client_b):
            r = c.put(f"/api/cases/{public_case}", json={"name": "改名"})
            assert r.status_code == 403, r.text
            assert "公共" in r.json()["detail"]
            assert c.delete(f"/api/cases/{public_case}").status_code == 403

    def test_anonymous_gets_401_on_write(self, anon, public_case):
        assert anon.put(f"/api/cases/{public_case}", json={"name": "x"}).status_code == 401

    def test_claim_moves_it_under_my_account(self, client_a, client_b, public_case):
        """认领是唯一出口：否则用户连自己过去积累的案件都改不动，
        「能改」变「只能看」是一次静默的功能倒退。"""
        assert client_a.post(f"/api/cases/{public_case}/claim").status_code == 200
        assert client_a.put(f"/api/cases/{public_case}",
                            json={"name": "认领后改名"}).status_code == 200
        # 认领后别人就看不见了
        assert client_b.get(f"/api/cases/{public_case}").status_code == 404

    def test_claim_twice_is_idempotent(self, client_a, public_case):
        """同一人重复认领不该报错：按钮点两次就 500 是最低级的体验事故。"""
        assert client_a.post(f"/api/cases/{public_case}/claim").status_code == 200
        assert client_a.post(f"/api/cases/{public_case}/claim").status_code == 200


# ============================================================
# 3. 经验库：含向量层
# ============================================================

@pytest.fixture(scope="module")
def entry_a(client_a):
    r = client_a.post("/api/knowledge/entries", json={
        "scope": "global", "source_type": "B",
        "title": "A 的独门裁判口径", "content": "这是只属于 A 的经验，B 不该在任何地方看到它。",
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


class TestKnowledgeIsolation:

    def test_owner_sees_own_entry(self, client_a, entry_a):
        ids = [e["id"] for e in client_a.get("/api/knowledge/entries?scope=global").json()]
        assert entry_a in ids

    def test_other_user_does_not_see_it(self, client_b, entry_a):
        ids = [e["id"] for e in client_b.get("/api/knowledge/entries?scope=global").json()]
        assert entry_a not in ids

    def test_anonymous_only_sees_public(self, anon, entry_a):
        rows = anon.get("/api/knowledge/entries?scope=global").json()
        assert entry_a not in [e["id"] for e in rows]
        assert all(e["is_public"] for e in rows)

    def test_other_user_cannot_delete_it(self, client_b, entry_a):
        assert client_b.delete(f"/api/knowledge/entries/{entry_a}").status_code == 404

    def test_search_does_not_leak_across_users(self, client_a, client_b, entry_a):
        """检索是向量路径，和列表走的不是同一段代码——两边都得拦。"""
        body = {"query": "独门裁判口径", "scope": "global", "top_k": 10}
        mine = [r["id"] for r in client_a.post("/api/knowledge/search", json=body).json()]
        theirs = [r["id"] for r in client_b.post("/api/knowledge/search", json=body).json()]
        assert entry_a in mine
        assert entry_a not in theirs

    def test_vector_layer_filters_by_user_id(self, entry_a):
        """直接打 service 层，确认过滤发生在**向量 where** 而不只是 SQL join 之后。

        只在 SQL 侧拦的话，别人的块仍会占掉 store.query 的 top_k 名额，
        表现为「检索结果莫名变少」——不报错，但召回是错的。
        """
        db = SessionLocal()
        try:
            mine = search_knowledge(db, "独门裁判口径", scope="global", top_k=10,
                                    user_id=_user_id_of(entry_a, db))
            anon_hits = search_knowledge(db, "独门裁判口径", scope="global", top_k=10,
                                         user_id=None)
            other = search_knowledge(db, "独门裁判口径", scope="global", top_k=10,
                                     user_id="someone-else")
        finally:
            db.close()
        assert entry_a in [h["id"] for h in mine]
        assert entry_a not in [h["id"] for h in anon_hits]
        assert entry_a not in [h["id"] for h in other]

    def test_public_entry_is_visible_to_everyone(self, client_a, client_b, db_session):
        """反向对照：公共经验人人都该看到——别把过滤写反了把公共数据也挡掉。"""
        e = add_knowledge(db_session, scope="global", source_type="D",
                          title="公共经验-D", content="所有人都该检索到的行业常识内容。")
        try:
            for c in (client_a, client_b):
                ids = [r["id"] for r in c.post("/api/knowledge/search", json={
                    "query": "行业常识", "scope": "global", "top_k": 10}).json()]
                assert e.id in ids, "公共经验被自己的过滤挡掉了"
        finally:
            from core.knowledge import delete_knowledge
            delete_knowledge(db_session, e.id)

    def test_deposit_belongs_to_the_depositor(self, client_a, client_b, case_a):
        """沉淀出来的经验记在自己名下：它是跨案复用的私有经验，
        不该变成所有人都看得到、却谁都删不掉的公共数据。

        观点沉淀内容由后端生成评估结果全文（不接收前端 content），
        本用例只验证归属隔离与标题格式，不校验内容。
        """
        r = client_a.post(f"/api/knowledge/cases/{case_a}/deposit")
        assert r.status_code == 200, r.text
        eid = r.json()["id"]
        assert r.json()["title"] == "A 的私密案件 评估结果"
        mine = [e["id"] for e in client_a.get("/api/knowledge/entries?scope=global").json()]
        theirs = [e["id"] for e in client_b.get("/api/knowledge/entries?scope=global").json()]
        assert eid in mine
        assert eid not in theirs

    def test_deposit_stores_markdown_not_summary(self, client_a, case_a):
        """观点沉淀存的是评估结果 markdown 原文（H1 开头），不是前端拼装的摘要。

        前端摘要以「结论：」开头，markdown 原文以「# 主诉评估结果」开头——
        用开头即可区分，锁死「观点沉淀 = 评估结果完整原文」这条契约，防回归。
        """
        eid = client_a.post(f"/api/knowledge/cases/{case_a}/deposit").json()["id"]
        entries = client_a.get("/api/knowledge/entries?scope=global").json()
        content = next(e["content"] for e in entries if e["id"] == eid)
        assert content.startswith("# 主诉评估结果"), \
            f"沉淀内容不是 markdown 原文：{content[:40]!r}"
        assert "评估未完成" in content, "空案件也应生成完整报告骨架，而非空内容或报错"

    def test_public_entry_cannot_be_deleted(self, client_a, db_session):
        e = add_knowledge(db_session, scope="global", source_type="D",
                          title="公共经验-不可删", content="公共数据，谁都不能删。")
        try:
            r = client_a.delete(f"/api/knowledge/entries/{e.id}")
            assert r.status_code == 403
            assert "公共" in r.json()["detail"]
        finally:
            from core.knowledge import delete_knowledge
            delete_knowledge(db_session, e.id)


def _user_id_of(entry_id: str, db):
    row = db.query(KnowledgeEntry).filter(KnowledgeEntry.id == entry_id).first()
    return row.user_id


@pytest.fixture(scope="module")
def db_session():
    init_db()
    s = SessionLocal()
    yield s
    s.close()


# ============================================================
# 4. 向量块 user_id 回填（存量数据迁移）
# ============================================================

class TestVectorBackfill:

    def test_legacy_chunk_becomes_findable_after_backfill(self):
        """完整的迁移语义：一块「没有 user_id 的老块」在回填前搜不到，回填后搜得到。

        这正是不做回填会发生的事——老经验库不是报错，而是静默地一条都搜不出来。
        """
        from core.knowledge.embeddings import embed_query, embed_texts
        from core.knowledge.service import _global_where
        from core.knowledge.vector_store import get_store

        store = get_store()
        cid = f"legacy-test-chunk-{uuid.uuid4().hex[:8]}"
        text = "存量老经验：回填前它应当检索不到。"
        # 故意不带 user_id，模拟隔离上线前入库的块
        store.upsert([cid], embed_texts([text]), [text],
                     [{"scope": "global", "entry_id": "legacy-entry", "title": "老经验"}])
        try:
            assert cid not in [h["id"] for h in
                               store.query(embed_query(text), _global_where(None), top_k=20)]

            assert store.backfill_user_id(PUBLIC_USER_ID) >= 1
            assert cid in [h["id"] for h in
                           store.query(embed_query(text), _global_where(None), top_k=20)]

            # 幂等：再跑一次不该动任何块
            assert store.backfill_user_id(PUBLIC_USER_ID) == 0
        finally:
            store.delete([cid])

    def test_fallback_store_understands_or_where(self):
        """降级存储必须认得 $or：否则「自己的 或 公共的」在 chroma 上生效、
        在没装 chromadb 的机器上被当成普通键名比对，两边行为不一致。"""
        from core.knowledge.vector_store import _FallbackStore

        store = _FallbackStore.__new__(_FallbackStore)
        store._data = {
            "c1": {"vector": [1.0, 0.0], "document": "我的",
                   "metadata": {"scope": "global", "user_id": "A"}},
            "c2": {"vector": [1.0, 0.0], "document": "公共",
                   "metadata": {"scope": "global", "user_id": ""}},
            "c3": {"vector": [1.0, 0.0], "document": "别人的",
                   "metadata": {"scope": "global", "user_id": "B"}},
        }
        store._dirty = False

        where = {"$and": [{"scope": "global"},
                          {"$or": [{"user_id": "A"}, {"user_id": ""}]}]}
        hits = {h["document"] for h in store.query([1.0, 0.0], where, top_k=10)}
        assert hits == {"我的", "公共"}
