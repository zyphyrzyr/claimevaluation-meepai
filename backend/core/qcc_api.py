"""
企查查 MCP Connector API — 子维度 2.1：财务回报
封装 8 阶段工作流，调用 qcc-company/risk/executive/ipr/operation/legal-case 6 个 Server。
参考指引：企查查MCP调用指引-2.1财务回报.md
"""

import json
import urllib.request
import urllib.error
from datetime import datetime
from typing import Dict, List, Optional, Any

from .config import get_runtime_settings

API_BASE = "https://agent.qcc.com/mcp"

# 工具 → 所属 Server
TOOL_SERVER = {
    # qcc-company
    "get_company_by_query": "company",
    "get_company_profile": "company",
    "get_company_registration_info": "company",
    "get_financial_data": "company",
    "get_listing_info": "company",
    "get_branches": "company",
    "get_external_investments": "company",
    "get_actual_controller": "company",
    "get_key_personnel": "company",
    "get_annual_reports": "company",
    "get_change_records": "company",
    "get_contact_info": "company",
    "verify_company_accuracy": "company",
    # qcc-risk
    "get_company_risk_scan": "risk",
    "get_dishonest_info": "risk",
    "get_judgment_debtor_info": "risk",
    "get_high_consumption_restriction": "risk",
    "get_terminated_cases": "risk",
    "get_business_exception": "risk",
    "get_serious_violation": "risk",
    "get_equity_freeze": "risk",
    "get_cancellation_record_info": "risk",
    "get_liquidation_info": "risk",
    "get_bankruptcy_reorganization": "risk",
    "get_chattel_mortgage_info": "risk",
    "get_land_mortgage_info": "risk",
    "get_equity_pledge_info": "risk",
    "get_stock_pledge_info": "risk",
    "get_guarantee_info": "risk",
    "get_judicial_auction": "risk",
    "get_case_filing_info": "risk",
    "get_court_notice": "risk",
    "get_tax_arrears_notice": "risk",
    "get_tax_violation": "risk",
    "get_tax_abnormal": "risk",
    "get_administrative_penalty": "risk",
    "get_environmental_penalty": "risk",
    "get_disciplinary_list": "risk",
    "get_default_info": "risk",
    "get_judicial_documents": "risk",
    "get_company_related_risk_scan": "risk",
    "get_property_asset_announcement": "risk",
    "get_exit_restriction": "risk",
    # qcc-executive
    "get_executive_risk_scan": "executive",
    "get_executive_dishonest": "executive",
    "get_executive_judgment_debtor": "executive",
    "get_executive_equity_freeze": "executive",
    "get_executive_equity_pledge": "executive",
    "get_executive_high_consumption_ban": "executive",
    "get_executive_property_reward_notice": "executive",
    "get_executive_exit_restriction": "executive",
    "get_executive_tax_violation": "executive",
    "get_executive_admin_penalty": "executive",
    "get_executive_controlled_companies": "executive",
    "get_executive_investments": "executive",
    "get_executive_related_risk_scan": "executive",
    "get_executive_case_filing": "executive",
    # qcc-ipr
    "get_trademark_info": "ipr",
    "get_online_store": "ipr",
    "get_app_info": "ipr",
    "get_mini_program": "ipr",
    "get_wechat_official_account": "ipr",
    "get_douyin_account": "ipr",
    "get_ipr_pledge": "ipr",
    # qcc-operation
    "get_bidding_info": "operation",
    "get_financing_records": "operation",
    "get_honor_info": "operation",
    "get_ranking_list_info": "operation",
    "get_recruitment_info": "operation",
    "get_credit_evaluation": "operation",
    "get_administrative_license": "operation",
    "get_qualifications": "operation",
    # qcc-legal-case
    "get_judicial_case_search": "case",
}

def _qcc_configured() -> bool:
    return bool(get_runtime_settings().get("qcc_api_token", "").strip())


def _qcc_headers() -> dict:
    token = get_runtime_settings().get("qcc_api_token", "").strip()
    if not token:
        raise RuntimeError("未配置 QCC_API_TOKEN，无法调用企查查服务")
    auth_value = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    return {
        "Authorization": auth_value,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }


def _qcc_unconfigured_result() -> Dict:
    message = "未配置 QCC_API_TOKEN，企查查画像已跳过"
    return {
        "status": "skipped",
        "error": message,
        "_summary": message,
        "stages": {},
        "metrics": {},
    }


