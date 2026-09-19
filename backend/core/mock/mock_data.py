"""
Mock 数据（v4 二维模型）

三案由各有独立数据：商标侵权 / 著作权侵权 / 不正当竞争。
早期实现只有一套商标数据、案由参数被直接忽略，导致：
  - 三个案由跑出完全相同的分数（要钱一律同分、要名一律同分）
  - 选「著作权侵权」做独立演练，庭审内容却是「第25类服装上的注册商标」（演示事故）
本文件改为按案由分派，且对未知案由显式报错，不静默回落商标。

函数签名与真实模块一致，USE_MOCK=True 时全链路离线可跑。
"""

from typing import Any, Dict, List

from ..config import (CAUSE_TRADEMARK, CAUSE_COPYRIGHT,
                      CAUSE_UNFAIR_COMPETITION, SUPPORTED_CAUSE_TYPES)


def _require_supported(cause_type: str) -> str:
    """
    未知案由显式报错，不静默回落商标清单。

    静默回落的危害：用户填「专利侵权」，系统拿商标清单去核验，
    输出一份看起来正常、实际完全跑偏的评估报告，且界面无任何提示。
    """
    if cause_type not in SUPPORTED_CAUSE_TYPES:
        raise ValueError(
            f"不支持的案由：{cause_type}；当前支持 {SUPPORTED_CAUSE_TYPES}")
    return cause_type


# ============================================================
# 证据矩阵（四类：权利基础 / 侵权认定 / 损害赔偿 / 取证技术规范）
# ============================================================

def _matrix_trademark():
    return [
        {"id": "权利基础证据-1", "category": "权利基础证据", "item": "商标注册证/续展证明",
         "basis": "《商标法》第3条", "element": "权利有效性", "status": "sufficient",
         "reason": "已提供注册证，注册号/有效期/注册人信息完整"},
        {"id": "权利基础证据-2", "category": "权利基础证据", "item": "近三年商标使用证据（防撤三）",
         "basis": "《商标法》第49条", "element": "防撤三", "status": "partial",
         "reason": "有销售记录但缺少连续三年的完整使用链"},
        {"id": "权利基础证据-3", "category": "权利基础证据", "item": "商标知名度证据（可主张驰名跨类保护）",
         "basis": "《商标法》第13、14条", "element": "保护范围", "status": "missing",
         "reason": "未提供广告宣传、市场排名等知名度材料"},
        {"id": "侵权认定证据-1", "category": "侵权认定证据", "item": "侵权商品/服务截图、页面快照",
         "basis": "《商标法》第57条", "element": "商标性使用", "status": "sufficient",
         "reason": "已提供侵权店铺页面截图，标识使用清晰"},
        {"id": "侵权认定证据-2", "category": "侵权认定证据", "item": "购买取证（侵权实物+交易记录）",
         "basis": "《商标法》第57条", "element": "混淆可能性", "status": "missing",
         "reason": "未进行购买取证"},
        {"id": "侵权认定证据-3", "category": "侵权认定证据", "item": "公证文书（侵权事实固定）",
         "basis": "《民事诉讼法》第72条", "element": "证据效力", "status": "missing",
         "reason": "截图未公证，证据效力存疑"},
        {"id": "损害赔偿证据-1", "category": "损害赔偿证据", "item": "被告获利/原告损失/许可费依据",
         "basis": "《商标法》第63条", "element": "判赔计算", "status": "partial",
         "reason": "有许可合同但费率与本案品类不完全对应"},
        {"id": "损害赔偿证据-2", "category": "损害赔偿证据", "item": "侵权规模证据（销量/评价数/店铺数）",
         "basis": "《商标法》第63条", "element": "判赔计算", "status": "sufficient",
         "reason": "页面显示月销 3000+，评价数 1.2 万"},
        {"id": "取证技术规范-1", "category": "取证技术规范", "item": "可信时间戳/区块链存证/公证取证",
         "basis": "《电子签名法》第8条", "element": "证据三性", "status": "partial",
         "reason": "部分截图做了时间戳存证，覆盖不全"},
    ]


def _matrix_copyright():
    return [
        {"id": "权利基础证据-1", "category": "权利基础证据", "item": "作品登记证书/创作底稿",
         "basis": "《著作权法》第12条", "element": "权利归属", "status": "sufficient",
         "reason": "已提供作品登记证书及原始分层源文件，创作时间与署名清晰"},
        {"id": "权利基础证据-2", "category": "权利基础证据", "item": "职务作品/委托作品权属约定",
         "basis": "《著作权法》第18、19条", "element": "权属链条", "status": "partial",
         "reason": "有劳动合同但著作权归属条款表述笼统，未明确约定财产权归属"},
        {"id": "权利基础证据-3", "category": "权利基础证据", "item": "作品独创性说明（非公有领域素材）",
         "basis": "《著作权法实施条例》第2条", "element": "独创性", "status": "missing",
         "reason": "未说明涉案图形与公有领域素材的差异点"},
        {"id": "侵权认定证据-1", "category": "侵权认定证据", "item": "被诉作品与权利作品比对材料",
         "basis": "《著作权法》第52条", "element": "实质性相似", "status": "sufficient",
         "reason": "已作并列比对，构图、配色、元素排布高度一致"},
        {"id": "侵权认定证据-2", "category": "侵权认定证据", "item": "被告接触可能性证据（发布时间/传播路径）",
         "basis": "《著作权法》第52条", "element": "接触", "status": "partial",
         "reason": "权利作品先于被诉作品公开发表，但被告下载/访问记录未固定"},
        {"id": "侵权认定证据-3", "category": "侵权认定证据", "item": "公证文书（网页/实物固定）",
         "basis": "《民事诉讼法》第72条", "element": "证据效力", "status": "missing",
         "reason": "网页截图未公证"},
        {"id": "损害赔偿证据-1", "category": "损害赔偿证据", "item": "许可使用费/稿酬标准依据",
         "basis": "《著作权法》第54条", "element": "判赔计算", "status": "partial",
         "reason": "有同类作品授权合同，但授权范围与本案使用场景不同"},
        {"id": "损害赔偿证据-2", "category": "损害赔偿证据", "item": "侵权传播规模证据（下载量/转发量）",
         "basis": "《著作权法》第54条", "element": "判赔计算", "status": "sufficient",
         "reason": "被诉图片用于商品主图，店铺月销 2000+，传播范围可量化"},
        {"id": "取证技术规范-1", "category": "取证技术规范", "item": "可信时间戳/区块链存证",
         "basis": "《电子签名法》第8条", "element": "证据三性", "status": "partial",
         "reason": "作品原图已做时间戳，被诉页面未做"},
    ]


