"""Conservative catalog selection; never promote uncertain suggestions."""

from decimal import Decimal, InvalidOperation
import re
import unicodedata

from .units import invoice_quantity, price_basis


def _normalized(text):
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or ""))).casefold()
    return text.replace("耗油", "蚝油").replace("蒜薹", "蒜苔")


# Explicit food brands are constraints, including when a reference order is wrong.
# This vocabulary is a guard, not an exhaustive brand recognizer; AI must still
# inspect names and packaging for brands absent from this list.
KNOWN_BRANDS = ("海天", "东古", "太太乐", "中盐", "秋林", "农大", "金锣", "正大",
                "北大荒", "桃李", "海欣", "安井", "三全", "完达山", "伊利", "蒙牛",
                "李锦记", "美狮", "九三", "亨氏", "名扬", "香其", "甘竹", "白猫", "太阳岛")

ATTRIBUTE_GROUPS = (
    ('猪','牛','羊','鸡','鸭','鹅'),
    ('前肘','后肘'), ('前槽','后腿','里脊','五花'),
    ('鸡胸','鸡翅','鸡爪','鸡腿'), ('粉','粒','段'), ('肉丝','肉片','肉块'),
    ('鲜活','冷冻','熟制'), ('油豆角','架豆王','豇豆'),
    ('五色','七色'), ('罐装','袋装','盒装'),
)

PROCESSING_REQUESTS = ('去皮', '去骨', '去核', '切块', '切丝', '切片', '切段', '磨成粉', '去毛', '燎毛', '撩毛', '收拾好')
_MEASURE = re.compile(r'(\d+(?:\.\d+)?)\s*(公斤|千克|毫升|kg|ml|克|斤|两|升|g|l)(?![a-z])', re.I)
_MEASURE_FACTORS = {
    '公斤': ('mass_g', '1000'), '千克': ('mass_g', '1000'), 'kg': ('mass_g', '1000'),
    '克': ('mass_g', '1'), 'g': ('mass_g', '1'), '斤': ('mass_g', '500'), '两': ('mass_g', '50'),
    '毫升': ('volume_ml', '1'), 'ml': ('volume_ml', '1'), '升': ('volume_ml', '1000'), 'l': ('volume_ml', '1000'),
}


def _measures(text):
    """Normalize explicit sizes, without treating the order quantity as size."""
    return {(_MEASURE_FACTORS[m[2].lower()][0], Decimal(m[1]) * Decimal(_MEASURE_FACTORS[m[2].lower()][1]))
            for m in _MEASURE.finditer(_normalized(text))}


def _package_spec_conflict(record):
    """A name declaring one pack's size cannot contradict its measured spec."""
    name = _normalized(record.get('name'))
    package_size = re.search(_MEASURE.pattern + r'/[袋包盒瓶罐桶箱件个支块]', name, re.I)
    basis = price_basis(record)
    if not package_size or basis[0] not in ('mass_g', 'volume_ml'):
        return False
    declared = next(iter(_measures(package_size[0])))
    return declared != (basis[0], Decimal(basis[1]))


