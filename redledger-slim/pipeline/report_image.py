from __future__ import annotations

import io
import hashlib
import os
import queue
import threading
import time
import uuid
from html import unescape
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from PIL import Image, ImageDraw, ImageFont


CANVAS_WIDTH = 1240
MARGIN = 22
CARD_RADIUS = 22
ROW_HEIGHT = 56
MIN_ROW_HEIGHT = 56
LOG_ENTRY_HEIGHT = 18
LOG_GROUP_GAP = 6
HEADER_HEIGHT = 140
SECTION_HEADER = 58
TABLE_HEAD = 46
BG = (235, 243, 239)
PAPER = (252, 254, 253)
INK = (23, 39, 35)
MUTED = (95, 113, 108)
LINE = (220, 232, 228)
TEAL = (31, 171, 160)
TEAL_DARK = (5, 116, 107)
TEAL_LIGHT = (198, 240, 234)
QUIET_HEAD = (255, 239, 201)
POSITIVE = (17, 136, 111)
NEGATIVE = (178, 74, 83)
ZERO = (85, 97, 93)
_AVATAR_CACHE: dict[str, Image.Image] = {}
_AVATAR_CACHE_LOCK = threading.RLock()
_AVATAR_FETCH_QUEUE: queue.Queue[str] = queue.Queue(maxsize=1024)
_AVATAR_FETCHING: set[str] = set()
_AVATAR_RETRY_AFTER: dict[str, float] = {}
_AVATAR_WORKERS_STARTED = False
try:
    _AVATAR_WORKER_COUNT = max(1, min(int(os.environ.get("REDLEDGER_AVATAR_WORKERS", "4") or "4"), 8))
except ValueError:
    _AVATAR_WORKER_COUNT = 4
_AVATAR_RETRY_SECONDS = 60.0
_AVATAR_TIMEOUT_SECONDS = 2.5
_AVATAR_MAX_BYTES = 2_000_000


def _save_png_atomic(image: Image.Image, path: Path) -> None:
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        image.save(temporary, "PNG", compress_level=1)
        replace_deadline = time.monotonic() + 2.0
        while True:
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if time.monotonic() >= replace_deadline:
                    raise
                time.sleep(0.02)
    finally:
        temporary.unlink(missing_ok=True)


def render_round_report(state: dict[str, Any], output_dir: str | Path = "C:/Temp/redledger-reports") -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    session = state["session"]
    round_row = state.get("active_round") or {}
    board = state.get("board") or {}
    current = state.get("current") or {"active_rows": [], "inactive_rows": []}
    active_rows = list(current.get("active_rows") or [])
    inactive_rows = list(current.get("inactive_rows") or [])
    active_row_heights = _row_heights(active_rows)
    inactive_row_heights = _row_heights(inactive_rows)
    height = MARGIN + HEADER_HEIGHT + 18
    height += SECTION_HEADER + TABLE_HEAD + sum(active_row_heights)
    if inactive_rows:
        height += 18 + SECTION_HEADER + TABLE_HEAD + sum(inactive_row_heights)
    height += MARGIN
    height = max(height, 720)

    image = Image.new("RGB", (CANVAS_WIDTH, height), BG)
    draw = ImageDraw.Draw(image)
    fonts = _fonts()

    y = MARGIN
    _rounded_rect(draw, (MARGIN, y, CANVAS_WIDTH - MARGIN, y + HEADER_HEIGHT), CARD_RADIUS, fill=TEAL)
    _draw_header(draw, state, fonts, y)
    y += HEADER_HEIGHT + 18

    y = _draw_section(draw, active_rows, "本轮参与参数", f"活跃 {len(active_rows)}", y, fonts, quiet=False)
    if inactive_rows:
        y += 18
        _draw_section(draw, inactive_rows, "本轮未参与参数", f"静默 {len(inactive_rows)}", y, fonts, quiet=True)

    session_id = int(session.get("id") or 0)
    round_id = int(round_row.get("id") or 0)
    round_no = int(round_row.get("round_no") or 0)
    result = str(round_row.get("result") or "pending")
    filename = f"session-{session_id}-round-{round_no}-{round_id}-result-{result}.png"
    path = output / filename
    _save_png_atomic(image, path)
    return path.resolve()