def _matrix_unfair_competition():
    return [
        {"id": "权利基础证据-1", "category": "权利基础证据", "item": "有一定影响的商品名称/包装装潢",
         "basis": "《反不正当竞争法》第6条第1项", "element": "权益基础", "status": "sufficient",
         "reason": "已提供持续使用五年以上的包装装潢实物及销售记录"},
        {"id": "权利基础证据-2", "category": "权利基础证据", "item": "知名度证据（销售区域/时长/宣传投入）",
         "basis": "《反不正当竞争法》第6条", "element": "有一定影响", "status": "partial",
         "reason": "有区域销售数据与部分广告投放凭证，缺少行业排名或获奖材料"},
        {"id": "权利基础证据-3", "category": "权利基础证据", "item": "在先使用证明（早于被告使用时间）",
         "basis": "《反不正当竞争法》第6条", "element": "在先性", "status": "missing",
         "reason": "被告主张其在先使用，我方缺少早期上架时间戳"},
        {"id": "侵权认定证据-1", "category": "侵权认定证据", "item": "被诉商品与权利商品装潢比对",
         "basis": "《反不正当竞争法》第6条第1项", "element": "混淆行为", "status": "sufficient",
         "reason": "整体视觉近似，主要识别部分高度一致"},
        {"id": "侵权认定证据-2", "category": "侵权认定证据", "item": "实际混淆证据（消费者误认/投诉记录）",
         "basis": "《反不正当竞争法》第6条", "element": "混淆后果", "status": "missing",
         "reason": "未收集到消费者实际混淆的例证"},
        {"id": "侵权认定证据-3", "category": "侵权认定证据", "item": "公证购买取证",
         "basis": "《民事诉讼法》第72条", "element": "证据效力", "status": "missing",
         "reason": "仅有线上页面截图，未做公证购买"},
        {"id": "损害赔偿证据-1", "category": "损害赔偿证据", "item": "被告获利/原告损失依据",
         "basis": "《反不正当竞争法》第17条", "element": "判赔计算", "status": "partial",
         "reason": "可推算被告销量区间，但成本结构不明，获利额难以精确"},
        {"id": "损害赔偿证据-2", "category": "损害赔偿证据", "item": "侵权规模证据（销量/店铺数/持续时间）",
         "basis": "《反不正当竞争法》第17条", "element": "判赔计算", "status": "sufficient",
         "reason": "被告在 3 个平台开设店铺，侵权持续 14 个月"},
        {"id": "取证技术规范-1", "category": "取证技术规范", "item": "可信时间戳/公证取证",
         "basis": "《电子签名法》第8条", "element": "证据三性", "status": "partial",
         "reason": "部分页面已存证，覆盖不全"},
    ]


# ============================================================
# 各案由的评估节点 mock
# ============================================================

def _eval_trademark():
    return {
        "rights": {
            "score": 82,
            "analysis": "商标合法注册且在有效期内，连续三年使用证据基本充分，核定商品覆盖侵权商品类别，权利基础较为稳固。",
            "strengths": ["注册证齐全，权利状态稳定", "有持续使用记录，撤三风险低", "核定范围覆盖侵权商品"],
            "risks": ["知名度证据不足，难以主张驰名跨类保护"],
            "red_light": False,
        },
        "infringement": {
            "score": 76,
            "elements": [
                {"name": "商标性使用", "status": "满足", "analysis": "被告在商品标题和详情页突出使用涉案标识"},
                {"name": "商品/服务相同或类似", "status": "满足", "analysis": "与核定使用商品属同类商品"},
                {"name": "商标相同或近似", "status": "满足", "analysis": "标识在字形、读音上高度近似"},
                {"name": "混淆可能性", "status": "存疑", "analysis": "价格差异较大，需结合购买取证进一步固定"},
                {"name": "是否正当使用", "status": "存疑", "analysis": "被告可能主张描述性使用，需庭审检验"},
            ],
            "analysis": "侵权构成要件总体成立，混淆可能性与正当使用抗辩是主要争点，建议通过购买取证和公证加固。",
        },
        "procedure": {
            "score": 85,
            "risks": [
                {"item": "诉讼时效", "level": "low", "detail": "侵权行为持续中且在 3 年时效内"},
                {"item": "管辖与仲裁", "level": "none", "detail": "无仲裁协议，可在被告住所地或侵权地法院起诉"},
                {"item": "主体适格", "level": "low", "detail": "原告为注册商标专用权人，被告主体明确"},
                {"item": "前置程序", "level": "none", "detail": "商标侵权无行政前置要求"},
            ],
            "analysis": "程序层面无明显障碍，建议优先选择侵权结果发生地法院以获得管辖便利。",
        },
        "damages": {
            "score": 68,
            "p10": 8, "p50": 25, "p90": 60,
            "return_multiple": 2.1,
            "scale_support": "medium",
            "analysis": "参考同类案件判赔区间，P50 约 25 万元，覆盖预估成本约 2 倍；侵权规模证据可支撑中等偏上判赔，若补齐被告获利证据可进一步上探。",
        },
        "precedent": {
            "score": 45,
            "first_case_index": 30,
            "influence_level": "个案级",
            "analysis": "同类商标侵权判例较多，首案潜力有限；案情事实常见，规则明晰价值一般，主要价值在于个案维权本身。",
        },
        "completeness": 55.6,
        "extra_evidence": [
            {"name": "被告同类侵权历史", "value": "案情提及被告曾被投诉，可用于证明主观恶意、争取惩罚性赔偿"}
        ],
    }


