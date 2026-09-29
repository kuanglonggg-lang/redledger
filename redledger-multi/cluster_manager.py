#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RedLedger Cluster Supervisor.
Starts the Ingress Router and all configured Group Workers.
Monitors process health and provides unified lifecycle management.
"""
import sys
sys.path.insert(0, r"E:\RedLedger\server\RedLedgerServer\_internal\base_library.zip")
sys.path.insert(0, r"E:\RedLedger\runtime\Lib")
sys.path.insert(0, r"E:\RedLedger\runtime\site-packages")
sys.path.insert(0, r"E:\RedLedger\server\RedLedgerServer\_internal")
sys.path.insert(0, r"E:\RedLedger\redledger-slim")

import os
import json
import time
import signal
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PYTHON_EXE = r"E:\RedLedger\server\RedLedgerServer\_internal\python.exe"
if not os.path.exists(PYTHON_EXE):
    PYTHON_EXE = sys.executable

CONFIG_PATH = BASE_DIR / "routes.json"

class ClusterSupervisor:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.processes: dict[str, subprocess.Popen] = {}
        self.running = True

    def load_config(self) -> dict:
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def start_worker(self, group_id: str, cfg: dict):
        port = cfg.get("port", 8771)
        name = cfg.get("name", group_id)
        db = cfg.get("db", "")
        cmd = [
            PYTHON_EXE,
            str(BASE_DIR / "worker.py"),
            "--port", str(port),
            "--group", str(group_id),
        ]
        if db:
            cmd.extend(["--db", str(db)])

        print(f"[Cluster] Spawning Worker for '{name}' ({group_id}) on port {port}...")
        proc = subprocess.Popen(cmd, cwd=str(BASE_DIR))
        self.processes[f"worker_{group_id}"] = proc

    def start_router(self, ingress_port: int):
        cmd = [
            PYTHON_EXE,
            str(BASE_DIR / "router.py"),
        ]
        env = os.environ.copy()
        env["ROUTER_PORT"] = str(ingress_port)
        print(f"[Cluster] Spawning Ingress Router on port {ingress_port}...")
        proc = subprocess.Popen(cmd, cwd=str(BASE_DIR), env=env)
        self.processes["router"] = proc

    def stop_all(self):
        print("\n[Cluster] Shutting down all cluster processes...")
        self.running = False
        for tag, proc in self.processes.items():
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()
                print(f"[Cluster] Process {tag} stopped.")

    def run(self):
        config = self.load_config()
        ingress_port = config.get("ingress_port", 8766)
        groups = config.get("groups", {})

        # Start Workers first
        for gid, gcfg in groups.items():
            if gcfg.get("enabled", True):
                self.start_worker(gid, gcfg)
                time.sleep(0.5)

        # Start Router
        self.start_router(ingress_port)

        print("=" * 60)
        print(f"[Cluster] RedLedger Multi-Group Cluster running with {len(groups)} workers.")
        print(f"[Cluster] Main Ingress Port: {ingress_port}")
        print("=" * 60)

        try:
            while self.running:
                time.sleep(2.0)
                # Health monitor
                for tag, proc in list(self.processes.items()):
                    ret = proc.poll()
                    if ret is not None:
                        print(f"[Cluster Warning] Process {tag} exited with code {ret}! Auto-restarting in 2s...")
                        time.sleep(2.0)
                        if tag == "router":
                            self.start_router(ingress_port)
                        elif tag.startswith("worker_"):
                            gid = tag.replace("worker_", "")
                            if gid in groups and groups[gid].get("enabled", True):
                                self.start_worker(gid, groups[gid])
        except KeyboardInterrupt:
            self.stop_all()

if __name__ == "__main__":
    sup = ClusterSupervisor(CONFIG_PATH)
    sup.run()
