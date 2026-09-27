import sys
sys.path.insert(0, r"E:\RedLedger\server\RedLedgerServer\_internal\base_library.zip")
sys.path.insert(0, r"E:\RedLedger\runtime\Lib")
sys.path.insert(0, r"E:\RedLedger\runtime\site-packages")
sys.path.insert(0, r"E:\RedLedger\server\RedLedgerServer\_internal")

import os
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import time
import json
import subprocess
import urllib.request
from pathlib import Path

# Paths
PYTHON_EXE = r"E:\RedLedger\server\RedLedgerServer\_internal\python.exe"
SLIM_DIR = r"E:\RedLedger\redledger-slim"
LOG_PATH = r"E:\RedLedger\logs\watchdog_slim.log"
SERVER_LOG = r"E:\RedLedger\logs\server_slim.log"

os.makedirs(r"E:\RedLedger\logs", exist_ok=True)

def log(msg: str):
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass

def check_gateway_status() -> dict | None:
    try:
        req = urllib.request.urlopen("http://127.0.0.1:8766/api/status", timeout=4)
        if req.status == 200:
            return json.loads(req.read().decode('utf-8'))
    except Exception as e:
        log(f"[WATCHDOG CHECK] Gateway status probe failed: {e}")
    return None

def check_vxhook_status() -> bool:
    try:
        req = urllib.request.urlopen("http://127.0.0.1:19088/api/get_chatroom_list", timeout=3)
        return req.status == 200
    except Exception:
        return False

def kill_existing_server():
    try:
        cmd = 'powershell -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like \'*redledger-slim*main.py*\' -or $_.CommandLine -like \'*gateway.server*\' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"'
        subprocess.run(cmd, shell=True, capture_output=True)
    except Exception as e:
        log(f"Kill server error: {e}")

def start_server():
    log("[WATCHDOG] Starting RedLedger Slim Server...")
    cmd = f'powershell -WindowStyle Hidden -Command "Start-Process \'{PYTHON_EXE}\' -ArgumentList \'{SLIM_DIR}\\main.py\' -RedirectStandardOutput \'{SERVER_LOG}\' -RedirectStandardError \'{SERVER_LOG}\' -NoNewWindow"'
    subprocess.Popen(cmd, shell=True)
    time.sleep(3)

def run_watchdog_loop():
    log("=== RedLedger Slim Autonomous Watchdog Started ===")
    consecutive_failures = 0
    last_reported_round = -1
    last_unrecognized_count = 0

    while True:
        status = check_gateway_status()
        if not status:
            consecutive_failures += 1
            log(f"[ALERT] Gateway unresponsive (failure {consecutive_failures}/2)")
            if consecutive_failures >= 2:
                log("[ACTION] Recovering Slim Server...")
                kill_existing_server()
                time.sleep(1)
                start_server()
                consecutive_failures = 0
        else:
            consecutive_failures = 0
            
            # Check bridge health
            bridge = status.get('local_db_bridge', {})
            if not bridge.get('is_alive', False):
                log(f"[WARN] Local DB bridge reported not alive! {bridge}")
            
            # Check round progression
            round_info = status.get('active_round')
            if round_info:
                r_no = round_info.get('round_no')
                r_status = round_info.get('status')
                bets = round_info.get('bets_count', 0)
                if r_no != last_reported_round:
                    log(f"[ROUND] Active Round {r_no} ({r_status}) | Bets: {bets}")
                    last_reported_round = r_no
                elif bets > 200 and r_status == 'open':
                    # Round has many bets and is still open
                    pass

            # Check unrecognized emojis
            unrec = status.get('unrecognized_emojis', [])
            if len(unrec) > last_unrecognized_count:
                new_items = unrec[last_unrecognized_count:]
                for item in new_items:
                    log(f"[EMOJI ALERT] Unrecognized Banker Emoji: MD5={item.get('md5')} from {item.get('sender')}")
                last_unrecognized_count = len(unrec)

            # Check VXHook
            vx_ok = check_vxhook_status()
            if not vx_ok:
                log("[WARN] VXHook port 19088 is not responding!")

        time.sleep(10)

if __name__ == '__main__':
    run_watchdog_loop()
