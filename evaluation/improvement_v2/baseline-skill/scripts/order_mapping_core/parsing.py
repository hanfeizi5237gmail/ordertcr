"""Traceable, deliberately conservative parser for text orders."""

from decimal import Decimal
import re


_NUM = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千]+点[零〇一二两三四五六七八九]+|[零〇一二两三四五六七八九十百千]+)"
_UNIT = r"(?:公斤|千克|毫升|大包|小包|kg|ml|g|斤|两|克|升|袋|包|盒|瓶|罐|桶|箱|件|个|支|块|副|捆|筐|条|颗|根)"
_DATE = re.compile(r"(?:\d{4}年)?\d{1,2}月\d{1,2}(?:日|号)?(?:\s*(?:到|至|[-~～])\s*\d{1,2}(?:日|号)?)?(?:早上|上午|下午|晚上)?")
_SEPARATORS = re.compile(r"[，,、；;\n]")
_SPLIT_SPACE = re.compile(r"\s+")
_ENDED_ITEM = re.compile(rf"{_NUM}\s*{_UNIT}\s*$", re.I)
_STARTED_ITEM = re.compile(rf"^\S.+?{_NUM}\s*{_UNIT}(?:\s|$)", re.I)
_CNUM = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _number(text):
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


def _fragments(line, offset):
    start = 0
    for boundary in _SEPARATORS.finditer(line):
        yield from _spaced_fragment(line[start:boundary.start()], offset + start)
        start = boundary.end()
    yield from _spaced_fragment(line[start:], offset + start)


def _spaced_fragment(part, offset):
    # Space separates two complete items, but spaces inside a product name
    # (e.g. 烟桂 1斤) remain part of that item's source span.
    separators = [match for match in _SPLIT_SPACE.finditer(part)
                  if _ENDED_ITEM.search(part[:match.start()])
                  and _STARTED_ITEM.search(part[match.end():])]
    starts = [0] + [match.end() for match in separators]
    ends = [match.start() for match in separators] + [len(part)]
    for begin, end in zip(starts, ends):
        fragment = part[begin:end]
        stripped = fragment.strip()
        if stripped:
            left = len(fragment) - len(fragment.lstrip())
            yield stripped, offset + begin + left, offset + begin + left + len(stripped)


def _parse_item(raw, source_name, source_line, start, end, context, index):
    # Choose the rightmost plausible quantity with nonempty product text. A
    # leading number in 5色糙米 or a Chinese numeral in 一品鲜 are part of the
    # item name, and are not consumed unless followed by a unit/position.
    matches = []
    for match in re.finditer(rf"(?P<quantity>{_NUM})\s*(?P<unit>{_UNIT})?", raw, re.I):
        if match.start() and raw[:match.start()].strip() and (match.group("unit") or not raw[match.end():].strip()):
            matches.append(match)
    issues = []
    if matches:
        match = matches[-1]
        name = raw[:match.start()].strip()
        quantity = _number(match.group("quantity"))
        unit = match.group("unit")
        notes = raw[match.end():].strip()
        if not unit:
            issues.append({"code": "missing_unit", "message": "原文数量缺少单位"})
    else:
        name, quantity, unit, notes = raw, None, None, ""
        issues.append({"code": "missing_quantity", "message": "原文没有可确定的数量"})
        packaging = re.search(r"(大袋的|小袋的|大包的|小包的)$", name)
        if packaging:
            name = name[:packaging.start()].strip()
            notes = packaging.group()
    processing = re.search(r"(去毛|撩毛|燎毛|切块|收拾好|去皮)$", name)
    if processing and name[:processing.start()].strip():
        name = name[:processing.start()].strip()
        notes = "、".join(part for part in (processing.group(), notes) if part)
    event = None
    target_station = None
    addition = re.fullmatch(r"(?:(?P<station>[\u4e00-\u9fff]+站))?再加(?P<reference>这样的)?(?P<name>.+)", name)
    if addition:
        event = "追加"
        target_station = addition.group("station")
        name = addition.group("name").strip()
        notes = "、".join(part for part in (addition.group("reference"), notes) if part)
    return {"line_id": f"{source_name}:{source_line}:{index}", "raw_text": raw,
            "source_name": source_name, "source_line": source_line,
            "char_start": start, "char_end": end, "name": name,
            "quantity": quantity, "unit": unit, "notes": notes,
            "date_context": context, "event": event, "target_station": target_station,
            "issues": issues}


def parse_chat(text, source_name="chat.txt"):
    """Parse item spans with 0-based half-open character offsets.

    Date headings change the raw date context but are not products; this
    function does not infer a year or merge duplicate product lines.
    Missing quantities and units are explicit issues rather than guesses.
    """
    if not isinstance(text, str):
        raise ValueError("聊天内容必须为文本")
    result = []
    context = None
    offset = 0
    for line_number, line in enumerate(text.splitlines(keepends=True), 1):
        body = line.rstrip("\r\n")
        heading = body.strip()
        date_match = _DATE.search(heading)
        if date_match and (re.fullmatch(r"\d{1,2}月\d{1,2}(?:日|号)?", heading)
                           or heading.startswith(date_match.group()) and any(word in heading for word in ("早上的菜", "当天的菜", "到货"))):
            context = date_match.group()
            offset += len(line)
            continue
        for raw, start, end in _fragments(body, offset):
            result.append(_parse_item(raw, source_name, line_number, start, end, context, len(result) + 1))
        offset += len(line)
    return result
