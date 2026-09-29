#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RedLedger Multi-Group Group Worker.
Spawns an isolated micro-engine for a specific WeChat group.
Completely isolated memory, independent SQLite database, zero lock contention.
"""
import os
import sys

# PyInstaller & Runtime Paths MUST come before any standard lib imports
sys.path.insert(0, r"E:\RedLedger\server\RedLedgerServer\_internal\base_library.zip")
sys.path.insert(0, r"E:\RedLedger\runtime\Lib")
sys.path.insert(0, r"E:\RedLedger\runtime\site-packages")
sys.path.insert(0, r"E:\RedLedger\runtime\site-packages\win32")
sys.path.insert(0, r"E:\RedLedger\runtime\site-packages\win32\lib")
sys.path.insert(0, r"E:\RedLedger\runtime\site-packages\Pythonwin")
sys.path.insert(0, r"E:\RedLedger\runtime\Lib\site-packages")
sys.path.insert(0, r"E:\RedLedger\server\RedLedgerServer\_internal")
sys.path.insert(0, r"E:\RedLedger\redledger-slim")

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

dll_dir = r"E:\RedLedger\runtime\site-packages\pywin32_system32"
if os.path.exists(dll_dir):
    try:
        os.add_dll_directory(dll_dir)
    except (AttributeError, OSError):
        pass
    os.environ["PATH"] = dll_dir + ";" + os.environ.get("PATH", "")

import argparse
import logging
from waitress import serve
from gateway.server import SlimGatewayApp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("RedLedgerWorker")

def main():
    parser = argparse.ArgumentParser(description="RedLedger Group Worker")
    parser.add_argument("--port", type=int, default=8771, help="Worker listening port")
    parser.add_argument("--group", type=str, default="", help="Target WeChat chatroom group ID")
    parser.add_argument("--db", type=str, default="", help="Path to group-specific SQLite database")
    parser.add_argument("--report-dir", type=str, default=r"C:\Temp\redledger-reports", help="Reports output directory")
    args = parser.parse_args()

    port = args.port
    group_id = args.group.strip()
    db_path = args.db.strip()
    
    if not db_path:
        if group_id:
            safe_name = group_id.split("@")[0]
            db_path = os.path.join(r"E:\RedLedger\data", f"redledger_{safe_name}.sqlite3")
        else:
            db_path = r"E:\RedLedger\data\redledger.sqlite3"

    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    logger.info("=" * 60)
    logger.info(f"  Starting RedLedger Group Worker on port {port}")
    logger.info(f"  Target Group:   {group_id or '(Any / Default)'}")
    logger.info(f"  Database Path:  {db_path}")
    logger.info(f"  Reports Dir:    {args.report_dir}")
    logger.info("=" * 60)

    gateway = SlimGatewayApp(db_path=db_path, report_dir=args.report_dir, target_group_id=group_id)
    if group_id:
        gateway.local_bridge.monitored_groups = {group_id}
        
    gateway.start()

    logger.info(f"Serving Worker HTTP on http://0.0.0.0:{port} via Waitress...")
    serve(gateway.app, host="0.0.0.0", port=port, threads=8)

if __name__ == "__main__":
    main()