def _eval_copyright():
    return {
        "rights": {
            "score": 74,
            "analysis": "作品登记证书与创作底稿齐备，独创性可初步认定；但职务作品权属条款表述笼统，原告是否单独享有诉权存在解释空间，是本案权利基础的主要不确定点。",
            "strengths": ["有作品登记证书，权利推定成立", "保留分层源文件，创作过程可回溯", "发表时间早于被诉作品"],
            "risks": ["劳动合同的著作权归属条款未明确约定财产权", "未说明与公有领域素材的差异，独创性可能被攻击"],
            "red_light": False,
        },
        "infringement": {
            "score": 71,
            "elements": [
                {"name": "接触", "status": "存疑", "analysis": "权利作品已公开发表，被告有接触可能，但缺少下载或访问记录"},
                {"name": "实质性相似", "status": "满足", "analysis": "构图、配色、元素排布高度一致，超出巧合范围"},
                {"name": "是否落入合理使用", "status": "存疑", "analysis": "被告可能主张借鉴公有领域元素，需界定独创性部分"},
            ],
            "analysis": "实质性相似较为明确，争点集中在接触要件的证明与独创性范围界定，建议补充接触路径证据并准备独创性说明。",
        },
        "procedure": {
            "score": 80,
            "risks": [
                {"item": "诉讼时效", "level": "low", "detail": "侵权行为持续中且在 3 年时效内"},
                {"item": "管辖与仲裁", "level": "none", "detail": "无仲裁协议，可由侵权行为地或被告住所地法院管辖"},
                {"item": "主体适格", "level": "medium", "detail": "职务作品情形下需明确原告是否单独享有诉权"},
                {"item": "前置程序", "level": "none", "detail": "著作权侵权无行政前置要求"},
            ],
            "analysis": "程序层面基本无碍，但权属链条需先行厘清，避免被告以原告不适格作程序抗辩。",
        },
        "damages": {
            "score": 55,
            "p10": 3, "p50": 10, "p90": 30,
            "return_multiple": 1.4,
            "scale_support": "low",
            "analysis": "单张图片类作品的司法判赔普遍偏低，P50 约 10 万元；虽有传播规模证据，但缺许可费对应依据，判赔上探空间有限，成本覆盖倍数勉强。",
        },
        "precedent": {
            "score": 58,
            "first_case_index": 45,
            "influence_level": "类案级",
            "analysis": "图形作品批量维权案件较多，但本案涉及职务作品权属认定，有一定规则明晰价值，可作为同类权属争议的参考。",
        },
        "completeness": 48.9,
        "extra_evidence": [
            {"name": "被诉图片的其他使用场景", "value": "核查是否用于其他店铺或平台，可扩大侵权规模认定"}
        ],
    }


def _eval_unfair_competition():
    return {
        "rights": {
            "score": 62,
            "analysis": "包装装潢持续使用五年以上、有稳定销售区域，可初步认定「有一定影响」；但缺少行业排名、获奖等知名度硬证据，且被告主张在先使用而我方缺少早期时间戳，权利基础存在实质争议。",
            "strengths": ["持续使用时间长，已形成识别性", "有区域销售数据与广告投放凭证"],
            "risks": ["知名度证据偏弱，「有一定影响」认定存疑", "缺少在先使用的早期时间戳", "未注册为商标，保护边界依赖个案认定"],
            "red_light": False,
        },
        "infringement": {
            "score": 66,
            "elements": [
                {"name": "混淆行为构成", "status": "满足", "analysis": "被诉装潢与权利装潢整体视觉近似，主要识别部分一致"},
                {"name": "商业诋毁构成（如适用）", "status": "不适用", "analysis": "本案未涉及商业诋毁主张"},
                {"name": "互联网专条构成（如适用）", "status": "存疑", "analysis": "若涉平台流量劫持，需另行固定技术证据"},
            ],
            "analysis": "装潢近似较为直观，但「有一定影响」是前提性要件，该要件一旦不成立则混淆行为无从认定，本案胜败几乎系于此点。",
        },
        "procedure": {
            "score": 72,
            "risks": [
                {"item": "诉讼时效", "level": "low", "detail": "侵权持续中且在 3 年时效内"},
                {"item": "管辖与仲裁", "level": "none", "detail": "无仲裁协议，可由侵权行为地法院管辖"},
                {"item": "主体适格", "level": "medium", "detail": "需证明原告为 impacted 经营者，存在竞争关系"},
                {"item": "前置程序", "level": "none", "detail": "不正当竞争无行政前置要求"},
            ],
            "analysis": "程序上可行，但需先证明双方存在竞争关系，且「有一定影响」的举证责任较重。",
        },
        "damages": {
            "score": 64,
            "p10": 10, "p50": 30, "p90": 80,
            "return_multiple": 1.8,
            "scale_support": "medium",
            "analysis": "被告在 3 个平台持续经营 14 个月，销量区间可推算，P50 约 30 万元；但成本结构不明导致获利额难以精确，判赔依赖酌定。",
        },
        "precedent": {
            "score": 66,
            "first_case_index": 55,
            "influence_level": "类案级",
            "analysis": "未注册装潢的保护边界在司法实践中标准不一，本案若能确认「有一定影响」，对同类未注册标识维权有较高参考价值。",
        },
        "completeness": 44.4,
        "extra_evidence": [
            {"name": "行业排名或获奖材料", "value": "补齐知名度证据，直接支撑「有一定影响」要件"}
        ],
    }


# ============================================================
# 模拟法庭 Mock：各案由独立的 7 轮庭审 + 法官归纳
# ============================================================

