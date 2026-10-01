from __future__ import annotations
import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import re
import json
import time
import queue
import threading
from typing import Any
from pathlib import Path
from flask import Flask, request, jsonify

# Ensure DLL search directory
dll_dir = r"E:\RedLedger\server\RedLedgerServer\_internal"
if os.path.exists(dll_dir):
    try:
        os.add_dll_directory(dll_dir)
    except (AttributeError, OSError):
        pass
    os.environ['PATH'] = dll_dir + ';' + os.environ.get('PATH', '')

# Pywin32 search paths
sys.path.insert(0, 'E:/RedLedger/runtime/site-packages/win32')
sys.path.insert(0, 'E:/RedLedger/runtime/site-packages/win32/lib')
sys.path.insert(0, 'E:/RedLedger/runtime/site-packages/Pythonwin')
sys.path.insert(0, 'E:/RedLedger/server/RedLedgerServer/_internal/win32')

try:
    from core.engine import CoreEngine, Round, BetEntry, Member, PlayerRoundSummary
    from core.route import clean_route, append_result_to_route
    from storage.db import Database, now_iso
    from pipeline.renderer import SlimRenderer
    from pipeline.dispatcher import Dispatcher, DispatchJob
    from gateway.vxhook import normalize_vxhook_payload
    from gateway.local_source import LocalDBSourceBridge
except ImportError:
    from ..core.engine import CoreEngine, Round, BetEntry, Member, PlayerRoundSummary
    from ..core.route import clean_route, append_result_to_route
    from ..storage.db import Database, now_iso
    from ..pipeline.renderer import SlimRenderer
    from ..pipeline.dispatcher import Dispatcher, DispatchJob
    from .vxhook import normalize_vxhook_payload
    from .local_source import LocalDBSourceBridge

MD5_PATTERN = re.compile(r'md5=["\']([a-fA-F0-9]{32})["\']', re.IGNORECASE)
TEXT_RESULT_PATTERN = re.compile(r'^(?:开|结果|奖号|第[0-9]+[期把局轮])?\s*([1-4])\s*(?:门|号)?$', re.IGNORECASE)
BAOLU_PATTERN = re.compile(r'^[1-4]{3,30}$')

# Built-in Emoji and Image Fingerprints
DEFAULT_EMOJI_RESULT_MAPPINGS: dict[str, list[str]] = {
    "1": [
        "4ca63a3062f091f814e718bff6e56fc1",
        "964bfd3d69e62ddc9a182a700df40d68",
        "0785394dde1cfbf0af008dc161368b1c",
        "35578b995beb52fba43f832fb834693b",
        "526bb12f925e65a4d65d2abfeb832ca0",
    ],
    "2": [
        "0a819d4b70240c6654f66f11b8edae8e",
        "1dfae7e5bf933b4b8e14f7f566b9e125",
        "3c2037711c7e5905ed33576a72fe44ae",
        "0dd0f79846d57c35d81790834f689bcc",
        "6a31e7087cdbd2f05b6a47705921b2ed",
    ],
    "3": [
        "30e13bea7f4d1b8bbac99c1a5983d693",
        "d6533e80bc69d65628e378ec6a60da1f",
        "07a15803aa61fed45194cccec070f2e4",
        "4dd8cb8e8bd5533aae6c80f15b8ed7c5",
        "b0edc4b631c6b0901eaa72584e4729c6",
        "006dd205569de4adde25905d53c66d71",
    ],
    "4": [
        "6b3f234bb4772868d9584b7ea8da839a",
        "8c72f2675946de21f67ef6fad239e02e",
    ],
}

# Reverse lookup dictionary: md5 -> result '1'..'4'
EMOJI_MD5_TO_RESULT: dict[str, str] = {}
for res_key, md5_list in DEFAULT_EMOJI_RESULT_MAPPINGS.items():
    for md5_val in md5_list:
        EMOJI_MD5_TO_RESULT[md5_val.lower()] = res_key

START_IMAGE_FINGERPRINTS = {
    "090bf80a90f128fcd966d85899c2fba2",
    "2c7ef42d7dd0eba33c3540bafe155f50",
    "e13da46a0b539f153a7050fd62f4e1ef",
    "838e56c7272bd136be351a819f80f522",
    "633c7e34d75c87f6e5e43d9b1f040ed2",
    "5d9862cd524a6c5401e3549bb8bf247e",
    "fddfe2d2a9a303361134ba61d239a3d1",
    "34acd3a72b81ec3129e3c1c796ed40c3",
    "8e15fec5efd541241dd009fa3a4315cf",
    "132f536acd6fd3abf57e4e7d2401e03e",
    "742a661fbb18473287d0d2df62d324d6",
    "a79fa1d5a353496ea816ee289f0eed81",
    "0fdb2a4ee4d2b6f521423384aad54fa5",
    "108efe38ad3a919ba1572d99d62ef252",
    "1406973d113c337c0ed5f1981249addb",
    "f1b523374a20ed3091c7c9c23bc7328f",
}

SEAL_IMAGE_FINGERPRINTS = {
    "540fd6066535d1028b1dbef175e257db",
    "a3abd95f33fd2da18bf15f6571e8428e",
    "6656a5e7092618954af09eac539944c1",
    "671cf760f396a447a8d08e45798f7fb4",
    "ec0820db3eae50eef59ad17eb2cf6242",
    "dc354209744f7d87a1356a7050494423",
    "5bab1a3257dce80dcb4d856cee3dd06c",
    "96176ead1ea1a357dec62e5b612276ec",
    "1ad29ffaa44f05f6d278f337ed94e75a",
}

CLASS_END_IMAGE_FINGERPRINTS = {
    "d45a7198a86689999b00ecb5a4b80d90",
    "dcbdd6fa74cce3b4da5a236a73fb171a",
    "00dfe3fbfc11a86beab177e55dfe19d0",
    "1150efbd5e3ca0ff1caa90bf4a8193df",
    "7829ed94999ad717f66495b0b3709d30",
    "c8ac5246edc829c251bc3b4e04f1baf6",
}

SEAL_KEYWORDS = {"封门", "停止下注", "封", "停", "同学们停", "停止", "封盘"}
START_KEYWORDS = {"开始下注", "开始投注", "开始", "开", "继续"}
CLASS_END_KEYWORDS = {"下课", "下课了", "结束", "全场结束"}


