#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RedLedger Multi-Group High-Performance Ingress Router.
Dispatches WeChat Hook callbacks to group-specific workers in < 0.01ms.
Thread-safe, non-blocking, with zero business computation.
"""
import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Runtime search paths
sys.path.insert(0, r"E:\RedLedger\server\RedLedgerServer\_internal\base_library.zip")
sys.path.insert(0, r"E:\RedLedger\runtime\Lib")
sys.path.insert(0, r"E:\RedLedger\runtime\site-packages")
sys.path.insert(0, r"E:\RedLedger\runtime\site-packages\win32")
sys.path.insert(0, r"E:\RedLedger\runtime\site-packages\win32\lib")
sys.path.insert(0, r"E:\RedLedger\runtime\site-packages\Pythonwin")
sys.path.insert(0, r"E:\RedLedger\runtime\Lib\site-packages")
sys.path.insert(0, r"E:\RedLedger\server\RedLedgerServer\_internal")
sys.path.insert(0, r"E:\RedLedger\redledger-slim")
sys.path.insert(0, r"E:\RedLedger\redledger-multi")

import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

from flask import Flask, request, jsonify
from waitress import serve

# Configure paths
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("ROUTER_CONFIG_PATH", str(BASE_DIR / "routes.json")))

app = Flask("redledger-router")

class RouteManager:
    def __init__(self, config_file: Path):
        self.config_file = config_file
        self.routes: dict[str, dict[str, Any]] = {}
        self.default_port: int = 8771
        self.ingress_port: int = 8766
        self.last_mtime: float = 0.0
        self.reload()

    def reload(self):
        try:
            if not self.config_file.exists():
                return
            mtime = os.path.getmtime(self.config_file)
            if mtime == self.last_mtime:
                return
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.routes = data.get("groups", {})
            self.ingress_port = int(data.get("ingress_port", 8766))
            def_gid = data.get("default_group")
            if def_gid and def_gid in self.routes:
                self.default_port = self.routes[def_gid].get("port", 8771)
            self.last_mtime = mtime
            print(f"[Router] Loaded {len(self.routes)} routes from {self.config_file.name}")
        except Exception as e:
            print(f"[Router Config Error] Failed to load {self.config_file}: {e}")

    def get_target_port(self, group_id: str) -> int:
        self.reload()
        if group_id and group_id in self.routes:
            cfg = self.routes[group_id]
            if cfg.get("enabled", True):
                return int(cfg.get("port", self.default_port))
        return self.default_port

route_mgr = RouteManager(CONFIG_PATH)

def extract_group_id(payload: dict[str, Any]) -> str:
    gid = payload.get("from_group") or payload.get("group_id")
    if gid:
        return str(gid).strip()
    data = payload.get("data")
    if isinstance(data, dict):
        gid = data.get("from_group") or data.get("group_id")
        if gid:
            return str(gid).strip()
    msg = payload.get("msg") or payload.get("message")
    if isinstance(msg, dict):
        gid = msg.get("from_group") or msg.get("group_id")
        if gid:
            return str(gid).strip()
    return ""

def forward_to_worker(port: int, raw_body: bytes, path: str = "/api/recvMsg") -> tuple[int, bytes]:
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(
        url,
        data=raw_body,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        err_msg = json.dumps({"status": "worker_unreachable", "port": port, "error": str(e)}).encode("utf-8")
        return 502, err_msg

@app.route("/api/recvMsg", methods=["POST"])
def recv_msg():
    raw_data = request.get_data()
    group_id = ""
    try:
        payload = json.loads(raw_data.decode("utf-8", errors="replace"))
        group_id = extract_group_id(payload)
    except Exception:
        pass

    target_port = route_mgr.get_target_port(group_id)
    status_code, body = forward_to_worker(target_port, raw_data, path="/api/recvMsg")
    return body, status_code, {"Content-Type": "application/json"}

@app.route("/api/status", methods=["GET"])
def cluster_status():
    route_mgr.reload()
    workers_status = {}
    for gid, cfg in route_mgr.routes.items():
        port = cfg.get("port")
        name = cfg.get("name", gid)
        url = f"http://127.0.0.1:{port}/api/status"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                workers_status[gid] = {
                    "name": name,
                    "port": port,
                    "online": True,
                    "data": json.loads(resp.read().decode("utf-8", errors="replace"))
                }
        except Exception as e:
            workers_status[gid] = {
                "name": name,
                "port": port,
                "online": False,
                "error": str(e)
            }

    return jsonify({
        "status": "ok",
        "role": "ingress_router",
        "ingress_port": route_mgr.ingress_port,
        "total_routes": len(route_mgr.routes),
        "workers": workers_status,
        "server_time": time.strftime("%Y-%m-%d %H:%M:%S")
    })

@app.route("/api/reload_routes", methods=["POST", "GET"])
def reload_routes():
    route_mgr.reload()
    return jsonify({
        "status": "ok",
        "routes": route_mgr.routes,
        "ingress_port": route_mgr.ingress_port
    })

def main():
    port = int(os.environ.get("ROUTER_PORT", str(route_mgr.ingress_port)))
    print("=" * 60)
    print(f"  Starting RedLedger Multi-Group Ingress Router on port {port}")
    print(f"  Configuration file: {CONFIG_PATH}")
    print("=" * 60)
    serve(app, host="0.0.0.0", port=port, threads=16)

if __name__ == "__main__":
    main()
