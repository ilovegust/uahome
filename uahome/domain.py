"""Validated commerce calculations and factual, editable document drafts.

Rates are percentages and all monetary inputs are per unit. Profit is an
estimated contribution after the user-entered tax reserve, not tax advice.
"""

from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP, localcontext
import re
from urllib.parse import urlsplit


class ValidationError(ValueError):
    """Invalid user input; safe to return as a validation message."""


D = Decimal
HUNDRED = D("100")
MONEY_FIELDS = (
    "sale_price", "purchase_price", "inbound_shipping", "outbound_shipping",
    "packaging", "other_cost", "customer_shipping", "fixed_fee", "return_cost",
    "fulfillment_fee", "storage_fee",
)
RATE_FIELDS = (
    "platform_fee_rate", "payment_fee_rate", "ad_rate", "return_rate",
    "tax_reserve_rate", "target_margin_rate",
)
PRODUCT_STATUSES = {
    "candidate", "selected", "purchase_approved", "ordered", "received",
    "listing_ready", "listed", "paused", "inbound_planned", "in_transit", "stocked",
}
TEXT_FIELDS = (
    "name", "category", "supplier", "source_url", "channel", "notes",
    "keywords", "selling_points", "specs",
    "seller_sku", "coupang_product_id", "fulfillment_center", "inbound_reference",
    "tracking_number",
)


