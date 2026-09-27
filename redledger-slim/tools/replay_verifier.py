import sys
import os

# PyInstaller internal paths must come first
sys.path.insert(0, r"E:\RedLedger\server\RedLedgerServer\_internal\base_library.zip")
sys.path.insert(0, r"E:\RedLedger\runtime\Lib")
sys.path.insert(0, r"E:\RedLedger\runtime\Lib\site-packages")
sys.path.insert(0, r"E:\RedLedger\server\RedLedgerServer\_internal")
sys.path.insert(0, r"E:\RedLedger\redledger-slim")

import sqlite3
import time
import json
from typing import Any

from core.engine import CoreEngine, Round, BetEntry, Member
from core.route import clean_route

def load_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    settings = {}
    for r in rows:
        settings[r["key"]] = r["value"]
    return {
        "water_rate_bp": int(settings.get("water_rate_bp", 400)),
        "water_unit": int(settings.get("water_unit", 10)),
        "water_free_max_win": int(settings.get("water_free_max_win", 100)),
        "water_free_half_multiplier": bool(int(settings.get("water_free_half_multiplier", "1"))),
        "water_profile": str(settings.get("water_profile", "default")),
        "water_rules": str(settings.get("water_rules", "")),
        "water_rules_enabled": bool(int(settings.get("water_rules_enabled", "0"))),
        "special_water_rules": str(settings.get("special_water_rules", "")),
        "special_water_rules_enabled": bool(int(settings.get("special_water_rules_enabled", "0"))),
    }

def run_parity_test(db_path: str = r"E:\RedLedger\data\redledger.sqlite3", session_id: int = 1345):
    print(f"=== STARTING REPLAY PARITY VERIFICATION FOR SESSION {session_id} ===")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    settings = load_settings(conn)
    print(f"Loaded Settings: {settings}")
    
    engine = CoreEngine(settings=settings)
    
    # Load all members
    members_db = conn.execute("SELECT * FROM members").fetchall()
    members_map = {
        m["id"]: Member(
            id=m["id"],
            wxid=m["wxid"],
            display_name=m["display_name"],
            avatar_url=m["avatar_url"] or ""
        )
        for m in members_db
    }
    
    # Load rounds for session
    rounds_db = conn.execute("""
        SELECT * FROM rounds 
        WHERE session_id = ? AND status = 'settled' AND result IN ('1', '2', '3', '4')
        ORDER BY id ASC
    """, (session_id,)).fetchall()
    
    print(f"Loaded {len(rounds_db)} settled rounds from session {session_id}.")
    
    total_bets_checked = 0
    total_rounds_checked = 0
    mismatches = []
    
    historical_pnl = {}
    t_start = time.perf_counter()
    
    for r in rounds_db:
        round_id = r["id"]
        result = str(r["result"]).strip()
        
        # Load bets for this round from production DB
        bets_db = conn.execute("""
            SELECT * FROM bets 
            WHERE round_id = ? AND status = 'accepted'
            ORDER BY id ASC
        """, (round_id,)).fetchall()
        
        round_obj = Round(
            id=round_id,
            session_id=session_id,
            round_no=r["round_no"],
            display_round_no=r["display_round_no"] or r["round_no"],
            status="sealed",
            route=clean_route(r["route"] or ""),
            banker_id=session_id,
            bets=[
                BetEntry(
                    id=b["id"],
                    member_id=b["member_id"],
                    raw_text=b["raw_text"],
                    picks=b["picks"],
                    amount=b["amount"],
                    play_type=b["play_type"] or "",
                    is_revoked=False
                )
                for b in bets_db
            ]
        )
        
        # Run in-memory settlement through new slim engine
        settled_round, summaries = engine.settle_round(round_obj, result, members_map, historical_pnl)
        
        # Verify every bet's hit_count, gross_result, water, and net_result
        for bet_res, b_db in zip(round_obj.bets, bets_db):
            total_bets_checked += 1
            exp_hit = b_db["hit_count"] or 0
            exp_gross = b_db["gross_result"] or 0
            exp_water = b_db["water"] or 0
            exp_net = b_db["net_result"] or 0
            
            if (bet_res.hit_count != exp_hit or 
                bet_res.gross_result != exp_gross or 
                bet_res.water != exp_water or 
                bet_res.net_result != exp_net):
                mismatches.append({
                    "round_id": round_id,
                    "round_no": r["round_no"],
                    "bet_id": b_db["id"],
                    "member_id": b_db["member_id"],
                    "picks": b_db["picks"],
                    "amount": b_db["amount"],
                    "play_type": b_db["play_type"],
                    "result": result,
                    "calc": {
                        "hit": bet_res.hit_count,
                        "gross": bet_res.gross_result,
                        "water": bet_res.water,
                        "net": bet_res.net_result
                    },
                    "expected": {
                        "hit": exp_hit,
                        "gross": exp_gross,
                        "water": exp_water,
                        "net": exp_net
                    }
                })
                
        # Update historical pnl for next rounds
        for s in summaries:
            historical_pnl[s.member_id] = s.cumulative_output
            
        total_rounds_checked += 1
        
    t_elapsed = (time.perf_counter() - t_start) * 1000
    
    print("\n" + "=" * 60)
    print("=== REPLAY PARITY VERIFICATION SUMMARY ===")
    print(f"Total Settled Rounds Replayed: {total_rounds_checked}")
    print(f"Total Accepted Bets Verified:  {total_bets_checked}")
    print(f"Calculation Execution Time:    {t_elapsed:.2f} ms ({t_elapsed/max(1, total_rounds_checked):.3f} ms/round)")
    print(f"Total Discrepancies / Errors:  {len(mismatches)}")
    
    if mismatches:
        print(f"\n[FAILED] {len(mismatches)} mismatches detected:")
        for m in mismatches[:10]:
            print(f"  {m}")
        return False
    else:
        print("\n[SUCCESS] 100.000% EXACT MATCH! Every single bet calculation matches production database down to the penny!")
        return True

if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else r"E:\RedLedger\data\redledger.sqlite3"
    sess = int(sys.argv[2]) if len(sys.argv) > 2 else 1345
    run_parity_test(db, sess)
