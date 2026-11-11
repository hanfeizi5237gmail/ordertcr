"""Exact business arithmetic; Excel receives precomputed values, never guesses."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

POLICIES = {"sample_exact", "line_cent"}


def decimal_value(value, label="数值", *, minimum=None, positive=False):
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label}必须是明确数值")
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label}不是有效十进制数") from exc
    if not result.is_finite() or (positive and result <= 0) or (minimum is not None and result < minimum):
        raise ValueError(f"{label}超出允许范围")
    if len(result.as_tuple().digits) > 24 or abs(result.adjusted()) > 18:
        raise ValueError(f"{label}精度或数量级超出支持范围")
    return result


def text_number(value):
    value = Decimal(value)
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def discount_value(value):
    text = str(value).strip().replace("％", "%")
    divisor = Decimal(1)
    if text.endswith("%"):
        text, divisor = text[:-1], Decimal(100)
    elif text.endswith("折"):
        text, divisor = text[:-1], Decimal(10)
    result = decimal_value(text, "折扣率", minimum=0) / divisor
    if result > 1:
        raise ValueError("折扣率应在0到1之间，或明确使用百分号/折")
    return text_number(result)


def calculate(price, discount, quantity, policy):
    if policy not in POLICIES:
        raise ValueError("未知金额策略")
    price = decimal_value(price, "原价", minimum=0)
    discount = Decimal(discount_value(discount))
    quantity = decimal_value(quantity, "计价数量", positive=True)
    with localcontext() as context:
        context.prec = 128
        unit_price = price * discount
        amount = unit_price * quantity
        if policy == "line_cent":
            amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return {"unit_price": text_number(unit_price), "amount": text_number(amount)}


def sum_amounts(values):
    def amount_value(value):
        if isinstance(value, bool):
            raise ValueError("金额必须是明确数值")
        try:
            amount = Decimal(str(value).strip())
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("金额不是有效十进制数") from exc
        if not amount.is_finite() or amount < 0 or len(amount.as_tuple().digits) > 100 or abs(amount.adjusted()) > 64:
            raise ValueError("金额精度或数量级超出支持范围")
        return amount

    with localcontext() as context:
        context.prec = 128
        return sum((amount_value(x) for x in values if x is not None), Decimal(0))


def cents(value):
    with localcontext() as context:
        context.prec = 128
        return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