def _rpc_call(tool_name: str, args: dict, server: str = None) -> Dict:
    """通用 QCC JSON-RPC 调用"""
    if server is None:
        server = TOOL_SERVER.get(tool_name, "company")
    url = f"{API_BASE}/{server}/stream"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args},
    }
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=_qcc_headers())
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return {"error": str(e)[:200]}

    # SSE / JSON-RPC 解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        for line in raw.split("\n"):
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
        return {"error": f"SSE parse fail: {raw[:200]}"}


def _rpc_failed(rpc_result: dict) -> str:
    """识别 RPC 失败并返回人类可读原因；无失败返回空串。

    专用于把被 _rpc_call 吞掉的 401 / 网络错误显式暴露出来，
    避免上层把「鉴权失败」误当成「检索到 0 条」或「回款概率 50%」。
    """
    if not isinstance(rpc_result, dict):
        return ""
    err = rpc_result.get("error")
    if not err:
        return ""
    if "401" in err or "Unauthorized" in err or "Authorization" in err:
        return f"企查查鉴权失败(401)：QCC_API_TOKEN 无效或已过期"
    if "403" in err or "Forbidden" in err:
        return f"企查查无权限(403)：该 token 未开通对应工具"
    return f"企查查调用失败：{err}"


def _auth_failed_metrics(error_msg: str) -> dict:
    """鉴权/传输失败时返回的指标占位：明确置 None，绝不回填假分数。"""
    return {
        "recovery_probability": None,
        "damages_p50": None,
        "time_extra_months": 0,
        "red_flags": [],
        "green_flags": [],
        "auth_error": True,
        "error": error_msg,
    }


def _extract_text(rpc_result: dict) -> str:
    """从 rpc 结果提取文本"""
    if not rpc_result or "error" in rpc_result:
        return ""
    r = rpc_result.get("result", {})
    if not isinstance(r, dict):
        return str(r)[:1000]
    content = r.get("content", [])
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and "text" in first:
            return first["text"]
    # fallback
    data = r.get("Data", r.get("data", r.get("result", {})))
    if isinstance(data, (list, dict)):
        return json.dumps(data, ensure_ascii=False)[:2000]
    return str(r)[:1000]


def _is_empty_risk_wrapper(item: dict) -> bool:
    """判断是否为 未发现任何记录 / 无记录 的包装响应"""
    if not isinstance(item, dict):
        return False
    # QCC 风险工具在无记录时返回包装，包含多种"未发现"的表述
    # 检查所有字符串字段
    no_record_keywords = ["未发现", "未发现任何", "未查到", "无相关记录", "无记录", "当前无【", "当前无"]
    for val in item.values():
        if isinstance(val, str):
            for kw in no_record_keywords:
                if kw in val:
                    return True
    # 只含元数据、无实质内容的也过滤
    if set(item.keys()) <= {"企业名称", "搜索结果", "检索关键字", "匹配结果", "摘要", "关联分析"}:
        if item.get("匹配结果") == "未匹配":
            return True
    return False


def _filter_empty(items: list) -> list:
    """过滤掉 未发现任何记录 的包装项"""
    return [it for it in items if not _is_empty_risk_wrapper(it)]


def _extract_items(rpc_result: dict) -> list:
    """从 JSON-RPC 结果中提取列表，过滤 未发现任何记录 的包装响应"""
    if not rpc_result or "error" in rpc_result:
        return []
    r = rpc_result.get("result", {})
    if not isinstance(r, dict):
        return []

    content = r.get("content", [])
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and "text" in first:
            try:
                parsed = json.loads(first["text"])
                if isinstance(parsed, list):
                    return _filter_empty(parsed)
                if isinstance(parsed, dict):
                    # 可能是 {data: [...], total: N} 结构
                    for k in ("data", "Data", "items", "list"):
                        v = parsed.get(k, [])
                        if isinstance(v, list) and v:
                            return _filter_empty(v)
                    return _filter_empty([parsed])
            except (json.JSONDecodeError, TypeError):
                return [{"_raw": first["text"]}]

    # fallback: result.result / Data
    for k in ("result", "Result", "Data", "data", "items", "item"):
        v = r.get(k, [])
        if isinstance(v, list) and v:
            return _filter_empty(v)
    return []


def _count_items(rpc_result: dict) -> int:
    """从结果中提取条目数量"""
    items = _extract_items(rpc_result)
    return len(items)