def _number(value, field, maximum=D("1000000000000")):
    if isinstance(value, bool) or value is None:
        raise ValidationError(f"{field}: 0 이상의 숫자를 입력하세요.")
    try:
        result = D(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(f"{field}: 올바른 숫자를 입력하세요.") from None
    if not result.is_finite() or result < 0 or result > maximum:
        raise ValidationError(f"{field}: 0~{maximum} 범위의 유한한 숫자를 입력하세요.")
    if result.as_tuple().exponent < -6 and result != result.quantize(D("0.000001")):
        raise ValidationError(f"{field}: 소수점 아래 최대 6자리까지 입력하세요.")
    return result


def _integer(value, field, minimum=1):
    result = _number(value, field, D("1000000"))
    if result != result.to_integral_value() or result < minimum:
        raise ValidationError(f"{field}: {minimum} 이상의 정수를 입력하세요.")
    return int(result)


def _text(value, field, limit=20000):
    if not isinstance(value, str):
        raise ValidationError(f"{field}: 문자열을 입력하세요.")
    result = value.strip()
    if len(result) > limit:
        raise ValidationError(f"{field}: {limit}자 이내로 입력하세요.")
    return result


def _money(value):
    with localcontext() as context:
        context.prec = 60
        return float(value.quantize(D("0.01"), rounding=ROUND_HALF_UP))


def _costs(data):
    if not isinstance(data, dict):
        raise ValidationError("costs: 비용 객체가 필요합니다.")
    result = {key: _number(data.get(key, 0), key) for key in MONEY_FIELDS}
    for key in RATE_FIELDS:
        maximum = D("99.99") if key == "target_margin_rate" else HUNDRED
        result[key] = _number(data.get(key, 0), key, maximum)
    result["exchange_rate"] = _number(data.get("exchange_rate", 1), "exchange_rate")
    if not result["exchange_rate"]:
        raise ValidationError("exchange_rate: 0보다 커야 합니다.")
    return result


def normalize_product(data):
    """Normalize only editable fields; discard IDs, timestamps and derived data."""
    if not isinstance(data, dict):
        raise ValidationError("상품 객체가 필요합니다.")
    result = {key: _text(data.get(key, ""), key) for key in TEXT_FIELDS}
    if not result["name"]:
        raise ValidationError("상품명을 입력하세요.")
    if len(result["name"]) > 200:
        raise ValidationError("상품명은 200자 이내로 입력하세요.")
    if result["source_url"]:
        try:
            parsed = urlsplit(result["source_url"])
            valid_url = parsed.scheme in {"https", "http"} and bool(parsed.hostname)
            valid_url = valid_url and not parsed.username and not parsed.password
        except ValueError:
            valid_url = False
        if not valid_url:
            raise ValidationError("source_url: 인증정보가 없는 http/https 주소를 입력하세요.")
    status = data.get("status", "candidate")
    if not isinstance(status, str) or status not in PRODUCT_STATUSES:
        raise ValidationError("올바른 상품 상태를 선택하세요.")
    result["status"] = status
    for key in ("quantity", "moq"):
        result[key] = _integer(data.get(key, 1), key)
    result["lead_days"] = _integer(data.get("lead_days", 0), "lead_days", minimum=0)
    for key in ("demand_score", "competition_score", "supplier_score"):
        result[key] = float(_number(data.get(key, 0), key, D("5")))
    result["costs"] = {key: float(value) for key, value in _costs(data.get("costs", {})).items()}
    return result


def calculate_margin(costs):
    # Six fractional places and bounded inputs can still produce a 33-digit
    # required price when the fee-rate sum is very close to 100 percent.
    with localcontext() as context:
        context.prec = 60
        return _calculate_margin(costs)


def _calculate_margin(costs):
    c = _costs(costs)
    revenue = c["sale_price"] + c["customer_shipping"]
    landed = c["purchase_price"] * c["exchange_rate"] + c["inbound_shipping"]
    return_reserve = c["return_rate"] / HUNDRED * c["return_cost"]
    fixed = (landed + c["outbound_shipping"] + c["packaging"]
             + c["other_cost"] + c["fixed_fee"] + return_reserve
             + c["fulfillment_fee"] + c["storage_fee"])
    variable_rate = sum((c[key] for key in (
        "platform_fee_rate", "payment_fee_rate", "ad_rate", "tax_reserve_rate"
    )), D("0")) / HUNDRED
    total = fixed + revenue * variable_rate
    profit = revenue - total
    margin = profit / revenue * HUNDRED if revenue else D("0")
    warnings = []
    if not revenue:
        warnings.append("판매가를 입력하면 마진율을 계산합니다. 현재 마진율 0은 수익성 판단값이 아닙니다.")
    if profit < 0:
        warnings.append("예상 손실입니다. 판매가와 비용을 다시 검토하세요.")
    if not c["purchase_price"]:
        warnings.append("사입가가 0입니다. 무료 공급이 아니라면 사입가를 입력하세요.")
    if not c["tax_reserve_rate"]:
        warnings.append("세금 예비비가 0%입니다. 실제 세금은 별도로 확인하세요.")

    def required_price(denominator):
        if denominator <= 0:
            return None
        return float(max(D("0"), fixed / denominator - c["customer_shipping"]).quantize(D("1"), rounding=ROUND_CEILING))

    break_even = required_price(D("1") - variable_rate)
    recommended = required_price(D("1") - variable_rate - c["target_margin_rate"] / HUNDRED)
    if break_even is None:
        warnings.append("매출 비례 비용률 합계가 100% 이상으로 손익분기 판매가를 산출할 수 없습니다.")
    if recommended is None:
        warnings.append("비용률과 목표 마진율 합계가 100% 이상으로 목표 판매가를 산출할 수 없습니다.")
    return {
        "revenue": _money(revenue), "landed_cost": _money(landed),
        "total_cost": _money(total), "profit": _money(profit),
        "margin_rate": _money(margin), "break_even_price": break_even,
        "recommended_price": recommended,
        "return_reserve": _money(return_reserve),
        "variable_cost": _money(revenue * variable_rate),
        "warnings": warnings,
        "assumptions": [
            "모든 비용은 개당 금액입니다. 입고 운임은 수량으로 나눈 개당 금액을 입력합니다.",
            "로켓그로스 입출고·배송 예상비와 보관 예상비는 개당 fulfillment_fee·storage_fee로 별도 반영합니다. 같은 배송비를 outbound_shipping에 중복 입력하지 마세요. 실제 적용 요율을 확인해 입력하세요.",
            "매출 = 판매가 + 고객 부담 배송비. 플랫폼·결제·광고·세금 예비비 비율은 모두 이 매출에 적용합니다.",
            "반품 예비비 = 반품률 × 건당 추가 반품비. 반품 상품 손실 등은 추가 비용에 포함하세요.",
            "세금 예비비는 사용자가 지정한 단순 추정치이며 부가세·소득세의 실제 계산이 아닙니다.",
            "권장 판매가는 비용과 비율이 일정하다는 가정의 목표 마진 판매가이며 시장 가격 예측이 아닙니다.",
        ],
    }


def rank_product(product):
    p = normalize_product(product)
    margin = calculate_margin(p["costs"])
    target = max(D("1"), D(str(p["costs"]["target_margin_rate"])))
    margin_score = max(D("0"), min(D("1"), D(str(margin["margin_rate"])) / target))
    score = (D(str(p["demand_score"])) * 5
             + (5 - D(str(p["competition_score"]))) * 4
             + D(str(p["supplier_score"])) * 4 + margin_score * 35)
    reasons = [
        "수동 입력 점수 기반: 수요 25점 + 낮은 경쟁도 20점 + 공급처 20점 + 목표 마진 달성도 35점.",
        "실시간 시장 조사나 판매량 예측이 아닙니다. 점수의 근거를 메모에 기록하세요.",
    ]
    if any(p[key] == 0 for key in ("demand_score", "competition_score", "supplier_score")):
        reasons.append("0점은 미입력 기본값일 수 있습니다. 수요·경쟁도·공급처의 미확인 점수는 사용자 평가가 필요합니다.")
    if p["quantity"] < p["moq"]:
        reasons.append("계획 수량이 최소 주문 수량(MOQ)보다 적습니다.")
    if not p["supplier"] or not p["source_url"]:
        reasons.append("공급처 또는 출처 주소 확인이 필요합니다.")
    if not p["costs"]["sale_price"] or not p["costs"]["purchase_price"]:
        recommendation = "비용 입력 필요"
    elif margin["profit"] <= 0:
        recommendation = "수익성 재검토"
    elif p["quantity"] < p["moq"]:
        recommendation = "주문 수량 확인"
    else:
        recommendation = "검토 우선" if score >= 70 else "추가 조사"
    return {"score": _money(score), "recommendation": recommendation, "reasons": reasons}


def build_listing(product):
    p = normalize_product(product)
    keywords = list(dict.fromkeys(x.strip() for x in re.split(r"[,\n]", p["keywords"]) if x.strip()))[:20]
    missing = [key for key in ("category", "channel", "selling_points", "specs") if not p[key]]
    title = p["name"][:100]
    sections = [p["name"]]
    if p["selling_points"]:
        sections.extend(["", "상품 특징", p["selling_points"]])
    if p["specs"]:
        sections.extend(["", "상품 정보", p["specs"]])
    sections.extend(["", "등록 전 확인: 옵션·재고·배송·교환/반품 정책, 필수 상품정보와 인증 해당 여부를 확인하세요."])
    return {"title": title, "description": "\n".join(sections), "keywords": keywords,
            "missing_fields": missing, "channel": p["channel"], "mode": "draft"}


def build_purchase(product):
    with localcontext() as context:
        context.prec = 60
        return _build_purchase(product)


def _build_purchase(product):
    p = normalize_product(product)
    c = _costs(p["costs"])
    estimated = (c["purchase_price"] * c["exchange_rate"] + c["inbound_shipping"]) * p["quantity"]
    warnings = ["예상 사입 총액은 환산 사입가와 개당 입고 운임만 포함합니다. 통관·검수·라벨 등 수입 부대비용은 총액을 수량으로 나눠 inbound_shipping에 배분해 저장할 수 있습니다. 이미 배분했다면 중복 합산하지 말고 실제 견적·관세를 확인하세요.",
                "발주서 초안입니다. 주문이나 결제는 전송되지 않았습니다."]
    if p["quantity"] < p["moq"]:
        warnings.append("최소 주문 수량(MOQ)을 충족하지 않습니다.")
    if not p["supplier"]:
        warnings.append("공급처를 입력하세요.")
    if not c["purchase_price"]:
        warnings.append("사입가를 입력하세요.")
    text = (f"발주 검토서 (미전송)\n공급처: {p['supplier'] or '미입력'}\n상품: {p['name']}\n"
            f"수량: {p['quantity']}개 / MOQ: {p['moq']}개\n"
            f"예상 사입·입고 총액: {_money(estimated):,.2f}원\n"
            f"납기 입력값: {p['lead_days']}일 (공급처 확인 필요)\n"
            f"상품 출처: {p['source_url'] or '미입력'}\n"
            f"메모: {p['notes']}\n\n견적·옵션·수량·배송·세금·납기를 확인한 후 발주하세요.")
    return {"supplier": p["supplier"], "product_name": p["name"], "quantity": p["quantity"],
            "estimated_total": _money(estimated), "currency": "KRW", "text": text, "warnings": warnings}


def draft_review_reply(review, product):
    if not isinstance(review, dict):
        raise ValidationError("실제 고객 리뷰 객체가 필요합니다.")
    p = normalize_product(product)
    rating = _integer(review.get("rating"), "rating")
    if rating > 5:
        raise ValidationError("rating: 1~5 정수를 입력하세요.")
    content = _text(review.get("content", ""), "content")
    if not content:
        raise ValidationError("실제 고객 리뷰 내용을 입력하세요.")
    attention = rating <= 2 or any(word in content for word in (
        "파손", "불량", "환불", "교환", "알레르기", "다쳤", "위험", "누락", "오배송", "고장",
    ))
    if attention:
        category = "확인 필요"
        text = (f"고객님, {p['name']} 이용 중 불편을 겪으셨다는 말씀 확인했습니다. "
                "정확한 상황을 확인할 수 있도록 판매처 문의 채널로 주문 정보와 상세 내용을 보내주시면 확인 후 안내드리겠습니다.")
    elif rating <= 3:
        category = "개선 의견"
        text = (f"고객님, {p['name']}에 대한 의견을 남겨주셔서 감사합니다. "
                "말씀하신 내용을 확인하고 상품과 서비스 개선에 참고하겠습니다.")
    else:
        category = "긍정 후기"
        text = f"고객님, {p['name']}을 선택해 주시고 후기를 남겨주셔서 감사합니다. 보내주신 의견을 잘 살펴보겠습니다."
    return {"text": text, "category": category, "requires_attention": attention, "mode": "draft"}
