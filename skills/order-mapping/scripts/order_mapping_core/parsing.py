"""Traceable, deliberately conservative parser for text orders."""

from decimal import Decimal
import re
import unicodedata


_NUM = r"(?:\d+/\d+|\d+(?:\.\d+)?|半|[零〇一二两三四五六七八九十百千]+点[零〇一二两三四五六七八九]+|[零〇一二两三四五六七八九十百千]+)"
_UNIT = r"(?:公斤|千克|毫升|大包|小包|kg|ml|g|斤|两|克|升|袋|包|盒|瓶|罐|桶|箱|件|个|支|块|副|捆|筐|条|颗|根)"
_DATE = re.compile(r"(?:\d{4}年)?\d{1,2}月\d{1,2}(?:日|号)?(?:\s*(?:到|至|[-~～])\s*\d{1,2}(?:日|号)?)?(?:早上|上午|下午|晚上)?")
_SEPARATORS = re.compile(r"[，,、；;\n]|(?<!\d)/")
_SPLIT_SPACE = re.compile(r"\s+")
_ENDED_ITEM = re.compile(rf"{_NUM}\s*{_UNIT}\s*$", re.I)
_STARTED_ITEM = re.compile(rf"^\S.+?{_NUM}\s*{_UNIT}(?:\s|$)", re.I)
_CNUM = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_REQUIREMENT_START = r'(?:要求[:：]?|备注[:：]?|只要|需要|必须|要|按|切片|切丝|切块|切段|去皮|去骨|去核|磨成粉)'


def _number(text):
    text = unicodedata.normalize('NFKC', text).strip().replace('−', '-').replace('负', '-')
    if text.startswith('-'):
        return '-' + _number(text[1:].strip())
    if text == '半':
        return '0.5'
    if '/' in text:
        numerator, denominator = text.split('/', 1)
        if not int(denominator):
            raise ValueError('数量分母不能为零')
        return format((Decimal(numerator) / Decimal(denominator)).normalize(), 'f')
    if text[0].isdigit():
        return format(Decimal(text).normalize(), "f")
    if "点" in text:
        integer, fractional = text.split("点", 1)
        return f"{_number(integer)}.{''.join(str(_CNUM[digit]) for digit in fractional)}"
    if "千" in text or "百" in text or "十" in text:
        remaining = text
        total = 0
        for mark, scale in (("千", 1000), ("百", 100), ("十", 10)):
            if mark in remaining:
                part, remaining = remaining.split(mark, 1)
                total += (_CNUM[part] if part else 1) * scale
        remaining = remaining.lstrip("零〇")
        if remaining and remaining not in _CNUM:
            raise ValueError("中文数量表达无法确定")
        return str(total + (_CNUM[remaining] if remaining else 0))
    return str(_CNUM[text])


def _bracket_ranges(text):
    """Return ranges for balanced round brackets in one source line."""
    pairs = {')': '(', '）': '（'}
    stack, ranges = [], []
    for position, char in enumerate(text):
        if char in ('(', '（'):
            stack.append((char, position))
        elif char in pairs and stack and stack[-1][0] == pairs[char]:
            _, start = stack.pop()
            ranges.append((start, position + 1))
    return ranges


def _slash_is_protected(line, position, product_names, bracket_ranges):
    if any(start <= position < end for start, end in bracket_ranges):
        return True
    for name in product_names:
        if '/' not in name:
            continue
        start = line.find(name)
        while start >= 0:
            if start <= position < start + len(name):
                return True
            start = line.find(name, start + 1)
    return False