def _safe_call(tool_name: str, search_key: str, server: str = None, extra_args: dict = None) -> dict:
    """安全包装调用"""
    args = {"searchKey": search_key}
    if extra_args:
        args.update(extra_args)
    try:
        return _rpc_call(tool_name, args, server)
    except Exception as e:
        return {"error": str(e)[:200]}


# ============================================================
# 8 阶段工作流
# ============================================================

def search_for_financial_qcc_full(defendant_info: dict = None) -> Dict:
    """
    企查查 2.1 财务回报全流程评估
    参数: defendant_info 为 Phase 1 LLM 提取的结构化被告信息
      至少包含 name, type 字段；可选 aliases, location_hint, industry_hint
    返回: {_summary, stages:{...}, metrics:{...}}
    """
    if not _qcc_configured():
        return _qcc_unconfigured_result()
    # 兼容旧版字符串调用
    if isinstance(defendant_info, str):
        defendant_info = {"name": defendant_info, "type": "enterprise"}

    if not defendant_info or not defendant_info.get("name", "").strip():
        return {"_summary": "⚠️ 未提供被告名称，无法调用企查查", "stages": {}, "metrics": {}}

    d_type = defendant_info.get("type", "enterprise")

    if d_type == "individual":
        return _search_individual(defendant_info)
    else:
        return _search_enterprise(defendant_info)


def _search_enterprise(d: dict) -> Dict:
    name = d.get("name", "").strip()
    location_hint = d.get("location_hint", "")
    industry_hint = d.get("industry_hint", "")

    result = {
        "_summary": "",
        "stages": {},
        "metrics": {
            "recovery_probability": 50.0,
            "damages_p50": None,
            "time_extra_months": 0,
            "red_flags": [],
            "green_flags": [],
        }
    }
    summary_parts = []

    # ── A: 主体锁定（含消歧）──
    stage_a = _stage_a_lock_entity(name, location_hint, industry_hint)
    result["stages"]["A_主体锁定"] = stage_a
    if stage_a.get("error"):
        failed = _rpc_failed(stage_a) or stage_a["error"]
        result["status"] = "auth_error" if "鉴权" in failed or "401" in failed else "error"
        result["error"] = failed
        result["metrics"] = _auth_failed_metrics(failed)
        result["_summary"] = f"❌ 阶段A失败: {failed}"
        return result
    locked_name = stage_a.get("locked_name", name)
    summary_parts.append(f"✅ 锁定主体: {locked_name}")

    # ── B: 基本盘 + 财务画像 ──
    stage_b = _stage_b_basics(locked_name)
    result["stages"]["B_基本盘"] = stage_b
    # 从基本盘提取绿灯信号
    fin_data = stage_b.get("财务数据", {})
    listing = stage_b.get("上市信息", {})
    reg_info = stage_b.get("工商登记", {})
    if fin_data:
        summary_parts.append(f"📊 财务数据: {fin_data.get('_summary', '已获取')}")
    if listing:
        result["metrics"]["green_flags"].append("上市公司")
    if reg_info:
        summary_parts.append(f"🏢 工商登记: {reg_info.get('_summary', '已获取')}")

    # 提取核心人员名单
    personnel = stage_b.get("核心人员", {})
    controller = stage_b.get("实际控制人", {})

    # ── C: 风险分诊 ──
    stage_c = _stage_c_risk_scan(locked_name)
    result["stages"]["C_风险分诊"] = stage_c
    scan_hits = stage_c.get("hits", {})
    summary_parts.append(f"🔍 风险分诊: {stage_c.get('_summary','')}")

    # ── D: 风险下钻（按 C 命中结果）──
    stage_d = _stage_d_risk_drill(locked_name, scan_hits)
    result["stages"]["D_风险下钻"] = stage_d

    # ── E: 核心人员风险 ──
    stage_e = _stage_e_executive_risk(locked_name, controller, personnel)
    result["stages"]["E_人员风险"] = stage_e

    # ── F: 经营规模 + 侵权渠道 ──
    stage_f = _stage_f_business_scale(locked_name)
    result["stages"]["F_经营规模"] = stage_f
    summary_parts.append(f"🏭 经营规模: {stage_f.get('_summary','已获取')}")

    # ── G: 诉讼时间预估 ──
    stage_g = _stage_g_litigation_timeline(locked_name)
    result["stages"]["G_诉讼时间"] = stage_g

    # ── H: 指标计算 ──
    metrics = _stage_h_calc_metrics(stage_b, stage_c, stage_d, stage_e, stage_f, stage_g)
    result["metrics"] = metrics

    # 最终摘要
    summary_parts.append("")
    summary_parts.append(f"💰 回款概率: {metrics['recovery_probability']:.0f}%")
    summary_parts.append(f"📈 判赔调整: {metrics.get('damages_adjustment','基准')}")
    summary_parts.append(f"⏱ 时间延长: +{metrics['time_extra_months']}月")
    if metrics["red_flags"]:
        summary_parts.append(f"🚨 风险信号: {'; '.join(metrics['red_flags'][:5])}")
    if metrics["green_flags"]:
        summary_parts.append(f"✅ 利好信号: {'; '.join(metrics['green_flags'][:5])}")
    result["_summary"] = "\n".join(summary_parts)

    return result


