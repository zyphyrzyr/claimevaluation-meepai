"""
法院与地域推导：把「被告工商所在地」这类粗糙字符串，换算成北大法宝
`search_case` 能吃的 `courthouse_province` / `courthouse_name` 精确值。

为什么必须单独成模块（2026-09-19）：
    《诉算类案检索规则 v1.1》的顺位③④需要「本省高级人民法院全称」「上一级法院全称」
    「本院全称」。而 `search_case` **没有「法院层级」这样的抽象参数**——只能传法院全称。
    换句话说，规则里每一个顺位落地前，都得先把「省份 / 法院」这两个概念实体化。

    更关键的是：本系统当前**没有采集管辖法院**（`CaseContext` 里没有任何法院字段，
    `legal_basis` 只有描述性文本）。所以顺位④在绝大多数案件里是不可用的——这里是全文
    唯一决定「能不能做」的地方，必须一处裁决、显式留痕，不允许在别处悄悄猜一个法院出来。

三条硬规则：
    1. 取不到就返回 None 并记录原因 ——「没查过」和「查过但没命中」必须可区分。
    2. 不确定时不猜。直辖市的中院分号、知识产权法院的上诉路径都无法靠名字推导，
       宁可返回 None 也不产出错误法院名（错误的法院名会让整个顺位静默检索到无关案例）。
    3. 每条推导都带 `basis`（推导依据），供报告与追溯展示。
"""

import re
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# 省份数据
# ============================================================

# courthouse_province 期望的取值形态就是省/市/自治区的**全称**
PROVINCE_FULL: Tuple[str, ...] = (
    "北京市", "天津市", "上海市", "重庆市",
    "河北省", "山西省", "辽宁省", "吉林省", "黑龙江省",
    "江苏省", "浙江省", "安徽省", "福建省", "江西省", "山东省",
    "河南省", "湖北省", "湖南省", "广东省", "海南省",
    "四川省", "贵州省", "云南省", "陕西省", "甘肃省", "青海省",
    "内蒙古自治区", "广西壮族自治区", "西藏自治区",
    "宁夏回族自治区", "新疆维吾尔自治区",
)

# 简称 → 全称（用于在案情文本 / 工商地址里识别省份）
PROVINCE_ALIASES: Dict[str, str] = {
    "内蒙古": "内蒙古自治区",
    "广西": "广西壮族自治区",
    "西藏": "西藏自治区",
    "宁夏": "宁夏回族自治区",
    "新疆": "新疆维吾尔自治区",
    "北京": "北京市", "天津": "天津市", "上海": "上海市", "重庆": "重庆市",
}
for _p in PROVINCE_FULL:
    if _p.endswith("省"):
        PROVINCE_ALIASES[_p[:-1]] = _p

# 无法归属到中国内地省份体系的两个特别行政区不参与推导
_NON_MAINLAND = ("香港特别行政区", "澳门特别行政区", "台湾省")

# 省会 + 计划单列市 → 省份。仅在输入里**没有**省份前缀时作为次优兜底，
# 用法与原因都写在 `province_from_text` 的 basis 里，绝不冒充精确数据。
CITY_TO_PROVINCE: Dict[str, str] = {
    "石家庄": "河北省", "太原": "山西省", "沈阳": "辽宁省", "大连": "辽宁省",
    "长春": "吉林省", "哈尔滨": "黑龙江省", "南京": "江苏省", "苏州": "江苏省",
    "杭州": "浙江省", "宁波": "浙江省", "合肥": "安徽省", "福州": "福建省",
    "厦门": "福建省", "南昌": "江西省", "济南": "山东省", "青岛": "山东省",
    "郑州": "河南省", "武汉": "湖北省", "长沙": "湖南省", "广州": "广东省",
    "深圳": "广东省", "珠海": "广东省", "东莞": "广东省", "佛山": "广东省",
    "海口": "海南省", "三亚": "海南省", "成都": "四川省", "贵阳": "贵州省",
    "昆明": "云南省", "西安": "陕西省", "兰州": "甘肃省", "西宁": "青海省",
    "呼和浩特": "内蒙古自治区", "南宁": "广西壮族自治区", "拉萨": "西藏自治区",
    "银川": "宁夏回族自治区", "乌鲁木齐": "新疆维吾尔自治区",
}

_COURT_LEVELS = ("最高人民法院", "高级人民法院", "中级人民法院", "知识产权法院",
                 "知识产权法庭", "基层人民法院", "人民法院")


def _clean(text: str) -> str:
    return (text or "").strip()


# ============================================================
# 省份识别
# ============================================================

def _match_provinces(text: str) -> List[str]:
    """找出文本里出现的省份（含简称），按出现位置去重保序。

    只认「省/市/自治区」这些有行政含义的完整词，不做子串瞎猜——
    把一段无关文字里的省名碎片当成省份，会把整个检索引向无关辖区。
    """
    found: List[Tuple[int, str]] = []
    candidates = {**{p: p for p in PROVINCE_FULL}, **PROVINCE_ALIASES}
    # 长词优先，避免「广东」先吃掉「广东省」里的位置判定
    for alias in sorted(candidates, key=len, reverse=True):
        full = candidates[alias]
        for m in re.finditer(re.escape(alias), text):
            found.append((m.start(), full))
    seen: List[str] = []
    for _, name in sorted(found):
        if name not in seen:
            seen.append(name)
    return seen


