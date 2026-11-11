"""Limited shared expression grammar; unknown words always remain in identity."""
import re

from .matching import _normalized, match_item, constraint_conflicts
from .money import decimal_value
from .units import price_basis


def _signature(text, record, rule):
    text = _normalized(text)
    if any(_normalized(compound) in text for compound in rule.get('protected_compounds', [])):
        return None
    labels = rule.get('category_labels', {}).get(record.get('category'), [])
    for label in sorted(labels, key=len, reverse=True):
        label = _normalized(label)
        text = text.replace('(' + label + ')', '')
        if text.startswith(label):
            text = text[len(label):]
        elif text.endswith(label):
            text = text[:-len(label)]
    for label, basis in rule.get('pricing_labels', {}).items():
        if price_basis(record) == tuple(basis):
            text = text.replace('(' + _normalized(label) + ')', '')
    spellings = { _normalized(word): attribute
                  for attribute, words in rule['attributes'].items() for word in words }
    attributes = []

    def take(match):
        attributes.append(spellings[match[0]])
        return ''

    pattern = '|'.join(re.escape(word) for word in sorted(spellings, key=len, reverse=True))
    # Parentheses delimit a literal catalog qualifier; their contents remain.
    text = re.sub(pattern, take, text).replace('(', '').replace(')', '')
    if len(text) < 2:
        return None
    return text, tuple(sorted(attributes))


def _request_variants(text, rule):
    """Compose literal demand phrases, never sort characters or drop a word.

    Both compositions are checked against today's catalog. If they identify
    different products (e.g. 干豆腐/豆腐干), the caller keeps the ambiguity.
    """
    text = _normalized(text)
    yield text
    grammar = rule.get('relations')
    if not grammar or re.search(r'不|勿|别|拒绝|排除|切|磨', text):
        return
    markers = '|'.join(re.escape(x) for x in grammar['markers'])
    aliases = grammar.get('modifier_aliases', {})
    # ``connectors`` are the infix forms (base + connector + qualifier).
    # Prefix/suffix forms are separate because the same word can be a
    # grammatical request marker at either edge of a phrase.  Keeping the
    # lists explicit prevents us from treating arbitrary words as removable
    # glue and silently changing product identity.
    connector_words = sorted(grammar.get('connectors', []), key=len, reverse=True)
    prefix_connector_words = sorted(grammar.get('prefix_connectors', []), key=len, reverse=True)
    suffix_connector_words = sorted(grammar.get('suffix_connectors', []), key=len, reverse=True)
    connectors = '|'.join(re.escape(x) for x in connector_words)

    def clean_qualifier(value):
        """Remove only configured relation grammar, never unknown identity words."""
        value = value.strip('，,、;；:：()（） ')
        value = value.removesuffix('的')
        for marker in sorted(grammar.get('markers', []), key=len, reverse=True):
            if value.endswith(marker):
                value = value[:-len(marker)].removesuffix('的')
                break
        return aliases.get(value, value)

    def clean_base(value):
        value = value.strip('，,、;；:：()（） ')
        for prefix in grammar.get('context_prefixes', []):
            if value.startswith(prefix) and len(value) - len(prefix) >= 2:
                value = value[len(prefix):]
                break
        return value

    def composed(base, qualifier):
        base = clean_base(base)
        qualifier = clean_qualifier(qualifier)
        if not base or len(base) < 2 or not qualifier:
            return
        # A configured connector/marker inside the qualifier means that the
        # request has another unparsed clause.  Keep the raw expression for
        # review rather than deleting the word and guessing a SKU.
        grammar_words = connector_words + prefix_connector_words + suffix_connector_words + list(grammar.get('markers', []))
        if any(word and word in qualifier for word in grammar_words):
            return
        yield base + qualifier
        yield qualifier + base

    # Prefix connector: ``选干的山楂``.  Try every boundary in the remaining
    # phrase, then let the current catalog prove which complete composition is
    # real.  No split is accepted merely because it looks linguistically nice.
    for connector in prefix_connector_words:
        if not text.startswith(connector):
            continue
        body = text[len(connector):]
        for boundary in range(1, len(body) - 1):
            yield from composed(body[boundary:], body[:boundary])

    # Suffix connector after a qualifier and base: ``干的山楂要``.  This is
    # deliberately separate from the ordinary infix ``山楂要干`` form.
    for connector in suffix_connector_words:
        if not text.endswith(connector):
            continue
        body = text[:-len(connector)]
        for boundary in range(1, len(body) - 1):
            yield from composed(body[boundary:], body[:boundary])

    # Infix connectors include expressions such as ``山楂选干的`` and
    # ``山楂请按干供货``.  Longest-first ordering keeps ``请按`` intact when
    # ``按`` is also configured as a valid connector.
    if connectors:
        relation = re.fullmatch(r'(.{2,}?)(?:' + connectors + r')(.+)', text)
    else:
        relation = None
    if relation:
        base, qualifier = relation.groups()
        yield from composed(base, qualifier)

    # These complete structures explicitly separate the base name and its
    # qualifier. Only grammatical words disappear; all identity words must
    # still match a complete current catalog name in one of the compositions.
    for pattern in (r'(.{2,}?)的(.+?)款',
                    r'要(.+?)的(.{2,})',
                    r'(.{2,}?)[，,]要求[:：]?(.+)'):
        structured = re.fullmatch(pattern, text)
        if structured:
            first, last = structured.groups()
            base, qualifier = (last, first) if pattern.startswith('要') else (first, last)
            qualifier = aliases.get(qualifier.removesuffix('的'), qualifier.removesuffix('的'))
            if qualifier:
                yield base + qualifier
                yield qualifier + base
    relation = re.fullmatch(r'(.{2,}?)(?:' + connectors + r')(.+)', text) if connectors else None
    if relation:
        base, qualifier = relation.groups()
        for prefix in grammar.get('context_prefixes', []):
            if base.startswith(prefix) and len(base) - len(prefix) >= 2:
                base = base[len(prefix):]
                break
        qualifier = qualifier.removesuffix('的')
        marker = re.search(r'(?:' + markers + r')$', qualifier)
        if marker:
            qualifier = qualifier[:marker.start()]
        qualifier = aliases.get(qualifier, qualifier)
        if qualifier and not re.search(connectors, qualifier):
            yield base + qualifier
            yield qualifier + base
            # An explicit part/variety clause may repeat a complete attribute
            # already contained in the literal product name.
            if marker and len(qualifier) >= 2 and qualifier in base:
                yield base
    tagged = re.fullmatch(r'(.{2,}?)(?:' + markers + r')(.{2,})', text)
    if tagged:
        first, last = tagged.groups()
        yield first + last
        yield last + first
    for word, canonical in aliases.items():
        for prefix in (word + '的', word):
            if text.startswith(prefix) and len(text) - len(prefix) >= 2:
                yield canonical + text[len(prefix):]