class SlimGatewayApp:
    def __init__(self, db_path: str, report_dir: str = 'C:/Temp/redledger-reports', target_group_id: str | None = None):
        self.target_group_id = str(target_group_id).strip() if target_group_id else None
        self.db_path = db_path
        self.db = Database(db_path)
        self.settings = self.db.load_settings()
        self.engine = CoreEngine(settings=self.settings)
        self.renderer = SlimRenderer(output_dir=report_dir)
        self.dispatcher = Dispatcher(on_job_completed=self._handle_dispatch_completed)
        
        self.members_map: dict[int, Member] = self.db.load_members()
        self.wxid_to_member_id: dict[str, int] = {m.wxid: m.id for m in self.members_map.values() if m.wxid}
        
        # In-memory active session & round state
        self.active_session: dict[str, Any] | None = None
        self.active_round: Round | None = None
        self.historical_pnl: dict[int, int] = {}
        self.recent_red_packets: list[float] = []
        self.unrecognized_emojis: list[dict[str, Any]] = []
        self.processed_message_ids: set[str] = set()
        self.last_summary_dispatched_at: float = 0.0
        self.last_summary_session_id: int = 0
        self.state_lock = threading.RLock()
        
        # Background DB persistence queue
        self.db_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=8192)
        self.db_worker_thread = threading.Thread(target=self._db_worker, name='slim-db-worker', daemon=True)
        
        # Integrated Local WeChat DB Snapshot Bridge
        self.local_bridge = LocalDBSourceBridge(
            message_callback=self._handle_bridge_message,
            snapshot_dir='E:/RedLedgerData/local-db-snapshots',
            poll_interval=0.1
        )
        self.local_bridge.add_monitored_group('59220588167@chatroom')
        self.local_bridge.add_monitored_group('51629062897@chatroom')

        self._load_active_session_from_db()

        self.app = Flask('redledger-slim-gateway')
        self._setup_routes()

    def start(self):
        self.db_worker_thread.start()
        self.dispatcher.start()
        print('[SlimGatewayApp] Internal engines and dispatcher started!')

    def stop(self):
        self.dispatcher.stop()

    def _handle_bridge_message(self, raw_msg: dict[str, Any]):
        # Direct in-process handler for messages read from local WeChat DB snapshot
        try:
            normalized = normalize_vxhook_payload(raw_msg)
            self.process_message(normalized)
        except Exception as e:
            print(f'[Bridge Handler Error] {e}')
            raise

    def _handle_dispatch_completed(self, job: DispatchJob):
        try:
            status = 'completed' if job.status == 'sent' else 'failed'
            now_str = now_iso()
            self.db.execute("""
                UPDATE report_jobs 
                SET status = ?, sent_at = ?, error = ?, updated_at = ?
                WHERE round_id = ?
            """, (status, now_str if job.status == 'sent' else None, job.error, now_str, job.round_id))
            self.db.commit()
            print(f"[Gateway] Report job for round {job.round_id} marked as {status} (error: {job.error or 'none'})")
        except Exception as e:
            print(f"[Gateway] Update report_jobs error: {e}")

    def _is_banker(self, sender_wxid: str, sender_name: str) -> bool:
        if not sender_wxid and not sender_name:
            return False

        # 1. Configured banker wxid list in settings
        configured_wxids = set(self.settings.get('banker_wxids', []))
        if sender_wxid and (sender_wxid in configured_wxids or sender_wxid in {'wxid_o8wyw4awcok622'}):
            return True

        # 2. Configured banker names in settings
        configured_names = self.settings.get('banker_names', []) + ['华丽的跌倒', '东南亚']
        if any(bname in sender_name for bname in configured_names if bname):
            return True

        # 3. Check current active session's banker member ID
        banker_id = (self.active_session.get('banker_member_id') if self.active_session else None)
        if banker_id:
            banker_member = self.members_map.get(int(banker_id))
            if banker_member and banker_member.wxid and sender_wxid == banker_member.wxid:
                return True

        # 4. Check if member is marked as banker in DB / members map
        m_id = self.wxid_to_member_id.get(sender_wxid)
        if m_id and m_id in self.members_map:
            if getattr(self.members_map[m_id], 'is_banker', False):
                return True

        return False

    def _ensure_active_session(self, group_id: str | None = None, allow_create: bool = True) -> dict[str, Any] | None:
        """Ensure an active open session exists in memory and DB."""
        if not self.active_session or self.active_session.get('status') == 'closed':
            # Check latest open session in DB
            if self.target_group_id:
                sess_row = self.db.fetch_one("SELECT * FROM sessions WHERE status = 'open' AND group_id = ? ORDER BY id DESC LIMIT 1", (self.target_group_id,))
            else:
                sess_row = self.db.fetch_one("SELECT * FROM sessions WHERE status = 'open' ORDER BY id DESC LIMIT 1")
            if sess_row:
                self.active_session = dict(sess_row)
                self.historical_pnl = self.db.recover_historical_pnl(sess_row['id'])
            elif allow_create:
                gid = self.target_group_id or group_id or '59220588167@chatroom'
                gname = '奥数练习班'
                if gid == '51629062897@chatroom':
                    gname = '腾达学院2500'
                else:
                    try:
                        cr = self.db.fetch_one("SELECT nick_name FROM chatrooms WHERE user_name = ? LIMIT 1", (gid,))
                        if cr and cr.get('nick_name'):
                            gname = str(cr['nick_name'])
                    except Exception:
                        pass
                banker_id = 1554
                notes = f'{gname}自动会话'
                cur = self.db.execute(
                    "INSERT INTO sessions (title, group_name, group_id, banker_member_id, status, trend, started_at, notes) VALUES ('wechat redpacket ledger', ?, ?, ?, 'open', '', ?, ?)",
                    (gname, gid, banker_id, now_iso(), notes)
                )
                new_id = cur.lastrowid
                self.db.commit()
                self.active_session = {
                    'id': new_id,
                    'title': 'wechat redpacket ledger',
                    'group_name': gname,
                    'group_id': gid,
                    'banker_member_id': banker_id,
                    'status': 'open',
                    'trend': '',
                    'class_end_count': 0,
                    'started_at': now_iso(),
                    'notes': notes
                }
                self.historical_pnl = {}
                self.active_round = None
                print(f"[Gateway] Auto-created new Session #{new_id} for {gname} ({gid})")
            else:
                return None

            if self.active_session and self.active_session.get('group_id') and hasattr(self, 'local_bridge'):
                self.local_bridge.add_monitored_group(self.active_session['group_id'])
        return self.active_session

    def _ensure_active_round(self) -> Round | None:
        """Ensure an active round object exists in memory and is backed in DB."""
        if not self.active_session or self.active_session.get('status') == 'closed':
            return None
            
        if not self.active_round and self.active_session:
            stat_rnd = self.db.fetch_one("SELECT MAX(round_no) as max_r, MAX(display_round_no) as max_d FROM rounds WHERE session_id = ?", (self.active_session['id'],))
            last_rnd = self.db.fetch_one("SELECT route FROM rounds WHERE session_id = ? ORDER BY id DESC LIMIT 1", (self.active_session['id'],))
            next_no = (int(stat_rnd['max_r']) + 1) if (stat_rnd and stat_rnd['max_r'] is not None) else 1
            init_route = clean_route(last_rnd['route'] if last_rnd else (self.active_session.get('trend') or ''))
            disp_no = (int(stat_rnd['max_d']) + 1) if (stat_rnd and stat_rnd['max_d'] is not None) else ((len(init_route) + 1) if init_route else next_no)
            
            # Create in DB synchronously
            cur = self.db.execute(
                "INSERT INTO rounds (session_id, round_no, display_round_no, status, result, route, opened_at) VALUES (?, ?, ?, 'open', '', ?, ?)",
                (self.active_session['id'], next_no, disp_no, init_route, now_iso())
            )
            round_id = cur.lastrowid
            self.db.commit()

            self.active_round = Round(
                id=round_id,
                session_id=self.active_session['id'],
                round_no=next_no,
                display_round_no=disp_no,
                status='open',
                route=init_route,
                banker_id=self.active_session.get('banker_member_id', 1),
                opened_at=now_iso()
            )
            print(f"[Gateway] Auto-created Active Round #{next_no} (ID: {round_id}) for Session #{self.active_session['id']}")
        return self.active_round

    def _load_active_session_from_db(self):
        with self.state_lock:
            if self.target_group_id:
                sess_row = self.db.fetch_one("SELECT * FROM sessions WHERE status = 'open' AND group_id = ? ORDER BY id DESC LIMIT 1", (self.target_group_id,))
            else:
                sess_row = self.db.fetch_one("SELECT * FROM sessions WHERE status = 'open' ORDER BY id DESC LIMIT 1")
            if sess_row:
                self.active_session = dict(sess_row)
                if hasattr(self, 'local_bridge') and self.active_session.get('group_id'):
                    self.local_bridge.add_monitored_group(self.active_session['group_id'])
                
                # Recover historical pnl
                self.historical_pnl = self.db.recover_historical_pnl(sess_row['id'])

                round_row = self.db.fetch_one("SELECT * FROM rounds WHERE session_id = ? AND status IN ('open', 'sealed') ORDER BY id DESC LIMIT 1", (sess_row['id'],))
                if round_row:
                    bets_rows = self.db.fetch_all("SELECT * FROM bets WHERE round_id = ? AND status = 'accepted'", (round_row['id'],))
                    self.active_round = Round(
                        id=round_row['id'],
                        session_id=round_row['session_id'],
                        round_no=round_row['round_no'],
                        display_round_no=round_row['display_round_no'],
                        status=round_row['status'],
                        result=round_row['result'] or '',
                        route=clean_route(round_row['route'] or ''),
                        banker_id=self.active_session.get('banker_member_id', 1),
                        bets=[
                            BetEntry(
                                id=b['id'],
                                member_id=b['member_id'],
                                raw_text=b['raw_text'],
                                picks=b['picks'],
                                amount=b['amount'],
                                play_type=b['play_type'] or '',
                                status=b['status'] or 'accepted',
                                is_revoked=False,
                                source_message_id=b['source_message_id'] or ''
                            )
                            for b in bets_rows
                        ]
                    )
                else:
                    self._ensure_active_round()
            else:
                self.active_session = None
                self.active_round = None
            print(f"[Gateway] Initialized in-memory state. Active session: {self.active_session.get('id') if self.active_session else None}, Active round: {self.active_round.round_no if self.active_round else None}")

    def _db_worker(self):
        db_writer = Database(self.db_path)
        while True:
            try:
                task_type, payload = self.db_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            try:
                if task_type == 'settle_round':
                    round_obj, summaries = payload
                    db_writer.save_round_settlement(round_obj, summaries)
                elif task_type == 'revoke_bet':
                    bet_id = payload
                    db_writer.execute("UPDATE bets SET status = 'revoked' WHERE id = ?", (bet_id,))
                    db_writer.commit()
                elif task_type == 'seal_round':
                    round_id = payload
                    db_writer.execute("UPDATE rounds SET status = 'sealed' WHERE id = ?", (round_id,))
                    db_writer.commit()
                elif task_type == 'close_session':
                    sess_id = payload
                    db_writer.execute("UPDATE sessions SET status = 'closed', ended_at = ? WHERE id = ?", (now_iso(), sess_id))
                    db_writer.commit()
            except Exception as e:
                print(f'[DBWorker Error] Failed to execute {task_type}: {e}')
            finally:
                self.db_queue.task_done()

    def _setup_routes(self):
        @self.app.route('/api/recvMsg', methods=['POST'])
        def recv_msg():
            t_start = time.perf_counter()
            data = request.get_json(force=True, silent=True) or {}
            normalized = normalize_vxhook_payload(data) if ('fromUserName' in data or 'msgContent' in data) else data
            
            res = self.process_message(normalized)
            elapsed_ms = (time.perf_counter() - t_start) * 1000
            
            return jsonify({
                'code': 1 if res.get('status') not in {'error', 'ignored'} else 0,
                'result': res,
                'cost_ms': round(elapsed_ms, 2)
            })

        @self.app.route('/api/status', methods=['GET'])
        def get_status():
            with self.state_lock:
                return jsonify({
                    'status': 'ok',
                    'active_session': self.active_session,
                    'active_round': {
                        'id': self.active_round.id,
                        'round_no': self.active_round.round_no,
                        'display_round_no': self.active_round.display_round_no,
                        'status': self.active_round.status,
                        'bets_count': len([b for b in self.active_round.bets if not b.is_revoked and getattr(b, 'status', 'accepted') == 'accepted'])
                    } if self.active_round else None,
                    'monitored_groups': list(self.local_bridge.monitored_groups) if hasattr(self, 'local_bridge') else [],
                    'local_db_bridge': self.local_bridge.stats if hasattr(self, 'local_bridge') else {},
                    'unrecognized_emojis': self.unrecognized_emojis[-10:],
                    'dispatcher_stats': self.dispatcher.stats,
                    'db_queue_depth': self.db_queue.qsize()
                })

        @self.app.route('/api/settle', methods=['POST'])
        def manual_settle():
            data = request.get_json(force=True, silent=True) or {}
            result_val = str(data.get('result', '')).strip()
            if result_val not in {'1', '2', '3', '4'}:
                return jsonify({'status': 'error', 'message': f'Invalid result: {result_val}'}), 400
            
            with self.state_lock:
                res = self._execute_settlement(result_val)
                return jsonify(res)

        @self.app.route('/api/send_image', methods=['POST'])
        def send_image_endpoint():
            data = request.get_json(force=True, silent=True) or {}
            img_path = str(data.get('image_path') or '').strip()
            target_group = str(data.get('target_group_name') or '奥数练习班')
            target_gid = str(data.get('target_group_id') or '59220588167@chatroom')
            
            if not img_path or not os.path.exists(img_path):
                return jsonify({'status': 'error', 'message': f'Image not found: {img_path}'}), 400
                
            job = DispatchJob(
                job_id=f'api-{int(time.time())}',
                session_id=self.active_session['id'] if self.active_session else 0,
                round_id=0,
                target_group_name=target_group,
                target_group_id=target_gid,
                artifact_type='image',
                payload=img_path
            )
            enqueued = self.dispatcher.enqueue(job)
            return jsonify({'status': 'ok', 'enqueued': enqueued, 'job_id': job.job_id})

        @self.app.route('/api/close_session', methods=['POST'])
        def close_session_endpoint():
            with self.state_lock:
                if self.active_session:
                    sess_id = self.active_session['id']
                    self.db.execute("UPDATE sessions SET status = 'closed', ended_at = ? WHERE id = ?", (now_iso(), sess_id))
                    self.db.commit()
                    self.active_session['status'] = 'closed'
                    self.active_round = None
                    self.active_session = None
                    return jsonify({'status': 'ok', 'closed_session_id': sess_id})
                return jsonify({'status': 'ok', 'message': 'no_active_session'})

    def _resolve_target_group(self, source_group_id: str = "") -> tuple[str, str]:
        """Dynamically resolve target delivery group (ID, Name) for a given source group."""
        source_gid = str(source_group_id or self.target_group_id or (self.active_session.get('group_id') if self.active_session else '')).strip()
        source_name = str((self.active_session.get('group_name') if self.active_session else '') or '当前群').strip()

        # Hardcoded fast-path for 100% reliable production routing
        if source_gid == '51629062897@chatroom':
            return '46309141921@chatroom', '800'
        if source_gid == '59220588167@chatroom':
            return '48173026511@chatroom', '奥数结果群'

        # 1. Query group_report_targets table in DB
        if source_gid:
            try:
                row = self.db.fetch_one(
                    "SELECT target_group_id, target_group_name FROM group_report_targets WHERE source_group_id = ? LIMIT 1",
                    (source_gid,)
                )
                if row and row.get('target_group_id'):
                    return str(row['target_group_id']).strip(), str(row.get('target_group_name') or '').strip()
            except Exception as e:
                print(f"[Resolve Target Warning] Failed to query group_report_targets: {e}")

        # 2. Check general settings fallback
        settings = self.db.load_settings()
        if settings.get('report_target_group_id'):
            return str(settings.get('report_target_group_id')).strip(), str(settings.get('report_target_group_name') or source_name).strip()

        return source_gid, source_name

    def _execute_settlement(self, result_val: str) -> dict[str, Any]:
        if not self.active_session:
            return {'status': 'error', 'reason': 'no_active_session'}
        
        self._ensure_active_round()
        if not self.active_round:
            return {'status': 'error', 'reason': 'no_active_round'}

        active_bets = [b for b in self.active_round.bets if not b.is_revoked and getattr(b, 'status', 'accepted') == 'accepted']
        if len(active_bets) == 0 and self.active_round.status != 'sealed':
            print(f"[Gateway] Round {self.active_round.round_no} has 0 active bets and is not sealed. Ignored duplicate/premature settlement signal.")
            return {'status': 'ignored', 'reason': 'no_active_bets_and_not_sealed'}

        t0 = time.perf_counter()
        settled_round, summaries = self.engine.settle_round(
            self.active_round, result_val, self.members_map, self.historical_pnl
        )
        t_settle = (time.perf_counter() - t0) * 1000
        
        for s in summaries:
            self.historical_pnl[s.member_id] = s.cumulative_output

        self.settings = self.db.load_settings()
        source_gid = self.active_session.get('group_id', '') if self.active_session else ''
        target_group_id, target_group_name = self._resolve_target_group(source_gid)
        if not target_group_name:
            target_group_name = self.active_session.get('group_name', '当前群') if self.active_session else '当前群'
        
        next_round_no = self.active_round.round_no + 1
        next_disp_no = (self.active_round.display_round_no or 0) + 1
        next_route = settled_round.route

        # Atomically settle current round and create next round in single DB transaction
        report_job_data = {
            'source_group_id': self.active_session.get('group_id', ''),
            'target_group_id': target_group_id,
            'artifact_type': 'image',
            'artifact_path': ''
        }
        
        next_round_id = self.db.settle_and_create_next_round_tx(
            session_id=self.active_session['id'],
            settled_round=settled_round,
            summaries=summaries,
            next_round_no=next_round_no,
            next_disp_no=next_disp_no,
            next_route=next_route,
            report_job_data=report_job_data
        )

        # Update in-memory state to next open round
        next_round = Round(
            id=next_round_id,
            session_id=self.active_session['id'],
            round_no=next_round_no,
            display_round_no=next_disp_no,
            status='open',
            route=next_route,
            banker_id=self.active_session.get('banker_member_id', 1),
            opened_at=now_iso()
        )
        self.active_round = next_round
        if self.active_session:
            self.active_session['trend'] = next_route

        # Asynchronously render report and enqueue dispatch job if enabled
        img_path = ""
        t_render = 0.0
        auto_send = self.settings.get('auto_send_round_report', True)
        
        if auto_send:
            try:
                t1 = time.perf_counter()

                # Build inactive members list (players who participated in this session but didn't bet in this round)
                active_member_ids = {s.member_id for s in summaries}
                banker_id = int(self.active_session.get('banker_member_id') or settled_round.banker_id or 1554)
                
                # Fetch all members who placed accepted bets in this session
                session_bettor_rows = self.db.fetch_all(
                    "SELECT DISTINCT member_id FROM bets WHERE session_id = ? AND status = 'accepted'",
                    (self.active_session['id'],)
                )
                session_bettor_ids = {r['member_id'] for r in session_bettor_rows}
                all_participant_ids = set(self.historical_pnl.keys()).union(session_bettor_ids)
                
                inactive_ids = sorted(all_participant_ids - active_member_ids - {banker_id})
                inactive_members = []
                for idx, m_id in enumerate(inactive_ids, 1):
                    member = self.members_map.get(m_id)
                    if not member:
                        m_row = self.db.fetch_one("SELECT * FROM members WHERE id = ?", (m_id,))
                        if m_row:
                            member = Member(
                                id=m_row['id'],
                                wxid=m_row.get('wxid', ''),
                                display_name=m_row.get('display_name') or m_row.get('name') or f"玩家_{m_id}",
                                avatar_url=m_row.get('avatar_url', ''),
                                is_banker=bool(m_row.get('is_banker', 0))
                            )
                            self.members_map[m_id] = member

                    disp_name = member.display_name if member else f"玩家_{m_id}"
                    avatar = member.avatar_url if member else ""
                    cum_pnl = self.historical_pnl.get(m_id, 0)
                    
                    inactive_members.append({
                        "index": idx,
                        "member": {
                            "id": m_id,
                            "display_name": disp_name,
                            "wxid": member.wxid if member else "",
                            "avatar_url": avatar,
                            "is_banker": 0
                        },
                        "is_banker": False,
                        "is_settled": True,
                        "round_output": 0,
                        "cumulative_output": cum_pnl,
                        "log": "未参加本轮",
                        "log_items": []
                    })

                img_path = str(self.renderer.render(
                    self.active_session, 
                    settled_round, 
                    summaries, 
                    inactive_members=inactive_members
                ))
                t_render = (time.perf_counter() - t1) * 1000

                # Update artifact_path in report_jobs
                self.db.execute("UPDATE report_jobs SET artifact_path = ?, updated_at = ? WHERE round_id = ?", (img_path, now_iso(), settled_round.id))
                self.db.commit()

                job = DispatchJob(
                    job_id=f'r-{settled_round.id}-{int(time.time())}',
                    session_id=self.active_session['id'],
                    round_id=settled_round.id,
                    target_group_name=target_group_name,
                    target_group_id=target_group_id,
                    artifact_type='image',
                    payload=img_path
                )
                self.dispatcher.enqueue(job)

                # Send companion Baolu route text message if available
                auto_send_route = self.settings.get('auto_send_route', True)
                if auto_send_route and next_route:
                    text_job = DispatchJob(
                        job_id=f't-{settled_round.id}-{int(time.time())}',
                        session_id=self.active_session['id'],
                        round_id=settled_round.id,
                        target_group_name=target_group_name,
                        target_group_id=target_group_id,
                        artifact_type='text',
                        payload=f"宝路：{next_route}"
                    )
                    self.dispatcher.enqueue(text_job)
            except Exception as e:
                print(f"[Renderer/Dispatcher Warning] Report dispatch failed: {e}")

        print(f"[SETTLEMENT COMPLETE] Round {settled_round.round_no} -> Result {result_val}. Bets: {len(settled_round.bets)}. Next Round: {next_round_no}")
        return {
            'status': 'settled',
            'round_no': settled_round.round_no,
            'result': result_val,
            'settle_ms': round(t_settle, 2),
            'render_ms': round(t_render, 2),
            'image_path': img_path
        }

    def _handle_class_end(self, msg_id: str = "") -> dict[str, Any]:
        if not self.active_session:
            return {'status': 'ignored', 'reason': 'no_active_session'}
        
        session_meta = dict(self.active_session)
        session_id = session_meta['id']
        
        # 1. Load settled rounds count BEFORE closing
        cur = self.db.execute("SELECT COUNT(id) FROM rounds WHERE session_id = ? AND status = 'settled' AND result != ''", (session_id,))
        row = cur.fetchone()
        settled_count = row[0] if row else 0

        # Mark closed in DB
        now_str = now_iso()
        self.db.execute("UPDATE sessions SET status = 'closed', ended_at = ? WHERE id = ?", (now_str, session_id))
        self.db.commit()
        
        self.active_session = None
        self.active_round = None
        if msg_id:
            self.processed_message_ids.add(msg_id)

        # Zero-round suppression: If no rounds were settled, do NOT dispatch summary overview
        if settled_count == 0:
            print(f"[Gateway] Session {session_id} closed with 0 settled rounds. Suppressed summary overview dispatch.")
            return {'status': 'class_end_acknowledged', 'session_id': session_id, 'summary_rendered': False, 'reason': 'zero_settled_rounds'}

        # Throttle: Check cooldown (60 seconds)
        now_ts = time.time()
        if (now_ts - self.last_summary_dispatched_at) < 60.0:
            print(f"[Gateway] Summary dispatch throttled (last dispatched {now_ts - self.last_summary_dispatched_at:.1f}s ago). Session {session_id} closed without duplicate image.")
            return {'status': 'class_end_acknowledged', 'session_id': session_id, 'summary_rendered': False, 'reason': 'cooldown_throttled'}

        # 2. Build summary rows from historical_pnl and members_map
        banker_id = int(session_meta.get('banker_member_id') or 1554)
        banker_member = self.members_map.get(banker_id)
        banker_dict = {
            'id': banker_member.id if banker_member else banker_id,
            'display_name': banker_member.display_name if banker_member else "庄家",
            'avatar_url': banker_member.avatar_url if banker_member else "",
            'is_banker': 1
        }
        
        rows = []
        for m_id, member in self.members_map.items():
            if m_id == banker_id:
                continue
            amount = self.historical_pnl.get(m_id, 0)
            rows.append({
                'member': {
                    'id': member.id,
                    'display_name': member.display_name,
                    'wxid': member.wxid,
                    'avatar_url': member.avatar_url,
                    'is_banker': 0
                },
                'amount': amount
            })
            
        positive = sorted([r for r in rows if r['amount'] > 0], key=lambda r: (-r['amount'], r['member']['display_name']))
        negative = sorted([r for r in rows if r['amount'] < 0], key=lambda r: (r['amount'], r['member']['display_name']))
        zero = sorted([r for r in rows if r['amount'] == 0], key=lambda r: r['member']['display_name'])
        
        # 3. Render session summary report
        source_gid = session_meta.get('group_id', '')
        target_group_id, target_group_name = self._resolve_target_group(source_gid)
        if not target_group_name:
            target_group_name = session_meta.get('group_name', '奥数结果群')
        
        try:
            summary_img = str(self.renderer.render_session_summary(
                session_meta=session_meta,
                banker_member=banker_dict,
                positive=positive,
                negative=negative,
                zero=zero,
                rounds_count=settled_count,
                total_rounds=settled_count
            ))
            
            # 4. Dispatch to target group
            job = DispatchJob(
                job_id=f'summary-{session_id}-{int(time.time())}',
                session_id=session_id,
                round_id=0,
                target_group_name=target_group_name,
                target_group_id=target_group_id,
                artifact_type='image',
                payload=summary_img
            )
            self.dispatcher.enqueue(job)
            self.last_summary_dispatched_at = now_ts
            self.last_summary_session_id = session_id
            print(f"[Gateway] Dispatched session summary overview image for session {session_id} to {target_group_name} ({target_group_id})")
        except Exception as e:
            print(f"[Gateway Error] Failed to render/dispatch session summary: {e}")
            
        return {'status': 'class_end_acknowledged', 'session_id': session_id, 'summary_rendered': True}

    def process_message(self, msg: dict[str, Any]) -> dict[str, Any]:
        with self.state_lock:
            # Check group routing
            msg_group = str(msg.get('group_id') or msg.get('from_group') or msg.get('room_id') or '').strip()
            content = str(msg.get('content') or msg.get('raw_content') or '').strip()
            msg_type = str(msg.get('message_type') or msg.get('msg_type') or msg.get('type') or 'text').lower()
            sender_wxid = str(msg.get('sender_wxid') or msg.get('wxid') or msg.get('from_user') or '')
            sender_name = str(msg.get('sender_name') or msg.get('nickname') or f'用户_{sender_wxid[-6:]}')
            msg_id = str(msg.get('message_id') or msg.get('msg_id') or msg.get('event_key') or msg.get('local_message_id') or msg.get('local_id') or '').strip()

            is_banker = self._is_banker(sender_wxid, sender_name)

            # Collect all possible identifier aliases for this event
            id_candidates: set[str] = set()
            if msg_id:
                id_candidates.add(msg_id)
            for k in ('message_ids', 'revoke_message_ids'):
                for v in msg.get(k, []):
                    if v:
                        id_candidates.add(str(v).strip())
            for k in ('server_message_id', 'transport_message_id', 'client_message_id', 'local_message_id'):
                val = str(msg.get(k) or '').strip()
                if val:
                    id_candidates.add(val)

            # Idempotency check against any matching ID
            if any(cid in self.processed_message_ids for cid in id_candidates):
                return {'status': 'bets_accepted', 'count': 1, 'idempotent': True}

            def mark_processed():
                for cid in id_candidates:
                    self.processed_message_ids.add(cid)

            # Extract MD5 if emoji or image
            md5 = ''
            if msg_type in {'emoji', 'image'} or '<emoji ' in content or '<img ' in content:
                m_match = MD5_PATTERN.search(content)
                if m_match:
                    md5 = m_match.group(1).lower()
                else:
                    md5 = str(msg.get('md5') or msg.get('originsourcemd5') or '').lower()

            # Ensure active session: first try without auto-creating
            if not self.active_session or self.active_session.get('status') == 'closed':
                self._ensure_active_session(msg_group, allow_create=False)

            # If still no active session, check if this message is a valid session start event
            if not self.active_session or self.active_session.get('status') == 'closed':
                is_start_event = False
                if msg_type == 'red_packet' or '<type>2001</type>' in content:
                    is_start_event = True
                elif md5 and md5 in START_IMAGE_FINGERPRINTS:
                    is_start_event = True
                elif content in START_KEYWORDS:
                    is_start_event = True
                elif BAOLU_PATTERN.match(content) and len(content) >= 3 and (is_banker or len(self.members_map) <= 2):
                    is_start_event = True
                elif msg_type == 'text' or content:
                    temp_bets = self.engine.parse_bets(content, 0, created_at=now_iso(), source_message_id=msg_id)
                    if temp_bets:
                        is_start_event = True

                if is_start_event:
                    self._ensure_active_session(msg_group, allow_create=True)
                else:
                    return {'status': 'ignored', 'reason': 'no_active_session'}

            if not self.active_session or self.active_session.get('status') == 'closed':
                return {'status': 'ignored', 'reason': 'no_active_session'}

            # Reject other groups
            active_gid = self.target_group_id or str(self.active_session.get('group_id') or '').strip()
            if msg_group and active_gid and msg_group != active_gid:
                return {'status': 'ignored', 'reason': 'group_mismatch'}

            # Track red packets
            if msg_type == 'red_packet' or '<type>2001</type>' in content:
                now_t = time.time()
                self.recent_red_packets.append(now_t)
                self.recent_red_packets = [t for t in self.recent_red_packets if now_t - t < 120]
                
                # Automatically recognize/bind red packet sender as banker
                if sender_wxid:
                    member = self.db.get_or_create_member(sender_wxid, sender_name, is_banker=True)
                    member.is_banker = True
                    self.members_map[member.id] = member
                    self.wxid_to_member_id[sender_wxid] = member.id
                    self.db.execute("UPDATE members SET is_banker = 1 WHERE id = ?", (member.id,))
                    if self.active_session:
                        self.active_session['banker_member_id'] = member.id
                        self.db.execute("UPDATE sessions SET banker_member_id = ? WHERE id = ?", (member.id, self.active_session['id']))
                    self.db.commit()
                    print(f"[Gateway] Red packet sender '{sender_name}' ({sender_wxid}) dynamically recognized as Banker.")

                mark_processed()
                return {'status': 'red_packet_recorded', 'count_in_2m': len(self.recent_red_packets)}

            # 1. Emoji / Image Signal Handling
            if md5:
                # Check overrides first
                override_val = self.settings.get('emoji_overrides', {}).get(md5)
                if override_val == 'ignore':
                    mark_processed()
                    return {'status': 'ignored', 'reason': 'emoji_ignored'}
                
                result_val = override_val if (override_val in {'1', '2', '3', '4'}) else EMOJI_MD5_TO_RESULT.get(md5)
                if result_val in {'1', '2', '3', '4'}:
                    active_bets = [b for b in self.active_round.bets if not b.is_revoked and getattr(b, 'status', 'accepted') == 'accepted'] if self.active_round else []
                    if self.active_round and (self.active_round.status == 'sealed' or len(active_bets) > 0):
                        mark_processed()
                        return self._execute_settlement(result_val)
                    mark_processed()
                    return {'status': 'ignored', 'reason': 'round_has_no_bets_or_not_sealed'}

                # Check Seal Image
                seal_fps = SEAL_IMAGE_FINGERPRINTS.union(set(self.settings.get('control_image_seal_fingerprints', [])))
                if md5 in seal_fps:
                    self._ensure_active_round()
                    if self.active_round and self.active_round.status == 'open':
                        self.active_round.status = 'sealed'
                        self.db.execute("UPDATE rounds SET status = 'sealed', sealed_at = ? WHERE id = ?", (now_iso(), self.active_round.id))
                        self.db.commit()
                        mark_processed()
                        return {'status': 'sealed', 'round_no': self.active_round.round_no}

                # Check Start Image
                start_fps = START_IMAGE_FINGERPRINTS.union(set(self.settings.get('control_image_start_fingerprints', [])))
                if md5 in start_fps:
                    if self.active_round and self.active_round.status == 'sealed':
                        self.active_round.status = 'open'
                        self.db.execute("UPDATE rounds SET status = 'open' WHERE id = ?", (self.active_round.id,))
                        self.db.commit()
                    mark_processed()
                    return {'status': 'start_acknowledged'}

                # Check Class End Image
                class_end_fps = CLASS_END_IMAGE_FINGERPRINTS.union(set(self.settings.get('control_image_class_end_fingerprints', [])))
                if md5 in class_end_fps:
                    if is_banker or len(self.members_map) <= 2:
                        mark_processed()
                        return self._handle_class_end(msg_id)
                    else:
                        mark_processed()
                        return {'status': 'ignored', 'reason': 'non_banker_class_end_ignored'}

                # Record unrecognized emoji from banker
                if is_banker:
                    self.unrecognized_emojis.append({
                        'time': time.strftime('%H:%M:%S'),
                        'md5': md5,
                        'sender': sender_name,
                        'content_preview': content[:100]
                    })
                    print(f"[WARN] Unrecognized Banker Emoji MD5: {md5}")

            # 2. Text Control Commands & Text Result
            if msg_type == 'text' or content:
                # Text Result (e.g. 开1, 开2, 1, 2)
                m_res = TEXT_RESULT_PATTERN.match(content)
                if m_res and (is_banker or len(self.members_map) <= 2):
                    res_val = m_res.group(1)
                    active_bets = [b for b in self.active_round.bets if not b.is_revoked and getattr(b, 'status', 'accepted') == 'accepted'] if self.active_round else []
                    if self.active_round and (self.active_round.status == 'sealed' or len(active_bets) > 0):
                        mark_processed()
                        return self._execute_settlement(res_val)
                    mark_processed()
                    return {'status': 'ignored', 'reason': 'round_has_no_bets_or_not_sealed'}

                # Text Baolu (e.g. 224, 2241, 22411, 224113)
                if BAOLU_PATTERN.match(content) and (is_banker or len(self.members_map) <= 2):
                    curr_route = str(self.active_round.route if self.active_round else (self.active_session.get('trend') or '')).strip()
                    curr_round_no = self.active_round.round_no if self.active_round else 1
                    active_bets = [b for b in self.active_round.bets if not b.is_revoked and getattr(b, 'status', 'accepted') == 'accepted'] if self.active_round else []

                    # 1. Exact match with current route -> Already settled by dice emoji, ignore duplicate!
                    if content == curr_route:
                        mark_processed()
                        print(f"[Gateway] Bao Lu '{content}' matches current route. Already settled. Ignored duplicate.")
                        return {'status': 'ignored', 'reason': 'baolu_already_settled'}

                    # 2. Initial session trend setting (e.g. at start of session before bets/dice)
                    if (not curr_route or curr_round_no == 1) and len(content) >= 3 and not curr_route.startswith(content):
                        if not curr_route or (curr_round_no == 1 and len(active_bets) == 0):
                            if self.active_session:
                                self.active_session['trend'] = content
                                self.db.execute("UPDATE sessions SET trend = ? WHERE id = ?", (content, self.active_session['id']))
                            if self.active_round:
                                self.active_round.route = content
                                self.active_round.display_round_no = len(content) + 1
                                self.db.execute("UPDATE rounds SET route = ?, display_round_no = ? WHERE id = ?", (content, len(content) + 1, self.active_round.id))
                            self.db.commit()
                            mark_processed()
                            print(f"[Gateway] Set initial session trend to '{content}', display_round_no to {len(content) + 1}")
                            return {'status': 'initial_trend_set', 'trend': content}

                    # 3. Single new result appended (len == len(curr_route) + 1 and starts with curr_route)
                    # Or canonical baolu update with full prefix matching current display_round_no
                    is_direct_next = (len(content) == len(curr_route) + 1 and content.startswith(curr_route))
                    is_canonical_next = (self.active_round and len(content) == (self.active_round.display_round_no or 0) and content.endswith(curr_route[-2:] + content[-1] if len(curr_route) >= 2 else content[-1]))
                    
                    if is_direct_next or is_canonical_next:
                        res_val = content[-1]
                        if self.active_round:
                            self.active_round.route = content[:-1]
                        if self.active_round and (self.active_round.status == 'sealed' or len(active_bets) > 0):
                            mark_processed()
                            print(f"[Gateway] Settling round {self.active_round.round_no} via Bao Lu string: result {res_val}")
                            return self._execute_settlement(res_val)
                        mark_processed()
                        return {'status': 'ignored', 'reason': 'round_has_no_bets_or_not_sealed'}

                    # Other cases: either mismatch or older string -> ignore
                    mark_processed()
                    return {'status': 'ignored', 'reason': 'baolu_mismatch_or_duplicate'}

                # Clean text for control commands
                cleaned_text = re.sub(r'@[^\s\u2005\u00a0]+', '', content).strip()
                cleaned_text_nopunc = re.sub(r'[\s\r\n\t,，.。!！?？~～、]+', '', cleaned_text)
                has_at_all = bool('@所有人' in content or '@all' in content.lower() or '<atuserlist>' in content)

                # Seal Keywords
                is_seal_text = bool(
                    content in SEAL_KEYWORDS or
                    cleaned_text in SEAL_KEYWORDS or
                    cleaned_text_nopunc in {"封门", "停止下注", "封", "停", "同学们停", "停止", "封盘", "停手"} or
                    (has_at_all and any(k in content for k in ["封门", "停止下注", "封盘", "停"]))
                )
                if is_seal_text and (is_banker or has_at_all or len(self.members_map) <= 2):
                    self._ensure_active_round()
                    if self.active_round and self.active_round.status == 'open':
                        self.active_round.status = 'sealed'
                        self.db.execute("UPDATE rounds SET status = 'sealed', sealed_at = ? WHERE id = ?", (now_iso(), self.active_round.id))
                        self.db.commit()
                        mark_processed()
                        return {'status': 'sealed', 'round_no': self.active_round.round_no}

                # Start Keywords
                is_start_text = bool(
                    content in START_KEYWORDS or
                    cleaned_text in START_KEYWORDS or
                    cleaned_text_nopunc in {"开始下注", "开始投注", "开始", "开门", "继续", "开工"} or
                    (has_at_all and any(k in content for k in ["开始下注", "开始投注", "开门", "开工"]))
                )
                if is_start_text and (is_banker or has_at_all or len(self.members_map) <= 2):
                    if self.active_round and self.active_round.status == 'sealed':
                        self.active_round.status = 'open'
                        self.db.execute("UPDATE rounds SET status = 'open' WHERE id = ?", (self.active_round.id,))
                        self.db.commit()
                    mark_processed()
                    return {'status': 'start_acknowledged'}

                # Class End Keywords
                is_class_end_text = bool(
                    content in CLASS_END_KEYWORDS or
                    cleaned_text in CLASS_END_KEYWORDS or
                    cleaned_text_nopunc in {"下课", "下课了", "结束", "全场结束", "今天结束", "打完了", "今天下课", "下课下课"} or
                    (has_at_all and any(k in content for k in ["下课", "全场结束", "结束了", "打完了"]))
                )
                if is_class_end_text:
                    if is_banker or has_at_all or len(self.members_map) <= 2:
                        mark_processed()
                        return self._handle_class_end(msg_id)
                    else:
                        mark_processed()
                        return {'status': 'ignored', 'reason': 'non_banker_class_end_ignored'}

            # 3. Bet Revocation
            if msg_type == 'revoke' or '<sysmsg type="revokemsg">' in content:
                if self.active_round and self.active_round.status in {'open', 'sealed'}:
                    revoke_msg_id = str(msg.get('revoke_message_id') or msg.get('target_msg_id') or '').strip()
                    member = self.db.get_or_create_member(sender_wxid, sender_name)
                    member_id = member.id
                    
                    revoked_count = 0
                    if revoke_msg_id:
                        # Revoke all bets matching revoke_msg_id
                        for b in self.active_round.bets:
                            if b.source_message_id == revoke_msg_id and not b.is_revoked:
                                b.is_revoked = True
                                revoked_count += 1
                        self.db.execute("UPDATE bets SET status = 'revoked', revoked_at = ? WHERE round_id = ? AND source_message_id = ?", (now_iso(), self.active_round.id, revoke_msg_id))
                        self.db.commit()
                        return {'status': 'bet_revoked', 'revoked_count': revoked_count, 'source_message_id': revoke_msg_id}
                    else:
                        # Revoke last active bet of member
                        for b in reversed(self.active_round.bets):
                            if b.member_id == member_id and not b.is_revoked:
                                b.is_revoked = True
                                if b.id:
                                    self.db.execute("UPDATE bets SET status = 'revoked', revoked_at = ? WHERE id = ?", (now_iso(), b.id))
                                    self.db.commit()
                                return {'status': 'bet_revoked', 'bet_raw': b.raw_text}

            # 4. Text Bet Parsing
            if (msg_type == 'text' or content):
                if not self.active_round or self.active_round.status != 'open':
                    # Check if session is closed or round is sealed
                    if self.active_round and self.active_round.status == 'sealed':
                        return {'status': 'ignored', 'reason': 'unhandled_content'}
                    self._ensure_active_round()

                # Sync with DB round status in case Web/External sealed it
                if self.active_round and self.active_round.id:
                    row_stat = self.db.fetch_one("SELECT status FROM rounds WHERE id = ?", (self.active_round.id,))
                    if row_stat and row_stat['status'] == 'sealed':
                        self.active_round.status = 'sealed'
                        return {'status': 'ignored', 'reason': 'unhandled_content'}

                if self.active_round and self.active_round.status == 'open':
                    # Persist member to DB
                    member = self.db.get_or_create_member(sender_wxid, sender_name)
                    member_id = member.id
                    self.members_map[member_id] = member
                    if sender_wxid:
                        self.wxid_to_member_id[sender_wxid] = member_id

                    parsed_bets = self.engine.parse_bets(content, member_id, created_at=now_iso(), source_message_id=msg_id)
                    if parsed_bets:
                        inserted_any = False
                        for b in parsed_bets:
                            is_dup = any(
                                eb.member_id == member_id and eb.raw_text == b.raw_text and eb.sub_index == b.sub_index and not eb.is_revoked
                                for eb in self.active_round.bets
                            )
                            if is_dup:
                                continue
                            b.session_id = self.active_session['id']
                            b.round_id = self.active_round.id
                            cur = self.db.execute(
                                "INSERT INTO bets (session_id, round_id, member_id, source_message_id, sub_index, raw_text, token, normalized, picks, play_type, amount, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted', ?)", 
                                (self.active_session['id'], self.active_round.id, member_id, msg_id, b.sub_index, b.raw_text, b.raw_text, b.raw_text, b.picks, b.play_type, b.amount, now_iso())
                            )
                            b.id = cur.lastrowid
                            self.active_round.bets.append(b)
                            inserted_any = True
                        
                        if inserted_any:
                            self.db.commit()
                        
                        for cid in id_candidates:
                            self.processed_message_ids.add(cid)

                        print(f"[BET ACCEPTED] {sender_name}: {content} ({len(parsed_bets)} bets) -> Round #{self.active_round.round_no}")
                        return {'status': 'bets_accepted', 'count': len(parsed_bets)}

            return {'status': 'ignored', 'reason': 'unhandled_content'}