def _moot_trademark():
    return {
        "rounds": [
            {"step": 1, "step_name": "开庭陈述", "role": "plaintiff", "role_name": "原告代理律师",
             "content": "审判长、审判员：原告系第25类服装商品上第XXXXXXX号注册商标的专用权人，商标合法注册且在有效期内。被告未经许可，在其经营的电商平台店铺中，于商品标题、详情页显著位置使用与原告注册商标高度近似的标识，销售同类服装商品，月销量达3000余件。依据《商标法》第五十七条第（二）项，被告在同一种商品上使用与原告注册商标近似的标识、易导致混淆之行为，已侵犯原告注册商标专用权。原告前期评估显示权利基础稳固、侵权要件基本成立，请求法庭判令被告停止侵权，并依据《商标法》第六十三条赔偿原告经济损失及合理开支。"},
            {"step": 2, "step_name": "被告答辩", "role": "defendant", "role_name": "被告代理律师",
             "content": "审判长：被告对原告所述事实部分不予认可。第一，被告使用的标识与原告商标在字形、读音上存在明显差异，且商品售价仅原告正品的三分之一，消费群体并不重合，不会产生混淆，《商标法》第五十七条所称的近似与混淆要件均不成立；第二，被告销售的商品有合法进货渠道，供货商具备品牌授权材料，依据《商标法》第六十四条第二款，被告不应承担赔偿责任；第三，原告主张的赔偿数额缺乏依据，其未提供实际损失或被告获利的任何证据。恳请法庭驳回原告诉请。"},
            {"step": 3, "step_name": "举证质证-原告举证", "role": "plaintiff", "role_name": "原告代理律师",
             "content": "原告提交三组证据：证据一，商标注册证及续展证明，证明原告系注册商标专用权人，权利状态稳定；证据二，被告店铺页面截图及时间戳存证，证明被告在商品标题、详情页突出使用被诉标识，且页面显示月销3000+、评价1.2万条，侵权规模较大；证据三，原告正品与被诉商品的标识对比图，证明二者在整体观感上构成近似。需要说明的是，本案尚未进行公证购买取证，原告将在庭后补充。"},
            {"step": 3, "step_name": "举证质证-被告质证", "role": "defendant", "role_name": "被告代理律师",
             "content": "对证据一的真实性无异议，但关联性有异议：原告商标知名度有限，不应获得过宽保护。对证据二的证据效力有异议：截图未经公证，且部分未做时间戳，真实性无法确认，无法证明侵权行为的持续状态；月销数据存在刷单可能，不能作为侵权规模依据。对证据三的证明目的有异议：隔离比对下两标识差异明显，且价格、渠道的差异已足以区分商品来源。"},
            {"step": 4, "step_name": "法庭辩论-原告", "role": "plaintiff", "role_name": "原告代理律师",
             "content": "针对被告质证意见，原告认为：第一，被告未提交任何授权材料或进货凭证，合法来源抗辩不能成立；第二，商标近似应以整体观感和主要部分比对为准，被诉标识与原告商标在整体视觉上高度近似，足以造成混淆；第三，被告以低价销售恰恰会挤占原告的市场，并拉低原告品牌商誉，价格差异不能阻却混淆的认定。被告店铺销量巨大，即使按法定赔偿，也应从高认定赔偿数额。"},
            {"step": 4, "step_name": "法庭辩论-被告", "role": "defendant", "role_name": "被告代理律师",
             "content": "原告始终未能证明实际混淆的发生，混淆可能性仅是其单方推测。电商平台同类标识众多，相关公众施以一般注意力即可区分。关于赔偿，原告既无损失证据，也无被告获利证据，即使适用法定赔偿，也应考虑被告经营规模小、主观无恶意、商品单价低等因素酌定。被告愿在合理范围内协商和解。"},
            {"step": 5, "step_name": "法官归纳", "role": "judge", "role_name": "审判法官",
             "content": "本案争议焦点有三：一是被诉标识与原告商标是否构成近似、是否足以造成混淆，即《商标法》第五十七条的构成要件是否成立；二是被告的合法来源抗辩能否成立；三是赔偿数额如何确定，涉及《商标法》第六十三条的适用。关于焦点一，原告权利商标核定使用于同类商品，被诉标识使用于商品显著位置，整体比对构成近似，但原告未提交购买取证，混淆可能性的证明存在瑕疵。关于焦点二，被告仅口头主张有授权材料，未提交任何证据，抗辩不能成立。关于焦点三，原告举证以页面数据为主，判赔可参照侵权规模酌定。综合双方表现，本案原告胜诉基础存在但证据链条需补强，尤其缺少公证购买与被告获利证据。"},
        ],
        "judge": {
            "summary": "原告权利基础稳固，侵权认定方向正确，但证据固定存在明显瑕疵：未公证、未购买取证，被告合法来源抗辩虽不能成立，但其对证据效力的质疑有相当说服力。整体而言原告胜诉预期存在，然证据补强前判赔数额恐难足额支持。",
            "summary_structured": {
                "争议焦点": "标识近似与混淆可能性、合法来源抗辩、赔偿数额确定",
                "双方表现": "原告主张清晰但证据链不完整；被告抗辩虽未获证据支持，但对证据效力的攻击有效",
                "核心判断": "原告胜诉基础存在，证据补强后可获得更佳结果",
            },
            # correction_coefficient 是模型的**自评**，不参与取值——最终系数由下方
            # 10 项子分经 coefficient.derive_coefficient 推导（这里两者刻意取一致，
            # 表示「直觉与推导吻合」的理想情形；真实模式下不一致会记进 detail 备查）。
            "correction_coefficient": 0.85,
            "coefficient_tier": "substantial",
            "defense_strength": 83,
            "weak_points": [
                "侵权事实未公证，证据效力受到有效攻击",
                "未进行购买取证，混淆可能性证明存在瑕疵",
                "无被告获利或原告损失证据，判赔数额支撑不足",
            ],
            "focus_points": [
                "庭前完成公证购买取证，固定侵权事实",
                "补充被告获利线索（销量×单价×行业利润率）以支撑判赔",
            ],
            "plaintiff_scores": {"rights": 78, "infringement": 76, "evidence": 55,
                                 "legal_application": 74, "claim_reasonableness": 62},
            "defendant_scores": {"fact_defense": 80, "legal_defense": 82,
                                 "evidence_challenge": 92, "alternative_explanation": 78,
                                 "procedural_defense": 83},
            "plaintiff_scores_detail": {
                "rights": "商标注册证与核定范围引用准确，权利基础稳固",
                "infringement": "标识近似与商品类似的论证成立，但混淆可能性缺购买取证支撑",
                "evidence": "未公证、未购买取证，证据链存在明显缺口",
                "legal_application": "第五十七条的适用正确，赔偿顺位援引无误",
                "claim_reasonableness": "诉请金额偏高，缺获利证据支撑",
            },
            "defendant_scores_detail": {
                "fact_defense": "合法来源抗辩未提交授权材料，事实层面不成立",
                "legal_defense": "正当使用与在先使用的主张方向正确但无证据",
                "evidence_challenge": "对未公证截图与自述销量的质疑相当有力，直接命中原告软肋",
                "alternative_explanation": "价格与渠道差异的解释有一定说服力",
                "procedural_defense": "未提出时效等程序抗辩，影响有限",
            },
            "coefficient_reasoning": "命中的抗辩：被告对证据效力的质疑（未公证、未购买取证）直接动摇混淆可能性的证明，属「实质削弱」档；推导：原告论证强度 69 ÷ 被告抗辩强度 83 = 0.85。",
            "legal_basis": [
                {"article": "《商标法》第五十七条第（二）项", "cited_text": "未经许可在同一种商品上使用近似商标、易导致混淆", "applied_to": "支撑原告侵权构成要件"},
                {"article": "《商标法》第六十三条", "cited_text": "法定赔偿与惩罚性赔偿的计算顺位", "applied_to": "支撑赔偿数额主张"},
            ],
            "precedents": [
                {"name": "（202X）最高法知民终XX号", "court": "最高人民法院", "holding": "商标近似以整体观感与主要部分比对为准", "applied_to": "支撑混淆可能性认定"},
            ],
            "experience_refs": [
                {"title": "商标侵权公证购买取证要点", "applied_to": "补强证据固定流程"},
            ],
        },
    }