def render_session_summary_report(state: dict[str, Any], output_dir: str | Path = "C:/Temp/redledger-reports") -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    session = state["session"]
    summary = state.get("summary") or {}
    banker = state.get("banker") or {}
    rounds = list(state.get("rounds") or [])
    settled_rounds = [
        round_row for round_row in rounds
        if str(round_row.get("status") or "") == "settled" and round_row.get("display_round_no") is not None
    ]
    settled_count = int(summary.get("round_count") or len(settled_rounds))
    generated_at = str(state.get("board", {}).get("generated_at") or "")
    if not generated_at:
        from datetime import datetime

        generated_at = datetime.now().isoformat(timespec="seconds")

    pos_rows = _summary_rows(summary.get("positive") or [])
    neg_rows = _summary_rows(summary.get("negative") or [])
    zero_rows = _summary_rows(summary.get("zero") or [])
    top_cards_height = 92
    summary_header_height = 170
    table_top = MARGIN + summary_header_height + 22 + top_cards_height + 18
    left_height = _summary_table_height(pos_rows)
    middle_height = _summary_table_height(neg_rows)
    right_height = _summary_table_height(zero_rows)
    content_height = max(left_height, middle_height, right_height)
    height = max(820, table_top + content_height + MARGIN)

    image = Image.new("RGB", (CANVAS_WIDTH, height), BG)
    draw = ImageDraw.Draw(image)
    fonts = _fonts()
    y = MARGIN
    _rounded_rect(draw, (MARGIN, y, CANVAS_WIDTH - MARGIN, y + summary_header_height), CARD_RADIUS, fill=TEAL)
    _draw_summary_header(draw, state, generated_at, fonts, y)
    y += summary_header_height + 22
    _draw_summary_metric_cards(draw, summary, banker, settled_count, y, fonts)
    y += top_cards_height + 18
    col_gap = 16
    column_width = (CANVAS_WIDTH - MARGIN * 2 - col_gap * 2) // 3
    x1 = MARGIN
    x2 = x1 + column_width + col_gap
    x3 = x2 + column_width + col_gap
    _draw_summary_table(draw, x1, y, column_width, "正向用户", f"累计总和 {int(summary.get('positive_total') or 0)}", pos_rows, POSITIVE, fonts)
    _draw_summary_table(draw, x2, y, column_width, "负向用户", f"累计总和 {int(summary.get('negative_total') or 0)}", neg_rows, NEGATIVE, fonts)
    _draw_summary_table(draw, x3, y, column_width, "扯平用户", "累计总和 0", zero_rows, ZERO, fonts)

    session_id = int(session.get("id") or 0)
    filename = f"session-{session_id}-summary-{settled_count}-rounds.png"
    path = output / filename
    _save_png_atomic(image, path)
    return path.resolve()


def _draw_header(draw: ImageDraw.ImageDraw, state: dict[str, Any], fonts: dict[str, ImageFont.ImageFont], y: int) -> None:
    session = state["session"]
    board = state.get("board") or {}
    round_row = state.get("active_round") or {}
    batch_code = str(board.get("batch_code") or "")
    route = str(board.get("route") or "")
    result = str(round_row.get("result") or "")
    batch_no = board.get("batch_no") or round_row.get("round_no") or ""
    title = _clean_text(f"第{batch_no}批次号{batch_code}")
    group = _clean_text(str(session.get("group_name") or "微信群"))
    generated = str(board.get("generated_at") or "")
    current = state.get("current") or {}
    inactive_rows = list(current.get("inactive_rows") or [])
    if inactive_rows:
        subtitle = _clean_text(f"双区看板：行为产出与静默状态一屏可见。- [{generated.replace('T', ' ')}]")
    else:
        subtitle = _clean_text(f"本轮参与看板：产出、累计与输出日志一屏可见。- [{generated.replace('T', ' ')}]")
    x = MARGIN + 28
    draw.text((x, y + 22), _fit_text(draw, group, fonts["small_bold"], 460), font=fonts["small_bold"], fill=(226, 255, 251))
    hero_font = _fit_font(title, fonts["hero"], fonts["hero_small"], draw, 1010)
    draw.text((x, y + 50), _fit_text(draw, title, hero_font, 1010), font=hero_font, fill=(255, 255, 255))
    draw.text((x, y + 103), _fit_text(draw, subtitle, fonts["body"], 900), font=fonts["body"], fill=(231, 255, 252))
    result = str(round_row.get("result") or "待开奖")
    badge = f"本局答案：{result}"
    bx2 = CANVAS_WIDTH - MARGIN - 28
    bw = _text_width(draw, badge, fonts["badge"]) + 34
    _rounded_rect(draw, (bx2 - bw, y + 32, bx2, y + 70), 19, fill=(231, 255, 252))
    draw.text((bx2 - bw + 17, y + 41), badge, font=fonts["badge"], fill=TEAL_DARK)

    report_status = str(state.get("report_status") or "complete").strip().lower()
    report_issues = [str(item).strip() for item in state.get("report_issues") or [] if str(item).strip()]
    if report_status != "complete":
        label = "缺失标注版" if report_status == "missing" else "异常标注版"
        if report_issues:
            label = f"{label}：{report_issues[0]}"
        label = _fit_text(draw, label, fonts["tiny"], 470)
        status_w = min(520, _text_width(draw, label, fonts["tiny"]) + 28)
        status_left = bx2 - status_w
        status_fill = (255, 238, 198) if report_status == "missing" else (255, 220, 220)
        status_ink = (126, 82, 20) if report_status == "missing" else (135, 32, 42)
        _rounded_rect(draw, (status_left, y + 84, bx2, y + 120), 16, fill=status_fill)
        draw.text((status_left + 14, y + 94), label, font=fonts["tiny"], fill=status_ink)