# ── 阶段 A ──
def _stage_a_lock_entity(name: str, location_hint: str = "", industry_hint: str = "") -> dict:
    """模糊搜索 → 锁定唯一主体 → 用 location/industry 消歧"""
    result = {"ok": False, "locked_name": "", "credit_code": "", "candidates": [], "match_status": ""}

    r = _safe_call("get_company_by_query", name, server="company")
    if r.get("error"):
        result["error"] = r["error"]
        return result

    items = _extract_items(r)
    if not items:
        result["error"] = "未匹配到任何企业"
        return result

    # QCC 返回结构: [{匹配结果, 企业信息: [{企业名称, 统一社会信用代码, ...}]}]
    company_list = []
    match_status = ""

    for it in items:
        if not isinstance(it, dict):
            continue
        match_status = it.get("匹配结果", "")
        # 直接在 item 里的 企业信息 字段
        inner = it.get("企业信息", it.get("companyInfo", []))
        if isinstance(inner, list):
            company_list.extend(inner)
        elif isinstance(inner, dict):
            company_list.append(inner)

    if not company_list:
        result["error"] = f"匹配结果={match_status}，但未解析到企业信息"
        return result

    # 构建候选列表
    candidates = []
    for c in company_list[:5]:
        if isinstance(c, dict):
            cname = c.get("企业名称", c.get("companyName", c.get("name", "")))
            ccode = c.get("统一社会信用代码", c.get("creditCode", c.get("uscc", "")))
            candidates.append({"name": cname, "credit_code": ccode})
    result["candidates"] = candidates

    # 消歧锁定：多候选时用 industry_hint / location_hint 过滤
    if len(candidates) == 1:
        first = candidates[0]
    else:
        # 优先匹配行业线索
        best = None
        for c in candidates:
            cname = c.get("name", "")
            if industry_hint and industry_hint in cname:
                best = c
                break
        # 优先匹配所在地
        if not best and location_hint:
            for c in candidates:
                if location_hint in c.get("name", ""):
                    best = c
                    break
        # 匹配搜索词
        if not best:
            for c in candidates:
                if name in c.get("name", ""):
                    best = c
                    break
        first = best or candidates[0]

    result["ok"] = True
    result["locked_name"] = first.get("name", name)
    result["credit_code"] = first.get("credit_code", "")
    result["match_status"] = match_status
    result["match_status"] = match_status
    return result


# ── 阶段 B ──
def _stage_b_basics(locked_name: str) -> dict:
    """并行获取基本盘"""
    result = {}

    calls = [
        ("工商登记", "get_company_registration_info", "company"),
        ("企业简介", "get_company_profile", "company"),
        ("财务数据", "get_financial_data", "company"),
        ("上市信息", "get_listing_info", "company"),
        ("分支机构", "get_branches", "company"),
        ("对外投资", "get_external_investments", "company"),
        ("实际控制人", "get_actual_controller", "company"),
        ("核心人员", "get_key_personnel", "company"),
        ("年报", "get_annual_reports", "company"),
        ("近一年变更", "get_change_records", "company"),
    ]

    for label, tool, server in calls:
        r = _safe_call(tool, locked_name, server)
        items = _extract_items(r)
        result[label] = {
            "_count": len(items),
            "_summary": f"{len(items)}条记录" if items else "无数据",
            "_items": items[:3],  # 保留前3条详情
        }

    return result


