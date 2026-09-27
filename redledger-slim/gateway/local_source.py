"""
Local WeChat DB Snapshot Poller & Self-Healing Pipeline for RedLedger Slim.
Embedded directly in Slim Engine to ensure zero-latency message ingestion and 100% self-healing.
"""
from __future__ import annotations

import os
import sys
import time
import json
import shutil
import threading
from pathlib import Path
from typing import Callable, Any

# Ensure access to source-hotfix modules
source_root = 'E:/RedLedger/source-hotfix'
if os.path.exists(source_root) and source_root not in sys.path:
    sys.path.insert(0, source_root)

try:
    from redledger.local_db_source import WeChatLocalReader, local_db_reader_config
    from redledger.wechat_snapshot import StableSnapshotPublisher
except ImportError:
    WeChatLocalReader = None
    StableSnapshotPublisher = None
    def local_db_reader_config():
        return None, None


class LocalDBSourceBridge:
    def __init__(
        self,
        message_callback: Callable[[dict[str, Any]], Any],
        snapshot_dir: str = 'E:/RedLedgerData/local-db-snapshots',
        poll_interval: float = 0.1,
    ):
        self.callback = message_callback
        self.snapshot_dir = Path(snapshot_dir)
        self.poll_interval = poll_interval
        self.cli_root, self.config_path = local_db_reader_config()
        self.publisher: StableSnapshotPublisher | None = None
        self.reader: WeChatLocalReader | None = None
        
        self.cursor_file = self.snapshot_dir / 'poller_cursors.json'
        self.cursors: dict[str, dict[str, int]] = self._load_cursors()
        
        self.running = False
        self.thread: threading.Thread | None = None
        self.monitored_groups: set[str] = set()
        
        # Self-healing & metrics tracking
        self.stats = {
            'is_alive': False,
            'last_snapshot_at': '',
            'last_message_at': '',
            'total_messages_ingested': 0,
            'last_error': '',
            'consecutive_errors': 0,
            'auto_healing_count': 0,
            'last_healthy_at': '',
        }

    def _load_cursors(self) -> dict[str, dict[str, int]]:
        if self.cursor_file.exists():
            try:
                raw = json.loads(self.cursor_file.read_text(encoding='utf-8'))
                res = {}
                for k, v in raw.items():
                    if isinstance(v, dict):
                        res[k] = {'local_id': int(v.get('local_id') or 0), 'timestamp': int(v.get('timestamp') or 0)}
                    elif isinstance(v, int):
                        res[k] = {'local_id': v, 'timestamp': 0}
                return res
            except Exception:
                pass
        return {}

    def _save_cursors(self):
        try:
            self.cursor_file.parent.mkdir(parents=True, exist_ok=True)
            self.cursor_file.write_text(json.dumps(self.cursors, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass

    def add_monitored_group(self, group_id: str):
        if group_id and str(group_id).endswith('@chatroom'):
            self.monitored_groups.add(str(group_id).strip())

    def set_monitored_groups(self, group_ids: list[str] | set[str]):
        self.monitored_groups = {str(g).strip() for g in group_ids if g and str(g).endswith('@chatroom')}

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, name='slim-local-db-bridge', daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _clean_and_reinit_snapshots(self):
        """Self-healing action: wipe corrupted snapshot cache and force fresh full decrypt."""
        self.stats['auto_healing_count'] += 1
        print('[LocalSourceBridge] ⚠️ Triggering auto-healing: resetting snapshot cache...')
        
        try:
            if self.publisher:
                self.publisher.stop()
        except Exception:
            pass

        # Wipe corrupted snapshot cache files to force fresh full decrypt
        try:
            cur_p = self.snapshot_dir / 'current.json'
            if cur_p.exists():
                cur_p.unlink(missing_ok=True)
            gen_p = self.snapshot_dir / 'generations'
            if gen_p.exists():
                shutil.rmtree(gen_p, ignore_errors=True)
        except Exception as wipe_err:
            print(f"[LocalSourceBridge] Snapshot wipe warning: {wipe_err}")

        # Re-initialize StableSnapshotPublisher
        self.publisher = StableSnapshotPublisher(
            cli_root=self.cli_root,
            config_path=self.config_path,
            snapshot_root=self.snapshot_dir,
            interval_seconds=0.08
        )
        self.reader = WeChatLocalReader(cli_root=self.cli_root, config_path=self.config_path)
        self.stats['consecutive_errors'] = 0

    def _run_loop(self):
        self.stats['is_alive'] = True
        print('[LocalSourceBridge] Local WeChat DB snapshot poller started.')
        
        # Initial setup
        try:
            self._clean_and_reinit_snapshots()
        except Exception as e:
            self.stats['last_error'] = str(e)
            print(f'[LocalSourceBridge] Initial setup warning: {e}')

        while self.running:
            t0 = time.time()
            try:
                if not self.publisher or not self.reader:
                    self._clean_and_reinit_snapshots()

                # 1. Publish snapshot from WeChat live DB
                self.publisher.publish_once()
                self.stats['last_snapshot_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
                
                # 2. Process all monitored chatrooms
                for group_id in list(self.monitored_groups):
                    group_cursor = self.cursors.get(group_id, {})
                    last_id = int(group_cursor.get('local_id') or 0)
                    last_ts = int(group_cursor.get('timestamp') or 0)
                    
                    # If cursor is completely unset, look back 1 hour to bootstrap
                    if last_ts <= 0:
                        last_ts = int(time.time()) - 3600

                    try:
                        # Query messages since last_ts with buffer
                        query_ts = max(0, last_ts - 2)
                        msgs = self.reader.messages(group_id, query_ts, limit=500)
                        
                        has_new = False
                        delivered_events = set()
                        
                        for m in msgs:
                            local_id = int(m.get('local_id') or 0)
                            msg_ts = int(m.get('timestamp') or 0)
                            event_key = str(m.get('event_key') or f"{local_id}:{msg_ts}")
                            
                            is_newer = (msg_ts > last_ts) or (msg_ts == last_ts and local_id > last_id)
                            # Handle multi-shard or partition
                            if is_newer or (event_key and event_key not in delivered_events and msg_ts >= last_ts):
                                # Deliver to callback; if it fails, exception will prevent advancing cursor
                                self.callback(m)
                                
                                # Advance cursor ONLY on success
                                if msg_ts > last_ts:
                                    last_ts = msg_ts
                                    last_id = local_id
                                elif msg_ts == last_ts:
                                    last_id = max(last_id, local_id)
                                    
                                delivered_events.add(event_key)
                                self.stats['total_messages_ingested'] += 1
                                self.stats['last_message_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
                                has_new = True
                        
                        if has_new or group_id not in self.cursors:
                            self.cursors[group_id] = {'local_id': last_id, 'timestamp': last_ts}
                            self._save_cursors()
                            
                    except Exception as e:
                        err_str = str(e)
                        if 'malformed' in err_str or 'locked' in err_str or 'unavailable' in err_str:
                            self.stats['consecutive_errors'] += 1
                            if self.stats['consecutive_errors'] >= 3:
                                self._clean_and_reinit_snapshots()
                        raise

                self.stats['consecutive_errors'] = 0
                self.stats['last_healthy_at'] = time.strftime('%Y-%m-%d %H:%M:%S')

            except Exception as e:
                self.stats['last_error'] = str(e)
                self.stats['consecutive_errors'] += 1
                if self.stats['consecutive_errors'] >= 5:
                    try:
                        self._clean_and_reinit_snapshots()
                    except Exception:
                        pass
                time.sleep(0.5)

            # Sleep remaining time to maintain target poll interval
            elapsed = time.time() - t0
            sleep_time = max(0.02, self.poll_interval - elapsed)
            time.sleep(sleep_time)

        self.stats['is_alive'] = False