def _draw_summary_header(
    draw: ImageDraw.ImageDraw,
    state: dict[str, Any],
    generated_at: str,
    fonts: dict[str, ImageFont.ImageFont],
    y: int,
) -> None:
    session = state["session"]
    summary = state.get("summary") or {}
    rounds = list(state.get("rounds") or [])
    settled_count = int(summary.get("round_count") or 0)
    group = _clean_text(str(session.get("group_name") or "微信群"))
    x = MARGIN + 28
    draw.text((x, y + 22), _fit_text(draw, f"场次 {session.get('id')}", fonts["small_bold"], 260), font=fonts["small_bold"], fill=(226, 255, 251))
    draw.text((x, y + 50), "总览汇总", font=fonts["hero"], fill=(255, 255, 255))
    started = str(session.get("started_at") or "").replace("T", " ")
    ended = str(session.get("ended_at") or generated_at).replace("T", " ")
    subtitle = _clean_text(f"场次时间：{started} - {ended}")
    draw.text((x, y + 103), _fit_text(draw, subtitle, fonts["body"], 900), font=fonts["body"], fill=(231, 255, 252))
    badge_texts = [_fit_text(draw, group, fonts["badge"], 230), f"第 {settled_count or len(rounds)} 局封板"]
    bx = x
    for badge in badge_texts:
        bw = _text_width(draw, badge, fonts["badge"]) + 34
        _rounded_rect(draw, (bx, y + 132, bx + bw, y + 170), 19, fill=(231, 255, 252))
        draw.text((bx + 17, y + 141), badge, font=fonts["badge"], fill=TEAL_DARK)
        bx += bw + 12


def _draw_summary_metric_cards(
    draw: ImageDraw.ImageDraw,
    summary: dict[str, Any],
    banker: dict[str, Any],
    settled_rounds: int,
    y: int,
    fonts: dict[str, ImageFont.ImageFont],
) -> None:
    gap = 14
    width = (CANVAS_WIDTH - MARGIN * 2 - gap * 2) // 3
    cards = [
        ("发起人总览", str(banker.get("display_name") or "-"), "发起方"),
        ("发起人总盈亏", str(int(summary.get("banker_total") or 0)), ""),
        ("已结算局数", str(settled_rounds), ""),
    ]
    for idx, (label, value, badge) in enumerate(cards):
        left = MARGIN + idx * (width + gap)
        right = left + width
        _rounded_rect(draw, (left, y, right, y + 92), 10, fill=PAPER)
        draw.text((left + 22, y + 18), label, font=fonts["small_bold"], fill=MUTED)
        if idx == 1:
            amount = int(value) if value.lstrip("-").isdigit() else 0
            value_color = POSITIVE if amount > 0 else NEGATIVE if amount < 0 else ZERO
        elif idx == 2:
            value_color = TEAL_DARK
        else:
            value_color = INK
        if idx == 0:
            _draw_avatar(draw, banker, left + 22, y + 47, 28, fonts["tiny"], TEAL_LIGHT, TEAL_DARK)
            draw.text((left + 60, y + 48), _fit_text(draw, _clean_text(value), fonts["metric"], width - 122), font=fonts["metric"], fill=value_color)
        else:
            draw.text((left + 22, y + 48), _fit_text(draw, _clean_text(value), fonts["metric"], width - 44), font=fonts["metric"], fill=value_color)
        if badge:
            bw = _text_width(draw, badge, fonts["tiny"]) + 24
            _rounded_rect(draw, (right - bw - 22, y + 48, right - 22, y + 73), 12, fill=TEAL_LIGHT)
            draw.text((right - bw - 10, y + 54), badge, font=fonts["tiny"], fill=TEAL_DARK)


