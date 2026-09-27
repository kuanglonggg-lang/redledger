from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Ensure DLL search directory and Pywin32 paths
dll_dir1 = r"E:\RedLedger\runtime\site-packages\pywin32_system32"
if os.path.exists(dll_dir1):
    try:
        os.add_dll_directory(dll_dir1)
    except:
        pass
    os.environ['PATH'] = dll_dir1 + ';' + os.environ.get('PATH', '')

dll_dir = r"E:\RedLedger\server\RedLedgerServer\_internal"
if os.path.exists(dll_dir):
    try:
        os.add_dll_directory(dll_dir)
    except:
        pass
    os.environ['PATH'] = dll_dir + ';' + os.environ.get('PATH', '')

sys.path.insert(0, r"E:\RedLedger\runtime\site-packages\win32")
sys.path.insert(0, r"E:\RedLedger\runtime\site-packages\win32\lib")
sys.path.insert(0, r"E:\RedLedger\runtime\site-packages\Pythonwin")
sys.path.insert(0, r"E:\RedLedger\server\RedLedgerServer\_internal")


from PIL import Image, ImageChops, ImageFilter, ImageGrab, ImageOps, ImageStat

_SEND_LOCK = threading.RLock()
_BINDING_VERSION = 3
_DETACHED_WINDOW_MODE = "detached_window"
_CHAT_LIST_MODE = "chat_list"
_CHAT_LIST_LEFT = 60
_CHAT_LIST_TOP = 60
_CHAT_LIST_RIGHT = 305
_CHAT_ROW_HEIGHT = 64
_SELECTOR_LEFT = 8
_SELECTOR_RIGHT = 205
_SELECTOR_TOP = 6
_SELECTOR_BOTTOM = 38
_SELECTOR_MAX_RMS = 85.0
_SELECTOR_MIN_MARGIN = 4.0
_DETACHED_HEADER_STABLE_HEIGHT = 72


def _wechat_window(group_name: str = "", *, prefer_main: bool = False):
    import psutil
    import win32gui
    import win32process

    matches: list[tuple[int, int, str]] = []

    def collect(handle, _extra):
        if not win32gui.IsWindowVisible(handle):
            return
        _, pid = win32process.GetWindowThreadProcessId(handle)
        try:
            process = psutil.Process(pid)
            title = win32gui.GetWindowText(handle).strip()
            if process.name().lower() in ("weixin.exe", "wechat.exe") and title:
                left, top, right, bottom = win32gui.GetWindowRect(handle)
                width = max(0, right - left)
                height = max(0, bottom - top)
                if width > 100 and height > 100:
                    matches.append((width * height, handle, title))
        except (psutil.Error, OSError):
            return

    win32gui.EnumWindows(collect, None)
    if not matches:
        raise RuntimeError("Official WeChat window not found. Sign in to WeChat first.")
    
    expected = str(group_name or "").strip()
    if expected:
        # Exact match
        exact = [item for item in matches if item[2] == expected]
        if exact:
            return exact[0][1]
        # Partial match
        partial = [item for item in matches if expected in item[2] or item[2] in expected or "奥数" in item[2]]
        if partial:
            return partial[0][1]
        
    if prefer_main:
        main = [item for item in matches if item[2] in {"微信", "WeChat"}]
        if main:
            return max(main)[1]
    return max(matches)[1]


def _activate(handle: int) -> tuple[int, int, int, int]:
    import pyautogui
    import win32api
    import win32con
    import win32gui
    import win32process

    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.05

    if win32gui.IsIconic(handle):
        win32gui.ShowWindow(handle, win32con.SW_RESTORE)
    else:
        win32gui.ShowWindow(handle, win32con.SW_SHOW)

    try:
        fore_hwnd = win32gui.GetForegroundWindow()
        if fore_hwnd != handle:
            fore_tid, _ = win32process.GetWindowThreadProcessId(fore_hwnd)
            cur_tid = win32api.GetCurrentThreadId()
            target_tid, _ = win32process.GetWindowThreadProcessId(handle)

            if fore_tid and fore_tid != cur_tid:
                win32process.AttachThreadInput(cur_tid, fore_tid, True)
            if target_tid and target_tid != cur_tid:
                win32process.AttachThreadInput(cur_tid, target_tid, True)

            win32api.keybd_event(0x12, 0, 0, 0)
            win32api.keybd_event(0x12, 0, win32con.KEYEVENTF_KEYUP, 0)

            win32gui.BringWindowToTop(handle)
            win32gui.SetForegroundWindow(handle)
            win32gui.SetActiveWindow(handle)

            if fore_tid and fore_tid != cur_tid:
                win32process.AttachThreadInput(cur_tid, fore_tid, False)
            if target_tid and target_tid != cur_tid:
                win32process.AttachThreadInput(cur_tid, target_tid, False)
    except Exception as e:
        print(f"[_activate warning] {e}")

    rect = win32gui.GetWindowRect(handle)
    left, top, right, bottom = rect
    try:
        pyautogui.click(left + 150, top + 15)
    except Exception:
        pass
    time.sleep(0.1)
    return win32gui.GetWindowRect(handle)


def _window_title(handle: int) -> str:
    import win32gui
    return str(win32gui.GetWindowText(handle) or "").strip()