def _moot_copyright():
    return {
        "rounds": [
            {"step": 1, "step_name": "开庭陈述", "role": "plaintiff", "role_name": "原告代理律师",
             "content": "审判长、审判员：原告系涉案美术作品《XX》的著作权人，该作品由原告设计人员于2023年独立创作完成，已办理作品自愿登记，并保留完整的分层设计底稿。被告未经许可，将与该作品构成实质性相似的图案用于其电商店铺商品主图及详情页，涉及商品月销量2000余件。原告先于被告公开发表该作品，被告具备接触条件。依据《著作权法》第五十二条，被告行为侵犯了原告的复制权与信息网络传播权，请求判令停止侵权并赔偿损失。"},
            {"step": 2, "step_name": "被告答辩", "role": "defendant", "role_name": "被告代理律师",
             "content": "审判长：被告不同意原告全部诉请。第一，原告所称作品的构图元素系行业通用素材，属于公有领域，缺乏独创性，不应受著作权法保护；第二，被诉图案系被告委托第三方独立设计，被告已支付对价，主观上无过错；第三，原告依据的作品登记证书仅作形式审查，不能作为权利归属的实质证据，且该作品系职务作品，原告是否单独享有诉权存疑。恳请法庭驳回起诉。"},
            {"step": 3, "step_name": "举证质证-原告举证", "role": "plaintiff", "role_name": "原告代理律师",
             "content": "原告提交四组证据：证据一，作品登记证书及创作底稿分层文件，证明创作完成时间与独创性表达；证据二，原告官网及社交账号的发布时间记录，证明作品先于被诉图案公开发表；证据三，被诉商品页面截图与原图并列比对图，证明二者在构图、配色、元素排布上高度一致；证据四，被告店铺销量数据，证明侵权传播规模。需说明，被诉页面尚未办理公证，原告将补充。"},
            {"step": 3, "step_name": "举证质证-被告质证", "role": "defendant", "role_name": "被告代理律师",
             "content": "对证据一的真实性不持异议，但对证明目的有异议：作品登记系形式审查，不能证明独创性；底稿文件的时间戳可由软件任意修改，真实性存疑。对证据二的关联性有异议：公开发表不等于被告实际接触，原告未提交任何下载、访问或浏览记录。对证据三有异议：二者在细节处理、线条走向上存在明显差异，属于独立创作的巧合。对证据四的证明目的有异议，销量与图案无直接因果关系。"},
            {"step": 4, "step_name": "法庭辩论-原告", "role": "plaintiff", "role_name": "原告代理律师",
             "content": "针对被告质证，原告强调三点：第一，独创性判断应看表达的选择与安排，而非单个元素是否通用，本案图形在整体组合上体现了明显的个性化选择；第二，「接触」要件采高度盖然性标准，原告作品已在先公开发表于主流平台，被告作为同业经营者接触可能性极高，不应苛求原告提供下载记录；第三，被诉图案与权利作品的整体观感一致，差异仅属细微调整，不足以否定实质性相似。关于权属，原告已提交劳动合同与创作任务记录，足以证明职务作品财产权归属。"},
            {"step": 4, "step_name": "法庭辩论-被告", "role": "defendant", "role_name": "被告代理律师",
             "content": "原告回避了核心问题：若图形元素均来自公有领域，则保护范围应极窄，任何细微差异都足以排除侵权。被告的委托设计合同与付款凭证已提交法庭，证明独立创作来源。关于赔偿，图形作品司法判赔普遍在数千元至数万元区间，原告主张的数额远超合理预期。此外，原告未能证明其单独享有诉权，主体资格存在根本瑕疵。"},
            {"step": 5, "step_name": "法官归纳", "role": "judge", "role_name": "审判法官",
             "content": "本案争议焦点有三：一是涉案图形是否构成受著作权法保护的作品；二是被告是否具备接触可能性且构成实质性相似；三是权属与赔偿数额。关于焦点一，图形在元素组合上体现一定个性化选择，但原告未充分说明与公有领域素材的差异，独创性论证偏弱。关于焦点二，并列比对显示整体观感高度一致，接触要件上原告作品确已先行发表，但缺少直接接触证据。关于焦点三，职务作品权属条款表述笼统，需原告进一步举证。综合判断，本案侵权方向成立但权利基础与独创性论证均有补强空间。"},
        ],
        "judge": {
            "summary": "实质性相似较为直观，被告的独立创作抗辩缺乏说服力；但原告在独创性说明与权属链条两处存在实质短板，且缺少接触路径的直接证据。整体胜诉预期中等，判赔数额受限于图形作品的司法惯例，成本覆盖存在压力。",
            "summary_structured": {
                "争议焦点": "独创性认定、接触可能性与实质性相似、职务作品权属",
                "双方表现": "原告相似比对扎实但独创性论证不足；被告抓住权属与公有领域两个薄弱点，攻击有效",
                "核心判断": "侵权方向成立，但权利基础与独创性需补强后方可稳妥推进",
            },
            "correction_coefficient": 0.78,
            "coefficient_tier": "substantial",
            "defense_strength": 85,
            "weak_points": [
                "未说明作品与公有领域素材的差异，独创性易被攻击",
                "职务作品权属条款笼统，原告单独诉权存疑",
                "缺少接触路径的直接证据",
                "被诉页面未公证，证据效力存疑",
            ],
            "focus_points": [
                "补充独创性说明，逐项标注个性化表达的选择与安排",
                "固化职务作品权属证据（创作任务单、劳动合同补充协议）",
                "补充接触路径证据（后台访问日志、平台推荐记录）",
            ],
            "plaintiff_scores": {"rights": 66, "infringement": 72, "evidence": 52,
                                 "legal_application": 70, "claim_reasonableness": 62},
            "defendant_scores": {"fact_defense": 84, "legal_defense": 90,
                                 "evidence_challenge": 88, "alternative_explanation": 80,
                                 "procedural_defense": 83},
            "plaintiff_scores_detail": {
                "rights": "登记证书与底稿齐备，但职务作品权属条款笼统，权属链存疑",
                "infringement": "并列比对扎实，实质性相似论证成立；接触要件仅靠高度盖然性推定",
                "evidence": "相似比对材料充分，但缺接触路径的直接证据、页面未公证",
                "legal_application": "第五十二、五十四条的适用正确",
                "claim_reasonableness": "图形作品判赔惯例偏低，诉请金额与预期差距较大",
            },
            "defendant_scores_detail": {
                "fact_defense": "委托独立创作有合同与付款凭证，事实抗辩有一定支撑",
                "legal_defense": "公有领域抗辩与权属瑕疵攻击均命中要害，法律抗辩最强",
                "evidence_challenge": "对登记证书形式审查性质与时间戳可修改性的质疑成立",
                "alternative_explanation": "元素来自公有领域的替代解释成立度中等",
                "procedural_defense": "以原告主体不适格作程序抗辩，构成实质威胁",
            },
            "coefficient_reasoning": "命中的抗辩：独创性不足与职务作品权属瑕疵，前者动摇权利基础本身，属「实质削弱」档；推导：原告论证强度 64 ÷ 被告抗辩强度 85 = 0.78。",
            "legal_basis": [
                {"article": "《著作权法》第五十二条", "cited_text": "复制权与信息网络传播权的侵权构成", "applied_to": "支撑原告侵权主张"},
                {"article": "《著作权法》第五十四条", "cited_text": "法定赔偿区间与计算顺位", "applied_to": "支撑赔偿数额主张"},
            ],
            "precedents": [
                {"name": "（202X）最高法民再XX号", "court": "最高人民法院", "holding": "接触要件采高度盖然性，同业经营者公知作品接触可能性高", "applied_to": "支撑接触可能性认定"},
            ],
            "experience_refs": [
                {"title": "图形作品独创性说明模板", "applied_to": "补强独创性论证"},
            ],
        },
    }