def _summary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _summary_table_height(rows: list[dict[str, Any]]) -> int:
    return SECTION_HEADER + TABLE_HEAD + max(1, len(rows)) * MIN_ROW_HEIGHT


def _draw_summary_table(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    title: str,
    count_text: str,
    rows: list[dict[str, Any]],
    amount_color: tuple[int, int, int],
    fonts: dict[str, ImageFont.ImageFont],
) -> None:
    height = _summary_table_height(rows)
    _rounded_rect(draw, (x, y, x + width, y + height), CARD_RADIUS, fill=PAPER)
    draw.rectangle((x, y + CARD_RADIUS, x + width, y + height - CARD_RADIUS), fill=PAPER)
    draw.rectangle((x + CARD_RADIUS, y, x + width - CARD_RADIUS, y + height), fill=PAPER)
    draw.text((x + 22, y + 19), title, font=fonts["section"], fill=INK)
    badge_w = _text_width(draw, count_text, fonts["badge"]) + 24
    badge_fill = (234, 248, 242) if amount_color == POSITIVE else (255, 242, 242) if amount_color == NEGATIVE else (247, 249, 252)
    _rounded_rect(draw, (x + width - badge_w - 18, y + 14, x + width - 18, y + 46), 16, fill=badge_fill, outline=LINE)
    draw.text((x + width - badge_w - 6, y + 22), _fit_text(draw, count_text, fonts["badge"], badge_w - 24), font=fonts["badge"], fill=amount_color)
    head_y = y + SECTION_HEADER
    draw.rectangle((x, head_y, x + width, head_y + TABLE_HEAD), fill=TEAL_LIGHT if amount_color != NEGATIVE else (255, 230, 232))
    draw.text((x + 22, head_y + 14), "序号", font=fonts["th"], fill=INK)
    draw.text((x + 76, head_y + 14), "账号", font=fonts["th"], fill=INK)
    _draw_cell_text(draw, "累计产出", x + width - 120, head_y + 14, 98, fonts["th"], INK, align_right=True)
    body_y = head_y + TABLE_HEAD
    if not rows:
        draw.line((x, body_y, x + width, body_y), fill=LINE)
        _draw_cell_text(draw, "暂无", x, body_y + 18, width, fonts["body_bold"], MUTED, align_center=True)
        return
    for index, row in enumerate(rows, start=1):
        ry = body_y + (index - 1) * MIN_ROW_HEIGHT
        draw.line((x, ry, x + width, ry), fill=LINE)
        member = row.get("member") or {}
        amount = int(row.get("amount") or 0)
        draw.text((x + 25, ry + 18), str(index), font=fonts["index"], fill=TEAL_DARK)
        avatar_x = x + 76
        avatar_y = ry + 14
        name = _clean_text(str(member.get("display_name") or "未知成员"))
        _draw_avatar(draw, member, avatar_x, avatar_y, 28, fonts["tiny"], (218, 236, 232), TEAL_DARK)
        _draw_cell_text(draw, name, avatar_x + 40, ry + 17, width - 238, fonts["name"], INK)
        color = POSITIVE if amount > 0 else NEGATIVE if amount < 0 else ZERO
        if amount_color == NEGATIVE and amount < 0:
            color = NEGATIVE
        _draw_cell_text(draw, str(amount), x + width - 132, ry + 17, 110, fonts["amount"], color, align_right=True)


