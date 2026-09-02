"""
L0 草稿功能（P4 中途保存）

覆盖：
1. 仅填名称可保存草稿（status=draft），其余字段可空；
2. 草稿缺名称 → 400；
3. PUT 更新草稿（仅草稿可改），字段被覆盖、状态保持 draft；
4. 非草稿不可 PUT / 不可启动评估 → 400；
5. 启动评估时必填项缺失 → 400 并列出缺项；
6. 必填齐全后启动评估 → status=pending，当事人写入 context 与 Party 表。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from main import app
    from core.database import init_db
    init_db()
    with TestClient(app) as c:
        yield c


def _full_payload(**overrides):
    p = {
        "name": "草稿案件",
        "cause_type": "商标侵权",
        "goal_type": "要钱",
        "client_org": "我方公司",
        "defendant_name": "被告公司",
        "case_description": "完整案情描述",
        "evidence_texts": "已有证据材料文本。",
    }
    p.update(overrides)
    return p


class TestDraftLifecycle:

    def test_draft_only_name_is_allowed(self, client):
        """仅案件名称即可存为草稿，其余字段留空不报错。"""
        resp = client.post("/api/cases", json={"name": "只有名字的草稿", "draft": True})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "draft"
        assert data["name"] == "只有名字的草稿"

        detail = client.get(f"/api/cases/{data['id']}").json()
        assert detail["context"]["defendant_info"]["name"] == ""
        assert detail["client_org"] == ""  # detail 应可回填 client_org

    def test_draft_without_name_rejected(self, client):
        resp = client.post("/api/cases", json={"draft": True})
        assert resp.status_code == 400
        assert "案件名称" in resp.text

    def test_update_draft_overwrites_fields_and_stays_draft(self, client):
        created = client.post("/api/cases", json={"name": "初始草稿", "draft": True}).json()
        case_id = created["id"]
        updated = client.put(
            f"/api/cases/{case_id}",
            json=_full_payload(name="补全后的草稿"),
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["status"] == "draft"

        detail = client.get(f"/api/cases/{case_id}").json()
        assert detail["name"] == "补全后的草稿"
        assert detail["client_org"] == "我方公司"
        assert detail["context"]["defendant_info"]["name"] == "被告公司"
        assert detail["context"]["defendant_info"]["evidence_texts"] == "已有证据材料文本。"

    def test_put_rejected_when_not_draft(self, client):
        created = client.post("/api/cases", json=_full_payload()).json()
        case_id = created["id"]
        resp = client.put(f"/api/cases/{case_id}", json=_full_payload(name="改不动"))
        assert resp.status_code == 400
        assert "仅草稿" in resp.text

    def test_start_evaluation_lists_missing_required(self, client):
        created = client.post("/api/cases", json={"name": "缺字段草稿", "draft": True}).json()
        case_id = created["id"]
        resp = client.post(f"/api/cases/{case_id}/start-evaluation")
        assert resp.status_code == 400, resp.text
        # 应列出所有缺失的必填项
        for missing in ["我司主体", "被告名称", "案情描述", "证据材料"]:
            assert missing in resp.text

    def test_start_evaluation_marks_pending_and_writes_parties(self, client):
        created = client.post("/api/cases", json=_full_payload(draft=True)).json()
        case_id = created["id"]
        resp = client.post(f"/api/cases/{case_id}/start-evaluation")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "pending"

        detail = client.get(f"/api/cases/{case_id}").json()
        parties = detail["context"]["parties"]
        roles = {p["role"] for p in parties}
        assert roles == {"plaintiff", "defendant"}

        from core.database import SessionLocal, Party
        db = SessionLocal()
        try:
            db_parties = db.query(Party).filter(Party.case_id == case_id).all()
            assert {p.role for p in db_parties} == {"plaintiff", "defendant"}
        finally:
            db.close()

    def test_start_evaluation_rejected_when_not_draft(self, client):
        created = client.post("/api/cases", json=_full_payload()).json()
        case_id = created["id"]
        resp = client.post(f"/api/cases/{case_id}/start-evaluation")
        assert resp.status_code == 400
        assert "仅草稿" in resp.text