def constraint_conflicts(item, record):
    """Apply explicit demand constraints in both the name and trailing notes.

    Raw quantity measurements are not package specifications. Raw text is used
    only for state words that may follow the quantity in an unstructured input.
    A missing fresh/frozen/cooked declaration is not evidence of equivalence.
    """
    request = _normalized(str(item.get('name') or '') + ' ' + str(item.get('notes') or ''))
    actual = _normalized(record.get('name'))
    conflicts = []
    brand = brand_conflict(request, record)
    if brand:
        conflicts.append('品牌:' + brand)
    for brand in ('恒都','皓月','大庄园','翠花','佳农','中粮','庆丰','北冰洋'):
        if brand in request and brand not in actual:
            conflicts.append('品牌:' + brand)
    for group in ATTRIBUTE_GROUPS:
        wanted = [a for a in group if a in request]
        different = [a for a in group if a in actual and a not in wanted]
        if wanted and different and not all(a in actual for a in wanted):
            conflicts.append('属性:' + '/'.join(wanted) + '≠' + '/'.join(different))
    # Ordinary demand rules must not erase a qualifier written after the
    # quantity. These named varieties/thicknesses require positive catalog
    # evidence even when the chosen ordinary record declares no alternative.
    for qualifier in ('加厚', '绿帽', '红帽', '牛心', '小米蕉', '帝王蕉', '芭蕉',
                      '上五花', '下五花', '小木耳'):
        if qualifier in request and qualifier not in actual:
            conflicts.append('属性未证实:' + qualifier)
    state_text = request + _normalized(item.get('raw_text'))
    if re.search(r'不要|不用|别选|别用|别切|不选|不需要|不接受|勿用|勿选|拒绝|避开|排除', state_text):
        conflicts.append('属性未证实:原文否定或排除要求须单独核定，不能按正向词面选择')
    notes = _normalized(item.get('notes'))
    for requirement in re.findall(r'(?:只要|需要|指定|必须|要|选用)([^，,、;；。\n]+)', notes):
        target = re.split(r'但|而|并且|同时', requirement)[0].strip('的()（） ')
        if target and target not in actual + _normalized(record.get('spec')):
            conflicts.append('属性未证实:附加要求' + target)
    # Notes without an imperative are still requirements. Only independently
    # handled processing and explicit size clauses may be removed here; other
    # unmatched text stays pending rather than inheriting the generic choice.
    for clause in re.split(r'[，,、;；。\n()（）]', notes):
        # Remove only the independently checked size, not other attributes
        # appearing in that same clause (e.g. 要求：有机500g).
        target = re.sub(r'^(?:备注|要求|规格)[:：]?', '', _MEASURE.sub('', clause))
        # Selection/supply markers after the quantity have the same role as
        # the bounded expression grammar. Keep the complete qualifier; never
        # strip an unknown brand, processing word or negation to make it fit.
        connector = re.match(r'^(?:请按|只要|需要|指定|必须|选用|选|按|要|请)', target)
        if connector:
            target = target[connector.end():].removesuffix('供货')
        for processing in PROCESSING_REQUESTS:
            target = target.replace(processing, '')
        target = target.strip('的:： ')
        if target and target not in actual + _normalized(record.get('spec')):
            conflicts.append('属性未证实:备注' + target)
    for patterns, evidence, label in (
        (r'冷冻|冻的|要冻', r'冷冻|冻', '冷冻'),
        (r'熟制|熟的|要熟', r'熟制|熟', '熟制'),
        (r'鲜活|活的|要活', r'鲜活|活', '鲜活'),
    ):
        if re.search(patterns, state_text) and not re.search(evidence, actual):
            conflicts.append('属性未证实:' + label)
    if not _packaging_compatible(item, record):
        conflicts.append('包装不符合箱装要求')
    if item.get('unit') in ('袋','包','大包','小包') and re.search(r'罐头|/罐|罐装', actual):
        conflicts.append('袋装需求不能以罐装替代')
    combined = actual + _normalized(record.get('spec'))
    if '北极冰虾' in actual:
        def numeric_specs(text):
            return {s.replace('×', '*').replace('x', '*')
                    for s in re.findall(r'\d+[*×x]\d+', _normalized(text))}
        if not numeric_specs(item.get('notes')).issubset(numeric_specs(combined)):
            conflicts.append('规格:北极冰虾数字规格不一致')
    for dimension, value in _measures(request) - _measures(combined):
        conflicts.append('规格:' + str(value) + dimension)
    if _package_spec_conflict(record):
        conflicts.append('目录名称包装净含量与计价规格矛盾')
    return conflicts


def brand_conflict(request_name, record):
    requested = _normalized(request_name)
    actual = _normalized(record.get("name"))
    explicit = re.match(r'(?P<brand>[\u4e00-\u9fff]{2,10})牌', requested)
    if explicit and explicit.group('brand') not in actual:
        return explicit.group('brand')
    return next((brand for brand in KNOWN_BRANDS if brand in requested and brand not in actual), None)


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
    if name == "烟桂" and "去皮" in str(item.get("notes") or item.get("raw_text") or ""):
        found.extend(record for record in records if record.get("category") == "调料类"
                     and _normalized(record.get("name")) in {"桂皮", "(调料)桂皮"})
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
    if name == "罗马生菜":
        candidates = [record for record in candidates
                      if record.get("category") == "蔬菜" and _normalized(record.get("name")) == "生菜"]
    if name in {"5色糙米", "五色糙米"}:
        candidates = [record for record in candidates if _normalized(record.get("name")) in {"5色糙米", "五色糙米"}]
    if name == "旱黄瓜":
        # Only an unspecified fresh cucumber might satisfy the requested
        # variety; named varieties, preserved foods, and meat cannot.
        candidates = [record for record in candidates
                      if record.get("category") == "蔬菜"
                      and _normalized(record.get("name")) == "黄瓜"]
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
    candidates = [record for record in candidates if _packaging_compatible(item, record)]
    declared_measures = re.findall(r'\d+(?:\.\d+)?(?:kg|g|ml|l)(?![a-z])', name)
    if declared_measures:
        candidates = [record for record in candidates if all(
            measure in _normalized(record.get('name')) + _normalized(record.get('spec'))
            for measure in declared_measures)]
    requested_sizes = _measures(name + ' ' + str(item.get('notes') or ''))
    if requested_sizes and item.get('unit') in ('袋', '包', '瓶', '盒', '罐', '桶'):
        compatible = []
        for record in candidates:
            explicit_pack = re.search(_MEASURE.pattern + r'/[袋包瓶盒罐桶]',
                                      _normalized(record.get('name')), re.I)
            if not explicit_pack or _measures(explicit_pack[0]).issubset(requested_sizes):
                compatible.append(record)
        candidates = compatible
    # Other explicit brands remain excluded. A current unbranded, exact base
    # name is useful for asking the operator to verify an unavailable brand;
    # it is a review reference, never a selectable alias or a substitution.
    candidates = [record for record in candidates if not brand_conflict(name, record)]
    explicit_brand = re.match(r'([\u4e00-\u9fff]{2,10})牌(.+)', name)
    if explicit_brand:
        base_name = explicit_brand[2]
        for record in records:
            actual = _normalized(record.get('name'))
            if actual == base_name and not any(brand in actual for brand in KNOWN_BRANDS):
                candidates.append({**record, 'match_reason':
                                   '基础商品同名，但指定品牌未证；仅供核对，不可跨品牌替代'})
    return candidates