def _draw_section(
    draw: ImageDraw.ImageDraw,
    rows: list[dict[str, Any]],
    title: str,
    count_text: str,
    y: int,
    fonts: dict[str, ImageFont.ImageFont],
    quiet: bool,
) -> int:
    row_heights = _row_heights(rows)
    height = SECTION_HEADER + TABLE_HEAD + sum(row_heights)
    left, right = MARGIN, CANVAS_WIDTH - MARGIN
    _rounded_rect(draw, (left, y, right, y + height), CARD_RADIUS, fill=PAPER)
    draw.rectangle((left, y + CARD_RADIUS, right, y + height - CARD_RADIUS), fill=PAPER)
    draw.rectangle((left + CARD_RADIUS, y, right - CARD_RADIUS, y + height), fill=PAPER)

    draw.text((left + 24, y + 19), title, font=fonts["section"], fill=INK)
    badge_w = _text_width(draw, count_text, fonts["badge"]) + 28
    _rounded_rect(draw, (right - badge_w - 22, y + 14, right - 22, y + 46), 16, fill=(255, 255, 255), outline=LINE)
    draw.text((right - badge_w - 8, y + 22), count_text, font=fonts["badge"], fill=INK)

    head_y = y + SECTION_HEADER
    draw.rectangle((left, head_y, right, head_y + TABLE_HEAD), fill=QUIET_HEAD if quiet else TEAL_LIGHT)
    columns = _columns()
    headers = ["序号", "账号", "本轮产出", "累计产出", "输出日志"]
    for idx, (label, (x1, x2)) in enumerate(zip(headers, columns, strict=True)):
        align_right = idx in {2, 3}
        padding_left = 8 if idx == 4 else 0
        padding_right = 12 if idx in {2, 3} else 0
        _draw_cell_text(
            draw,
            label,
            x1 + padding_left,
            head_y + 14,
            x2 - x1 - padding_left - padding_right,
            fonts["th"],
            INK,
            align_right=align_right,
        )

    body_y = head_y + TABLE_HEAD
    if not rows:
        draw.line((left, body_y, right, body_y), fill=LINE)
        msg = "本轮暂无参与成员" if not quiet else "暂无静默成员"
        _draw_cell_text(draw, msg, left, body_y + 18, right - left, fonts["body_bold"], MUTED, align_center=True)
        return y + height

    ry = body_y
    for row, row_height in zip(rows, row_heights, strict=True):
        draw.line((left, ry, right, ry), fill=LINE)
        _draw_row(draw, row, ry, row_height, fonts, quiet)
        ry += row_height
    return y + height