def _moot_unfair_competition():
    return {
        "rounds": [
            {"step": 1, "step_name": "开庭陈述", "role": "plaintiff", "role_name": "原告代理律师",
             "content": "审判长、审判员：原告生产的「XX」系列商品，其包装装潢自2020年起持续使用至今已逾五年，在华东地区年销售额稳定在数千万元，经持续宣传已形成一定市场知名度，属于《反不正当竞争法》第六条第1项所称「有一定影响的商品装潢」。被告作为同业经营者，自2025年起在三个电商平台开设店铺，使用与原告装潢整体视觉近似的包装销售同类商品，持续14个月，明显具有攀附原告商誉的主观故意，足以导致相关公众混淆。请求判令被告停止不正当竞争行为并赔偿损失。"},
            {"step": 2, "step_name": "被告答辩", "role": "defendant", "role_name": "被告代理律师",
             "content": "审判长：被告不同意原告全部诉请。第一，原告所称装潢未注册为商标，其主张的「有一定影响」缺乏客观证据，仅有自制销售统计，不能证明为相关公众所知悉；第二，被告使用的包装早在2024年即已设计定稿并投入使用，时间上早于原告所称的知名度形成期，不存在攀附可能；第三，双方销售渠道与价格区间差异明显，相关公众施以一般注意力即可区分。恳请法庭驳回起诉。"},
            {"step": 3, "step_name": "举证质证-原告举证", "role": "plaintiff", "role_name": "原告代理律师",
             "content": "原告提交四组证据：证据一，2020年至今的包装装潢实物及设计定稿记录，证明持续使用；证据二，区域销售数据与广告投放合同，证明销售区域、时长与宣传投入；证据三，被诉商品与原告商品的装潢并列比对图，证明主要识别部分高度一致；证据四，被告三家店铺的销售数据，证明侵权规模与持续时间。需说明，我方早期上架的时间戳材料尚在调取中。"},
            {"step": 3, "step_name": "举证质证-被告质证", "role": "defendant", "role_name": "被告代理律师",
             "content": "对证据一的真实性无异议，但对证明目的有异议：持续使用不等于「有一定影响」，需以相关公众知悉程度为准。对证据二的证明力有异议：销售数据系原告单方制作，未经审计；广告投放合同仅显示投放金额，未显示实际触达效果，不足以证明知名度。对证据三有异议：二者在色彩饱和度、字体、版式上均有可识别差异。对证据四无异议，但强调销量与装潢无关，源于被告的低价策略。"},
            {"step": 4, "step_name": "法庭辩论-原告", "role": "plaintiff", "role_name": "原告代理律师",
             "content": "针对被告质证，原告强调：第一，「有一定影响」的认定应综合销售时间、区域、数额与宣传投入，原告五年持续经营、区域年销数千万元，已远超一般门槛；第二，被告所谓在先使用未提交任何设计定稿或上架的时间凭证，属口头主张，不应采信；第三，装潢近似应以整体视觉与主要识别部分为准，被告在无正当理由的情况下整体模仿，攀附故意明显，价格差异不能阻却混淆认定。"},
            {"step": 4, "step_name": "法庭辩论-被告", "role": "defendant", "role_name": "被告代理律师",
             "content": "原告始终未能完成「有一定影响」的举证责任，这是第六条的适用前提，前提不成立则后续混淆认定无从谈起。被告已提交委托设计合同与设计沟通记录，可证明独立来源。此外，原告未举证任何实际混淆的消费者例证，所谓混淆纯属推测。关于赔偿，原告未证明自身损失，被告获利亦未精确核算，数额主张缺乏依据。"},
            {"step": 5, "step_name": "法官归纳", "role": "judge", "role_name": "审判法官",
             "content": "本案争议焦点有三：一是原告商品装潢是否构成「有一定影响」；二是被诉装潢是否足以造成混淆；三是赔偿数额。关于焦点一，原告有五年持续使用与区域销售记录，但缺少行业排名、获奖等客观知名度证据，现有材料处于临界状态。关于焦点二，并列比对显示主要识别部分近似，被告的独立设计抗辩证据不足。关于焦点三，被告三店持续14个月，销量可推算，但成本结构不明，获利额需酌定。综合判断，本案成败几乎系于「有一定影响」的认定，原告须补强知名度证据。"},
        ],
        "judge": {
            "summary": "装潢近似较为直观，被告的独立设计抗辩证据薄弱；但「有一定影响」作为前提性要件，原告现有证据处于临界状态，且被告提出在先使用抗辩而我方缺少早期时间戳。本案胜负取决于知名度证据能否补强，风险显著高于一般侵权案件。",
            "summary_structured": {
                "争议焦点": "「有一定影响」的认定、装潢近似与混淆可能性、在先使用抗辩",
                "双方表现": "原告近似比对有效但知名度举证偏弱；被告抓住前提性要件与在先使用两处反击有力",
                "核心判断": "前提性要件处于临界，知名度证据补强前不宜贸然推进",
            },
            "correction_coefficient": 0.72,
            "coefficient_tier": "fatal_risk",
            "defense_strength": 87,
            "weak_points": [
                "缺少行业排名、获奖等客观知名度证据，「有一定影响」认定存疑",
                "被告主张在先使用，我方缺少早期上架时间戳",
                "未收集到实际混淆的消费者例证",
                "双方竞争关系需进一步证明",
            ],
            "focus_points": [
                "补充行业排名、获奖、媒体报道等客观知名度材料",
                "调取早期上架时间戳，反驳被告的在先使用主张",
                "收集消费者实际混淆例证（客服咨询记录、误购投诉）",
            ],
            "plaintiff_scores": {"rights": 58, "infringement": 70, "evidence": 46,
                                 "legal_application": 66, "claim_reasonableness": 60},
            "defendant_scores": {"fact_defense": 88, "legal_defense": 92,
                                 "evidence_challenge": 90, "alternative_explanation": 82,
                                 "procedural_defense": 83},
            "plaintiff_scores_detail": {
                "rights": "「有一定影响」仅有自制销售统计，前提性要件举证薄弱",
                "infringement": "装潢主要识别部分近似，比对有效；但实际混淆例证为零",
                "evidence": "知名度证据偏软、无消费者混淆例证、缺早期时间戳",
                "legal_application": "第六条适用方向正确，但前提要件论证不足",
                "claim_reasonableness": "损失与获利均未精确核算，数额主张缺乏依据",
            },
            "defendant_scores_detail": {
                "fact_defense": "在先使用的主张有委托设计合同支撑，事实抗辩有力",
                "legal_defense": "直击「有一定影响」这一前提性要件，前提不成立则全案不成立，威胁最大",
                "evidence_challenge": "对单方制作、未经审计的销售数据的质疑成立",
                "alternative_explanation": "低价策略解释销量来源，替代解释成立度较高",
                "procedural_defense": "以竞争关系欠缺作程序抗辩，具备一定威胁",
            },
            "coefficient_reasoning": "命中的抗辩：「有一定影响」不成立——这是第六条的适用前提，前提一旦不成立则混淆认定无从谈起，属「可能颠覆」档；推导：原告论证强度 60 ÷ 被告抗辩强度 87 = 0.72。",
            "legal_basis": [
                {"article": "《反不正当竞争法》第六条第1项", "cited_text": "有一定影响的商品装潢保护", "applied_to": "支撑原告权益基础"},
                {"article": "《反不正当竞争法》第十七条", "cited_text": "法定赔偿计算", "applied_to": "支撑赔偿数额主张"},
            ],
            "precedents": [
                {"name": "（202X）最高法民再XX号", "court": "最高人民法院", "holding": "有一定影响的认定应综合销售时间、区域、数额与宣传投入", "applied_to": "支撑知名度要件"},
            ],
            "experience_refs": [
                {"title": "装潢类不正当竞争知名度证据清单", "applied_to": "补强「有一定影响」举证"},
            ],
        },
    }


