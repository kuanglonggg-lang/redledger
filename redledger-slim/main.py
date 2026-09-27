import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

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

dll_dir = r"E:\RedLedger\runtime\site-packages\pywin32_system32"
if os.path.exists(dll_dir):
    try:
        os.add_dll_directory(dll_dir)
    except (AttributeError, OSError):
        pass
    os.environ["PATH"] = dll_dir + ";" + os.environ.get("PATH", "")

import logging
from waitress import serve
from gateway.server import SlimGatewayApp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("RedLedgerSlim")

def main():
    db_path = os.environ.get("REDLEDGER_DB", r"E:\RedLedger\data\redledger.sqlite3")
    port = int(os.environ.get("REDLEDGER_SLIM_PORT", "8766"))
    
    logger.info("=" * 60)
    logger.info("  Starting RedLedger-Slim Micro-Core Engine")
    logger.info(f"  Database Path: {db_path}")
    logger.info(f"  Ingress Port:  {port}")
    logger.info("=" * 60)
    
    gateway = SlimGatewayApp(db_path=db_path)
    gateway.start()
    
    logger.info(f"Serving HTTP Webhook Gateway on http://0.0.0.0:{port} via Waitress...")
    serve(gateway.app, host="0.0.0.0", port=port, threads=8)

if __name__ == "__main__":
    main()