def _fragments(line, offset, product_names=()):
    start = 0
    bracket_ranges = _bracket_ranges(line)
    for boundary in _SEPARATORS.finditer(line):
        if boundary.group() == '/' and _slash_is_protected(
                line, boundary.start(), product_names, bracket_ranges):
            continue
        # “商品，数量2斤” is one demand, with a labeled quantity clause.
        if re.match(rf'\s*数量\s*[-－−负]?\s*{_NUM}',line[boundary.end():]):
            continue
        if re.match(rf'\s*(?:(?:订购)?数量\s*(?:待确认|未定)|订购\s*(?:数量\s*)?{_NUM})', line[boundary.end():]):
            continue
        if re.match(r'\s*(?:换另一种包装|每袋|每包|切块|去皮)',line[boundary.end():]):
            continue
        if re.match(r'\s*请勿重复下单[:：]', line[boundary.end():]):
            continue
        clause = re.split(r'[，,、；;\n]', line[boundary.end():], maxsplit=1)[0].strip()
        # A labeled requirement belongs to this demand even if its quantity
        # appears at the end of the clause. Later specification measurements
        # must not become a second order or replace an earlier order quantity.
        if (line[start:boundary.start()].strip() and
                re.match(r'(?:要求|备注|规格)[:：]?', clause)):
            continue
        # A preparation/qualifier clause without its own order quantity belongs
        # to the preceding demand. A new quantified product remains separate.
        if (line[start:boundary.start()].strip() and
                not re.search(rf'{_NUM}\s*{_UNIT}', clause, re.I) and
                (re.match(_REQUIREMENT_START, clause) or
                 re.fullmatch(r'(?:别要|不要|不用|别选|不选|不接受|勿用).+的', clause))):
            continue
        yield from _spaced_fragment(line[start:boundary.start()], offset + start, product_names)
        start = boundary.end()
    yield from _spaced_fragment(line[start:], offset + start, product_names)


def _spaced_fragment(part, offset, product_names=()):
    # Space separates two complete items, but spaces inside a product name
    # (e.g. 烟桂 1斤) remain part of that item's source span.
    separators = [match for match in _SPLIT_SPACE.finditer(part)
                  if _ENDED_ITEM.search(part[:match.start()])
                  and (_STARTED_ITEM.search(part[match.end():]) or
                       re.match(rf'{_NUM}\s*{_UNIT}(?:的)?[\u4e00-\u9fff]', part[match.end():], re.I))]
    starts = [0] + [match.end() for match in separators]
    ends = [match.start() for match in separators] + [len(part)]
    for begin, end in zip(starts, ends):
        fragment = part[begin:end]
        stripped = fragment.strip()
        if stripped:
            left = len(fragment) - len(fragment.lstrip())
            origin = offset + begin + left
            if re.search(rf'{_NUM}\s*(?:{_UNIT})?\s*(?:或者|或|至|到|[-~～])\s*{_NUM}', stripped):
                yield stripped, origin, origin + len(stripped)
                continue
            prefix = re.match(rf'-?{_NUM}\s*{_UNIT}(?:的)?', stripped, re.I)
            if prefix and product_names:
                # A quantity-first item has no trailing quantity boundary.
                # Resolve its lexical end only from current business vocabulary.
                for name in product_names:
                    end_name = prefix.end() + len(name)
                    if stripped[prefix.end():end_name] != name:
                        continue
                    annotation = re.match(r'\s*[（(][^()（）]*[)）]', stripped[end_name:])
                    if annotation:
                        end_name += annotation.end()
                    following_name = any(stripped[end_name:].startswith(other) for other in product_names)
                    if (following_name and end_name < len(stripped)
                            and re.search(rf'{_NUM}\s*{_UNIT}', stripped[end_name:], re.I)):
                        yield stripped[:end_name], origin, origin + end_name
                        yield from _spaced_fragment(stripped[end_name:], origin + end_name, product_names)
                        break
                else:
                    prefix = None
                if prefix:
                    continue
            # An adjacent completed quantity followed by another named item
            # is a boundary; packaging numbers inside parentheses are not.
            cuts = [0]
            for match in re.finditer(rf'{_NUM}\s*{_UNIT}', stripped, re.I):
                after = match.end()
                next_is_quantity_first = re.match(rf'{_NUM}\s*{_UNIT}(?:的)?[\u4e00-\u9fff]', stripped[after:], re.I)
                if (match.start() > 0 and after < len(stripped)
                        and (re.match(r'[^\W\d_]', stripped[after]) or next_is_quantity_first)
                        and re.search(rf'{_NUM}\s*{_UNIT}', stripped[after:], re.I)
                        and not re.match(r'(?:整(?:袋|包|箱)装|每袋|每包|每箱|数量)', stripped[after:])
                        and not re.match(_REQUIREMENT_START, stripped[after:])
                        and not _inside_brackets(stripped, match.start())):
                    cuts.append(after)
            cuts.append(len(stripped))
            for first, last in zip(cuts, cuts[1:]):
                piece = stripped[first:last]
                if piece:
                    yield piece, origin + first, origin + last