# ── 阶段 C ──
def _stage_c_risk_scan(locked_name: str) -> dict:
    """风险分诊：get_company_risk_scan（35维）"""
    r = _safe_call("get_company_risk_scan", locked_name, server="risk")
    if r.get("error"):
        return {"error": r["error"], "hits": {}, "_summary": "分诊失败"}

    text = _extract_text(r)
    hits = {}

    try:
        data = json.loads(text) if isinstance(text, str) else text
    except json.JSONDecodeError:
        # 解析非 JSON 文本，尝试提取风险关键词
        data = {}
        if text:
            hits["_raw"] = text[:500]

    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (int, float)) and v > 0:
                hits[k] = v
            elif isinstance(v, list) and v:
                hits[k] = len(v)

    total_hits = sum(v for k, v in hits.items() if k != "_raw" and isinstance(v, (int, float)))
    return {
        "hits": hits,
        "_summary": f"命中 {len(hits)} 个风险维度, 共 {int(total_hits)} 条" if total_hits > 0 else "未命中风险维度",
        "total_hit_dimensions": len([k for k in hits if k != "_raw"]),
    }


# ── 阶段 D ──
def _stage_d_risk_drill(locked_name: str, scan_hits: dict) -> dict:
    """按风险分诊命中结果下钻"""
    result = {}

    # P0: 主体存续（一票否决）
    p0_tools = [
        ("注销记录", "get_cancellation_record_info"),
        ("清算信息", "get_liquidation_info"),
        ("破产重整", "get_bankruptcy_reorganization"),
    ]
    for label, tool in p0_tools:
        r = _safe_call(tool, locked_name, server="risk")
        result[label] = {"_count": _count_items(r), "_summary": f"{_count_items(r)}条"}

    # P1: 核心偿付
    p1_tools = [
        ("被执行人", "get_judgment_debtor_info"),
        ("失信信息", "get_dishonest_info"),
        ("终本案件", "get_terminated_cases"),
        ("限高消费", "get_high_consumption_restriction"),
        ("严重违法", "get_serious_violation"),
        ("经营异常", "get_business_exception"),
    ]
    for label, tool in p1_tools:
        # 按需下钻：scan 命中才调，但核心 6 项总是调
        r = _safe_call(tool, locked_name, server="risk")
        result[label] = {"_count": _count_items(r), "_summary": f"{_count_items(r)}条"}

    # P2: 资产可执行性
    p2_tools = [
        ("股权冻结", "get_equity_freeze"),
        ("动产抵押", "get_chattel_mortgage_info"),
        ("土地抵押", "get_land_mortgage_info"),
        ("股权出质", "get_equity_pledge_info"),
        ("股票质押", "get_stock_pledge_info"),
        ("司法拍卖", "get_judicial_auction"),
        ("担保信息", "get_guarantee_info"),
        ("财产悬赏", "get_property_asset_announcement"),
    ]
    for label, tool in p2_tools:
        r = _safe_call(tool, locked_name, server="risk")
        c = _count_items(r)
        if c > 0:
            result[label] = {"_count": c, "_summary": f"{c}条 ⚠️"}
        # 0 条不记录以节省摘要空间

    # P3: 税务合规
    p3_tools = [
        ("欠税公告", "get_tax_arrears_notice"),
        ("税收违法", "get_tax_violation"),
        ("税务异常", "get_tax_abnormal"),
        ("行政处罚", "get_administrative_penalty"),
        ("环保处罚", "get_environmental_penalty"),
        ("惩戒名单", "get_disciplinary_list"),
        ("违约信息", "get_default_info"),
    ]
    for label, tool in p3_tools:
        r = _safe_call(tool, locked_name, server="risk")
        c = _count_items(r)
        if c > 0:
            result[label] = {"_count": c, "_summary": f"{c}条"}

    # P4: 程序拖延
    p4_tools = [
        ("裁判文书", "get_judicial_documents"),
        ("法院立案", "get_case_filing_info"),
        ("法院公告", "get_court_notice"),
        ("限制出境", "get_exit_restriction"),
    ]
    for label, tool in p4_tools:
        r = _safe_call(tool, locked_name, server="risk")
        c = _count_items(r)
        if c > 0:
            result[label] = {"_count": c, "_summary": f"{c}条"}

    return result


