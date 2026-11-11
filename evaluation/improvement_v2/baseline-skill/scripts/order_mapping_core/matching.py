"""Conservative catalog selection; never promote uncertain suggestions."""

from decimal import Decimal, InvalidOperation
import re
import unicodedata

from .units import invoice_quantity, price_basis


def _normalized(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or ""))).casefold()


def _issue(code, message):
    return {"code": code, "message": message}


def _packaging_compatible(item, record):
    if item.get("unit") != "箱":
        return True
    packaging = _normalized(record.get("name")) + _normalized(record.get("spec"))
    return bool(re.search(r"箱|件|[×*]\d+(?:袋|包|瓶|罐|盒)", packaging))


def _suggestions(item, records):
    """Retrieve all plausible name variants; retrieval is never selection."""
    name = _normalized(item.get("name"))
    if not name:
        return []
    direct = [record for record in records if name in _normalized(record.get("name")) or _normalized(record.get("name")) in name]
    found = list(direct)
    # Compound names can add a brand before the noun or insert one short
    # qualifier inside it (鸡块 -> 鸡肉块). Retrieve before applying hard
    # brand, category, food-form and packaging exclusions.
    for length in range(min(4, len(name)), 1, -1):
        suffix = name[-length:]
        matches = [record for record in records if suffix in _normalized(record.get("name"))]
        if matches:
            found.extend(matches)
            break
    if 2 <= len(name) <= 5:
        fuzzy = re.compile(".{0,2}".join(map(re.escape, name)))
        found.extend(record for record in records if fuzzy.search(_normalized(record.get("name"))))
    if "肘" in name and any(word in str(item.get("notes") or "") for word in ("去毛", "撩毛", "燎毛")):
        found.extend(record for record in records
                     if "肘" in str(record.get("name")) and "燎毛" in str(record.get("name")))
    candidate_ids = {id(record) for record in found}
    candidates = [record for record in records if id(record) in candidate_ids]

    if name == "豆腐":
        candidates = [record for record in candidates if record.get("category") == "豆制品"
                      and not any(word in str(record.get("name")) for word in
                                  ("干豆腐", "冻豆腐", "鱼豆腐", "千叶豆腐", "豆腐脑", "豆腐丝", "豆腐卷", "豆腐泡"))]
    if "鸡蛋" in name:
        candidates = [record for record in candidates if record.get("category") == "蛋类"]
    if name == "排骨":
        candidates = [record for record in candidates if record.get("category") == "肉制品"
                      and "牛排骨" not in str(record.get("name"))]
    if name == "菜花":
        candidates = [record for record in candidates if record.get("category") == "蔬菜"]
    if name == "酸菜":
        candidates = [record for record in candidates if not any(word in str(record.get("name"))
                      for word in ("调料", "酸菜馅", "酸菜酱", "鱼酸菜"))]
    if "萝卜丁咸菜" in name:
        candidates = [record for record in candidates
                      if "萝卜丁" in str(record.get("name")) and "咸菜" in str(record.get("name"))]
    if name == "鸡丸" and item.get("unit") in ("袋", "包", "大包", "小包"):
        candidates = [record for record in candidates if "袋" in str(record.get("name"))
                      or "袋" in str(record.get("spec"))]
    if len(name) >= 3:
        prefix = name[:2]
        if prefix not in ("玉米",) and any(_normalized(record.get("name")).startswith(prefix) for record in candidates):
            candidates = [record for record in candidates if _normalized(record.get("name")).startswith(prefix)]
    if name.startswith("红色"):
        candidates = [record for record in candidates if "红" in str(record.get("name"))]
    if item.get("unit") in ("袋", "包", "大包", "小包"):
        candidates = [record for record in candidates if not any(word in str(record.get("name"))
                      for word in ("罐头", "/罐", "罐装")) and str(record.get("spec")) != "罐"]
    return [record for record in candidates if _packaging_compatible(item, record)]


def _comparable(record):
    return price_basis(record)


def _price(record):
    try:
        result = Decimal(str(record["price"]))
    except (InvalidOperation, KeyError, TypeError) as exc:
        raise ValueError("目录候选价格无效") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError("目录候选价格无效")
    return result


def match_item(item, records, *, aliases=None, unit_rules=None):
    """Return a source-preserving match with complete plausible candidates.

    Explicit aliases map a source name to a current catalog code (or a
    record containing ``code``). Only aliases and exact normalized catalog
    names can select a SKU. For exact names with the same comparable spec,
    the highest price wins; equal-price different SKUs stay ambiguous.
    Semantic candidates never become a selected SKU without confirmation.
    """
    result = dict(item)
    result["issues"] = list(item.get("issues") or [])
    result.update(status="unmapped", selected=None, candidates=[], invoice_quantity=None, price_unit=None,
                  match_reason="")
    name = _normalized(item.get("name"))
    records = list(records)
    alias_target = (aliases or {}).get(item.get("name"))
    if alias_target is None:
        alias_target = (aliases or {}).get(name)
    if alias_target is not None:
        code = alias_target.get("code") if isinstance(alias_target, dict) else alias_target
        matches = [record for record in records if str(record.get("code")) == str(code)]
        if len(matches) != 1:
            result["status"] = "needs_review"
            result["candidates"] = _suggestions(item, records)
            result["issues"].append(_issue("stale_alias", "已确认别名的材料编号不在当前目录或不唯一"))
            return result
        candidates = matches
        selected = matches[0]
    else:
        candidates = [record for record in records if _normalized(record.get("name")) == name
                      and _packaging_compatible(item, record)]
        if candidates:
            groups = {_comparable(record) for record in candidates}
            if len(groups) != 1:
                result["status"] = "ambiguous"
                result["candidates"] = candidates
                result["issues"].append(_issue("different_specs", "同名商品的包装或规格不能按挂牌价直接比较"))
                return result
            price = max(_price(record) for record in candidates)
            top = [record for record in candidates if _price(record) == price]
            if len(top) != 1:
                result["status"] = "ambiguous"
                result["candidates"] = candidates
                result["issues"].append(_issue("tied_candidates", "等价候选最高价相同，仍需确认SKU"))
                return result
            selected = top[0]
        else:
            candidates = _suggestions(item, records)
            result["status"] = "ambiguous" if len(candidates) > 1 else "needs_review" if candidates else "unmapped"
            result["candidates"] = candidates
            if item.get("unit") == "箱" and candidates:
                reason = "仅见单品参考候选，缺每箱件数和每箱净重，不能将1箱当成1袋或计价单位"
            elif item.get("unit") in ("袋", "包") and candidates:
                reason = "袋/包规格有差异，缺每袋净重或包装关系，候选只供人工核对"
            elif not candidates and "萝卜丁咸菜" in name:
                reason = "目录未见同品种的萝卜丁咸菜；其他咸菜品种不可替代"
            else:
                reason = "商品仅有语义候选，需人工确认" if candidates else "当前目录没有合理候选"
            result["match_reason"] = reason
            result["issues"].append(_issue("semantic_review" if candidates else "unmapped", reason))
            return result
    result["candidates"] = candidates
    result["selected"] = selected
    try:
        converted = invoice_quantity(item.get("quantity"), item.get("unit"), selected, unit_rules)
    except ValueError as exc:
        result["status"] = "needs_review"
        result["issues"].append(_issue("quantity_conversion", str(exc)))
    else:
        result["status"] = "matched"
        result["invoice_quantity"] = converted["quantity"]
        result["price_unit"] = converted["price_unit"]
    return result