def _draw_row(
    draw: ImageDraw.ImageDraw,
    row: dict[str, Any],
    y: int,
    row_height: int,
    fonts: dict[str, ImageFont.ImageFont],
    quiet: bool,
) -> None:
    cols = _columns()
    member = row.get("member") or {}
    center_y = y + max(0, (row_height - MIN_ROW_HEIGHT) // 2)
    draw.text((cols[0][0] + 22, center_y + 18), str(row.get("index") or ""), font=fonts["index"], fill=TEAL_DARK)

    avatar_x = cols[1][0]
    avatar_y = center_y + 14
    name_x = avatar_x + 40
    _draw_avatar(draw, member, avatar_x, avatar_y, 28, fonts["tiny"], (218, 236, 232), TEAL_DARK)
    name = _clean_text(str(member.get("display_name") or "未知成员"))
    max_name_width = cols[1][1] - name_x - (70 if row.get("is_banker") else 8)
    draw.text((name_x, center_y + 16), _fit_text(draw, name, fonts["name"], max_name_width), font=fonts["name"], fill=INK)
    if row.get("is_banker"):
        _rounded_rect(draw, (cols[1][1] - 66, center_y + 15, cols[1][1] - 12, center_y + 39), 12, fill=TEAL_LIGHT)
        draw.text((cols[1][1] - 58, center_y + 20), "发起方", font=fonts["tiny"], fill=TEAL_DARK)

    is_settled = bool(row.get("is_settled"))
    round_output = row.get("round_output", 0) if is_settled else "待开奖"
    cumulative = row.get("cumulative_output", 0) if is_settled else "待开奖"
    _draw_amount(draw, round_output, cols[2], center_y, fonts, is_settled)
    _draw_amount(draw, cumulative, cols[3], center_y, fonts, is_settled)
    log_items = list(row.get("log_items") or [])
    if log_items:
        _draw_log_items(draw, log_items, cols[4][0], y + 8, cols[4][1] - cols[4][0] - 10, fonts)
    else:
        log = _clean_text(str(row.get("log") or ("未参加本轮" if quiet else "")))
        _draw_wrapped(draw, log, cols[4][0], y + 9, cols[4][1] - cols[4][0] - 10, 2, fonts["log"], INK)


def _draw_amount(
    draw: ImageDraw.ImageDraw,
    value: Any,
    column: tuple[int, int],
    y: int,
    fonts: dict[str, ImageFont.ImageFont],
    settled: bool,
) -> None:
    text = str(int(value)) if isinstance(value, int) or str(value).lstrip("-").isdigit() else str(value)
    if not settled:
        color = MUTED
    else:
        amount = int(text) if text.lstrip("-").isdigit() else 0
        color = POSITIVE if amount > 0 else NEGATIVE if amount < 0 else ZERO
    _draw_cell_text(draw, text, column[0], y + 17, column[1] - column[0] - 14, fonts["amount"], color, align_right=True)


def _row_heights(rows: list[dict[str, Any]]) -> list[int]:
    if not rows:
        return [MIN_ROW_HEIGHT]
    return [_row_height(row) for row in rows]


def _row_height(row: dict[str, Any]) -> int:
    log_items = list(row.get("log_items") or [])
    if not log_items:
        return MIN_ROW_HEIGHT
    lines = 0
    groups = 0
    for item in log_items:
        groups += 1
        if item.get("kind") == "bet_group":
            entries = list(item.get("entries") or [])
            lines += max(1, len(entries))
            lines += sum(1 for entry in entries if entry.get("revoke"))
        else:
            lines += 1
    height = 16 + lines * LOG_ENTRY_HEIGHT + max(0, groups - 1) * LOG_GROUP_GAP
    return max(MIN_ROW_HEIGHT, height)


def _draw_log_items(
    draw: ImageDraw.ImageDraw,
    items: list[dict[str, Any]],
    x: int,
    y: int,
    width: int,
    fonts: dict[str, ImageFont.ImageFont],
) -> None:
    time_width = 126
    text_x = x + time_width + 10
    text_width = max(80, width - time_width - 10)
    cursor_y = y
    for item in items:
        if item.get("kind") == "bet_group":
            time_text = _clean_text(str(item.get("time") or ""))
            draw.text((x, cursor_y + 1), _fit_text(draw, time_text, fonts["log_time"], time_width), font=fonts["log_time"], fill=MUTED)
            entries = list(item.get("entries") or [])
            entry_y = cursor_y
            for entry in entries or [{"bet": "", "settlement": "", "revoke": ""}]:
                bet = _clean_text(str(entry.get("bet") or ""))
                settlement = _clean_text(str(entry.get("settlement") or ""))
                _draw_log_entry(draw, bet, settlement, text_x, entry_y, text_width, fonts)
                entry_y += LOG_ENTRY_HEIGHT
                revoke = _clean_text(str(entry.get("revoke") or ""))
                if revoke:
                    draw.text((text_x, entry_y), _fit_text(draw, revoke, fonts["log_revoke"], text_width), font=fonts["log_revoke"], fill=(133, 101, 26))
                    entry_y += LOG_ENTRY_HEIGHT
            cursor_y = entry_y + LOG_GROUP_GAP
        else:
            text = _clean_text(str(item.get("text") or ""))
            draw.text((x, cursor_y + 1), _fit_text(draw, text, fonts["log_note"], width), font=fonts["log_note"], fill=MUTED)
            cursor_y += LOG_ENTRY_HEIGHT + LOG_GROUP_GAP


def _draw_log_entry(
    draw: ImageDraw.ImageDraw,
    bet: str,
    settlement: str,
    x: int,
    y: int,
    width: int,
    fonts: dict[str, ImageFont.ImageFont],
) -> None:
    bet_width_limit = width if not settlement else max(56, width - 118)
    fitted_bet = _fit_text(draw, bet, fonts["log_bet"], bet_width_limit)
    draw.text((x, y), fitted_bet, font=fonts["log_bet"], fill=TEAL_DARK)
    if settlement:
        settlement_x = x + _text_width(draw, fitted_bet, fonts["log_bet"]) + 10
        settlement_width = max(0, width - (settlement_x - x))
        color = NEGATIVE if settlement.startswith("输") else POSITIVE if settlement.startswith("赔") else ZERO
        draw.text(
            (settlement_x, y),
            _fit_text(draw, settlement, fonts["log_settlement"], settlement_width),
            font=fonts["log_settlement"],
            fill=color,
        )


def _columns() -> list[tuple[int, int]]:
    left = MARGIN + 24
    return [
        (left, left + 72),
        (left + 72, left + 330),
        (left + 330, left + 475),
        (left + 485, left + 630),
        (left + 650, CANVAS_WIDTH - MARGIN - 24),
    ]


def _fonts() -> dict[str, ImageFont.ImageFont]:
    regular = _font_path("msyh.ttc")
    bold = _font_path("msyhbd.ttc") or regular
    fallback = regular or _font_path("simhei.ttf")

    def load(path: Path | None, size: int) -> ImageFont.ImageFont:
        if path:
            return ImageFont.truetype(str(path), size)
        return ImageFont.load_default()

    return {
        "hero": load(bold or fallback, 40),
        "hero_small": load(bold or fallback, 30),
        "metric": load(bold or fallback, 26),
        "section": load(bold or fallback, 21),
        "th": load(bold or fallback, 15),
        "name": load(bold or fallback, 17),
        "body": load(fallback, 17),
        "body_bold": load(bold or fallback, 17),
        "small": load(fallback, 13),
        "small_bold": load(bold or fallback, 14),
        "badge": load(bold or fallback, 13),
        "amount": load(bold or fallback, 19),
        "index": load(bold or fallback, 17),
        "tiny": load(bold or fallback, 11),
        "log": load(fallback, 14),
        "log_time": load(bold or fallback, 12),
        "log_bet": load(bold or fallback, 14),
        "log_settlement": load(bold or fallback, 13),
        "log_revoke": load(fallback, 11),
        "log_note": load(bold or fallback, 13),
    }


def _font_path(name: str) -> Path | None:
    path = Path("C:/Windows/Fonts") / name
    return path if path.exists() else None


def _rounded_rect(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    radius: int,
    fill: tuple[int, int, int],
    outline: tuple[int, int, int] | None = None,
) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline)