# ── 阶段 E ──
def _stage_e_executive_risk(locked_name: str, controller: dict, personnel: dict) -> dict:
    """核心人员个人风险"""
    result = {}

    # 从 B 阶段提取人员名单
    people = []
    if controller:
        ctrl_items = controller.get("_items", [])
        for c in ctrl_items:
            pname = c.get("name", c.get("controllerName", c.get("personName", "")))
            if pname:
                people.append({"name": pname, "role": "实际控制人"})

    if personnel:
        p_items = personnel.get("_items", [])
        for p in p_items:
            pname = p.get("name", p.get("personName", ""))
            role = p.get("position", p.get("role", "高管"))
            if pname:
                people.append({"name": pname, "role": role})

    # 对每个人做风险扫描 + 下钻
    for person in people[:3]:  # 最多3人
        pname = person["name"]
        person_result = {"role": person["role"], "scans": {}}

        # 风险分诊
        scan_r = _safe_call(
            "get_executive_risk_scan", pname, server="executive",
            extra_args={"personName": pname}
        )
        person_result["scans"]["风险分诊"] = {"_count": _count_items(scan_r)}

        # 核心下钻
        drill_tools = [
            ("失信", "get_executive_dishonest"),
            ("被执行", "get_executive_judgment_debtor"),
            ("限高", "get_executive_high_consumption_ban"),
            ("股权冻结", "get_executive_equity_freeze"),
            ("股权出质", "get_executive_equity_pledge"),
        ]
        for label, tool in drill_tools:
            r = _safe_call(tool, pname, server="executive", extra_args={"personName": pname})
            c = _count_items(r)
            if c > 0:
                person_result["scans"][label] = {"_count": c}

        result[pname] = person_result

    return result


# ── 阶段 F ──
def _stage_f_business_scale(locked_name: str) -> dict:
    """经营规模 + 侵权渠道"""
    result = {}

    # IPR 相关
    ipr_tools = [
        ("商标资产", "get_trademark_info", "ipr"),
        ("线上店铺", "get_online_store", "ipr"),
        ("APP信息", "get_app_info", "ipr"),
        ("小程序", "get_mini_program", "ipr"),
        ("微信公众号", "get_wechat_official_account", "ipr"),
        ("抖音账号", "get_douyin_account", "ipr"),
    ]
    for label, tool, server in ipr_tools:
        r = _safe_call(tool, locked_name, server)
        c = _count_items(r)
        result[label] = {"_count": c, "_summary": f"{c}条"}

    # Operation 相关
    op_tools = [
        ("招投标", "get_bidding_info", "operation"),
        ("融资记录", "get_financing_records", "operation"),
        ("荣誉信息", "get_honor_info", "operation"),
        ("榜单排名", "get_ranking_list_info", "operation"),
        ("招聘信息", "get_recruitment_info", "operation"),
        ("信用评估", "get_credit_evaluation", "operation"),
        ("行政许可", "get_administrative_license", "operation"),
        ("资质证书", "get_qualifications", "operation"),
    ]
    for label, tool, server in op_tools:
        r = _safe_call(tool, locked_name, server)
        c = _count_items(r)
        result[label] = {"_count": c, "_summary": f"{c}条"}

    # 汇总
    total_channels = sum(
        result[k]["_count"]
        for k in result
        if k in ("线上店铺", "APP信息", "小程序", "微信公众号", "抖音账号")
    )
    result["_summary"] = f"商标{result.get('商标资产',{}).get('_count',0)}件, 线上渠道{total_channels}个, 招投标{result.get('招投标',{}).get('_count',0)}项"
    return result


# ── 阶段 G ──
def _stage_g_litigation_timeline(locked_name: str) -> dict:
    """诉讼时间预估"""
    result = {}

    r = _safe_call("get_judicial_case_search", locked_name, server="case",
                   extra_args={"case_type": "民事案件", "size": 20})
    result["被诉历史"] = {"_count": _count_items(r), "_summary": f"{_count_items(r)}件"}

    # 从 D 阶段已有数据复用的裁判文书/终本统计已在 D 完成
    # 这里只是独立的 legal-case 调用
    return result


