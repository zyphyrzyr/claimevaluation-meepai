"""
L0 草稿功能（P4 中途保存）

覆盖：
1. 仅填名称可保存草稿（status=draft），其余字段可空；
2. 草稿缺名称 → 400；
3. PUT 更新已评估/待评估案件（非 evaluating）成功，字段被覆盖、状态保持原值；
4. evaluating 状态禁止 PUT / 启动评估 / 删除文件 → 400；
5. 启动评估时必填项缺失 → 400 并列出缺项；
6. 必填齐全后启动评估 → status=pending，当事人写入 context 与 Party 表。
"""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("USE_MOCK", "True")

import fitz
from fastapi.testclient import TestClient


def _pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontname="china-ss", fontsize=12)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


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

    def test_update_non_draft_case_succeeds(self, client):
        """已评估/待评估案件（非 evaluating）也可编辑，状态保持原值。"""
        created = client.post("/api/cases", json=_full_payload()).json()
        case_id = created["id"]
        assert created["status"] == "pending"
        resp = client.put(
            f"/api/cases/{case_id}",
            json=_full_payload(name="改得了", client_org="新原告"),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "pending"
        detail = client.get(f"/api/cases/{case_id}").json()
        assert detail["name"] == "改得了"
        assert detail["client_org"] == "新原告"

    def test_put_rejected_when_evaluating(self, client):
        """评估进行中禁止编辑。"""
        created = client.post("/api/cases", json=_full_payload()).json()
        case_id = created["id"]
        from core.database import SessionLocal, Case
        db = SessionLocal()
        try:
            db.query(Case).filter(Case.id == case_id).update({"status": "evaluating"})
            db.commit()
        finally:
            db.close()
        resp = client.put(f"/api/cases/{case_id}", json=_full_payload(name="改不动"))
        assert resp.status_code == 400
        assert "评估运行中" in resp.text

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

    def test_start_evaluation_on_completed_resets_pending(self, client):
        """已评估案件可重新启动评估：校验必填项后状态重置为 pending，且前一轮结果被清空。"""
        created = client.post("/api/cases", json=_full_payload()).json()
        case_id = created["id"]
        from core.database import SessionLocal, Case
        from core.case_context import CaseContext
        db = SessionLocal()
        try:
            # 模拟「上一轮评估已产出结果」：在已有输入字段的基础上追加决策层结果
            case = db.query(Case).filter(Case.id == case_id).one()
            ctx = CaseContext.from_dict(case.context_json or {})
            ctx.case_id = case_id
            ctx.set_dimension("rights", {"score": 80, "analysis": "x"}, status="ok")
            ctx.scores = {"final": 80}
            case.status = "completed"
            case.context_json = ctx.to_dict()
            db.commit()
        finally:
            db.close()
        resp = client.post(f"/api/cases/{case_id}/start-evaluation")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "pending"
        # 前一轮结果必须被清空（整体重跑，从头开始）
        db = SessionLocal()
        try:
            reloaded = db.query(Case).filter(Case.id == case_id).one()
            ctx2 = CaseContext.from_dict(reloaded.context_json or {})
            assert ctx2.dimension_results == {}, "重新评估应清空前一轮维度结果"
            assert ctx2.scores == {}, "重新评估应清空前一轮分数"
            # 输入字段保留（案情/当事人等），重置只动决策层
            assert reloaded.status == "pending"
        finally:
            db.close()

    def test_start_evaluation_rejected_when_evaluating(self, client):
        created = client.post("/api/cases", json=_full_payload()).json()
        case_id = created["id"]
        from core.database import SessionLocal, Case
        db = SessionLocal()
        try:
            db.query(Case).filter(Case.id == case_id).update({"status": "evaluating"})
            db.commit()
        finally:
            db.close()
        resp = client.post(f"/api/cases/{case_id}/start-evaluation")
        assert resp.status_code == 400
        assert "评估运行中" in resp.text

    def test_draft_with_uploaded_file_persists_and_lists_in_detail(self, client):
        """草稿上传 PDF 后解析落库，重开草稿时 evidence_texts 含解析文本且详情列出文件。"""
        pdf = _pdf_bytes("商标注册证第9988776号，核定使用商品第25类。")
        resp = client.post(
            "/api/cases",
            data={"payload": json.dumps({"name": "带文件草稿", "draft": True}, ensure_ascii=False)},
            files=[("evidence_files", ("cert.pdf", pdf, "application/pdf"))],
        )
        assert resp.status_code == 200, resp.text
        case_id = resp.json()["id"]

        detail = client.get(f"/api/cases/{case_id}").json()
        ctx = detail["context"]
        assert "商标注册证第9988776号" in ctx["defendant_info"]["evidence_texts"]
        assert len(detail["evidence_files"]) == 1
        assert detail["evidence_files"][0]["file_name"] == "cert.pdf"
        assert detail["evidence_files"][0]["parse_status"] == "ok"

    def test_delete_evidence_file_removes_snippet_and_record(self, client):
        """删除已上传文件：EvidenceFile 记录消失，evidence_texts 剥离其解析片段，详情不再列出。"""
        pdf = _pdf_bytes("待删除的证据内容第112233号。")
        created = client.post(
            "/api/cases",
            data={"payload": json.dumps({"name": "可删文件草稿", "draft": True}, ensure_ascii=False)},
            files=[("evidence_files", ("todelete.pdf", pdf, "application/pdf"))],
        ).json()
        case_id = created["id"]

        file_id = client.get(f"/api/cases/{case_id}").json()["evidence_files"][0]["id"]
        del_resp = client.delete(f"/api/cases/{case_id}/evidence-files/{file_id}")
        assert del_resp.status_code == 200, del_resp.text
        assert del_resp.json()["ok"] is True

        detail = client.get(f"/api/cases/{case_id}").json()
        assert detail["evidence_files"] == []
        assert "待删除的证据内容" not in detail["context"]["defendant_info"]["evidence_texts"]

    def test_delete_evidence_file_on_non_draft_succeeds(self, client):
        """已评估案件（completed）也可删除历史证据文件，并同步剥离解析片段、重建材料库。"""
        pdf = _pdf_bytes("待删除的证据内容第112233号。")
        created = client.post(
            "/api/cases",
            data={"payload": json.dumps({"name": "可删文件案件", "draft": True}, ensure_ascii=False)},
            files=[("evidence_files", ("todelete.pdf", pdf, "application/pdf"))],
        ).json()
        case_id = created["id"]
        from core.database import SessionLocal, Case
        db = SessionLocal()
        try:
            db.query(Case).filter(Case.id == case_id).update({"status": "completed"})
            db.commit()
        finally:
            db.close()
        file_id = client.get(f"/api/cases/{case_id}").json()["evidence_files"][0]["id"]
        del_resp = client.delete(f"/api/cases/{case_id}/evidence-files/{file_id}")
        assert del_resp.status_code == 200, del_resp.text
        detail = client.get(f"/api/cases/{case_id}").json()
        assert detail["evidence_files"] == []
        assert "待删除的证据内容" not in detail["context"]["defendant_info"]["evidence_texts"]

    def test_delete_evidence_file_rejected_when_evaluating(self, client):
        created = client.post("/api/cases", json=_full_payload()).json()
        case_id = created["id"]
        from core.database import SessionLocal, Case
        db = SessionLocal()
        try:
            db.query(Case).filter(Case.id == case_id).update({"status": "evaluating"})
            db.commit()
        finally:
            db.close()
        # 非 evaluating 无 evidence_files，但仍应被 400 拦截
        resp = client.delete(f"/api/cases/{case_id}/evidence-files/whatever")
        assert resp.status_code == 400
        assert "评估运行中" in resp.text

    def test_delete_case_removes_orphans_and_knowledge(self, client):
        """删除案件：Case/EvidenceFile 与案件材料库(scope=case)一并清除，详情 404。"""
        # 注意：ingest_case_materials 会过滤掉 < 10 字符的片段，故证据文本需足够长才会入库
        created = client.post("/api/cases", json=_full_payload(
            draft=True,
            evidence_texts="证据材料文本内容充分，足以触发入库分块。",
        )).json()
        case_id = created["id"]

        from core.database import SessionLocal, Case, EvidenceFile, KnowledgeEntry
        db = SessionLocal()
        try:
            assert db.query(Case).filter(Case.id == case_id).count() == 1
            # 证据文本自动入库产生 scope=case 知识条目
            assert db.query(KnowledgeEntry).filter(
                KnowledgeEntry.scope == "case", KnowledgeEntry.case_id == case_id).count() >= 1
        finally:
            db.close()

        del_resp = client.delete(f"/api/cases/{case_id}")
        assert del_resp.status_code == 200, del_resp.text
        assert del_resp.json()["ok"] is True

        db = SessionLocal()
        try:
            assert db.query(Case).filter(Case.id == case_id).count() == 0
            assert db.query(EvidenceFile).filter(EvidenceFile.case_id == case_id).count() == 0
            assert db.query(KnowledgeEntry).filter(
                KnowledgeEntry.scope == "case", KnowledgeEntry.case_id == case_id).count() == 0
        finally:
            db.close()

        # 详情应 404
        assert client.get(f"/api/cases/{case_id}").status_code == 404