def _inside_brackets(raw, position):
    before = raw[:position]
    return sum(before.count(opening) - before.count(closing)
               for opening, closing in (('(', ')'), ('（', '）'))) > 0


def _parse_item(raw, source_name, source_line, start, end, context, index, product_names=()):
    # Choose the rightmost plausible quantity with nonempty product text. A
    # leading number in 5色糙米 or a Chinese numeral in 一品鲜 are part of the
    # item name, and are not consumed unless followed by a unit/position.
    matches = []
    protected_end = next((len(name) for name in product_names
                          if raw.startswith(name) and re.search(r'\d', name)), 0)
    pattern = re.compile(rf"(?P<quantity>[-－−负]?\s*{_NUM})\s*(?P<unit>{_UNIT})?", re.I)
    for match in pattern.finditer(raw, protected_end):
        if (_inside_brackets(raw, match.start()) or
                re.search(r'(?:规格|每袋|每包|每箱)[:：]?\s*$', raw[:match.start()])):
            continue
        if match.start() and raw[:match.start()].strip() and (match.group("unit") or not raw[match.end():].strip()):
            matches.append(match)
    prefix = re.match(rf'(?P<quantity>[-－−负]?\s*{_NUM})\s*(?P<unit>{_UNIT})(?:的)?(?P<name>[\u4e00-\u9fff].*)$', raw, re.I)
    prefix_requirement = (prefix and matches and all(
        re.search(r'[，,、；;]\s*(?:要求|备注)[:：]?', raw[prefix.start('name'):m.start()])
        for m in matches))
    issues = []
    if matches and not prefix_requirement:
        match = matches[-1]
        # Later measurements in a labeled requirement are specifications, not
        # a replacement for the earlier explicit order quantity.
        for earlier in matches[:-1]:
            if re.match(r'\s*' + _REQUIREMENT_START, raw[earlier.end():]):
                match = earlier
                break
        name = raw[:match.start()].strip()
        name = re.sub(r'[，,、；;\s]*(?:(?:订购)?数量|订购)\s*$','',name).strip()
        # A colon immediately before the quantity labels the quantity. Keep
        # colons that are part of a known catalog name or occur internally.
        if name not in product_names:
            name = name.rstrip(':：').rstrip()
        try:
            quantity = _number(match.group("quantity"))
        except (ValueError, KeyError, ArithmeticError):
            quantity = None
            issues.append({'code': 'invalid_quantity', 'message': '原文数字表达无效，保留商品和单位待核'})
        unit = match.group("unit")
        notes = raw[match.end():].strip()
        if not unit:
            issues.append({"code": "missing_unit", "message": "原文数量缺少单位"})
    elif prefix:
        name = prefix.group('name').strip()
        try:
            quantity = _number(prefix.group('quantity'))
        except (ValueError, KeyError, ArithmeticError):
            quantity = None
            issues.append({'code': 'invalid_quantity', 'message': '原文数字表达无效，保留商品和单位待核'})
        unit = prefix.group('unit')
        notes = ''
        if prefix_requirement:
            requirement = re.search(r'[，,、；;]\s*(?:要求|备注)[:：]?', name)
            notes = name[requirement.start():].lstrip('，,、；; ')
            name = name[:requirement.start()].strip()
    else:
        name, quantity, unit, notes = raw, None, None, ""
        unknown_quantity = re.search(r'[，,、；;]\s*(?:订购)?数量\s*(?:待确认|未定)\s*$', name)
        if unknown_quantity:
            notes = name[unknown_quantity.start():].lstrip('，,、；; ')
            name = name[:unknown_quantity.start()].strip()
        issues.append({"code": "missing_quantity", "message": "原文没有可确定的数量"})
        invalid = re.fullmatch(rf'(?P<name>.+?)(?P<value>[xX?？]+)\s*(?P<unit>{_UNIT})', raw)
        if invalid:
            name, unit = invalid.group('name'), invalid.group('unit')
            issues = [{"code": "invalid_quantity", "message": "原文数量不是有效数值，保留商品与单位待核"}]
        packaging = re.search(r"(大袋的|小袋的|大包的|小包的)$", name)
        if packaging:
            name = name[:packaging.start()].strip()
            notes = packaging.group()
        if unit is None:
            issues.append({"code": "missing_unit", "message": "原文没有可确定的数量单位"})
    # A labeled size before the order quantity belongs to the requirement,
    # not the product's lexical name. Preserve it in notes so current-catalog
    # size validation still applies; no unlabeled name text is discarded.
    specification = re.search(
        r'[，,、；;\s]*规格[:：]?\s*\d+(?:\.\d+)?\s*(?:公斤|千克|毫升|kg|ml|克|斤|两|升|g|l)\s*$',
        name, re.I)
    if specification and name[:specification.start()].strip():
        notes = '、'.join(part for part in (specification.group().lstrip('，,、；; '), notes) if part)
        name = name[:specification.start()].strip()
    if quantity is not None and Decimal(quantity) <= 0:
        issues.append({"code": "invalid_quantity", "message": "原文数量不大于零，保留原值待核，不能计价"})
    processing_note = re.search(r'[（(](去皮|去骨|去核|切块|切丝|切片)[)）]$', name)
    if processing_note:
        name = name[:processing_note.start()].strip()
        notes = '、'.join(part for part in (processing_note.group(1), notes) if part)
    processing = re.search(r"(去毛|撩毛|燎毛|切块|切段|磨成粉|收拾好|去皮)$", name)
    if processing and name[:processing.start()].strip():
        name = name[:processing.start()].strip()
        notes = "、".join(part for part in (processing.group(), notes) if part)
    preparation_prefix = re.fullmatch(r'(切段|磨成粉)的(.{2,})', name)
    if preparation_prefix:
        name = preparation_prefix.group(2)
        notes = '、'.join(part for part in (preparation_prefix.group(1), notes) if part)
    if name == "厚皮鸡蛋" and "厚皮" not in notes:
        notes = "、".join(part for part in ("品质要求：厚皮", notes) if part)
    event = None
    target_station = None
    addition = re.fullmatch(r"(?:(?P<station>[\u4e00-\u9fff]+站))?再加(?P<reference>这样的)?(?P<name>.+)", name)
    if addition:
        event = "追加"
        target_station = addition.group("station")
        name = addition.group("name").strip()
        notes = "、".join(part for part in (addition.group("reference"), notes) if part)
    predecessor_clue = None
    if event == '追加':
        predecessor_clue = '这样的' if '这样的' in notes else name
    suffix_addition = re.fullmatch(r'(?P<name>.+?)(?:再追加|追加|再加|再增加|增加)', name)
    if suffix_addition:
        event, name = '追加', suffix_addition.group('name')
        predecessor_clue = name
    amendment_scope = None
    change_pattern = r'(?P<name>.+?)(?P<total>总量|总共|合计)?(?:刚才改(?:成|为|到)?|改(?:成|为|到))'
    change = re.fullmatch(change_pattern, name)
    if change:
        event, predecessor_clue, name = '改量', change.group('name'), change.group('name')
        amendment_scope = 'total' if change.group('total') else None
    duplicate = re.fullmatch(r'重复转发上一条[，,]\s*请勿重复下单[:：](?P<name>.+)', name)
    if duplicate:
        event, name, predecessor_clue = '重复', duplicate.group('name'), '上一条'
    cancellation = re.fullmatch(r'(?:取消|不要)(?P<name>.+)', name)
    if cancellation:
        event, predecessor_clue, name = '取消', cancellation.group('name'), cancellation.group('name')
    suffix_cancellation = re.fullmatch(r'(?P<name>.+?)(?:刚才那条|刚才的那条)?(?:取消|不要了|算了)', name)
    if suffix_cancellation:
        event = '取消'
        name = suffix_cancellation.group('name').strip()
        predecessor_clue = name
    uncertain_number = re.search(rf'{_NUM}\s*(?:{_UNIT})?\s*(?:或者|或|至|到|[-~～])\s*{_NUM}', raw)
    if uncertain_number:
        uncertain_change = re.match(change_pattern, raw)
        uncertain_cancel = re.match(r'(?:取消|不要)(?P<name>.+?)' + _NUM, raw)
        if uncertain_change:
            name, event = uncertain_change.group('name'), '改量'
            amendment_scope = 'total' if uncertain_change.group('total') else None
        elif uncertain_cancel:
            name, event = uncertain_cancel.group('name'), '取消'
        else:
            name = raw[:uncertain_number.start()].strip()
        quantity, unit = None, None
        issues.append({'code': 'invalid_quantity', 'message': '原文数量是范围或备选值，不能选取其中一个计价'})
        predecessor_clue = name if event in ('改量', '取消') else predecessor_clue
    return {"line_id": f"{source_name}:{source_line}:{index}", "raw_text": raw,
            "source_name": source_name, "source_line": source_line,
            "char_start": start, "char_end": end, "name": name,
            "quantity": quantity, "unit": unit, "notes": notes,
            "date_context": context, "event": event, "target_station": target_station,
            "predecessor_clue": predecessor_clue, "amendment_scope": amendment_scope,
            "issues": issues}