# ── 阶段 H ──
def _stage_h_calc_metrics(
    stage_b: dict, stage_c: dict, stage_d: dict,
    stage_e: dict, stage_f: dict, stage_g: dict
) -> dict:
    """计算最终指标：回款概率、判赔调整系数、时间延量"""
    metrics = {
        "recovery_probability": 50.0,
        "damages_adjustment": "基准",
        "time_extra_months": 0,
        "red_flags": [],
        "green_flags": [],
        "details": {},
    }

    # ── 回款概率基础 50% ──
    prob = 50.0

    # P0 一票否决
    for label in ("注销记录", "清算信息", "破产重整"):
        if stage_d.get(label, {}).get("_count", 0) > 0:
            prob = 0.0
            metrics["red_flags"].append(f"主体存续异常({label})")

    if prob == 0.0:
        metrics["recovery_probability"] = 0.0
        metrics["damages_adjustment"] = "一票否决-主体存续异常"
        return metrics

    # P1 失信/被执行/限高
    dishonest_count = stage_d.get("失信信息", {}).get("_count", 0)
    judgment_debtor_count = stage_d.get("被执行人", {}).get("_count", 0)
    high_consume_count = stage_d.get("限高消费", {}).get("_count", 0)
    terminated_count = stage_d.get("终本案件", {}).get("_count", 0)

    if dishonest_count > 0:
        prob *= 0.1
        metrics["red_flags"].append(f"失信被执行({dishonest_count}条)")
    if judgment_debtor_count >= 3:
        prob *= 0.3
        metrics["red_flags"].append(f"被执行≥3次({judgment_debtor_count}条)")
    if terminated_count >= 3:
        prob *= 0.4
        metrics["red_flags"].append(f"终本≥3次({terminated_count}条)")
    if dishonest_count > 0 and judgment_debtor_count > 0 and high_consume_count > 0:
        prob *= 0.05
        metrics["red_flags"].append("失信+被执行+限高三项全有")

    # 严重违法 / 经营异常
    if stage_d.get("严重违法", {}).get("_count", 0) > 0:
        prob *= 0.7
        metrics["red_flags"].append("严重违法")
    if stage_d.get("经营异常", {}).get("_count", 0) > 0:
        prob *= 0.8
        metrics["red_flags"].append("经营异常")

    # P2 资产冻结/质押
    frozen_count = 0
    for label in ("股权冻结", "动产抵押", "土地抵押", "股权出质", "股票质押"):
        c = stage_d.get(label, {}).get("_count", 0)
        if c > 0:
            frozen_count += 1
    if frozen_count >= 3:
        prob *= 0.2
        metrics["red_flags"].append(f"核心资产大面积冻结/质押({frozen_count}类)")

    # 上市公司 → +30%
    listing = stage_b.get("上市信息", {})
    if listing.get("_count", 0) > 0:
        prob *= 1.3
        metrics["green_flags"].append("上市公司")

    # 营收 > 阈值（从财务数据推断）
    fin_data = stage_b.get("财务数据", {})
    if fin_data.get("_items"):
        # 有财务数据 = 企业经营规范 → 适当上调
        prob *= 1.2
        metrics["green_flags"].append("有公开财务数据")

    # 实际控制人失信
    for pname, pdata in stage_e.items():
        if pdata.get("scans", {}).get("失信", {}).get("_count", 0) > 0:
            prob *= 0.6
            metrics["red_flags"].append(f"实控人{pname}失信")
        # 实控人有可执行资产推断（有股权/投资 → 有财产）
        has_assets = pdata.get("scans", {}).get("股权冻结", {}).get("_count", 0) > 0
        if not has_assets and pdata.get("scans", {}).get("股权出质", {}).get("_count", 0) > 0:
            has_assets = True
        if has_assets:
            prob *= 1.15
            metrics["green_flags"].append(f"实控人{pname}有可追溯资产")

    # 上限截断
    prob = max(min(prob, 100), 0)
    metrics["recovery_probability"] = round(prob, 1)

    # ── 判赔调整系数 ──
    channel_count = sum(
        stage_f.get(k, {}).get("_count", 0)
        for k in ("线上店铺", "APP信息", "小程序", "微信公众号", "抖音账号")
    )
    trademark_count = stage_f.get("商标资产", {}).get("_count", 0)

    if channel_count >= 5:
        metrics["damages_adjustment"] = "大规模侵权渠道 → 上调"
    elif trademark_count >= 20:
        metrics["damages_adjustment"] = "成熟品牌 → 上调"
    elif channel_count == 0 and trademark_count == 0:
        metrics["damages_adjustment"] = "小微型 → 下调"
    else:
        metrics["damages_adjustment"] = "中等规模 → 基准"

    # ── 时间延长量 ──
    case_count = stage_g.get("被诉历史", {}).get("_count", 0)
    terminated_c = stage_d.get("终本案件", {}).get("_count", 0)
    extra_months = 0
    if case_count > 0:
        extra_months += min(case_count, 10)  # 每件被诉 +1月，封顶10月
    if terminated_c > 0:
        extra_months += terminated_c * 3  # 每个终本 +3月
    if judgment_debtor_count > 0:
        extra_months += judgment_debtor_count * 2
    metrics["time_extra_months"] = extra_months

    return metrics


# ============================================================
# 自然人被告查询
# ============================================================

