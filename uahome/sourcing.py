"""Allocate an actual 1688/buying-agent quotation across a purchase batch.

Tax amounts are entered from the user's quotation, not inferred from an HS code
or a hard-coded tax rate. Cash needed and operating cost remain separate.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from .domain import ValidationError

D = Decimal
TOTAL_FIELDS = (
    "china_shipping_cny", "international_shipping_krw", "customs_duty_krw",
    "import_vat_krw", "brokerage_krw", "inspection_krw", "labeling_krw",
    "domestic_inbound_krw",
)


def _number(value, label):
    if value is None or isinstance(value, bool):
        raise ValidationError(f"{label}: 숫자를 입력해 주세요.")
    try:
        result = D(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError(f"{label}: 숫자를 입력해 주세요.") from None
    if not result.is_finite() or not 0 <= result <= D("1000000000000"):
        raise ValidationError(f"{label}: 0 이상의 유한한 금액을 입력해 주세요.")
    if result.as_tuple().exponent < -6:
        raise ValidationError(f"{label}: 소수 6자리까지만 입력해 주세요.")
    return result


def calculate_import_batch(data):
    if not isinstance(data, dict):
        raise ValidationError("수입 견적 정보를 입력해 주세요.")
    with localcontext() as ctx:
        ctx.prec = 60
        quantity = _number(data.get("quantity", 1), "사입 수량")
        if quantity < 1 or quantity > 1000000 or quantity != quantity.to_integral_value():
            raise ValidationError("사입 수량은 1~1,000,000 사이의 정수여야 합니다.")
        unit_price = _number(data.get("cny_unit_price", 0), "1688 위안화 단가")
        rate = _number(data.get("exchange_rate", 1), "원/위안 환율")
        if not rate:
            raise ValidationError("환율은 0보다 커야 합니다.")
        totals = {key: _number(data.get(key, 0), key) for key in TOTAL_FIELDS}
        recoverable = data.get("vat_recoverable", False)
        if not isinstance(recoverable, bool):
            raise ValidationError("수입부가세 공제 여부는 참/거짓 값이어야 합니다.")
        purchase = quantity * unit_price * rate
        logistics = totals["china_shipping_cny"] * rate + sum(
            (value for key, value in totals.items() if key != "china_shipping_cny"), D("0"))
        cash = purchase + logistics
        operating_inbound = logistics - (totals["import_vat_krw"] if recoverable else D("0"))
        operating = purchase + operating_inbound

        def amount(value, places="0.01"):
            return float(value.quantize(D(places), rounding=ROUND_HALF_UP))

        warnings = [
            "배송·통관·검수·라벨·센터 입고 운송은 전체 사입분의 총액을 입력합니다.",
            "관세와 수입부가세는 견적·통관 자료의 금액을 입력합니다. 이 도구는 HS코드별 세액을 계산하지 않습니다.",
            "공제 가능으로 선택한 수입부가세도 결제 시 현금은 필요합니다. 실제 공제 가능 여부는 확인하세요.",
            "로켓그로스 입출고·배송·보관비는 별도의 판매 마진 비용에 입력합니다. 같은 배송비를 중복 반영하지 마세요.",
        ]
        if not unit_price:
            warnings.append("1688 매입 단가가 0입니다. 실제 견적을 입력해 주세요.")
        return {
            "quantity": int(quantity), "currency": "KRW",
            "purchase_total_krw": amount(purchase), "logistics_total_krw": amount(logistics),
            "cash_outlay_total": amount(cash), "operating_cost_total": amount(operating),
            "per_unit_landed_cost": amount(operating / quantity),
            "per_unit_inbound_cost": amount(operating_inbound / quantity, "0.000001"),
            "import_vat_total": amount(totals["import_vat_krw"]), "vat_recoverable": recoverable,
            "costs_patch": {"purchase_price": float(unit_price), "exchange_rate": float(rate),
                            "inbound_shipping": amount(operating_inbound / quantity, "0.000001")},
            "input": {"quantity": int(quantity), "cny_unit_price": float(unit_price),
                      "exchange_rate": float(rate), "vat_recoverable": recoverable,
                      **{key: float(value) for key, value in totals.items()}},
            "warnings": warnings,
        }
