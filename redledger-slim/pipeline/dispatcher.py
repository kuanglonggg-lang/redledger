"""
Async Fair-Queue Report Dispatcher with Dual VXHook HTTP & Desktop Transport for RedLedger Slim.
"""
from __future__ import annotations

import os
import sys
import json
import queue
import threading
import time
from urllib import request as urlrequest, error as urlerror
from pathlib import Path
from typing import Any, Callable

try:
    from pipeline.wechat_desktop_sender import send_image, send_text
except ImportError:
    try:
        from .wechat_desktop_sender import send_image, send_text
    except ImportError:
        send_image = None
        send_text = None

VXHOOK_URL = os.environ.get("REDLEDGER_VXHOOK_URL", "http://127.0.0.1:19088")


def _vxhook_post(path: str, payload: dict[str, Any], timeout: float = 0.2) -> dict[str, Any]:
    url = f"{VXHOOK_URL.rstrip('/')}{path}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST"
    )
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


class DispatchJob:
    def __init__(
        self,
        job_id: str,
        session_id: int,
        round_id: int,
        target_group_name: str,
        target_group_id: str,
        artifact_type: str, # 'image' or 'text'
        payload: str | Path,
        binding_dir: str = r"D:\RedLedger\data\wechat-desktop-bindings"
    ):
        self.job_id = job_id
        self.session_id = session_id
        self.round_id = round_id
        self.target_group_name = target_group_name
        self.target_group_id = target_group_id
        self.artifact_type = artifact_type
        self.payload = payload
        self.binding_dir = binding_dir
        self.status = "queued"
        self.created_at = time.monotonic()
        self.sent_at: float | None = None
        self.error: str = ""


class Dispatcher:
    def __init__(
        self,
        binding_dir: str = r"D:\RedLedger\data\wechat-desktop-bindings",
        workers: int = 1,
        on_job_completed: Callable[[DispatchJob], None] | None = None
    ):
        self.binding_dir = binding_dir
        self.workers_count = workers
        self.on_job_completed = on_job_completed
        self.queue: queue.Queue[DispatchJob] = queue.Queue(maxsize=1024)
        self.stop_event = threading.Event()
        self.worker_threads: list[threading.Thread] = []
        self._lock = threading.RLock()
        self.stats = {
            "total_enqueued": 0,
            "total_sent": 0,
            "total_failed": 0,
            "last_send_latency_ms": 0.0,
            "last_sent_at": "",
            "last_error": ""
        }

    def start(self):
        self.stop_event.clear()
        for i in range(self.workers_count):
            t = threading.Thread(target=self._worker_loop, name=f"slim-dispatcher-{i+1}", daemon=True)
            t.start()
            self.worker_threads.append(t)
        print(f"[Dispatcher] Started {self.workers_count} dispatch worker(s).")

    def stop(self):
        self.stop_event.set()
        for t in self.worker_threads:
            t.join(timeout=2.0)
        self.worker_threads.clear()

    def enqueue(self, job: DispatchJob) -> bool:
        try:
            self.queue.put_nowait(job)
            with self._lock:
                self.stats["total_enqueued"] += 1
            return True
        except queue.Full:
            print(f"[Dispatcher] Queue full! Dropping job {job.job_id}")
            return False

    def _send_payload(self, job: DispatchJob) -> bool:
        target_wxid = job.target_group_id
        filepath = str(job.payload)
        
        # 1. First Priority: Try VXHook API
        if target_wxid:
            try:
                if job.artifact_type == "image":
                    res = _vxhook_post("/api/send_image_msg", {"wxid": target_wxid, "filepath": filepath})
                else:
                    res = _vxhook_post("/api/send_text_msg", {"wxid": target_wxid, "msg": str(job.payload)})
                
                is_success = (
                    int(res.get("code") or res.get("errCode") or 0) == 1 
                    or "成功" in str(res.get("errMsg") or "")
                    or "ok" in str(res.get("errMsg") or "").lower()
                )
                if is_success:
                    print(f"[Dispatcher] ✅ Successfully delivered {job.artifact_type} to {target_wxid} via VXHook")
                    return True
                else:
                    print(f"[Dispatcher] VXHook returned non-success response: {res}")
            except Exception as e:
                print(f"[Dispatcher] VXHook send failed: {e}")

        # 2. Fallback: Try Desktop GUI automation
        if job.artifact_type == "image" and send_image:
            try:
                res = send_image(
                    group_name=job.target_group_name,
                    image_path=filepath,
                    binding_dir=job.binding_dir,
                    group_id=job.target_group_id
                )
                if int(res.get("code") or 0) == 1:
                    print(f"[Dispatcher] ✅ Successfully delivered image via Desktop Automation")
                    return True
                job.error = str(res)
            except Exception as e:
                job.error = str(e)
        elif job.artifact_type == "text" and send_text:
            try:
                res = send_text(
                    group_name=job.target_group_name,
                    message=str(job.payload),
                    binding_dir=job.binding_dir,
                    group_id=job.target_group_id
                )
                if int(res.get("code") or 0) == 1:
                    print(f"[Dispatcher] ✅ Successfully delivered text via Desktop Automation")
                    return True
                job.error = str(res)
            except Exception as e:
                job.error = str(e)

        return False

    def _worker_loop(self):
        while not self.stop_event.is_set():
            try:
                job = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue

            t_start = time.monotonic()
            job.status = "in_flight"

            success = False
            try:
                success = self._send_payload(job)
            except Exception as exc:
                job.error = str(exc)
                print(f"[Dispatcher] Error sending {job.job_id}: {exc}")

            dur_ms = (time.monotonic() - t_start) * 1000
            with self._lock:
                self.stats["last_send_latency_ms"] = dur_ms
                if success:
                    job.status = "sent"
                    job.sent_at = time.monotonic()
                    self.stats["total_sent"] += 1
                    self.stats["last_sent_at"] = time.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    job.status = "failed"
                    self.stats["total_failed"] += 1
                    self.stats["last_error"] = job.error

            if self.on_job_completed:
                try:
                    self.on_job_completed(job)
                except Exception as cb_exc:
                    print(f"[Dispatcher] Callback error: {cb_exc}")

            self.queue.task_done()
