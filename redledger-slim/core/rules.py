from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


@dataclass(frozen=True)
class BetSlip:
    amount: int
    picks: str
    token: str
    normalized: str
    play_type: str = ""
    reject_reason: str = ""


class BetParser:
    """Parse group bet text into normalized slips.

    The requirement document describes both directions:
    13-100 means picks 1 and 3 with amount 100.
    100-13 means amount 100 with picks 1 and 3.
    One or more punctuation marks between the two sides are accepted.
    """

    PUNCTUATION_SEPARATORS = r"\-/+÷=.,，。．｡°:：;；|、\\＼／~～_＿—–－*＊#＃%％@＠·•￥¥×十一()（）\[\]【】{}｛｝!?！？^＾$＆&“”‘’'\"《》<>…"
    TOKEN_RE = re.compile(
        rf"(?<![A-Za-z0-9_])(\d{{1,9}})\s*([{PUNCTUATION_SEPARATORS}])\s*(\d{{1,9}})(?![A-Za-z0-9_])"
    )
    LETTER_TOKEN_RE = re.compile(
        rf"(?<![A-Za-z0-9_])([A-Da-d]{{1,4}}|\d{{1,9}})\s*([{PUNCTUATION_SEPARATORS}])\s*([A-Da-d]{{1,4}}|\d{{1,9}})(?![A-Za-z0-9_])"
    )
    LETTER_SPACED_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Da-d]{1,4})\s+(\d{1,9})(?![A-Za-z0-9_])")
    DIGIT_RE = re.compile(r"(?<![A-Za-z0-9_])\d{1,9}(?![A-Za-z0-9_])")
    SIDE_PATTERN = r"(?:[A-Da-d]{1,4}|\d{1,9})"
    PICK_PATTERN = r"(?:[A-Da-d]{1,4}|[1-4]{1,4})"
    AMOUNT_PATTERN = r"\d{1,9}"
    INCOMPLETE_AMOUNT_RE = re.compile(
        rf"(?<![A-Za-z0-9_])(?P<picks>{PICK_PATTERN})\s*(?P<sep>[{PUNCTUATION_SEPARATORS}])\s*(?=$|\s)"
    )
    CLIMB_MARKERS = "爬挖抓得歪"
    CLIMB_MARKER_ALIASES = (
        "pa", "pá", "pā", "pǎ", "pà", "趴", "扒", "耙",
        "wa", "wā", "wá", "wǎ", "wà", "哇", "娃", "蛙", "洼", "瓦",
        "wai", "wāi", "崴",
        "de", "dé", "dē", "dě", "dè", "的", "德",
        "zhua", "zhuā", "zhuǎ", "爪",
    )
    CLIMB_TOKEN_PATTERNS = (
        re.compile(
            rf"(?<![A-Za-z0-9_])(?P<amount>{AMOUNT_PATTERN})\s*(?P<sep>[{PUNCTUATION_SEPARATORS}])\s*(?P<marker>[{CLIMB_MARKERS}])\s*(?P<picks>{PICK_PATTERN})\s*(?P=sep)\s*(?P=amount)(?![A-Za-z0-9_])"
        ),
        re.compile(
            rf"(?<![A-Za-z0-9_])(?P<left>{SIDE_PATTERN})\s*(?P<sep>[{PUNCTUATION_SEPARATORS}])\s*(?P<right>{SIDE_PATTERN})\s*(?P<marker>[{CLIMB_MARKERS}])(?![A-Za-z0-9_])"
        ),
        re.compile(
            rf"(?<![A-Za-z0-9_])(?P<marker>[{CLIMB_MARKERS}])\s*(?P<left>{SIDE_PATTERN})\s*(?P<sep>[{PUNCTUATION_SEPARATORS}])\s*(?P<right>{SIDE_PATTERN})(?![A-Za-z0-9_])"
        ),
        re.compile(
            rf"(?<![A-Za-z0-9_])(?P<left>{SIDE_PATTERN})\s*(?P<marker>[{CLIMB_MARKERS}])\s*(?P<sep>[{PUNCTUATION_SEPARATORS}])\s*(?P<right>{SIDE_PATTERN})(?![A-Za-z0-9_])"
        ),
        re.compile(
            rf"(?<![A-Za-z0-9_])(?P<left>{SIDE_PATTERN})\s*(?P<sep>[{PUNCTUATION_SEPARATORS}])\s*(?P<marker>[{CLIMB_MARKERS}])\s*(?:[{PUNCTUATION_SEPARATORS}]\s*)?(?P<right>{SIDE_PATTERN})(?![A-Za-z0-9_])"
        ),
    )
    DANGLING_CLIMB_RE = re.compile(
        rf"(?<![A-Za-z0-9_])(?P<amount>{AMOUNT_PATTERN})\s*(?P<sep>[{PUNCTUATION_SEPARATORS}])\s*(?P<marker>[{CLIMB_MARKERS}])(?P<gap>\s+)(?P<picks>{PICK_PATTERN})(?![A-Za-z0-9_])"
    )
    MARKED_PICK_AMOUNT_CHAIN_RE = re.compile(
        rf"(?<![A-Za-z0-9_])(?P<picks>{PICK_PATTERN})\s*(?P<sep>[{PUNCTUATION_SEPARATORS}])\s*(?P<marker>[{CLIMB_MARKERS}])\s*(?P<amount>{AMOUNT_PATTERN})(?P<tail>(?:\s*[{PUNCTUATION_SEPARATORS}]\s*{PICK_PATTERN}\s*[{PUNCTUATION_SEPARATORS}]\s*{AMOUNT_PATTERN})+)(?![A-Za-z0-9_])"
    )
    MARKED_CHAIN_TAIL_RE = re.compile(
        rf"\s*[{PUNCTUATION_SEPARATORS}]\s*(?P<picks>{PICK_PATTERN})\s*(?P<sep>[{PUNCTUATION_SEPARATORS}])\s*(?P<amount>{AMOUNT_PATTERN})"
    )
    INLINE_PREFIX_CLIMB_RE = re.compile(
        rf"(?<![A-Za-z0-9_])(?P<amount>{AMOUNT_PATTERN})\s*(?P<sep>[{PUNCTUATION_SEPARATORS}])\s*(?P<marker>[{CLIMB_MARKERS}])(?P<picks>{PICK_PATTERN})(?!\s*(?P=sep)\s*(?P=amount)(?![A-Za-z0-9_]))(?![A-Za-z0-9_])"
    )
    MALFORMED_CLIMB_NO_SEPARATOR_RE = re.compile(
        rf"(?<![A-Za-z0-9_])(?P<picks>{PICK_PATTERN})\s*(?P<marker>[{CLIMB_MARKERS}])\s*(?P<amount>{AMOUNT_PATTERN})(?![A-Za-z0-9_])"
    )
    INLINE_SUFFIX_AMOUNT_PICK_RE = re.compile(
        rf"\s*[{PUNCTUATION_SEPARATORS}]\s*(?P<amount>{AMOUNT_PATTERN})\s*(?P<sep>[{PUNCTUATION_SEPARATORS}])\s*(?P<picks>{PICK_PATTERN})(?![A-Za-z0-9_])"
    )
    SHARED_AMOUNT_RE = re.compile(
        rf"(?<![A-Za-z0-9_])(?P<picks>(?:(?:{PICK_PATTERN})\s*[{PUNCTUATION_SEPARATORS}]\s*){{2,}})(?P<amount>{AMOUNT_PATTERN})(?![A-Za-z0-9_])"
    )
    TRAILING_RIGHT_PICK_RE = re.compile(
        rf"(?<![A-Za-z0-9_])(?P<left>{PICK_PATTERN})\s*(?P<sep>[{PUNCTUATION_SEPARATORS}])\s*(?P<amount>{AMOUNT_PATTERN})\s*(?P=sep)\s*(?P<right>{PICK_PATTERN})(?!\s*[{PUNCTUATION_SEPARATORS}]\s*{AMOUNT_PATTERN})(?![A-Za-z0-9_])"
    )
    FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
    EDGE_CHAT_PUNCTUATION = "。，,;\uff1b：:!\uff01?\uff1f、“”‘’'\"《》<>"
    LETTER_PICK_TRANSLATION = str.maketrans({"A": "1", "B": "2", "C": "3", "D": "4"})
    TOKEN_BOUNDARY_CHARS = set(PUNCTUATION_SEPARATORS)
    GROUP_DELIMITER_RE = re.compile(r"[。．｡，,、；;]+")
    COMPLETE_PAIR_AT_END_RE = re.compile(
        rf"(?<![A-Za-z0-9_]){SIDE_PATTERN}\s*[{PUNCTUATION_SEPARATORS}]\s*{SIDE_PATTERN}\s*$"
    )
    COMPLETE_PAIR_AT_START_RE = re.compile(
        rf"^\s*{SIDE_PATTERN}\s*[{PUNCTUATION_SEPARATORS}]\s*{SIDE_PATTERN}(?![A-Za-z0-9_])"
    )
    REPEAT_TOKEN_RE = re.compile(
        rf"(?<![A-Za-z0-9_])(?P<token>{SIDE_PATTERN}\s*[{PUNCTUATION_SEPARATORS}]\s*(?:[{CLIMB_MARKERS}]\s*)?{SIDE_PATTERN}(?:\s*[{CLIMB_MARKERS}])?)\s*[*＊×]\s*(?P<count>10|[1-9])(?!\d|[A-Za-z_])"
    )
    ADJACENT_STAR_BETS_RE = re.compile(
        rf"(?<![A-Za-z0-9_])"
        rf"(?P<left1>{SIDE_PATTERN})\s*(?P<sep1>[{PUNCTUATION_SEPARATORS}])\s*(?P<right1>{SIDE_PATTERN})"
        rf"\s*(?P<bridge>[{PUNCTUATION_SEPARATORS}])\s*"
        rf"(?P<left2>{SIDE_PATTERN})\s*(?P<sep2>[*＊×])\s*(?P<right2>{SIDE_PATTERN})"
        rf"(?![A-Za-z0-9_])"
    )

    CALCULATION_RE = re.compile(
        r"^[+\-]?\d+(?:\.\d+)?\s*[+\-*/xX×÷]\s*[+\-]?\d+(?:\.\d+)?\s*[=＝]\s*[+\-]?\d+(?:\.\d+)?\s*[+\-]?$"
    )
    NUMBERED_NOTICE_LINE_RE = re.compile(r"(?m)^\s*\d{1,2}\s*[.．。)、）]")
    INFORMATIONAL_NOTICE_MARKERS = ("群规", "本群规则", "群公告", "群通知")
    INFORMATIONAL_POLICY_MARKERS = ("无效", "发包", "红包", "下注", "遵守", "为准", "不计", "限门")

    @classmethod
    def is_calculation_message(cls, text: str) -> bool:
        """Return true for a complete arithmetic equation, not a bet token."""
        normalized = cls._prepare_text(text)
        return bool(cls.CALCULATION_RE.fullmatch(normalized.strip()))

    @classmethod
    def is_informational_notice(cls, text: str) -> bool:
        """Keep numbered group rules and policy notices out of bet parsing."""
        normalized = str(text or "").translate(cls.FULLWIDTH_DIGITS).strip()
        if not normalized:
            return False
        numbered_lines = len(cls.NUMBERED_NOTICE_LINE_RE.findall(normalized))
        if any(marker in normalized for marker in cls.INFORMATIONAL_NOTICE_MARKERS):
            return len(normalized) >= 20 or numbered_lines >= 2
        policy_markers = sum(marker in normalized for marker in cls.INFORMATIONAL_POLICY_MARKERS)
        return len(normalized) >= 120 and numbered_lines >= 4 and policy_markers >= 3

    @classmethod
    def parse(cls, text: str, max_repeat: int = 10) -> list[BetSlip]:
        if cls.is_calculation_message(text) or cls.is_informational_notice(text):
            return []
        slips: list[BetSlip] = []
        normalized_text = cls._prepare_text(text)
        normalized_text = cls._split_adjacent_star_bets(normalized_text)
        normalized_text = cls._collapse_separator_runs(cls._expand_multi_bets(normalized_text, max_repeat=max_repeat))
        consumed: list[tuple[int, int]] = []
        ordered: list[tuple[int, int, BetSlip]] = []

        for match in cls.MARKED_PICK_AMOUNT_CHAIN_RE.finditer(normalized_text):
            if cls._overlaps(match.span(), consumed):
                continue
            if cls._has_invalid_token_context(normalized_text, match.start(), match.end()):
                continue
            amount_text = match.group("amount")
            if not cls._is_valid_amount_text(amount_text):
                continue
            slip = cls._parse_pair(
                match.group("picks"),
                match.group("sep"),
                amount_text,
                play_type="climb",
                marker=match.group("marker"),
                token=normalized_text[match.start() : match.end("amount")],
            )
            if not slip:
                continue
            if not cls._is_climb_marker_picks(slip.picks):
                continue
            slips.append(slip)
            ordered.append((match.start(), len(ordered), slip))
            consumed.append(match.span())

            tail_start = match.end("amount")
            tail_text = normalized_text[tail_start : match.end()]
            for tail_match in cls.MARKED_CHAIN_TAIL_RE.finditer(tail_text):
                tail_amount = tail_match.group("amount")
                if not cls._is_valid_amount_text(tail_amount):
                    continue
                token_start = tail_start + tail_match.start("picks")
                token_end = tail_start + tail_match.end("amount")
                tail_slip = cls._parse_pair(
                    tail_match.group("picks"),
                    tail_match.group("sep"),
                    tail_amount,
                    token=normalized_text[token_start:token_end],
                )
                if not tail_slip:
                    continue
                slips.append(tail_slip)
                ordered.append((token_start, len(ordered), tail_slip))

        for match in cls.DANGLING_CLIMB_RE.finditer(normalized_text):
            consumed_span = (match.start(), match.start("picks"))
            if cls._overlaps(consumed_span, consumed):
                continue
            amount_text = match.group("amount")
            if not cls._is_valid_amount_text(amount_text):
                continue
            slip = cls._parse_pair(
                match.group("picks"),
                match.group("sep"),
                amount_text,
                play_type="climb",
                marker=match.group("marker"),
                token=normalized_text[match.start() : match.end("picks")],
            )
            if not slip:
                continue
            if not cls._is_climb_marker_picks(slip.picks):
                continue
            slips.append(slip)
            ordered.append((match.start(), len(ordered), slip))
            consumed.append(consumed_span)

        for match in cls.INLINE_PREFIX_CLIMB_RE.finditer(normalized_text):
            consumed_span = (match.start(), match.end("picks"))
            if cls._overlaps(consumed_span, consumed):
                continue
            amount_text = match.group("amount")
            if not cls._is_valid_amount_text(amount_text):
                continue
            slip = cls._parse_pair(
                match.group("picks"),
                match.group("sep"),
                amount_text,
                play_type="climb",
                marker=match.group("marker"),
                token=normalized_text[match.start() : match.end("picks")],
            )
            if not slip:
                continue
            if not cls._is_climb_marker_picks(slip.picks):
                continue
            slips.append(slip)
            ordered.append((match.start(), len(ordered), slip))
            consumed.append(consumed_span)
            tail_pos = match.end("picks")
            while True:
                tail_match = cls.INLINE_SUFFIX_AMOUNT_PICK_RE.match(normalized_text, tail_pos)
                if not tail_match:
                    break
                amount_text = tail_match.group("amount")
                if not cls._is_valid_amount_text(amount_text):
                    break
                tail_span = (tail_match.start("amount"), tail_match.end("picks"))
                if cls._overlaps(tail_span, consumed):
                    break
                tail_slip = cls._parse_pair(
                    amount_text,
                    tail_match.group("sep"),
                    tail_match.group("picks"),
                    token=normalized_text[tail_match.start("amount") : tail_match.end("picks")],
                )
                if not tail_slip:
                    break
                slips.append(tail_slip)
                ordered.append((tail_match.start("amount"), len(ordered), tail_slip))
                consumed.append(tail_span)
                tail_pos = tail_match.end("picks")

        for pattern in cls.CLIMB_TOKEN_PATTERNS:
            for match in pattern.finditer(normalized_text):
                if cls._overlaps(match.span(), consumed):
                    continue
                if cls._has_invalid_token_context(normalized_text, match.start(), match.end()):
                    continue
                if "amount" in match.groupdict():
                    left, sep, right = match.group("picks"), match.group("sep"), match.group("amount")
                else:
                    left, sep, right = match.group("left"), match.group("sep"), match.group("right")
                slip = cls._parse_pair(
                    left,
                    sep,
                    right,
                    play_type="climb",
                    marker=match.group("marker"),
                    token=match.group(0),
                )
                if not slip:
                    continue
                slips.append(slip)
                ordered.append((match.start(), len(ordered), slip))
                consumed.append(match.span())

        for match in cls.SHARED_AMOUNT_RE.finditer(normalized_text):
            if cls._overlaps(match.span(), consumed):
                continue
            if cls._preceded_by_unpaired_amount(normalized_text, match.start()):
                continue
            if cls._has_invalid_token_context(normalized_text, match.start(), match.end()):
                continue
            amount_text = match.group("amount")
            if cls._is_picks(amount_text):
                continue
            if not cls._is_valid_amount_text(amount_text):
                continue
            amount = int(amount_text)
            pick_tokens = re.findall(cls.PICK_PATTERN, match.group("picks"))
            if len(pick_tokens) < 2:
                continue
            for pick in pick_tokens:
                display_picks = pick.upper() if cls._is_letter_picks(pick) else pick
                slip = BetSlip(
                    amount=amount,
                    picks=cls._numeric_picks(display_picks),
                    token=match.group(0),
                    normalized=f"{amount}/{display_picks}",
                )
                slips.append(slip)
                ordered.append((match.start(), len(ordered), slip))
            consumed.append(match.span())

        for match in cls.LETTER_TOKEN_RE.finditer(normalized_text):
            if cls._overlaps(match.span(), consumed):
                continue
            if cls._has_invalid_token_context(normalized_text, match.start(), match.end()):
                continue
            left, sep, right = match.group(1), match.group(2), match.group(3)
            slip = cls._parse_pair(left, sep, right)
            if not slip:
                continue
            slips.append(slip)
            ordered.append((match.start(), len(ordered), slip))
            consumed.append(match.span())

        for match in cls.TRAILING_RIGHT_PICK_RE.finditer(normalized_text):
            amount_text = match.group("amount")
            if not cls._is_valid_amount_text(amount_text):
                continue
            slip = cls._parse_pair(match.group("right"), match.group("sep"), amount_text)
            if not slip:
                continue
            if any(
                existing.normalized == slip.normalized and existing.play_type == slip.play_type
                for _, _, existing in ordered
            ):
                continue
            slips.append(slip)
            ordered.append((match.start(), len(ordered), slip))

        for match in cls.LETTER_SPACED_RE.finditer(normalized_text):
            if cls._overlaps(match.span(), consumed):
                continue
            if cls._has_invalid_token_context(normalized_text, match.start(), match.end()):
                continue
            left, right = match.group(1), match.group(2)
            slip = cls._parse_pair(left, " ", right)
            if not slip:
                continue
            slips.append(slip)
            ordered.append((match.start(), len(ordered), slip))
            consumed.append(match.span())

        digit_matches = list(cls.DIGIT_RE.finditer(normalized_text))
        for left_match, right_match in zip(digit_matches, digit_matches[1:], strict=False):
            start, end = left_match.start(), right_match.end()
            if cls._overlaps(left_match.span(), consumed) or cls._overlaps(right_match.span(), consumed):
                continue
            if cls._has_invalid_token_context(normalized_text, start, end):
                continue
            if normalized_text[left_match.end() : right_match.start()].strip():
                continue
            left, right = left_match.group(0), right_match.group(0)
            slip = cls._parse_pair(left, " ", right)
            if not slip:
                continue
            slips.append(slip)
            ordered.append((start, len(ordered), slip))
            consumed.append((start, end))
        return [slip for _, _, slip in sorted(ordered, key=lambda item: item[:2])]

    @classmethod
    def has_unparsed_numeric_content(
        cls, text: str, slips: list[BetSlip], max_repeat: int = 10
    ) -> bool:
        if not slips or cls.is_informational_notice(text):
            return False
        normalized = cls._prepare_text(text)
        normalized = cls._collapse_separator_runs(cls._expand_multi_bets(normalized, max_repeat=max_repeat))
        source_numbers = Counter(re.findall(r"\d+", normalized))
        parsed_numbers: Counter[str] = Counter()
        for slip in slips:
            parsed_numbers.update(re.findall(r"\d+", str(slip.token or "")))
        return any(parsed_numbers[value] < count for value, count in source_numbers.items())

    @classmethod
    @classmethod
    def has_dangling_separators(cls, text: str) -> bool:
        """Check if the text contains dangling unclosed brackets/symbols, tolerating repeated delimiters like // or --."""
        for open_b, close_b in [("(", ")"), ("（", "）"), ("[", "]"), ("【", "】"), ("{", "}")]:
            if text.count(open_b) != text.count(close_b):
                return True
        return False
    @classmethod
    def _normalize_climb_aliases(cls, text: str) -> str:
        aliases = "|".join(sorted((re.escape(item) for item in cls.CLIMB_MARKER_ALIASES), key=len, reverse=True))
        separator = rf"[{cls.PUNCTUATION_SEPARATORS}]"
        value = re.sub(
            rf"(?i)(?P<prefix>[A-D0-9]\s*(?:{separator}\s*)?)(?:{aliases})",
            lambda match: f"{match.group('prefix')}挖",
            text,
        )
        value = re.sub(
            rf"(?i)(?:{aliases})(?P<suffix>\s*(?:{separator}\s*)?[A-D0-9])",
            lambda match: f"挖{match.group('suffix')}",
            value,
        )
        # A leading/trailing marker may have its own separator, for every climb
        # marker: 爬/200/112, 200/112/爬.
        markers = rf"[{cls.CLIMB_MARKERS}]"
        value = re.sub(
            rf"(?<![A-Da-d0-9])(?P<marker>{markers})\s*{separator}\s*(?=[A-Da-d0-9])",
            lambda match: match.group("marker"),
            value,
        )
        return re.sub(
            rf"(?<=[A-Da-d0-9])\s*{separator}\s*(?P<marker>{markers})(?!\s*{separator}?\s*[A-Da-d0-9])",
            lambda match: match.group("marker"),
            value,
        )

    @classmethod
    def _prepare_text(cls, text: str) -> str:
        """Normalize chat text consistently for valid and invalid-shape scans."""
        value = str(text or "").translate(cls.FULLWIDTH_DIGITS)
        edge_chars = re.escape(cls.EDGE_CHAT_PUNCTUATION)
        value = re.sub(rf"^[{edge_chars}]+|[{edge_chars}]+$", "", value.strip())
        value = re.sub(rf"(?m)^[{edge_chars}]+|[{edge_chars}]+$", "", value)
        value = cls._normalize_climb_aliases(value)
        return cls._separate_independent_pair_groups(value)

    @classmethod
    def _separate_independent_pair_groups(cls, text: str) -> str:
        """Stop a sentence separator from becoming part of an adjacent bet token."""
        matches = list(cls.GROUP_DELIMITER_RE.finditer(text))
        if not matches:
            return text
        characters = list(text)
        for match in matches:
            left = text[: match.start()]
            right = text[match.end() :]
            if not cls.COMPLETE_PAIR_AT_END_RE.search(left):
                continue
            if not cls.COMPLETE_PAIR_AT_START_RE.match(right):
                continue
            characters[match.start()] = "\n"
            for index in range(match.start() + 1, match.end()):
                characters[index] = " "
        return "".join(characters)

    @classmethod
    def parse_invalid_amount_format(cls, text: str, max_repeat: int = 10) -> list[BetSlip]:
        if cls.is_calculation_message(text) or cls.is_informational_notice(text):
            return []
        normalized_text = cls._expand_multi_bets(cls._prepare_text(text), max_repeat=max_repeat)
        slips: list[BetSlip] = []
        consumed: list[tuple[int, int]] = []
        ordered: list[tuple[int, int, BetSlip]] = []
        for match in cls.MALFORMED_CLIMB_NO_SEPARATOR_RE.finditer(normalized_text):
            if cls._has_invalid_token_context(normalized_text, match.start(), match.end()):
                continue
            display_picks = match.group("picks").upper() if cls._is_letter_picks(match.group("picks")) else match.group("picks")
            amount_text = match.group("amount")
            amount = int(amount_text) if amount_text.isdigit() else 0
            marker = match.group("marker")
            slip = BetSlip(
                amount=amount,
                picks=cls._numeric_picks(display_picks),
                token=match.group(0),
                normalized=f"{amount}/{display_picks}{marker}",
                play_type="climb",
                reject_reason=f"爬/挖/抓格式错误，应使用 {display_picks}/爬{amount_text} 这种格式，已拒收不计入",
            )
            consumed.append(match.span())
            ordered.append((match.start(), len(ordered), slip))
            slips.append(slip)
        for match in cls.LETTER_TOKEN_RE.finditer(normalized_text):
            if cls._overlaps(match.span(), consumed):
                continue
            if cls._has_invalid_token_context(normalized_text, match.start(), match.end()):
                continue
            # A complete pair must be excluded from the later incomplete-token
            # scan even when the pair itself has no amount-format error.
            consumed.append(match.span())
            slip = cls._parse_invalid_amount_pair(match.group(1), match.group(2), match.group(3), token=match.group(0))
            if not slip:
                continue
            ordered.append((match.start(), len(ordered), slip))
            slips.append(slip)
        for match in cls.INCOMPLETE_AMOUNT_RE.finditer(normalized_text):
            if cls._overlaps(match.span(), consumed):
                continue
            if cls._has_invalid_token_context(normalized_text, match.start(), match.end()):
                continue
            display_picks = match.group("picks").upper() if cls._is_letter_picks(match.group("picks")) else match.group("picks")
            slip = BetSlip(
                amount=0,
                picks=cls._numeric_picks(display_picks),
                token=match.group(0).strip(),
                normalized=f"0/{display_picks}",
                reject_reason="下注金额必须填写，已拒收不计入",
            )
            ordered.append((match.start(), len(ordered), slip))
            slips.append(slip)
        return [slip for _, _, slip in sorted(ordered, key=lambda item: item[:2])]

    @classmethod
    def _parse_pair(
        cls,
        left: str,
        sep: str,
        right: str,
        play_type: str = "",
        marker: str = "",
        token: str | None = None,
    ) -> BetSlip | None:
        left_display = left.upper() if cls._is_letter_picks(left) else left
        right_display = right.upper() if cls._is_letter_picks(right) else right
        left_is_picks = cls._is_picks(left) or cls._is_letter_picks(left)
        right_is_picks = cls._is_picks(right) or cls._is_letter_picks(right)
        if left_is_picks and not right_is_picks:
            amount_text = right
            display_picks = left_display
        elif right_is_picks and not left_is_picks:
            amount_text = left
            display_picks = right_display
        else:
            return None

        if not cls._is_valid_amount_text(amount_text):
            return None
        amount = int(amount_text)
        picks = cls._numeric_picks(display_picks)
        token = token if token is not None else f"{left}{sep}{right}"
        suffix = marker if play_type == "climb" and marker else ""
        return BetSlip(amount=amount, picks=picks, token=token, normalized=f"{amount}/{display_picks}{suffix}", play_type=play_type)

    @classmethod
    def _parse_invalid_amount_pair(cls, left: str, sep: str, right: str, token: str | None = None) -> BetSlip | None:
        valid = cls._parse_pair(left, sep, right, token=token)
        if valid:
            return None

        left_display = left.upper() if cls._is_letter_picks(left) else left
        right_display = right.upper() if cls._is_letter_picks(right) else right
        left_is_picks = cls._is_picks(left) or cls._is_letter_picks(left)
        right_is_picks = cls._is_picks(right) or cls._is_letter_picks(right)
        candidates: list[tuple[str, str]] = []
        if left_is_picks and not right_is_picks:
            candidates.append((right, left_display))
        if right_is_picks and not left_is_picks:
            candidates.append((left, right_display))
        if left_is_picks and right_is_picks:
            if len(left) >= 3 and len(right) <= 2:
                candidates.append((left, right_display))
            elif len(right) >= 3 and len(left) <= 2:
                candidates.append((right, left_display))
            elif len(left) >= 3 and len(right) >= 3:
                candidates.append((left, right_display))

        for amount_text, display_picks in candidates:
            if not amount_text.isdigit():
                continue
            if int(amount_text) <= 0 or amount_text[-1] in "05":
                continue
            picks = cls._numeric_picks(display_picks)
            return BetSlip(
                amount=int(amount_text),
                picks=picks,
                token=token if token is not None else f"{left}{sep}{right}",
                normalized=f"{int(amount_text)}/{display_picks}",
                reject_reason="下注金额尾数不是0/5，已拒收不计入",
            )
        if left.isdigit() and cls._is_valid_amount_text(left) and right.isdigit():
            return BetSlip(
                amount=int(left),
                picks=right_display,
                token=token if token is not None else f"{left}{sep}{right}",
                normalized=f"{int(left)}/{right_display}",
                reject_reason="答题不符合规则，已拒收不计入",
            )
        if right.isdigit() and cls._is_valid_amount_text(right) and left.isdigit():
            return BetSlip(
                amount=int(right),
                picks=left_display,
                token=token if token is not None else f"{left}{sep}{right}",
                normalized=f"{int(right)}/{left_display}",
                reject_reason="答题不符合规则，已拒收不计入",
            )
        return None

    @staticmethod
    def _overlaps(span: tuple[int, int], consumed: list[tuple[int, int]]) -> bool:
        start, end = span
        return any(start < used_end and end > used_start for used_start, used_end in consumed)

    @staticmethod
    def _has_negative_prefix(text: str, start: int) -> bool:
        if start <= 0 or text[start - 1] != "-":
            return False
        if start == 1:
            return True
        previous = text[start - 2].upper()
        return not (previous.isdigit() or previous in "ABCD")

    @classmethod
    def _has_invalid_token_context(cls, text: str, start: int, end: int) -> bool:
        if start > 0:
            previous = text[start - 1]
            if not previous.isspace() and previous not in cls.TOKEN_BOUNDARY_CHARS:
                return True
        return cls._has_trailing_loose_pick(text, end)

    @classmethod
    def _has_trailing_loose_pick(cls, text: str, end: int) -> bool:
        match = re.match(r"\s+([A-Da-d]{1,4}|[1-4]{1,4})(?=$|\s)", text[end:])
        if not match:
            return False
        remainder = text[end + match.end() :]
        # The next pick is not loose when it starts another complete bet with
        # spaces around the separator, for example: "14/210  1 /190".
        return not bool(re.match(rf"\s*[{cls.PUNCTUATION_SEPARATORS}]\s*\d", remainder))

    @classmethod
    def _preceded_by_unpaired_amount(cls, text: str, start: int) -> bool:
        prefix = text[:start]
        amount_match = re.search(rf"(?P<amount>\d{{1,9}})\s*[{cls.PUNCTUATION_SEPARATORS}]\s*$", prefix)
        if not amount_match or not cls._is_valid_amount_text(amount_match.group("amount")):
            return False
        before_amount = text[: amount_match.start("amount")]
        left_pick_match = re.search(rf"(?P<picks>{cls.PICK_PATTERN})\s*[{cls.PUNCTUATION_SEPARATORS}]\s*$", before_amount)
        if left_pick_match:
            return False
        return True

    @staticmethod
    def _is_valid_amount_text(value: str) -> bool:
        text = str(value or "")
        return bool(text) and text[-1] in "05" and int(text) > 0

    @classmethod
    def _expand_multi_bets(cls, text: str, max_repeat: int = 10) -> str:
        max_repeat = max(min(int(max_repeat or 10), 10), 1)

        def repl(match: re.Match[str]) -> str:
            token = match.group("token").strip()
            count = max(min(int(match.group("count") or "1"), max_repeat), 1)
            return "\n".join(token for _ in range(count))

        return cls.REPEAT_TOKEN_RE.sub(repl, text)

    @classmethod
    def _split_adjacent_star_bets(cls, text: str) -> str:
        """Protect a second `pick*amount` bet from the repeat multiplier parser."""

        def repl(match: re.Match[str]) -> str:
            first = cls._parse_pair(match.group("left1"), match.group("sep1"), match.group("right1"))
            second = cls._parse_pair(match.group("left2"), match.group("sep2"), match.group("right2"))
            if not first or not second or int(match.group("right2")) <= 10:
                return match.group(0)
            return (
                f'{match.group("left1")}{match.group("sep1")}{match.group("right1")}\n'
                f'{match.group("left2")}{match.group("sep2")}{match.group("right2")}'
            )

        return cls.ADJACENT_STAR_BETS_RE.sub(repl, text)

    @classmethod
    def _collapse_separator_runs(cls, text: str) -> str:
        return re.sub(
            rf"(?<=[A-Da-d0-9])[{cls.PUNCTUATION_SEPARATORS}]{{2,}}(?=[A-Da-d0-9])",
            lambda match: match.group(0)[0],
            text,
        )

    @staticmethod
    def _is_picks(value: str) -> bool:
        return 1 <= len(value) <= 4 and all(ch in "1234" for ch in value)

    @classmethod
    def _is_letter_picks(cls, value: str) -> bool:
        text = str(value or "").upper()
        return 1 <= len(text) <= 4 and all(ch in "ABCD" for ch in text)

    @classmethod
    def _numeric_picks(cls, value: str) -> str:
        text = str(value or "").upper()
        if cls._is_letter_picks(text):
            return text.translate(cls.LETTER_PICK_TRANSLATION)
        return text

    @staticmethod
    def _is_climb_marker_picks(value: str) -> bool:
        text = str(value or "")
        if len(text) != 3 or len(set(text)) != 2:
            return False
        return sorted(text.count(ch) for ch in set(text)) == [1, 2]