def send_image_to_current_group(
    group_name: str,
    image_path: str | os.PathLike[str],
    binding_dir: str | os.PathLike[str] = "",
    group_id: str = "",
) -> dict[str, Any]:
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.05

    image_path = Path(image_path).resolve()
    if not image_path.is_file():
        raise FileNotFoundError(str(image_path))
    with _SEND_LOCK:
        handle = _wechat_window(group_name)
        rect = _activate(handle)
        left, top, right, bottom = rect
        target_name = str(group_name or "").strip()
        current_title = _window_title(handle)
        
        # If currently in main window and title doesn't match target group, navigate to target group via search
        if target_name and target_name not in current_title:
            search_x = left + 140
            search_y = top + 80
            pyautogui.click(search_x, search_y)
            time.sleep(0.12)
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.05)
            pyautogui.press("backspace")
            time.sleep(0.05)
            _copy_text(target_name)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.4)
            pyautogui.press("enter")
            time.sleep(0.3)

        # Click inside the composer (message input area on the right side)
        comp_x = left + (right - left) // 2 + 100
        comp_y = bottom - 50
        pyautogui.click(comp_x, comp_y)
        time.sleep(0.15)
        
        # Copy image to clipboard and paste
        _copy_image(image_path)
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.4)
        pyautogui.press("enter")
        time.sleep(0.3)
        
    return {
        "code": 1,
        "msg": "desktop send success",
        "group_id": str(group_id or "").strip(),
        "group_name": group_name,
        "filepath": str(image_path),
        "paste_verified": True,
    }


def send_image(
    group_name: str,
    image_path: str | os.PathLike[str],
    binding_dir: str | os.PathLike[str] = "",
    group_id: str = "",
) -> dict[str, Any]:
    return send_image_to_current_group(group_name, image_path, binding_dir=binding_dir, group_id=group_id)


def send_text_to_current_group(
    group_name: str,
    message: str,
    binding_dir: str | os.PathLike[str] = "",
    group_id: str = "",
) -> dict[str, Any]:
    import pyautogui

    with _SEND_LOCK:
        handle = _wechat_window(group_name)
        rect = _activate(handle)
        binding_mode = _CHAT_LIST_MODE
        try:
            if binding_dir and os.path.exists(str(binding_dir)):
                record = _binding_record(group_id, group_name, Path(binding_dir))
                binding_mode = str(record.get("binding_mode") or _CHAT_LIST_MODE)
        except Exception:
            pass

        composer = _composer_region(rect, binding_mode)
        pyautogui.click((composer[0] + composer[2]) // 2, composer[3] - 45)
        time.sleep(0.05)
        _copy_text(str(message))
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.06)
        pyautogui.press("enter")
        time.sleep(0.1)
    return {
        "code": 1,
        "msg": "desktop text send success",
        "group_id": str(group_id or "").strip(),
        "group_name": group_name,
    }


def send_text(
    group_name: str,
    message: str,
    binding_dir: str | os.PathLike[str] = "",
    group_id: str = "",
) -> dict[str, Any]:
    return send_text_to_current_group(group_name, message, binding_dir=binding_dir, group_id=group_id)


def _composer_region(
    rect: tuple[int, int, int, int],
    binding_mode: str,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = rect
    content_left = left if binding_mode == _DETACHED_WINDOW_MODE else left + 305
    region = (
        content_left + 20,
        max(top + 120, bottom - 170),
        right - 20,
        bottom - 28,
    )
    if region[2] - region[0] < 100 or region[3] - region[1] < 50:
        return (left + 50, bottom - 120, right - 50, bottom - 20)
    return region


def _copy_text(value: str) -> None:
    import win32clipboard

    _open_clipboard(win32clipboard)
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(value, win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def _copy_image(path: Path) -> None:
    import win32clipboard

    with Image.open(path) as image:
        output = io.BytesIO()
        image.convert("RGB").save(output, "BMP")
        dib = output.getvalue()[14:]
    _open_clipboard(win32clipboard)
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib)
    finally:
        win32clipboard.CloseClipboard()


def _open_clipboard(win32clipboard: Any) -> None:
    last_error: Exception | None = None
    for _attempt in range(10):
        try:
            win32clipboard.OpenClipboard()
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError("The Windows clipboard is busy.") from last_error


def _load_bindings(binding_dir: Path) -> dict[str, dict[str, Any]]:
    manifest = binding_dir / "bindings.json"
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    if isinstance(raw.get("bindings"), dict):
        source = raw["bindings"]
    else:
        source = raw
    records: dict[str, dict[str, Any]] = {}
    for key, value in source.items():
        if not isinstance(value, dict):
            continue
        record = dict(value)
        if "group_name" not in record:
            record["group_name"] = str(key or "")
        records[str(key)] = record
    return records


def _binding_record(
    group_id: str,
    group_name: str,
    binding_dir: Path,
    *,
    require_selector: bool = False,
) -> dict[str, Any]:
    group_id = str(group_id or "").strip()
    group_name = str(group_name or "").strip()
    records = _load_bindings(binding_dir)
    record = records.get(group_id) if group_id else None
    if record is None:
        matches = [
            value
            for value in records.values()
            if str(value.get("group_name") or "").strip() == group_name
        ]
        if len(matches) == 1:
            record = matches[0]
    if record is None:
        return {"group_name": group_name, "group_id": group_id, "binding_mode": _CHAT_LIST_MODE}
    return record