def _search_individual(d: dict) -> Dict:
    """自然人被告：执行→失信→限高→控制企业→股权冻结→历史投资"""
    pname = d.get("name", "").strip()
    result = {
        "_summary": "",
        "stages": {},
        "metrics": {
            "recovery_probability": 50.0,
            "damages_p50": None,
            "time_extra_months": 0,
            "red_flags": [],
            "green_flags": [],
        }
    }
    summary_parts = [f"👤 被告类型: 自然人 ({pname})"]

    # 风险扫描
    scan_r = _safe_call("get_executive_risk_scan", pname, server="executive",
                        extra_args={"personName": pname})
    result["stages"]["E_人员风险扫描"] = {"_count": _count_items(scan_r)}
    fail = _rpc_failed(scan_r)
    if fail:
        result["status"] = "auth_error"
        result["error"] = fail
        result["metrics"] = _auth_failed_metrics(fail)
        result["_summary"] = f"❌ {fail}"
        return result

    # 失信 / 被执行 / 限高
    dishonest = _safe_call("get_executive_dishonest", pname, server="executive",
                           extra_args={"personName": pname})
    if _count_items(dishonest) > 0:
        result["metrics"]["red_flags"].append(f"失信被执行人")
        result["metrics"]["recovery_probability"] *= 0.1

    debtor = _safe_call("get_executive_judgment_debtor", pname, server="executive",
                        extra_args={"personName": pname})
    jd_count = _count_items(debtor)
    if jd_count >= 3:
        result["metrics"]["red_flags"].append(f"被执行人≥{jd_count}次")
        result["metrics"]["recovery_probability"] *= 0.3

    high_consume = _safe_call("get_executive_high_consumption_ban", pname, server="executive",
                               extra_args={"personName": pname})
    if _count_items(high_consume) > 0:
        result["metrics"]["red_flags"].append("限高消费")

    result["stages"]["E_失信被执行限高"] = {
        "失信": _count_items(dishonest),
        "被执行": jd_count,
        "限高": _count_items(high_consume),
    }

    # 控制企业 → 推断资产
    controlled = _safe_call("get_executive_controlled_companies", pname, server="executive",
                            extra_args={"personName": pname})
    cc_count = _count_items(controlled)
    if cc_count > 0:
        result["metrics"]["green_flags"].append(f"控制{cc_count}家企业")
        result["metrics"]["recovery_probability"] *= 1.2

    # 股权冻结/质押
    eq_freeze = _safe_call("get_executive_equity_freeze", pname, server="executive",
                           extra_args={"personName": pname})
    if _count_items(eq_freeze) > 0:
        result["metrics"]["red_flags"].append("股权冻结")

    eq_pledge = _safe_call("get_executive_equity_pledge", pname, server="executive",
                           extra_args={"personName": pname})
    if _count_items(eq_pledge) > 0:
        result["metrics"]["red_flags"].append("股权质押")

    result["stages"]["E_资产状况"] = {
        "控制企业": cc_count,
        "股权冻结": _count_items(eq_freeze),
        "股权质押": _count_items(eq_pledge),
    }

    # 最终计算
    result["metrics"]["recovery_probability"] = round(
        max(min(result["metrics"]["recovery_probability"], 100), 0), 1
    )
    result["metrics"]["time_extra_months"] = jd_count * 2
    result["metrics"]["damages_adjustment"] = "自然人 → 上限下调" if cc_count == 0 else "有控制企业 → 基准"

    summary_parts.append(f"💰 回款概率: {result['metrics']['recovery_probability']:.0f}%")
    if result["metrics"]["red_flags"]:
        summary_parts.append(f"🚨 风险: {'; '.join(result['metrics']['red_flags'][:5])}")
    if result["metrics"]["green_flags"]:
        summary_parts.append(f"✅ 利好: {'; '.join(result['metrics']['green_flags'][:5])}")
    result["_summary"] = "\n".join(summary_parts)
    return result


# ============================================================
# 健康检查
# ============================================================

def check_qcc_connection() -> bool:
    """检查企查查 API 连通性"""
    if not _qcc_configured():
        return False
    try:
        r = _rpc_call("get_company_by_query", {"searchKey": "腾讯"}, server="company")
        return "error" not in r
    except Exception:
        return False


if __name__ == "__main__":
    # quick test
    r = search_for_financial_qcc_full("腾讯科技")
    print(json.dumps({k: v for k, v in r.items() if k != "stages"}, ensure_ascii=False, indent=2))