# ============================================================
# 分派表
# ============================================================

_CAUSE_TABLE = {
    CAUSE_TRADEMARK: {
        "matrix": _matrix_trademark,
        "eval": _eval_trademark,
        "moot": _moot_trademark,
    },
    CAUSE_COPYRIGHT: {
        "matrix": _matrix_copyright,
        "eval": _eval_copyright,
        "moot": _moot_copyright,
    },
    CAUSE_UNFAIR_COMPETITION: {
        "matrix": _matrix_unfair_competition,
        "eval": _eval_unfair_competition,
        "moot": _moot_unfair_competition,
    },
}


def _table(cause_type: str) -> Dict[str, Any]:
    _require_supported(cause_type)
    return _CAUSE_TABLE[cause_type]


# ============================================================
# 对外接口（签名与真实模块一致）
# ============================================================

def mock_evidence_review(cause_type: str = CAUSE_TRADEMARK) -> Dict[str, Any]:
    matrix = _table(cause_type)["matrix"]()
    gap_list = [
        {**row, "suggestion": f"补充{row['item']}（支撑要件：{row['element']}）"}
        for row in matrix if row["status"] != "sufficient"
    ]
    ev = _table(cause_type)["eval"]()
    return {
        "matrix": matrix,
        "gap_list": gap_list,
        "extra_evidence": ev["extra_evidence"],
        "completeness": ev["completeness"],
        "note": "",
        "error": None,
    }


