"""
数据库模型 - SQLAlchemy ORM + SQLite
v4 调整（v2.1 清单）：
- 保留 7 张在用表：Case/Party/EvidenceFile/RuleHit/RetrievalRecord/ScoreSnapshot/MootRound/Report
- 删除 4 张死表：EvidenceFact/LegalElement/EvidenceMapping/Issue（旧代码 app.py 零引用）
- 新增：KnowledgeEntry（知识库条目）、AuditEvent（人机协作审计轨迹）
- Case 增加 context_json（CaseContext 共享状态总线落库）
- Report 增加 version（定稿版本快照，v1/v2... 并存）
- ScoreSnapshot.evidence_score 改存证据完整度（证据退出乘法后的置信度依据）
"""

from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Float, JSON, ForeignKey, Boolean
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from datetime import datetime
import uuid

from .config import SQLITE_URL, ensure_dirs

ensure_dirs()
engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def generate_id():
    return str(uuid.uuid4())[:8]


class Case(Base):
    __tablename__ = "cases"

    id = Column(String(20), primary_key=True, default=generate_id)
    name = Column(String(200), nullable=False)
    cause_type = Column(String(50), default="商标侵权")   # 商标侵权/著作权侵权/不正当竞争
    goal_type = Column(String(50))                        # 要钱 / 要名
    client_org = Column(String(200))
    status = Column(String(50), default="pending")        # pending/evaluating/partial/completed/blocked
    case_description = Column(Text)
    context_json = Column(JSON)                           # CaseContext 共享状态总线
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    parties = relationship("Party", back_populates="case", cascade="all, delete-orphan")
    evidence_files = relationship("EvidenceFile", back_populates="case", cascade="all, delete-orphan")
    rule_hits = relationship("RuleHit", back_populates="case", cascade="all, delete-orphan")
    score_snapshots = relationship("ScoreSnapshot", back_populates="case", cascade="all, delete-orphan")
    moot_rounds = relationship("MootRound", back_populates="case", cascade="all, delete-orphan")
    reports = relationship("Report", back_populates="case", cascade="all, delete-orphan")
    audit_events = relationship("AuditEvent", back_populates="case", cascade="all, delete-orphan")


class Party(Base):
    __tablename__ = "parties"

    id = Column(String(20), primary_key=True, default=generate_id)
    case_id = Column(String(20), ForeignKey("cases.id"))
    role = Column(String(50))            # plaintiff, defendant
    name = Column(String(200))
    party_type = Column(String(50))      # individual, company
    external_profile_json = Column(JSON) # 企查查 8 阶段画像缓存

    case = relationship("Case", back_populates="parties")


class EvidenceFile(Base):
    __tablename__ = "evidence_files"

    id = Column(String(20), primary_key=True, default=generate_id)
    case_id = Column(String(20), ForeignKey("cases.id"))
    file_name = Column(String(200))
    file_type = Column(String(50))
    parsed_text = Column(Text)           # 解析/OCR 文本（供证据盘点）
    parse_status = Column(String(50), default="pending")  # pending/ok/failed
    storage_uri = Column(String(500))
    uploaded_at = Column(DateTime, default=datetime.now)

    case = relationship("Case", back_populates="evidence_files")


class RuleHit(Base):
    __tablename__ = "rule_hits"

    id = Column(String(20), primary_key=True, default=generate_id)
    case_id = Column(String(20), ForeignKey("cases.id"))
    rule_code = Column(String(50))
    severity = Column(String(50))        # block, warning, pass
    result = Column(String(50))
    reason = Column(Text)

    case = relationship("Case", back_populates="rule_hits")


class RetrievalRecord(Base):
    __tablename__ = "retrieval_records"

    id = Column(String(20), primary_key=True, default=generate_id)
    case_id = Column(String(20), ForeignKey("cases.id"))
    query_text = Column(Text)
    query_type = Column(String(50))      # pkulaw / qcc / knowledge
    result_refs = Column(JSON)
    retrieval_score = Column(Float)


class ScoreSnapshot(Base):
    __tablename__ = "score_snapshots"

    id = Column(String(20), primary_key=True, default=generate_id)
    case_id = Column(String(20), ForeignKey("cases.id"))
    legal_score = Column(Float)          # 法律可行性
    business_score = Column(Float)       # 业务预期
    evidence_score = Column(Float)       # 证据完整度（置信度依据，不参与乘法）
    confidence_score = Column(Float)     # 置信度（独立输出）
    final_score = Column(Float)          # 主诉决策分 = 法律可行性 × 业务预期
    recommendation = Column(String(200))
    dimension_json = Column(JSON)        # 各维度明细（归因用）
    version = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.now)

    case = relationship("Case", back_populates="score_snapshots")


class MootRound(Base):
    __tablename__ = "moot_rounds"

    id = Column(String(20), primary_key=True, default=generate_id)
    case_id = Column(String(20), ForeignKey("cases.id"))
    mode = Column(String(20), default="embedded")  # embedded（内嵌）/ standalone（独立演练）
    round_type = Column(String(50))
    speaker_role = Column(String(50))    # plaintiff, defendant, judge
    content = Column(Text)
    source_refs = Column(JSON)

    case = relationship("Case", back_populates="moot_rounds")


class Report(Base):
    __tablename__ = "reports"

    id = Column(String(20), primary_key=True, default=generate_id)
    case_id = Column(String(20), ForeignKey("cases.id"))
    report_type = Column(String(50), default="memo")   # memo（决策备忘录）/ drill（演练报告）/ transcript（庭审记录）
    version = Column(Integer, default=1)               # 定稿快照版本
    markdown_content = Column(Text)
    file_uri = Column(String(500))                     # 导出的 Word/PDF 路径
    generated_at = Column(DateTime, default=datetime.now)

    case = relationship("Case", back_populates="reports")


class KnowledgeEntry(Base):
    """知识库条目（全局经验库；案件材料向量化见 core.knowledge，按 case_id 隔离）"""
    __tablename__ = "knowledge_entries"

    id = Column(String(20), primary_key=True, default=generate_id)
    scope = Column(String(20), default="global")       # global（全局经验库）/ case（案件材料库）
    case_id = Column(String(20), ForeignKey("cases.id"), nullable=True)  # scope=case 时必填
    source_type = Column(String(10))                   # A 证据文档 / B 手动粘贴 / C 观点沉淀 / D 独立建库
    title = Column(String(200))
    content = Column(Text)
    stale = Column(Boolean, default=False)             # 知识库健康检查：与权威源冲突标"待更新"
    created_at = Column(DateTime, default=datetime.now)


class AuditEvent(Base):
    """人机协作审计轨迹：观点注入、引导、节点重跑、版本定稿全留痕"""
    __tablename__ = "audit_events"

    id = Column(String(20), primary_key=True, default=generate_id)
    case_id = Column(String(20), ForeignKey("cases.id"))
    event_type = Column(String(50))      # viewpoint_inject / guide / node_rerun / snapshot / query
    node = Column(String(50))            # 关联的评估节点（可空）
    content = Column(Text)               # 用户输入或事件描述
    effect = Column(Text)                # 影响说明（如"侵权认定节点于 21:40 重跑"）
    created_at = Column(DateTime, default=datetime.now)

    case = relationship("Case", back_populates="audit_events")


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