class PayoutRules:
    """Default settlement rules from the supplied document.

    Net result is the player-facing score. Negative values mean the player
    loses the stake. Positive values mean gross win minus water fee.
    """

    HIT_MULTIPLIERS: dict[int, dict[int, Decimal]] = {
        1: {1: Decimal("3")},
        2: {1: Decimal("1"), 2: Decimal("3")},
        3: {1: Decimal("0.5"), 2: Decimal("1.5"), 3: Decimal("3")},
        4: {1: Decimal("0"), 2: Decimal("1"), 3: Decimal("2"), 4: Decimal("3")},
    }
    WATER_PROFILE_GUANGDONG_LOGISTICS = "guangdong_logistics"
    SPECIAL_BASE: dict[int, tuple[int, int]] = {
        50: (80, 20),
        300: (500, 100),
        500: (800, 200),
        700: (1040, 350),
        900: (1350, 450),
    }
    SINGLE_DATA: dict[int, tuple[int, int, int]] = {
        110: (60, 50, 170),
        115: (60, 55, 175),
        130: (70, 60, 200),
        150: (80, 70, 230),
        155: (80, 75, 235),
        170: (90, 80, 260),
        175: (90, 85, 265),
        190: (100, 90, 290),
        195: (100, 95, 295),
        210: (110, 100, 320),
        215: (110, 105, 325),
        230: (120, 110, 350),
        235: (120, 115, 355),
        250: (130, 120, 380),
        270: (140, 130, 410),
        290: (150, 140, 440),
        310: (160, 150, 470),
        330: (170, 160, 500),
        350: (180, 170, 530),
        370: (190, 180, 560),
        390: (200, 190, 590),
        410: (210, 200, 620),
        430: (220, 210, 650),
        450: (230, 220, 680),
        470: (240, 230, 710),
        475: (240, 235, 715),
        490: (250, 240, 740),
        510: (260, 250, 770),
        530: (270, 260, 800),
        550: (280, 270, 830),
        570: (290, 280, 860),
        590: (300, 290, 890),
        610: (310, 300, 920),
        630: (320, 310, 950),
        650: (330, 320, 980),
        670: (340, 330, 1010),
        690: (350, 340, 1040),
        710: (360, 350, 1070),
        730: (370, 360, 1100),
        750: (380, 370, 1130),
        770: (390, 380, 1160),
        790: (400, 390, 1190),
        810: (410, 400, 1220),
        830: (420, 410, 1250),
        850: (430, 420, 1280),
        870: (440, 430, 1310),
        890: (450, 440, 1340),
        910: (460, 450, 1370),
        930: (470, 460, 1400),
        950: (480, 470, 1430),
        970: (490, 480, 1460),
        990: (500, 490, 1490),
        1100: (600, 500, 1700),
        1150: (600, 550, 1750),
        1155: (600, 555, 1755),
        1300: (700, 600, 2000),
        1350: (700, 650, 2050),
        1355: (700, 655, 2055),
        1500: (800, 700, 2300),
        1550: (800, 750, 2350),
        1700: (900, 800, 2600),
        1750: (900, 850, 2650),
        1900: (1000, 900, 2900),
        1950: (1000, 950, 2950),
        2100: (1100, 1000, 3200),
        2150: (1100, 1050, 3250),
        2300: (1200, 1100, 3500),
        2350: (1200, 1150, 3550),
        2500: (1300, 1200, 3800),
        2700: (1400, 1300, 4100),
        2900: (1500, 1400, 4400),
        2950: (1588, 1450, 4450),
    }
    WATER_RANGES: tuple[tuple[int, int, int], ...] = (
        (0, 100, 0),
        (105, 189, 5),
        (190, 319, 10),
        (320, 599, 20),
        (600, 799, 30),
        (800, 1099, 40),
        (1100, 1499, 50),
        (1500, 1799, 60),
        (1800, 2100, 80),
        (2101, 2399, 90),
        (2400, 2700, 100),
        (2710, 2999, 110),
        (3000, 3149, 120),
        (3150, 3379, 130),
        (3380, 3620, 140),
        (3630, 3870, 150),
        (3880, 4120, 160),
        (4130, 4369, 170),
        (4370, 4620, 180),
    )
    HIGH_WATER_BP = 400
    HIGH_WATER_THRESHOLD = 4630
    HIGH_WATER_ROUND_UNIT = 10
    WATER_PROFILE_DEFAULT = "default"
    WATER_PROFILE_MAX1500 = "max1500"
    WATER_PROFILE_MAX2100 = "max2100"
    MAX1500_WATER_RANGES: tuple[tuple[int, int, int], ...] = WATER_RANGES
    MAX2100_WATER_RANGES: tuple[tuple[int, int, int], ...] = WATER_RANGES
    WATER_RANGE_LINE_RE = re.compile(r"^(\d+)\s*[-~至]\s*(\d+)\s*[=:：]\s*(\d+)$")
    WATER_PERCENT_LINE_RE = re.compile(r"^(\d+)\s*\+\s*[=:：]?\s*(\d+(?:\.\d+)?)\s*%$")
    SPECIAL_WATER_LINE_RE = re.compile(r"^(?:(.+?)\s+)?(\d+)\s*/\s*(\d+)\s*=\s*(\d+)$")

    @classmethod
    def default_water_rules(cls) -> str:
        lines = [f"{start}-{end}={water}" for start, end, water in cls.WATER_RANGES]
        lines.append(f"{cls.HIGH_WATER_THRESHOLD}+={Decimal(cls.HIGH_WATER_BP) / Decimal(100)}%")
        return "\n".join(lines)

    @classmethod
    def parse_water_rules(
        cls, text: str
    ) -> tuple[tuple[tuple[int, int, int], ...], int, int]:
        ranges: list[tuple[int, int, int]] = []
        high_rule: tuple[int, int] | None = None
        for line_no, raw_line in enumerate(str(text or "").splitlines(), start=1):
            line = raw_line.strip().replace("，", ",")
            if not line or line.startswith("#"):
                continue
            percent_match = cls.WATER_PERCENT_LINE_RE.fullmatch(line)
            if percent_match:
                if high_rule is not None:
                    raise ValueError(f"打水规则第 {line_no} 行：只能设置一条百分比规则")
                threshold = int(percent_match.group(1))
                percent = Decimal(percent_match.group(2))
                bp = int((percent * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                if threshold < 1 or bp < 1:
                    raise ValueError(f"打水规则第 {line_no} 行：起始金额和百分比必须大于 0")
                high_rule = (threshold, bp)
                continue
            range_match = cls.WATER_RANGE_LINE_RE.fullmatch(line)
            if not range_match:
                raise ValueError(f"打水规则第 {line_no} 行格式错误，请使用 105-150=5 或 4630+=4%")
            start, end, water = (int(range_match.group(index)) for index in range(1, 4))
            if start > end:
                raise ValueError(f"打水规则第 {line_no} 行：起始金额不能大于结束金额")
            if ranges and start <= ranges[-1][1]:
                raise ValueError(f"打水规则第 {line_no} 行：金额区间与上一行重叠或顺序错误")
            ranges.append((start, end, water))
        if not ranges:
            raise ValueError("打水规则至少需要一条金额区间")
        if high_rule is None:
            raise ValueError("打水规则缺少百分比行，例如 4630+=4%")
        if high_rule[0] <= ranges[-1][1]:
            raise ValueError("百分比规则的起始金额必须大于最后一个固定区间")
        return tuple(ranges), high_rule[0], high_rule[1]

    @classmethod
    def normalize_water_rules(cls, text: str) -> str:
        ranges, threshold, bp = cls.parse_water_rules(text)
        lines = [f"{start}-{end}={water}" for start, end, water in ranges]
        percent = Decimal(bp) / Decimal(100)
        lines.append(f"{threshold}+={format(percent, 'f').rstrip('0').rstrip('.')}%")
        return "\n".join(lines)

    @classmethod
    def parse_special_water_rules(cls, text: str) -> tuple[tuple[str, int, int, int], ...]:
        rules: list[tuple[str, int, int, int]] = []
        seen: set[tuple[int, int]] = set()
        for line_no, raw_line in enumerate(str(text or "").splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = cls.SPECIAL_WATER_LINE_RE.fullmatch(line)
            if not match:
                raise ValueError(f"特殊注打水规则第 {line_no} 行格式错误，请使用 古关玩法 700/2100=100")
            label = str(match.group(1) or "特殊注").strip()
            stake, gross, water = (int(match.group(index)) for index in range(2, 5))
            if stake < 1 or gross < 1 or water < 0 or water > gross:
                raise ValueError(f"特殊注打水规则第 {line_no} 行金额无效")
            key = (stake, gross)
            if key in seen:
                raise ValueError(f"特殊注打水规则第 {line_no} 行与前面规则重复")
            seen.add(key)
            rules.append((label, stake, gross, water))
        return tuple(rules)

    @classmethod
    def normalize_special_water_rules(cls, text: str) -> str:
        return "\n".join(
            f"{label} {stake}/{gross}={water}"
            for label, stake, gross, water in cls.parse_special_water_rules(text)
        )

    @classmethod
    def normalize_water_profile(cls, value: str | None) -> str:
        profile = str(value or "").strip().lower()
        if profile in {"guangdong_logistics", "广东物流", "广州物流"}:
            return cls.WATER_PROFILE_GUANGDONG_LOGISTICS
        if profile in {"max1500", "最大1500", "最大下1500", "limit1500"}:
            return cls.WATER_PROFILE_MAX1500
        if profile in {"max2100", "limit2100", "maxwin2100"}:
            return cls.WATER_PROFILE_MAX2100
        return cls.WATER_PROFILE_DEFAULT

    @classmethod
    def calculate(
        cls,
        amount: int,
        picks: str,
        result: str,
        water_bp: int,
        water_unit: int,
        water_free_max_win: int = 100,
        water_free_half_multiplier: bool = True,
        play_type: str = "",
        water_profile: str = "",
        water_rules: str = "",
        special_water_rules: str = "",
    ) -> dict[str, int]:
        profile = cls.normalize_water_profile(water_profile)
        if result not in "1234":
            return {"hit_count": 0, "gross_result": 0, "water": 0, "net_result": 0}

        if play_type == "climb":
            climb_shape = cls.climb_shape(picks)
            if climb_shape:
                majority, minor = climb_shape
                if result == majority:
                    multiplier = Decimal("2")
                    gross = int((Decimal(amount) * multiplier).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                    water = cls.water_amount(
                        gross,
                        water_bp,
                        water_unit,
                        free_max_win=water_free_max_win,
                        multiplier=multiplier,
                        free_half_multiplier=water_free_half_multiplier,
                        stake_amount=amount,
                        water_profile=profile,
                        water_rules=water_rules,
                        special_water_rules=special_water_rules,
                    )
                    return {"hit_count": picks.count(result), "gross_result": gross, "water": water, "net_result": gross - water}
                if result == minor:
                    return {"hit_count": 1, "gross_result": 0, "water": 0, "net_result": 0}
                return {"hit_count": 0, "gross_result": -amount, "water": 0, "net_result": -amount}

        admin_repeated = cls.admin_repeated_triple_result(amount, picks, result, water_profile=profile)
        if admin_repeated is not None:
            gross, hit_count, multiplier, tail_hit_free = admin_repeated
            if hit_count <= 0:
                return {"hit_count": 0, "gross_result": -amount, "water": 0, "net_result": -amount}
            water = cls.water_amount(
                gross,
                water_bp,
                water_unit,
                free_max_win=water_free_max_win,
                multiplier=multiplier,
                free_half_multiplier=water_free_half_multiplier,
                stake_amount=amount,
                tail_hit_free=tail_hit_free,
                water_profile=profile,
                water_rules=water_rules,
                special_water_rules=special_water_rules,
            )
            return {"hit_count": hit_count, "gross_result": gross, "water": water, "net_result": gross - water}

        hit_count = picks.count(result)
        if hit_count <= 0:
            return {"hit_count": 0, "gross_result": -amount, "water": 0, "net_result": -amount}

        multiplier = cls.HIT_MULTIPLIERS.get(len(picks), {}).get(hit_count, Decimal("0"))
        gross = int((Decimal(amount) * multiplier).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        water = cls.water_amount(
            gross,
            water_bp,
            water_unit,
            free_max_win=water_free_max_win,
            multiplier=multiplier,
            free_half_multiplier=water_free_half_multiplier,
            stake_amount=amount,
            water_profile=profile,
            water_rules=water_rules,
            special_water_rules=special_water_rules,
        )
        return {"hit_count": hit_count, "gross_result": gross, "water": water, "net_result": gross - water}

    @classmethod
    def water_amount(
        cls,
        gross_win: int,
        water_bp: int,
        water_unit: int,
        free_max_win: int = 100,
        multiplier: Decimal | None = None,
        free_half_multiplier: bool = True,
        stake_amount: int = 0,
        tail_hit_free: bool = False,
        water_profile: str = "",
        water_rules: str = "",
        special_water_rules: str = "",
    ) -> int:
        profile = cls.normalize_water_profile(water_profile)
        if gross_win <= 0 or water_bp <= 0:
            return 0
        if tail_hit_free:
            return 0
        if free_half_multiplier and multiplier == Decimal("0.5"):
            return 0
        if str(special_water_rules or "").strip():
            for _label, special_stake, special_gross, special_water in cls.parse_special_water_rules(special_water_rules):
                if int(stake_amount or 0) == special_stake and gross_win == special_gross:
                    return special_water
        if int(stake_amount or 0) == 2500 and multiplier == Decimal("1.5") and gross_win == 3750:
            return 100
        if gross_win == 3120 and int(stake_amount or 0) in {2080, 3120}:
            return 120
        if int(stake_amount or 0) == 2500 and multiplier == Decimal("3") and gross_win == 7500:
            return 300
        if profile == cls.WATER_PROFILE_MAX2100 and int(stake_amount or 0) == 700 and multiplier == Decimal("3") and gross_win == 2100:
            return 100
        if str(water_rules or "").strip():
            ranges, high_threshold, high_water_bp = cls.parse_water_rules(water_rules)
            return cls._profile_water_amount(
                gross_win,
                ranges,
                high_threshold=high_threshold,
                high_water_bp=high_water_bp,
                high_round_unit=max(int(water_unit or cls.HIGH_WATER_ROUND_UNIT), 1),
            )
        if profile == cls.WATER_PROFILE_MAX1500:
            return cls._profile_water_amount(
                gross_win,
                cls.MAX1500_WATER_RANGES,
                high_threshold=cls.HIGH_WATER_THRESHOLD,
                high_water_bp=cls.HIGH_WATER_BP,
                high_round_unit=cls.HIGH_WATER_ROUND_UNIT,
            )
        if profile == cls.WATER_PROFILE_MAX2100:
            return cls._profile_water_amount(
                gross_win,
                cls.MAX2100_WATER_RANGES,
                high_threshold=cls.HIGH_WATER_THRESHOLD,
                high_water_bp=cls.HIGH_WATER_BP,
                high_round_unit=cls.HIGH_WATER_ROUND_UNIT,
            )
        return cls._profile_water_amount(
            gross_win,
            cls.WATER_RANGES,
            high_threshold=cls.HIGH_WATER_THRESHOLD,
            high_water_bp=cls.HIGH_WATER_BP,
            high_round_unit=cls.HIGH_WATER_ROUND_UNIT,
        )

    @staticmethod
    def _profile_water_amount(
        gross_win: int,
        ranges: tuple[tuple[int, int, int], ...],
        high_threshold: int,
        high_water_bp: int,
        high_round_unit: int,
    ) -> int:
        for min_gross, max_gross, water in ranges:
            if min_gross <= gross_win <= max_gross:
                return water
        if gross_win < high_threshold:
            previous_water = 0
            for _min_gross, max_gross, water in ranges:
                if gross_win > max_gross:
                    previous_water = water
                    continue
                break
            return previous_water
        raw = Decimal(gross_win) * Decimal(high_water_bp) / Decimal(10000)
        truncated = Decimal(int(raw))
        return max(
            int(
                (truncated / Decimal(high_round_unit)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                * Decimal(high_round_unit)
            ),
            0,
        )

    @classmethod
    def admin_repeated_triple_result(
        cls,
        amount: int,
        picks: str,
        result: str,
        water_profile: str = "",
    ) -> tuple[int, int, Decimal, bool] | None:
        shape = cls.climb_shape(picks)
        if not shape:
            return None
        majority, minor = shape
        if result not in (majority, minor):
            return 0, 0, Decimal("0"), False
        profile = cls.normalize_water_profile(water_profile)
        if profile == cls.WATER_PROFILE_GUANGDONG_LOGISTICS and int(amount or 0) in {210, 1500}:
            stake = Decimal(int(amount or 0))
            if result == majority:
                return int(stake * Decimal("1.5")), picks.count(result), Decimal("1.5"), False
            return int(stake * Decimal("0.5")), picks.count(result), Decimal("0.5"), True
        if profile == cls.WATER_PROFILE_MAX1500 and int(amount or 0) == 1500:
            if result == majority:
                return 2250, picks.count(result), Decimal("1.5"), False
            return 750, picks.count(result), Decimal("0.5"), True
        head_win, tail_win = cls.admin_repeated_triple_prizes(amount)
        if result == majority:
            return head_win, picks.count(result), Decimal("1.5"), False
        return tail_win, picks.count(result), Decimal("0.5"), True

    @classmethod
    def admin_repeated_triple_prizes(cls, amount: int) -> tuple[int, int]:
        base = int(amount or 0)
        if base in cls.SPECIAL_BASE:
            return cls.SPECIAL_BASE[base]
        if base in cls.SINGLE_DATA:
            _coefficient, tail_win, head_win = cls.SINGLE_DATA[base]
            return head_win, tail_win
        head = cls._round_to_nearest_five(Decimal(base) * Decimal("1.5"))
        tail = cls._round_to_nearest_five(Decimal(base) / Decimal("2"))
        return head, tail

    @staticmethod
    def _round_to_nearest_five(value: Decimal) -> int:
        return int((value / Decimal("5")).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * Decimal("5"))

    @staticmethod
    def climb_shape(picks: str) -> tuple[str, str] | None:
        text = str(picks or "")
        if len(text) != 3 or len(set(text)) != 2:
            return None
        counts = {digit: text.count(digit) for digit in set(text)}
        if sorted(counts.values()) != [1, 2]:
            return None
        majority = next(digit for digit, count in counts.items() if count == 2)
        minor = next(digit for digit, count in counts.items() if count == 1)
        return majority, minor


def parse_rate_bp(text: str) -> int:
    raw = text.strip()
    if not raw:
        raise ValueError("打水比例不能为空")
    if raw.endswith("%"):
        value = Decimal(raw[:-1])
        bp = int((value * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    else:
        value = Decimal(raw)
        bp = int((value * Decimal(10000 if value <= 1 else 100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if bp < 0 or bp > 10000:
        raise ValueError("打水比例必须在 0 到 100% 之间")
    return bp


def format_rate(bp: int) -> str:
    return f"{(Decimal(bp) / Decimal(100)).normalize()}%"