def mock_rights(cause_type: str = CAUSE_TRADEMARK) -> Dict[str, Any]:
    return dict(_table(cause_type)["eval"]()["rights"])


def mock_infringement(cause_type: str = CAUSE_TRADEMARK) -> Dict[str, Any]:
    return dict(_table(cause_type)["eval"]()["infringement"])


def mock_procedure(cause_type: str = CAUSE_TRADEMARK) -> Dict[str, Any]:
    return dict(_table(cause_type)["eval"]()["procedure"])


def mock_damages(cause_type: str = CAUSE_TRADEMARK) -> Dict[str, Any]:
    return dict(_table(cause_type)["eval"]()["damages"])


def mock_precedent(cause_type: str = CAUSE_TRADEMARK) -> Dict[str, Any]:
    return dict(_table(cause_type)["eval"]()["precedent"])


def _mock_scale_stage(pairs) -> Dict[str, Any]:
    return {k: {"_count": v, "_summary": f"{v}条"} for k, v in pairs}


def mock_defendant_profile() -> Dict[str, Any]:
    """企查查 8 阶段画像 Mock：正常经营企业，回款能力中等偏上

    为什么要和真实模式刻意不一样：原先 mock 输出的 recovery_probability=50.0、
    damages_adjustment="中等规模 → 基准" 与真实模式的常见输出**完全相同**，
    演示时界面上自证不了数据来源（评测 P1-5）。现在除了数值错开，
    还额外给出 simulated=True 与 "(模拟)" 后缀的企业名，让来源一眼可辨。

    facts / stages 的字段形状必须与真实 qcc_api 一致，否则前端渲染
    在 mock 与真实两条路径下行为不一致，等于给展示层埋了个只在演示时炸的雷。
    """
    return {
        "locked_name": "示例被告科技有限公司（模拟）",
        "metrics": {
            # 基准 78（公开记录查不到问题）+ 有公开财务数据 +6 = 84，与企业侧真实规则表同口径。
            # 数值仍与真实默认值错开，演示时界面上自证得了数据来源。
            "recovery_probability": 84.0,
            "recovery_base": 78.0,
            "recovery_delta": 6.0,
            "recovery_tier": "ok",
            "damages_adjustment": "中型",
            "scale_tier": "中型",
            "scale_tier_basis": "参保 120 人 / 注册资本 500 万元",
            "time_extra_months": 0,
            "red_flags": [],
            "green_flags": ["有公开财务数据"],
            "details": {},
            "simulated": True,
            "facts": {
                "entity": {
                    "queried_name": "示例被告科技有限公司（模拟）",
                    "name": "示例被告科技有限公司（模拟）",
                    "locked_name": "示例被告科技有限公司（模拟）",
                    "credit_code": "91110000MA0000000X",
                    "legal_rep": "示例·法定代表人",
                    "reg_status": "开业（存续）",
                    "established": "2016-03-08",
                    "registered_capital": "500万元",
                    "registered_capital_wan": 500.0,
                    "insured_count": 120,
                    "staff_scale": "100-199人",
                    "industry": "科学研究和技术服务业",
                    "region": "北京市海淀区",
                    "match_status": "唯一精确匹配",
                    "name_matches_query": True,
                },
                "risk": {
                    "deregistered": 0, "liquidation": 0, "bankruptcy": 0,
                    "executed": 0, "dishonest": 0, "terminated": 0,
                    "restricted": 0, "serious_violation": 0, "abnormal": 0,
                    "court_filed": 0, "hit_dimensions": 0,
                },
                "scale": {
                    "trademark_count": 3, "online_shops": 2, "app": 1,
                    "miniprogram": 1, "wechat_mp": 1, "douyin": 0,
                    "bidding": 0, "financing": 1, "honors": 1,
                    "litigation_history": 0,
                },
                "financial_data_available": True,
                "signals_hit": 1,
                "scale_tier": "中型",
                "scale_tier_basis": "参保 120 人 / 注册资本 500 万元",
            },
        },
        "stages": {
            "A_主体锁定": {"ok": True, "locked_name": "示例被告科技有限公司（模拟）",
                           "credit_code": "91110000MA0000000X", "candidates": [],
                           "match_status": "唯一精确匹配"},
            "B_基本盘": {
                "工商登记": {"_count": 1, "_summary": "1条记录"},
                "财务数据": {"_count": 2, "_summary": "2条记录"},
                "上市信息": {"_count": 0, "_summary": "无数据"},
            },
            "C_风险分诊": {"hits": {}, "_summary": "未命中风险维度", "total_hit_dimensions": 0},
            "D_风险下钻": _mock_scale_stage([
                ("注销记录", 0), ("清算信息", 0), ("破产重整", 0), ("被执行人", 0),
                ("失信信息", 0), ("终本案件", 0), ("限高消费", 0), ("严重违法", 0),
                ("经营异常", 0), ("法院立案", 0),
            ]),
            "F_经营规模": _mock_scale_stage([
                ("商标资产", 3), ("线上店铺", 2), ("APP信息", 1), ("小程序", 1),
                ("微信公众号", 1), ("抖音账号", 0), ("招投标", 0),
                ("融资记录", 1), ("荣誉信息", 1),
            ]),
            "G_诉讼时间": {"被诉历史": {"_count": 0, "_summary": "0件"}},
        },
        "stages_skipped": False,
        "error": None,
    }


def mock_moot_rounds(cause_type: str = CAUSE_TRADEMARK) -> List[Dict[str, Any]]:
    """7 轮庭审发言（五步流程：陈述→答辩→举证→质证→辩论×2→归纳），按案由区分"""
    return [dict(r) for r in _table(cause_type)["moot"]()["rounds"]]


def mock_judge_result(cause_type: str = CAUSE_TRADEMARK) -> Dict[str, Any]:
    """法官结构化归纳，按案由区分（修正系数与抗辩强度各不相同）"""
    return dict(_table(cause_type)["moot"]()["judge"])
