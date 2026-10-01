"""开放名册统一规则：网页提交、直连请求、落库前改写三条路径共用同一套规则。

任何往读数表写入库区编码的入口，都必须经过本模块的判定与退回说法，
数据库写口另有同名册内容的触发器兜底，保证绕过接口直连或落库前偷改代号
也会以完全一致的说法被退回。
"""

# 统一退回说法（应用层与数据库触发器各写一份相同文案，缺一即视为未完成）
REJECT_DETAIL = "库区编码不在开放名册内，已退回；须由记录员在开放名册中登记后再提交"
REJECT_STATUS = 422

REJECT_SAMPLE_ZONE = "库X99"

REJECT_SAMPLE_PAYLOAD = {
    "probe_id": "探头X99",
    "zone_code": REJECT_SAMPLE_ZONE,
    "temp_c": 5.0,
}


def normalize_zone(zone_code) -> str:
    """规范化库区编码：去首尾空白；None 视为空。"""
    if zone_code is None:
        return ""
    return str(zone_code).strip()


def evaluate_zone(zone_code, allowed_codes) -> dict:
    """按名册判定库区编码。

    allowed_codes: 名册中已规范化编码的可迭代对象。
    返回 {accepted: bool, zone_code: str, detail: str}，三路径共用。
    """
    zone = normalize_zone(zone_code)
    allowed = {normalize_zone(c) for c in allowed_codes}
    if zone and zone in allowed:
        return {"accepted": True, "zone_code": zone, "detail": ""}
    return {"accepted": False, "zone_code": zone, "detail": REJECT_DETAIL}


def reject_response_payload(zone_code) -> dict:
    """统一退回响应体：HTTP 直连、网页提示、触发器兜底都长这个样子。"""
    return {"detail": REJECT_DETAIL, "zone_code": normalize_zone(zone_code)}


def reject_sample() -> dict:
    """开放名册落地页展示的退回样例：三路径共用的同一条说法。"""
    return {
        "path": "三条路径共用同一规则",
        "paths": [
            "网页表单提交（页面先拦）",
            "绕过页面直连接口 POST /api/readings（接口再拦）",
            "通过接口后、落库前偷改库区代号（数据库写口触发器兜底拦截）",
        ],
        "request": REJECT_SAMPLE_PAYLOAD,
        "status": REJECT_STATUS,
        "response": reject_response_payload(REJECT_SAMPLE_ZONE),
        "accepted": False,
    }
