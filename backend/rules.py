"""冷链探头读数判定 + 库区开放名册校验规则。

名册校验与退回话术在此定义一次，网页提交、直连请求、落库前触发器
三条路径共用同一套规则与同一套话术，任何地方不得另写一份。
"""


def judge_temp(temp_c: float) -> tuple[str, str]:
    if temp_c <= 8:
        return "合格", "探头温度未超过 8℃ 上限"
    return "超温", "探头温度超过 8℃ 冷链上限"


def verdict_for_display(verdict: str | None, status: str) -> str:
    if verdict:
        return verdict
    if status == "pending":
        return "待处理"
    if status == "processing":
        return "处理中"
    return "—"


# ---- 库区开放名册：三路径共用同一套规则与话术 ----

ZONE_REJECT_TEMPLATE = "库区「{zone}」不在开放名册内，已退回"


def zone_allowed(zone_code: str, roster_codes) -> bool:
    """名册校验唯一规则：库区编码必须落在开放名册内。"""
    return bool(zone_code) and zone_code in roster_codes


def zone_rejection_message(zone_code: str) -> str:
    """退回话术（网页 / 直连 / 落库触发器共用）。"""
    return ZONE_REJECT_TEMPLATE.format(zone=zone_code)


def zone_rejection_raise_sql() -> str:
    """同一套退回话术的 plpgsql 版本（RAISE EXCEPTION 以 % 占位）。"""
    return ZONE_REJECT_TEMPLATE.replace("{zone}", "%")
