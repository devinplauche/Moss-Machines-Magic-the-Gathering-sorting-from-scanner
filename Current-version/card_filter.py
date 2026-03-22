"""Metadata-first candidate filtering for card recognition."""

import difflib

from typing import Dict, List, Tuple, Any


class CardFilter:
    """Apply toggleable metadata filters before pHash matching."""

    DEFAULT_CONFIG = {
        'enabled': True,
        'name': True,
        'color_identity': True,
        'cmc': True,
        'set': True,
        'collector_number': True,
        'type': True,
        'subtype': True,
    }

    SCORE_WEIGHTS = {
        'name': 100,
        'collector_number': 90,
        'set': 80,
        'cmc': 70,
        'color_identity': 30,
        'type': 15,
        'subtype': 10,
    }

    def __init__(self, config: Dict[str, bool] = None):
        self.config = dict(self.DEFAULT_CONFIG)
        if config:
            self.config.update(config)

    def _levenshtein(self, a: str, b: str, max_distance: int = 2) -> int:
        """Bounded Levenshtein distance for OCR-tolerant name fallback."""
        if a == b:
            return 0
        if abs(len(a) - len(b)) > max_distance:
            return max_distance + 1

        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, start=1):
            curr = [i]
            row_min = i
            for j, cb in enumerate(b, start=1):
                cost = 0 if ca == cb else 1
                curr.append(min(
                    prev[j] + 1,
                    curr[j - 1] + 1,
                    prev[j - 1] + cost,
                ))
                row_min = min(row_min, curr[-1])
            if row_min > max_distance:
                return max_distance + 1
            prev = curr
        return prev[-1]

    def _normalize_colors(self, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            tokens = [str(v).strip().upper() for v in value]
        else:
            text = str(value).upper()
            text = text.replace('/', ',').replace('|', ',').replace(';', ',')
            tokens = [p.strip() for p in text.split(',') if p.strip()]

        normalized = []
        mapping = {
            'WHITE': 'W',
            'BLUE': 'U',
            'BLACK': 'B',
            'RED': 'R',
            'GREEN': 'G',
            'COLORLESS': 'COLORLESS',
            'MULTI': 'MULTI',
            'MULTICOLOR': 'MULTI',
        }
        for t in tokens:
            if t in {'W', 'U', 'B', 'R', 'G'}:
                normalized.append(t)
            elif t in mapping:
                normalized.append(mapping[t])
            elif len(t) > 1 and all(ch in {'W', 'U', 'B', 'R', 'G'} for ch in t):
                normalized.extend(list(t))

        return sorted(set(normalized))

    def _parse_cmc(self, value: Any):
        if value is None:
            return None
        try:
            return int(float(value))
        except Exception:
            return None

    def _get_card_id(self, card: Dict[str, Any], idx: int) -> str:
        pid = card.get('product_id')
        if pid is not None:
            return str(pid)
        return f"idx:{idx}"

    def apply(self, cards: List[Dict[str, Any]], hints: Dict[str, Any] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, int]]:
        """Apply enabled filters and return (cards, diagnostics, match_score_by_product_id)."""
        hints = hints or {}
        if not self.config.get('enabled', True):
            return list(cards), {'filters_used': 0, 'applied_filters': []}, {}

        working = list(cards)
        card_ids: Dict[int, str] = {id(c): self._get_card_id(c, i) for i, c in enumerate(cards)}
        scores: Dict[str, int] = {card_ids[id(c)]: 0 for c in cards}
        applied_filters = []

        def apply_match(filter_name: str, matcher, weight_bonus: int = 0):
            nonlocal working
            if not self.config.get(filter_name, False):
                return
            before = working
            matched = [c for c in before if matcher(c)]
            if len(matched) != len(before):
                working = matched
            if matched:
                weight = self.SCORE_WEIGHTS.get(filter_name, 1) + int(weight_bonus)
                for card in matched:
                    cid = card_ids.get(id(card))
                    if cid is None:
                        continue
                    scores[cid] = scores.get(cid, 0) + weight
            applied_filters.append(filter_name)

        # Name: exact first, then fuzzy <= 2 if no exact hit
        name_hint = hints.get('name')
        if name_hint and self.config.get('name', False):
            target = str(name_hint).strip().lower()
            exact = [c for c in working if str(c.get('name') or '').strip().lower() == target]
            if exact:
                working = exact
                for card in working:
                    cid = card_ids.get(id(card))
                    if cid is not None:
                        scores[cid] = scores.get(cid, 0) + self.SCORE_WEIGHTS['name'] + 30
                applied_filters.append('name')
            else:
                max_dist = max(2, min(4, len(target) // 6))
                apply_match(
                    'name',
                    lambda c: (
                        self._levenshtein(
                            str(c.get('name') or '').strip().lower(),
                            target,
                            max_distance=max_dist,
                        ) <= max_dist
                        or difflib.SequenceMatcher(
                            None,
                            str(c.get('name') or '').strip().lower(),
                            target,
                        ).ratio() >= 0.82
                    ),
                )

        # Color identity
        color_hint = hints.get('color_identity')
        if color_hint and self.config.get('color_identity', False):
            hint_colors = self._normalize_colors(color_hint)

            def color_match(card):
                card_colors = self._normalize_colors(card.get('color') or card.get('colors') or card.get('color_identity'))
                if hint_colors == ['MULTI']:
                    return len([c for c in card_colors if c in {'W', 'U', 'B', 'R', 'G'}]) > 1
                if hint_colors == ['COLORLESS']:
                    return len([c for c in card_colors if c in {'W', 'U', 'B', 'R', 'G'}]) == 0
                if not hint_colors:
                    return True
                card_symbol_colors = sorted([c for c in card_colors if c in {'W', 'U', 'B', 'R', 'G'}])
                hint_symbol_colors = sorted([c for c in hint_colors if c in {'W', 'U', 'B', 'R', 'G'}])
                return card_symbol_colors == hint_symbol_colors

            apply_match('color_identity', color_match)

        # CMC within +-1
        cmc_hint = self._parse_cmc(hints.get('cmc'))
        if cmc_hint is not None and self.config.get('cmc', False):
            tolerance = 0 if (hints.get('collector_number') or hints.get('set_code')) else 1
            apply_match('cmc', lambda c: (self._parse_cmc(c.get('cmc')) is not None and abs(self._parse_cmc(c.get('cmc')) - cmc_hint) <= tolerance))

        # Bonus: set code exact
        set_hint = hints.get('set_code')
        if set_hint and self.config.get('set', False):
            set_hint_norm = str(set_hint).strip().upper()
            apply_match('set', lambda c: str(c.get('set_code') or c.get('set') or '').strip().upper() == set_hint_norm)

        # Bonus: collector number exact
        number_hint = hints.get('collector_number')
        if number_hint and self.config.get('collector_number', False):
            n = str(number_hint).strip().lower()
            apply_match('collector_number', lambda c: str(c.get('number') or c.get('collector_number') or '').strip().lower() == n)

        # Bonus: card type/subtype
        type_hint = hints.get('card_type')
        if type_hint and self.config.get('type', False):
            t = str(type_hint).strip().lower()
            apply_match('type', lambda c: t in str(c.get('type') or '').lower())

        subtype_hint = hints.get('subtype')
        if subtype_hint and self.config.get('subtype', False):
            st = str(subtype_hint).strip().lower()
            apply_match('subtype', lambda c: st in str(c.get('subTypeName') or c.get('subtype') or '').lower())

        diagnostics = {
            'filters_used': len(applied_filters),
            'applied_filters': applied_filters,
            'input_count': len(cards),
            'output_count': len(working),
        }
        return working, diagnostics, scores
