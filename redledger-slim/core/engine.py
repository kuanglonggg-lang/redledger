"""
Pure In-Memory Core Engine.
Calculates payouts, player totals, and banker profit/loss without SQL contention.
"""
from dataclasses import dataclass, field
from typing import Any

try:
    from core.rules import BetParser, PayoutRules, BetSlip
    from core.route import clean_route, append_result_to_route, merge_canonical_route
except ImportError:
    from .rules import BetParser, PayoutRules, BetSlip
    from .route import clean_route, append_result_to_route, merge_canonical_route

@dataclass
class Member:
    id: int
    wxid: str
    display_name: str
    avatar_url: str = ""
    is_banker: bool = False
    cumulative_pnl: int = 0

@dataclass
class BetEntry:
    id: int
    member_id: int
    raw_text: str
    picks: str
    amount: int
    session_id: int = 0
    round_id: int = 0
    play_type: str = ""
    hit_count: int = 0
    gross_result: int = 0
    water: int = 0
    net_result: int = 0
    settled_text: str = ""
    is_revoked: bool = False
    status: str = "accepted"
    source_message_id: str = ""
    sub_index: int = 0
    created_at: str = ""

@dataclass
class Round:
    id: int
    session_id: int
    round_no: int
    display_round_no: int
    status: str  # 'open', 'sealed', 'settled'
    result: str = ""
    route: str = ""
    banker_id: int = 0
    banker_pnl: int = 0
    bets: list[BetEntry] = field(default_factory=list)
    opened_at: str = ""
    sealed_at: str = ""
    settled_at: str = ""

@dataclass
class PlayerRoundSummary:
    member_id: int
    member_name: str
    avatar_url: str
    is_banker: bool
    round_output: int
    cumulative_output: int
    log_text: str
    active: bool

class CoreEngine:
    """
    Pure memory business engine without SQL blocking dependencies.
    Executes all settlement calculations in < 1 millisecond.
    """
    def __init__(self, settings: dict[str, Any] | None = None):
        self.parser = BetParser()
        self.settings = settings or {
            "water_rate_bp": 400,
            "water_unit": 10,
            "water_free_max_win": 100,
            "water_free_half_multiplier": True,
            "water_profile": "default",
            "water_rules": "",
            "water_rules_enabled": False,
            "special_water_rules": "",
            "special_water_rules_enabled": False,
        }

    def parse_bets(self, text: str, member_id: int, created_at: str = "", source_message_id: str = "") -> list[BetEntry]:
        """Parse raw WeChat message text into BetEntries."""
        slips = self.parser.parse(text)
        entries = []
        for idx, s in enumerate(slips):
            entries.append(BetEntry(
                id=0,
                member_id=member_id,
                raw_text=getattr(s, 'token', getattr(s, 'raw_text', text)),
                picks=str(getattr(s, 'target', getattr(s, 'picks', ''))),
                amount=int(getattr(s, 'amount', 0)),
                play_type=getattr(s, 'play_type', ''),
                status="accepted",
                source_message_id=source_message_id,
                sub_index=idx,
                created_at=created_at
            ))
        return entries

    def settle_round(
        self, 
        round_data: Round, 
        result: str, 
        members_map: dict[int, Member],
        historical_pnl: dict[int, int]
    ) -> tuple[Round, list[PlayerRoundSummary]]:
        """
        Settle a round given result '1', '2', '3', or '4'.
        Returns updated Round object and sorted PlayerRoundSummary list.
        """
        res = str(result).strip()
        if res not in {"1", "2", "3", "4"}:
            raise ValueError(f"Invalid result: {result}")
        
        round_data.result = res
        round_data.status = "settled"
        if round_data.route:
            round_data.route = append_result_to_route(round_data.route, res)
        else:
            round_data.route = res
            
        banker_total_pnl = 0
        player_summaries: dict[int, PlayerRoundSummary] = {}
        
        water_bp = int(self.settings.get("water_rate_bp", 400))
        water_unit = int(self.settings.get("water_unit", 10))
        water_free_max_win = int(self.settings.get("water_free_max_win", 100))
        water_free_half_multiplier = bool(self.settings.get("water_free_half_multiplier", True))
        water_profile = str(self.settings.get("water_profile", "default"))
        water_rules = str(self.settings.get("water_rules", "")) if self.settings.get("water_rules_enabled") else ""
        special_water_rules = str(self.settings.get("special_water_rules", "")) if self.settings.get("special_water_rules_enabled") else ""
        
        # Settle active bets (exclude revoked and rejected bets)
        player_bets: dict[int, list[BetEntry]] = {}
        for b in round_data.bets:
            if b.is_revoked or getattr(b, 'status', 'accepted') == 'rejected':
                continue
            player_bets.setdefault(b.member_id, []).append(b)
            
        # Calculate per-player round outcomes
        for m_id, b_list in player_bets.items():
            member = members_map.get(m_id) or Member(id=m_id, wxid="", display_name=f"玩家_{m_id}")
            member_round_pnl = 0
            log_parts = []
            
            for bet in b_list:
                calc = PayoutRules.calculate(
                    amount=int(bet.amount),
                    picks=str(bet.picks),
                    result=res,
                    water_bp=water_bp,
                    water_unit=water_unit,
                    water_free_max_win=water_free_max_win,
                    water_free_half_multiplier=water_free_half_multiplier,
                    play_type=bet.play_type,
                    water_profile=water_profile,
                    water_rules=water_rules,
                    special_water_rules=special_water_rules,
                )
                bet.hit_count = calc.get("hit_count", 0)
                bet.gross_result = calc.get("gross_result", 0)
                bet.water = calc.get("water", 0)
                bet.net_result = calc.get("net_result", 0)
                
                # Text formatting
                if bet.net_result > 0:
                    text_str = f"赔{bet.gross_result}-{bet.water}={bet.net_result}"
                elif bet.net_result < 0:
                    text_str = f"输{abs(bet.net_result)}"
                else:
                    text_str = "平"
                bet.settled_text = text_str
                
                member_round_pnl += bet.net_result
                log_parts.append(f"{bet.amount}/{bet.picks} {text_str}")
                    
            prev_cum = historical_pnl.get(m_id, 0)
            new_cum = prev_cum + member_round_pnl
            
            # From Banker perspective: Banker loses what players win
            banker_total_pnl -= member_round_pnl
            
            player_summaries[m_id] = PlayerRoundSummary(
                member_id=m_id,
                member_name=member.display_name,
                avatar_url=member.avatar_url,
                is_banker=False,
                round_output=member_round_pnl,
                cumulative_output=new_cum,
                log_text=" | ".join(log_parts),
                active=True
            )
            
        # Add Banker summary
        banker_id = round_data.banker_id
        banker_member = members_map.get(banker_id) or Member(id=banker_id, wxid="", display_name="庄家", is_banker=True)
        prev_banker_cum = historical_pnl.get(banker_id, 0)
        round_data.banker_pnl = banker_total_pnl
        
        banker_summary = PlayerRoundSummary(
            member_id=banker_id,
            member_name=banker_member.display_name,
            avatar_url=banker_member.avatar_url,
            is_banker=True,
            round_output=banker_total_pnl,
            cumulative_output=prev_banker_cum + banker_total_pnl,
            log_text=f"本局答案：{res}",
            active=True
        )
        
        final_list = [banker_summary]
        for s in player_summaries.values():
            final_list.append(s)
            
        return round_data, final_list