def parse_chat(text, source_name="chat.txt", company_aliases=None, product_names=()):
    """Parse item spans with 0-based half-open character offsets.

    Date headings change the raw date context but are not products; this
    function does not infer a year or merge duplicate product lines.
    Missing quantities and units are explicit issues rather than guesses.
    """
    if not isinstance(text, str):
        raise ValueError("聊天内容必须为文本")
    if not isinstance(product_names, (list, tuple, set, frozenset)) or not all(isinstance(n, str) and n for n in product_names):
        raise ValueError('product_names须为当前目录或正式知识名称数组')
    product_names = sorted(set(product_names), key=lambda name: (-len(name), name))
    if company_aliases is None:
        known_companies = set()
    elif isinstance(company_aliases, (list, tuple, set, frozenset)) and all(
            isinstance(name, str) and name.strip() for name in company_aliases):
        known_companies = {name.strip() for name in company_aliases}
    else:
        raise ValueError('company_aliases必须是非空名称列表')

    def recognized_company(name):
        return name in known_companies or bool(re.fullmatch(r'[\u4e00-\u9fff]+站', name))

    result = []
    context = None
    station_context = None
    offset = 0
    for line_number, line in enumerate(text.splitlines(keepends=True), 1):
        body = line.rstrip("\r\n")
        heading = body.strip()
        company_label = re.fullmatch(r'(?:所属公司|公司)\s*[:：]\s*(\S.+|\S)', heading)
        if company_label:
            station_context = company_label.group(1).strip()
            offset += len(line)
            continue
        day_label = re.fullmatch(r'(?:发货日期|配送日期|订单日期|日期)\s*[:：]\s*(\d{4}-\d{2}-\d{2}|(?:\d{4}年)?\d{1,2}月\d{1,2}(?:日|号)?)', heading)
        if day_label:
            context = day_label.group(1)
            offset += len(line)
            continue
        if re.fullmatch(r'本批确认日期均为\d{4}年(?:[，,]\s*当前目录为[^。\n]+)?[。]?', heading):
            # Informational text never activates a catalog or alters a rule.
            offset += len(line)
            continue
        if re.fullmatch(r'各(?:单位|公司|站点)分开(?:记账|下单)[，,]\s*(?:下面公司名和配送日期适用于其后订单|日期与公司以文本为准)[。]?', heading):
            offset += len(line)
            continue
        combined_heading = re.match(r'^\s*(?P<company>[^:：\s]+)\s+(?P<day>\d{4}-\d{2}-\d{2})\s*[:：]',body)
        combined_offset=0
        if combined_heading:
            station_context=combined_heading.group('company')
            context=combined_heading.group('day')
            combined_offset=combined_heading.end()
            body=body[combined_offset:]
            heading=body.strip()
        station_heading = re.fullmatch(r'【(?P<company>[^【】]+)】', heading)
        if station_heading:
            station_context = station_heading.group('company')
            offset += len(line)
            continue
        # Chat exports often put a station label on its own line as
        # “星月站下单”/“星月站订单”, without a colon or brackets.  Treat this
        # as routing context only; it is never a product event.
        plain_station = re.fullmatch(r'(?P<company>[\u4e00-\u9fff]+站)(?:下单|订单)?', heading)
        if plain_station:
            station_context = plain_station.group('company')
            offset += len(line)
            continue
        inline_station = re.match(r'^\s*(?P<company>[^:：\s]+)[:：]', body)
        if inline_station and (recognized_company(inline_station.group('company')) or
                               re.search(r'公司|站|餐厅|酒店$',inline_station.group('company'))):
            station_context = inline_station.group('company')
            body = body[inline_station.end():]
            body_offset = offset + inline_station.end()
        else:
            body_offset = offset + combined_offset
        date_match = _DATE.search(heading)
        if (re.fullmatch(r'\d{1,2}\.\d{1,2}|\d{4}-\d{2}-\d{2}', heading)
                or date_match and (re.fullmatch(r'(?:\d{4}年)?\d{1,2}月\d{1,2}(?:日|号)?', heading)
                           or heading.startswith(date_match.group()) and any(word in heading for word in ("早上的菜", "当天的菜", "到货")))):
            context = date_match.group() if date_match else heading
            offset += len(line)
            continue
        each = re.fullmatch(r'\s*(?P<first>[^、，,；;]+)[、，,](?P<second>[^、，,；;]+?)各(?P<quantity>[-－−负]?\s*' + _NUM + r')(?P<unit>' + _UNIT + r')?\s*', body, re.I)
        if each:
            for key in ('first', 'second'):
                begin, end = each.span(key)
                if key == 'second':
                    end = each.end('unit') if each.group('unit') else each.end('quantity')
                raw = body[begin:end]
                row = _parse_item(raw, source_name, line_number, body_offset + begin, body_offset + end, context, len(result) + 1)
                row['name'] = each.group(key).strip()
                try:
                    row['quantity'] = _number(each.group('quantity'))
                except (ValueError, KeyError, ArithmeticError):
                    row['quantity'] = None
                row['unit'] = each.group('unit')
                row['shared_quantity_source_span'] = (body_offset + each.start('quantity'),
                                                      body_offset + (each.end('unit') if row['unit'] else each.end('quantity')))
                row['issues'] = [] if row['unit'] else [{'code': 'missing_unit', 'message': '原文数量缺少单位'}]
                if row['quantity'] is None or Decimal(row['quantity']) <= 0:
                    row['issues'].append({'code': 'invalid_quantity', 'message': '共享数量不大于零，保留原值待核，不能计价'})
                row['target_station'] = station_context
                result.append(row)
        else:
            for raw, start, end in _fragments(body, body_offset, product_names):
                company_switch = re.match(r'^\s*(?P<company>[^:：\s]+)[:：]\s*', raw)
                if company_switch and (recognized_company(company_switch.group('company')) or
                                       re.search(r'公司|站|餐厅|酒店$', company_switch.group('company'))):
                    station_context = company_switch.group('company')
                    raw = raw[company_switch.end():]
                    start += company_switch.end()
                    if not raw:
                        continue
                row = _parse_item(raw, source_name, line_number, start, end, context, len(result) + 1,
                                  product_names)
                row['target_station'] = row['target_station'] or station_context
                # A common chat pattern splits one product event at a comma:
                # “鲜百合片，数量未定”.  Keep the quantity-missing clause in
                # the same source event so the full original text, span and
                # exception annotation remain traceable.
                if (result and result[-1].get('source_line') == line_number and
                        re.match(r'^(?:多少|数量|待定|未定|待确认)', row.get('name', '')) and
                        result[-1].get('quantity') is None):
                    previous = result[-1]
                    previous['char_end'] = end
                    previous['raw_text'] = text[previous['char_start']:end]
                    continue
                result.append(row)
        offset += len(line)
    return result