def province_from_text(text: str) -> Tuple[Optional[str], str]:
    """从任意文本里识别省份。

    返回 (省全称 或 None, 依据说明)。出现多个省份时返回 None —— 无法确定管辖地时
    宁可跳过顺位③，也不拿一个可能错的省份去过滤（错误的过滤会让「检索到 3 条」
    看起来像正常结果）。
    """
    text = _clean(text)
    if not text:
        return None, "输入为空"

    for blocked in _NON_MAINLAND:
        if blocked in text:
            return None, f"涉及{blocked}，不在内地省份体系内"

    provinces = _match_provinces(text)
    if len(provinces) == 1:
        matched = provinces[0]
        note = ("文本中的省份简称" if matched not in text
                else "文本中的省份全称")
        return matched, f"{note}「{matched}」"
    if len(provinces) > 1:
        return None, f"文本出现多个省份（{'、'.join(provinces)}），省份不确定"

    hits = [c for c in CITY_TO_PROVINCE if c in text]
    if len(hits) == 1:
        return CITY_TO_PROVINCE[hits[0]], f"由城市「{hits[0]}」推导（无省份前缀时的兜底）"
    if len(hits) > 1:
        return None, f"文本出现多个已知城市（{'、'.join(hits)}），省份不确定"
    return None, "文本中未识别到省份或已知城市"


def province_from_sources(*candidates: str) -> Tuple[Optional[str], str]:
    """按优先级依次尝试多个来源，第一个成功的胜出。

    约定顺序：被告工商登记的「所属地区」> 被告信息里的 location_hint >
    案情描述里的地域线索。工商登记是登记事实，优先级最高。
    """
    tried = []
    for c in candidates:
        prov, basis = province_from_text(c)
        text = _clean(c)
        if prov:
            return prov, f"{basis}（来源：{text[:30]}）"
        if text:
            tried.append(f"{text[:20]}→{basis}")
    if tried:
        return None, "；".join(tried)
    return None, "没有任何地域线索"


# ============================================================
# 法院全称换算
# ============================================================

def province_to_high_court(province: str) -> Optional[str]:
    """省全称 → 该省高级人民法院全称（规则文档顺位③-B）。"""
    p = _clean(province)
    if not p or p in _NON_MAINLAND:
        return None
    if p not in PROVINCE_FULL:
        return None
    return f"{p}高级人民法院"


def parse_court_name(name: str) -> Tuple[Optional[str], Optional[str]]:
    """从法院全称里解析出 (法院层级, 省份)。无法识别返回 (None, None)。"""
    n = _clean(name)
    if not n:
        return None, None
    level = next((lv for lv in _COURT_LEVELS if lv in n), None)
    provinces = _match_provinces(n)
    return level, (provinces[0] if provinces else None)


def derive_upper_court(court_name: str) -> Tuple[Optional[str], str]:
    """推导上一级法院全称（规则文档顺位④-A）。

    能做：县/区级法院 → 所属地级市中级人民法院；中级人民法院 → 省高级人民法院；
          高级人民法院 → 最高人民法院。
    不做：直辖市基层法院的上诉中院分号（如北京市第一/第二中级人民法院无法靠名字区分）、
          知识产权法院/法庭的上诉路径。这些一律返回 None 并写明原因——
          猜错了会静默检索出完全无关的案例，比不做更糟。
    """
    n = _clean(court_name)
    if not n:
        return None, "未提供管辖法院"
    level, province = parse_court_name(n)
    if level is None:
        return None, f"无法识别法院层级：{n}"
    if not province:
        province, _basis = province_from_text(n)

    if level == "最高人民法院":
        return None, "最高人民法院已是最高层级"
    if level == "高级人民法院":
        return "最高人民法院", "高级人民法院的上诉审为最高人民法院"
    if level in ("知识产权法院", "知识产权法庭"):
        return None, f"知识产权法院/法庭的上诉路径不适用通用层级推导：{n}"
    if level == "中级人民法院":
        high = province_to_high_court(province)
        if high:
            return high, "中级人民法院的上诉审为所属省高级人民法院"
        return None, f"无法从名称确定所属省份的中级法院：{n}"

    # 基层：需要「市 + 区/县」结构才能推出对应的市中级人民法院
    if province in ("北京市", "天津市", "上海市", "重庆市"):
        return None, f"直辖市基层法院的上诉中院分号无法由名称推导：{n}"
    m = re.match(r"^(.*?市)(.+?)(?:人民)?法院$", n)
    if not m:
        return None, f"基层法院名称缺少可识别的地市前缀：{n}"
    return f"{m.group(1)}中级人民法院", "基层人民法院的上诉审为所在地市中级人民法院"


def hint_from_case(region: str = "", location_hint: str = "",
                   case_description: str = "") -> Dict:
    """把系统里现有的地域线索汇总成检索用的 hint。

    province 的取值来源按可靠性排序（用户 2026-09-19 选定：被告工商所在地兜底）：
      1. 被告工商登记的「所属地区」——登记事实，最可靠；
      2. 被告信息里的 location_hint；
      3. 案情描述里的地域线索。
    court 恒为 None：系统当前不采集管辖法院，顺位④跳过并写明原因，不猜。
    """
    province, basis = province_from_sources(region, location_hint, case_description)
    return {"province": province, "province_basis": basis, "court": None}


def resolve_court_targets(province: Optional[str] = None,
                          court: Optional[str] = None) -> Dict[str, Optional[str]]:
    """一次性给出顺位③④需要的全部法院参数。

    返回 dict，取不到的项为 None；调用方据此决定哪些顺位可最多跳到第几档，
    而不是拿到空字符串去当参数传（空字符串进接口会变成「不过滤」，更危险）。
    """
    return {
        "province": province or None,
        "high_court": province_to_high_court(province) if province else None,
        "self_court": _clean(court) or None,
        "upper_court": derive_upper_court(court)[0] if court else None,
    }
