"""Conservative conversion of requested quantities into catalog price units.

``unit_rules`` is a mapping keyed by a catalog record's ``code``. Each SKU
rule may contain ``price_unit`` (the unit displayed beside the invoice
quantity), ``request_units`` (request unit -> number of price units, as a
decimal string), ``grams_per_unit`` or ``ml_per_unit`` (measured content of
one price unit), and ``allow_fractional`` (whether a package may be split).
For example, a confirmed 490g boxed tofu has ``price_unit='盒'``,
``request_units={'盒': '1'}``, ``grams_per_unit='490'`` and
``allow_fractional=False``. The rule must be confirmed for the SKU; its
absence never licenses guessing the weight of a piece or the count in a box.

Without a SKU rule, a bare weight/volume catalog spec (such as 500g or 1L)
is a measured price unit, while an explicit package unit in the spec or
product name (such as 425g/罐) is one indivisible price unit. No rounding up
to a whole pack is permitted.
"""

from decimal import Decimal, InvalidOperation
import re


_MASS = {"g": Decimal("1"), "克": Decimal("1"), "kg": Decimal("1000"), "公斤": Decimal("1000"), "千克": Decimal("1000"), "斤": Decimal("500"), "两": Decimal("50")}
_VOLUME = {"ml": Decimal("1"), "毫升": Decimal("1"), "l": Decimal("1000"), "升": Decimal("1000")}
_PACKS = ("袋", "包", "盒", "瓶", "罐", "桶", "箱", "件", "个", "支", "块", "副", "捆", "筐", "条", "颗", "根")
_SIMPLE_MEASURE = re.compile(r"^\s*(\d+(?:\.\d+)?)?\s*(kg|公斤|千克|g|克|ml|毫升|l|升|斤|两)\s*$", re.I)


def _decimal(value, label):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{label}缺失或不是有效数字") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{label}必须大于零")
    return result


def _display(number):
    return format(number.normalize(), "f")


def _unit_key(unit):
    normalized = str(unit or "").strip()
    return normalized.lower() if normalized.lower() in _MASS or normalized.lower() in _VOLUME else normalized


def price_basis(record):
    """Comparable base measure for a simple price spec, or its literal spec."""
    spec = str(record.get("spec") or "").strip()
    match = _SIMPLE_MEASURE.fullmatch(spec)
    if match:
        count = Decimal(match.group(1) or "1")
        unit = match.group(2).lower()
        if unit in _MASS:
            return ("mass_g", _display(count * _MASS[unit]))
        if unit in _VOLUME:
            return ("volume_ml", _display(count * _VOLUME[unit]))
    return ("literal", spec.casefold())


def _per_price_unit(record, rule):
    spec = str(record.get("spec") or "").strip()
    name = str(record.get("name") or "").strip()
    match = _SIMPLE_MEASURE.fullmatch(spec)
    explicit_pack = None
    if spec in _PACKS:
        explicit_pack = spec
    else:
        # A package indicator belongs to this SKU only when it follows a
        # measured size, rather than appearing incidentally in its name.
        package = re.search(r"\d+(?:\.\d+)?\s*(?:kg|g|ml|l|斤|克|升)\s*/\s*(袋|包|盒|瓶|罐|桶|箱|件|个|支|块)", name, re.I)
        if package:
            explicit_pack = package.group(1)
    if explicit_pack is None and match and "罐头" in name:
        explicit_pack = "罐"
    price_unit = str(rule.get("price_unit") or explicit_pack or spec).strip()
    grams = rule.get("grams_per_unit")
    volume = rule.get("ml_per_unit")
    if match and grams is None and volume is None:
        base = _decimal(match.group(1) or "1", "目录规格")
        measured_unit = match.group(2).lower()
        if measured_unit in _MASS:
            grams = base * _MASS[measured_unit]
        elif measured_unit in _VOLUME:
            volume = base * _VOLUME[measured_unit]
    fractional = rule.get("allow_fractional")
    if fractional is None:
        fractional = not bool(explicit_pack or price_unit in _PACKS)
    elif not isinstance(fractional, bool):
        raise ValueError("allow_fractional必须为布尔值")
    return price_unit, grams, volume, fractional


def invoice_quantity(quantity, unit, record, unit_rules=None):
    """Return {quantity, price_unit}; raise ValueError if conversion is unknown.

    ``unit_rules`` is the code-keyed SKU mapping described in this module's
    docstring. Explicit request-unit ratios supersede inferred size ratios.
    A missing quantity, unknown mass per piece, or partial whole package is
    unresolved and never becomes an invoice amount.
    """
    count = _decimal(quantity, "数量")
    requested_unit = _unit_key(unit)
    if not requested_unit:
        raise ValueError("原始单位缺失，无法确定计价数量")
    rule = (unit_rules or {}).get(str(record.get("code")), {})
    if not isinstance(rule, dict):
        raise ValueError("SKU单位规则必须为对象")
    price_unit, grams, volume, fractional = _per_price_unit(record, rule)
    if not price_unit:
        raise ValueError("目录计价单位缺失")
    ratios = rule.get("request_units") or {}
    if requested_unit in ratios:
        converted = count * _decimal(ratios[requested_unit], "单位换算比例")
    # Package words are not universal synonyms.  “包” may describe a
    # different pack size from a catalog “袋”; only a formally recorded
    # request_units rule may bridge them.  This keeps unknown packaging facts
    # in manual review instead of silently inventing a one-to-one ratio.
    elif requested_unit == _unit_key(price_unit):
        converted = count
    elif requested_unit in _MASS and grams is not None:
        converted = count * _MASS[requested_unit] / _decimal(grams, "单价单位重量")
    elif requested_unit in _VOLUME and volume is not None:
        converted = count * _VOLUME[requested_unit] / _decimal(volume, "单价单位体积")
    else:
        raise ValueError(f"缺少{requested_unit}到{price_unit}的已确认换算关系")
    if not fractional and converted != converted.to_integral_value():
        raise ValueError("整包商品不允许将需求自动取整或拆分")
    return {"quantity": _display(converted), "price_unit": price_unit}
