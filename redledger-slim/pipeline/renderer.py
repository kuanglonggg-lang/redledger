"""
High-Performance Report Renderer Wrapper.
Takes CoreEngine settlement results and renders PNG images via report_image in < 80ms.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from datetime import datetime

try:
    from pipeline.report_image import render_round_report, render_session_summary_report
    from core.engine import Round, PlayerRoundSummary, Member
except ImportError:
    from .report_image import render_round_report, render_session_summary_report
    from ..core.engine import Round, PlayerRoundSummary, Member

class SlimRenderer:
    def __init__(self, output_dir: str = r"C:\Temp\redledger-reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def render(
        self,
        session_meta: dict[str, Any],
        round_obj: Round,
        summaries: list[PlayerRoundSummary],
        inactive_members: list[dict[str, Any]] | None = None
    ) -> Path:
        """
        Build minimal state dict and render PNG synchronously.
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        active_rows = []
        for idx, s in enumerate(summaries, 1):
            active_rows.append({
                "index": idx,
                "member": {
                    "id": s.member_id,
                    "display_name": s.member_name,
                    "wxid": "",
                    "avatar_url": s.avatar_url,
                    "is_banker": 1 if s.is_banker else 0
                },
                "is_banker": s.is_banker,
                "is_settled": True,
                "round_output": s.round_output,
                "cumulative_output": s.cumulative_output,
                "log": s.log_text,
                "log_items": [{"kind": "bet", "text": s.log_text, "entries": []}]
            })
            
        inactive_rows = inactive_members or []
        
        batch_no = round_obj.display_round_no or round_obj.round_no
        route_str = round_obj.route or ""
        
        state = {
            "session": {
                "id": session_meta.get("id", 0),
                "group_name": session_meta.get("group_name", "微信群"),
                "group_id": session_meta.get("group_id", ""),
                "banker_member_id": session_meta.get("banker_member_id", round_obj.banker_id)
            },
            "active_round": {
                "id": round_obj.id,
                "session_id": round_obj.session_id,
                "round_no": round_obj.round_no,
                "display_round_no": round_obj.display_round_no,
                "status": round_obj.status,
                "result": round_obj.result,
                "route": route_str,
                "opened_at": round_obj.opened_at,
                "sealed_at": round_obj.sealed_at,
                "settled_at": round_obj.settled_at,
            },
            "board": {
                "batch_no": batch_no,
                "batch_code": route_str,
                "route": route_str,
                "generated_at": now_str,
                "active_count": len(active_rows),
                "inactive_count": len(inactive_rows),
                "status_line": f"结算完成 / 答案 {round_obj.result} / 活跃 {len(active_rows)}"
            },
            "current": {
                "active_rows": active_rows,
                "inactive_rows": inactive_rows,
                "active_count": len(active_rows),
                "inactive_count": len(inactive_rows)
            }
        }
        
        return render_round_report(state, output_dir=self.output_dir)

    def render_session_summary(
        self,
        session_meta: dict[str, Any],
        banker_member: dict[str, Any],
        positive: list[dict[str, Any]],
        negative: list[dict[str, Any]],
        zero: list[dict[str, Any]],
        rounds_count: int,
        total_rounds: int
    ) -> Path:
        """
        Render session summary overview report PNG.
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        positive_total = sum(row["amount"] for row in positive)
        negative_total = sum(row["amount"] for row in negative)
        negative_magnitude_total = abs(negative_total)
        banker_total = negative_magnitude_total - positive_total

        summary = {
            "positive": positive,
            "negative": negative,
            "zero": zero,
            "positive_total": positive_total,
            "negative_total": negative_total,
            "negative_magnitude_total": negative_magnitude_total,
            "zero_total": 0,
            "round_count": rounds_count,
            "total_round_count": total_rounds,
            "unsettled_round_count": 0,
            "banker_total": banker_total,
            "recorded_banker_total": banker_total
        }

        state = {
            "session": {
                "id": session_meta.get("id", 0),
                "group_name": session_meta.get("group_name", "微信群"),
                "group_id": session_meta.get("group_id", ""),
                "banker_member_id": banker_member.get("id", 1),
                "title": session_meta.get("title", "wechat redpacket ledger"),
                "started_at": session_meta.get("started_at", ""),
                "ended_at": session_meta.get("ended_at", "") or now_str,
            },
            "banker": banker_member,
            "summary": summary,
            "board": {
                "generated_at": now_str
            },
            "rounds": []
        }

        return render_session_summary_report(state, output_dir=self.output_dir)