def _mutual_name_records(name, records):
    """Return records whose current catalog name explicitly lists ``name``.

    This is deliberately narrower than fuzzy matching: only a slash outside
    brackets, with two substantive name parts, is treated as a catalog-owned
    mutual name.  Packaging syntax such as ``250克/袋`` and arbitrary slash
    names therefore cannot become aliases by accident.
    """
    wanted = _normalized(name)
    found = []
    for record in records:
        raw = str(record.get('name') or '')
        depth = 0
        parts, start = [], 0
        for index, char in enumerate(raw):
            if char in '（(': depth += 1
            elif char in '）)': depth = max(0, depth - 1)
            elif char == '/' and depth == 0:
                parts.extend((raw[start:index], raw[index + 1:]))
                break
        if len(parts) != 2 or any(len(_normalized(part)) < 2 for part in parts):
            continue
        if wanted in {_normalized(part) for part in parts}:
            found.append(record)
    return found


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
                  match_reason="", product_status="unmapped", quantity_status="needs_review")
    name = _normalized(item.get("name"))
    records = list(records)
    normalized_aliases = [value for key, value in (aliases or {}).items() if _normalized(key) == name]
    targets = {str(value.get("code") if isinstance(value, dict) else value) for value in normalized_aliases}
    if len(targets) > 1:
        result["status"] = "ambiguous"
        result["candidates"] = [r for r in records if str(r.get("code")) in targets]
        result["issues"].append(_issue("conflicting_aliases", "规范化后的商品别名指向不同SKU，须核定"))
        return result
    alias_target = normalized_aliases[0] if normalized_aliases else None
    if isinstance(alias_target, dict) and alias_target.get("request_units") and item.get("unit") not in alias_target["request_units"]:
        alias_target = None
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
        conflict = brand_conflict(item.get("name"), selected)
        if conflict:
            result["status"] = "needs_review"
            result["candidates"] = _suggestions(item, records)
            result["issues"].append(_issue("brand_conflict", f"已确认别名与原文明确品牌{conflict}冲突，不沿用历史替代"))
            return result
    else:
        candidates = [record for record in records if _normalized(record.get("name")) == name
                      and _packaging_compatible(item, record)]
        mutual = [record for record in _mutual_name_records(name, records)
                  if _packaging_compatible(item, record)]
        if mutual:
            # An exact record and an explicit mutual-name record are both
            # real current catalog evidence; differing records remain
            # ambiguous instead of letting a slash alias override a SKU.
            candidates.extend(record for record in mutual if record not in candidates)
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
            explicit_brand = next((brand for brand in KNOWN_BRANDS if brand in name), None)
            if not candidates and explicit_brand:
                reason = f"未找到满足显式品牌{explicit_brand}的同品候选"
            elif re.match(r'[\u4e00-\u9fff]{2,10}牌', name) and candidates:
                reason = "当前目录未证明指定品牌；仅列同名基础商品核对参考，不可自动替代"
            elif name in {"5色糙米", "五色糙米"} and not candidates:
                reason = "目录未见同品种五色糙米；普通或七色糙米不能替代"
            elif name == "罗马生菜" and candidates:
                reason = "仅列泛称生菜候选；未证明是罗马品种，需人工确认"
            elif name == "旱黄瓜" and candidates:
                reason = "仅列泛称黄瓜候选；老/水果黄瓜品种不同，泛称黄瓜是否满足旱黄瓜需人工确认"
            elif name == "烟桂" and candidates:
                reason = "仅列泛称桂皮同类候选；烟桂品种与去皮要求未证实，需人工确认"
            elif item.get("unit") == "箱" and candidates:
                reason = "仅见单品参考候选，缺每箱件数和每箱净重，不能将1箱当成1袋或计价单位"
            elif item.get("unit") in ("袋", "包") and candidates:
                unit = item["unit"]
                request = _normalized(str(item.get("name") or "") + " " + str(item.get("notes") or ""))
                explicit_size = (re.search(_MEASURE.pattern + "/" + unit, request, re.I) or
                                 re.search("每" + unit + r"(?:净重|净含量|重量|规格)?(?:为|是|:)?" + _MEASURE.pattern,
                                           request, re.I))
                if explicit_size:
                    reason = "原文已明确每袋/包净重，目录供货包装或计价关系未证实，或规格不符；候选只供人工核对"
                    annotated = []
                    for record in candidates:
                        packaging = _normalized(str(record.get("name") or "") + " " + str(record.get("spec") or ""))
                        if _measures(record.get("spec")) and not re.search(r"[/每][袋包]|[袋包]装", packaging):
                            note = f'目录规格或计价基准为{record["spec"]}，未证明每袋/包净重及供货包装；仅供核对，不可自动替代'
                            previous = record.get("match_reason")
                            annotated.append({**record, "match_reason": f"{previous}；{note}" if previous else note})
                        else:
                            annotated.append(record)
                    result["candidates"] = annotated
                else:
                    reason = "袋/包规格有差异，缺每袋净重或包装关系，候选只供人工核对"
            elif not candidates and "萝卜丁咸菜" in name:
                reason = "目录未见同品种的萝卜丁咸菜；其他咸菜品种不可替代"
            else:
                reason = "商品仅有语义候选，需人工确认" if candidates else "当前目录没有合理候选"
            result["match_reason"] = reason
            result["issues"].append(_issue("semantic_review" if candidates else "unmapped", reason))
            if re.match(r'[\u4e00-\u9fff]{2,10}牌', name):
                result['issues'].append(_issue('brand_unverified', '原文明示品牌未在当前目录同品条目中证实，不能跨品牌替代'))
            if re.search(r'前腱|后腱', name):
                result['issues'].append(_issue('part_unverified', '原文明示部位未在当前目录中证实，不能以泛称或其他部位替代'))
            if re.search(r'\d+(?:\.\d+)?(?:kg|g|ml|l).*装', name):
                result['issues'].append(_issue('packaging_unverified', '原文整装规格在当前目录未证实，不能使用其他净重或散装条目'))
            return result
    conflicts = constraint_conflicts(item, selected)
    if conflicts:
        result.update(status='needs_review', product_status='needs_review', candidates=candidates)
        result['issues'].append(_issue('attribute_conflict', '当前目录未满足原文需求约束：' + '；'.join(conflicts)))
        return result
    result["candidates"] = candidates
    result["selected"] = selected
    result["product_status"] = "matched"
    for processing in PROCESSING_REQUESTS:
        if processing in str(item.get('notes') or '') and processing not in str(selected.get('name')):
            result['issues'].append(_issue('processing_unverified', f'原文要求{processing}，当前商品未证明包含该加工；保留身份待核加工及计价'))
    result["match_basis"] = ("historical_preference" if alias_target is not None else
                              "catalog_mutual_name" if selected in _mutual_name_records(name, records)
                              else "catalog_exact")
    if isinstance(alias_target, dict):
        result["mapping_evidence"] = alias_target.get("evidence", alias_target.get("reason"))
    try:
        converted = invoice_quantity(item.get("quantity"), item.get("unit"), selected, unit_rules)
    except ValueError as exc:
        result["status"] = "needs_review"
        result["issues"].append(_issue("quantity_conversion", str(exc)))
    else:
        result["status"] = "matched"
        result["quantity_status"] = "matched"
        result["invoice_quantity"] = converted["quantity"]
        result["price_unit"] = converted["price_unit"]
    if any(issue['code'] == 'processing_unverified' for issue in result['issues']):
        result['status'] = 'needs_review'
        # Preparation requirements are not proven by a plain catalog SKU.
        # Keep product identity and the original request visible, but leave
        # the final pricing quantity unresolved until the preparation basis
        # is confirmed.
        result['invoice_quantity'] = None
        result['price_unit'] = None
        result['quantity_status'] = 'needs_review'
        result['fulfillment_status'] = 'needs_review'
    return result