def match_expression(item, records, rules, *, unit_rules=None):
    """Require equal base name and all explicit attributes before price sorting."""
    for rule in rules:
        candidates = []
        interpretations = set()
        # The parser keeps a post-quantity requirement in ``notes`` (for
        # example ``蛋鸡1公斤要冷冻`` becomes name=蛋鸡, notes=要冷冻).
        # Reconstruct only the grammatical relation for expression matching;
        # the original item name, quantity and notes remain unchanged in the
        # returned row.  Unknown note words still have to survive the
        # catalog-grounded signature check and therefore cannot be erased.
        expression_texts = [item.get('name')]
        notes = _normalized(item.get('notes'))
        if notes and any(token in notes for token in (
                *rule.get('relations', {}).get('connectors', []),
                *rule.get('relations', {}).get('markers', []),
                *rule.get('relations', {}).get('prefix_connectors', []),
                *rule.get('relations', {}).get('suffix_connectors', []))):
            expression_texts.extend((
                _normalized(item.get('name')) + notes,
                _normalized(item.get('name')) + '，' + notes,
            ))
        variants = {variant for text in expression_texts
                    for variant in _request_variants(text, rule)}
        for record in records:
            actual = _signature(record['name'], record, rule)
            if actual is not None and any(_signature(text, record, rule) == actual for text in variants):
                candidates.append(record)
                interpretations.add(actual)
        if not candidates:
            continue
        eligible = [record for record in candidates if not constraint_conflicts(item, record)]
        # Keep witnesses for review even when none satisfies the complete
        # demand. Price sorting never decides which constraints to discard.
        if eligible:
            interpretations = {_signature(record['name'], record, rule) for record in eligible}
        evidence = dict(knowledge_rule_ids=[rule['id']], mapping_evidence=rule['evidence'],
                        match_basis='shared_expression')
        if len(interpretations) > 1:
            result = match_item(item, [], unit_rules=unit_rules)
            result.update(status='ambiguous', candidates=candidates, **evidence)
            result['issues'] = [issue for issue in result['issues'] if issue['code'] != 'unmapped']
            result['issues'].append(dict(code='expression_interpretation_ambiguous',
                                         message='需求关系可组成不同商品，不能按价格消除商品歧义'))
            return result
        selectable = eligible or candidates
        if len({price_basis(record) for record in selectable}) != 1:
            result = match_item(item, [], unit_rules=unit_rules)
            result.update(status='ambiguous', candidates=candidates, **evidence)
            result['issues'] = [issue for issue in result['issues'] if issue['code'] != 'unmapped']
            result['issues'].append(dict(code='different_specs', message='同一表达对应不同计价规格，不能直接按价格选择'))
            return result
        highest = max(decimal_value(record['price'], '挂牌价', positive=True) for record in selectable)
        top = [record for record in selectable if decimal_value(record['price'], '挂牌价', positive=True) == highest]
        if len(top) != 1:
            result = match_item(item, [], unit_rules=unit_rules)
            result.update(status='ambiguous', candidates=candidates, **evidence)
            result['issues'] = [issue for issue in result['issues'] if issue['code'] != 'unmapped']
            result['issues'].append(dict(code='tied_candidates', message='相同表达最高价并列，仍需核定SKU'))
            return result
        result = match_item(item, records, aliases={item['name']: {'code': top[0]['code']}},
                            unit_rules=unit_rules)
        result.update(candidates=candidates, **evidence)
        return result
    return None