def _draw_cell_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    width: int,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    align_right: bool = False,
    align_center: bool = False,
) -> None:
    fitted = _fit_text(draw, text, font, width)
    tw = _text_width(draw, fitted, font)
    if align_right:
        tx = x + max(0, width - tw)
    elif align_center:
        tx = x + max(0, (width - tw) // 2)
    else:
        tx = x
    draw.text((tx, y), fitted, font=font, fill=fill)


def _draw_wrapped(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    width: int,
    max_lines: int,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    lines = _wrap_text(draw, text, font, width, max_lines)
    for idx, line in enumerate(lines):
        draw.text((x, y + idx * 20), line, font=font, fill=fill)


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    width: int,
    max_lines: int,
) -> list[str]:
    text = " ".join(str(text or "").replace("\n", " | ").split())
    if not text:
        return [""]
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if _text_width(draw, candidate, font) <= width or not current:
            current = candidate
            continue
        lines.append(current)
        current = char
        if len(lines) == max_lines:
            break
    if len(lines) < max_lines and current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if len(lines) == max_lines and _text_width(draw, lines[-1], font) > width:
        lines[-1] = _fit_text(draw, lines[-1], font, width)
    elif len(lines) == max_lines and len("".join(lines)) < len(text):
        lines[-1] = _fit_text(draw, lines[-1] + "...", font, width)
    return lines


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> str:
    text = str(text or "")
    if _text_width(draw, text, font) <= width:
        return text
    ellipsis = "..."
    result = ""
    for char in text:
        if _text_width(draw, result + char + ellipsis, font) > width:
            return result + ellipsis if result else ellipsis
        result += char
    return result


def _fit_font(
    text: str,
    regular: ImageFont.ImageFont,
    compact: ImageFont.ImageFont,
    draw: ImageDraw.ImageDraw,
    width: int,
) -> ImageFont.ImageFont:
    return regular if _text_width(draw, text, regular) <= width else compact


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return int(box[2] - box[0])


def _draw_avatar(
    draw: ImageDraw.ImageDraw,
    member: dict[str, Any],
    x: int,
    y: int,
    size: int,
    font: ImageFont.ImageFont,
    fallback_fill: tuple[int, int, int],
    fallback_text_fill: tuple[int, int, int],
) -> None:
    name = _clean_text(str(member.get("display_name") or "?"))
    avatar = _avatar_image(str(member.get("avatar_url") or ""), size)
    base = getattr(draw, "_image", None)
    if avatar is not None and base is not None:
        mask = Image.new("L", (size, size), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle((0, 0, size, size), radius=max(4, size // 5), fill=255)
        base.paste(avatar, (x, y), mask)
        return

    _rounded_rect(draw, (x, y, x + size, y + size), max(4, size // 5), fill=fallback_fill)
    _draw_cell_text(draw, _initials(name), x, y + max(3, size // 4), size, font, fallback_text_fill, align_center=True)


def _avatar_image(avatar_url: str, size: int) -> Image.Image | None:
    source = _load_avatar_source(avatar_url)
    if source is None:
        return None
    image = source.copy().convert("RGB")
    width, height = image.size
    if width <= 0 or height <= 0:
        return None
    side = min(width, height)
    left = max(0, (width - side) // 2)
    top = max(0, (height - side) // 2)
    image = image.crop((left, top, left + side, top + side))
    return image.resize((size, size), Image.Resampling.LANCZOS)


def _load_avatar_source(avatar_url: str) -> Image.Image | None:
    url = _normalize_avatar_url(avatar_url)
    if not url:
        return None
    with _AVATAR_CACHE_LOCK:
        cached = _AVATAR_CACHE.get(url)
    if cached is not None:
        return cached

    if url.lower().startswith(("http://", "https://")):
        image = _load_disk_cached_avatar(url)
        if image is not None:
            with _AVATAR_CACHE_LOCK:
                _AVATAR_CACHE[url] = image
            return image
        _schedule_avatar_fetch(url)
        return None

    try:
        path = Path(url)
        image = Image.open(path).convert("RGBA") if path.exists() and path.is_file() else None
    except (OSError, ValueError, urlerror.URLError, TimeoutError):
        image = None
    if image is not None:
        with _AVATAR_CACHE_LOCK:
            _AVATAR_CACHE[url] = image
    return image


def prefetch_avatar_urls(avatar_urls: list[str] | set[str] | tuple[str, ...]) -> None:
    """Queue remote avatars without putting network I/O on the report path."""
    for avatar_url in avatar_urls:
        url = _normalize_avatar_url(avatar_url)
        if url.lower().startswith(("http://", "https://")):
            _schedule_avatar_fetch(url)


def _normalize_avatar_url(avatar_url: str) -> str:
    url = unescape(str(avatar_url or "").strip().strip('"'))
    return "https:" + url if url.startswith("//") else url


def _avatar_cache_dir() -> Path:
    configured = os.environ.get("REDLEDGER_AVATAR_CACHE_DIR", "").strip()
    return Path(configured or "C:/Temp/redledger-avatar-cache")


def _avatar_cache_path(url: str) -> Path:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return _avatar_cache_dir() / f"{key}.png"


def _load_disk_cached_avatar(url: str) -> Image.Image | None:
    path = _avatar_cache_path(url)
    try:
        if path.is_file():
            with Image.open(path) as source:
                return source.convert("RGBA")
    except (OSError, ValueError):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    return None


def _schedule_avatar_fetch(url: str) -> None:
    global _AVATAR_WORKERS_STARTED

    now = time.monotonic()
    with _AVATAR_CACHE_LOCK:
        if url in _AVATAR_CACHE or url in _AVATAR_FETCHING:
            return
        if now < _AVATAR_RETRY_AFTER.get(url, 0.0):
            return
        if not _AVATAR_WORKERS_STARTED:
            for index in range(_AVATAR_WORKER_COUNT):
                threading.Thread(
                    target=_avatar_fetch_worker,
                    name=f"redledger-avatar-{index + 1}",
                    daemon=True,
                ).start()
            _AVATAR_WORKERS_STARTED = True
        _AVATAR_FETCHING.add(url)
    try:
        _AVATAR_FETCH_QUEUE.put_nowait(url)
    except queue.Full:
        with _AVATAR_CACHE_LOCK:
            _AVATAR_FETCHING.discard(url)
            _AVATAR_RETRY_AFTER[url] = now + _AVATAR_RETRY_SECONDS


def _avatar_fetch_worker() -> None:
    while True:
        url = _AVATAR_FETCH_QUEUE.get()
        try:
            _download_avatar(url)
        except Exception:  # noqa: BLE001 - one bad avatar must not stop the cache workers.
            with _AVATAR_CACHE_LOCK:
                _AVATAR_RETRY_AFTER[url] = time.monotonic() + _AVATAR_RETRY_SECONDS
        finally:
            with _AVATAR_CACHE_LOCK:
                _AVATAR_FETCHING.discard(url)
            _AVATAR_FETCH_QUEUE.task_done()


def _download_avatar(url: str) -> None:
    image: Image.Image | None = None
    try:
        request = urlrequest.Request(url, headers={"User-Agent": "Mozilla/5.0 RedLedger/1.0"})
        with urlrequest.urlopen(request, timeout=_AVATAR_TIMEOUT_SECONDS) as response:
            raw = response.read(_AVATAR_MAX_BYTES)
        with Image.open(io.BytesIO(raw)) as source:
            image = source.convert("RGBA")
        cache_path = _avatar_cache_path(url)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_name(f".{cache_path.name}.{threading.get_ident()}.tmp")
        image.save(temporary, "PNG", optimize=True)
        os.replace(temporary, cache_path)
    except (OSError, ValueError, urlerror.URLError, TimeoutError):
        with _AVATAR_CACHE_LOCK:
            _AVATAR_RETRY_AFTER[url] = time.monotonic() + _AVATAR_RETRY_SECONDS
        return
    with _AVATAR_CACHE_LOCK:
        _AVATAR_CACHE[url] = image
        _AVATAR_RETRY_AFTER.pop(url, None)


def _initials(name: str) -> str:
    name = name.strip()
    return name[:2] if name else "?"


def _clean_text(text: str) -> str:
    cleaned: list[str] = []
    for char in str(text or ""):
        code = ord(char)
        if code in {0x200B, 0x200C, 0x200D, 0xFE0F}:
            continue
        if code < 32:
            cleaned.append(" ")
            continue
        if code > 0xFFFF:
            cleaned.append(" ")
            continue
        cleaned.append(char)
    return "".join(cleaned).strip()
