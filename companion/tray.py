"""ForeverPrices tray app: runs the companion in the background with an icon by the clock.

Right-click menu: Update now, Open log, Notifications, Start with Windows, Quit.
Build a single .exe with build.ps1 (PyInstaller).
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import winreg
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

import companion

APP = "ForeverPrices"
DATA_DIR = companion.STATE_DIR
LOG_FILE = DATA_DIR / "companion.log"
SETTINGS_FILE = DATA_DIR / "settings.json"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
DEFAULTS = {
    "wow": str(companion.DEFAULT_WOW),
    "region": "eu",
    "creds": str(companion.DEFAULT_CREDS),
    "interval": 600,
    "notify": True,
}


# ---------------------------------------------------------------- settings & log
def load_settings() -> dict:
    s = dict(DEFAULTS)
    try:
        s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        pass
    return s


def save_settings(s: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")


def file_sink(line: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # keep the log small: start over when it passes 1 MB
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > 1_000_000:
        LOG_FILE.replace(LOG_FILE.with_suffix(".old.log"))
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d')} {line}\n")


# ---------------------------------------------------------------- icon
def make_icon(state: str = "ok", size: int = 64) -> Image.Image:
    """A gold coin; a red dot in the corner on errors."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 64
    d.ellipse((2 * s, 2 * s, 62 * s, 62 * s), fill=(150, 98, 18), outline=(90, 55, 5), width=max(1, int(2 * s)))
    d.ellipse((8 * s, 8 * s, 56 * s, 56 * s), fill=(242, 190, 50))
    d.ellipse((14 * s, 12 * s, 34 * s, 26 * s), fill=(255, 231, 140))  # shine
    d.ellipse((20 * s, 20 * s, 44 * s, 44 * s), outline=(176, 120, 22), width=max(1, int(4 * s)))
    if state == "error":
        d.ellipse((38 * s, 38 * s, 63 * s, 63 * s), fill=(215, 40, 40), outline=(255, 255, 255), width=max(1, int(2 * s)))
    elif state == "busy":
        d.ellipse((38 * s, 38 * s, 63 * s, 63 * s), fill=(60, 150, 235), outline=(255, 255, 255), width=max(1, int(2 * s)))
    return img


# ---------------------------------------------------------------- start with Windows
def launch_command() -> str:
    if getattr(sys, "frozen", False):  # running as ForeverPrices.exe
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{Path(__file__).resolve()}"'


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(k, APP)
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------- the app
class TrayApp:
    def __init__(self):
        self.settings = load_settings()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.status = "starting…"
        self.icon = pystray.Icon(APP, make_icon("busy"), f"{APP}: starting…", menu=self.menu())

    def menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(lambda _: self.status, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Update now", self.on_update_now, default=True),
            pystray.MenuItem("Open log", self.on_open_log),
            pystray.MenuItem("Notifications", self.on_toggle_notify, checked=lambda _: self.settings["notify"]),
            pystray.MenuItem("Start with Windows", self.on_toggle_autostart, checked=lambda _: autostart_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self.on_quit),
        )

    def set_status(self, text: str, state: str = "ok") -> None:
        self.status = text
        self.icon.icon = make_icon(state)
        self.icon.title = f"{APP}: {text}"[:127]
        self.icon.update_menu()

    # menu actions
    def on_update_now(self, *_):
        self.wake.set()

    def on_open_log(self, *_):
        if not LOG_FILE.exists():
            file_sink("(log is empty)")
        os.startfile(LOG_FILE)

    def on_toggle_notify(self, *_):
        self.settings["notify"] = not self.settings["notify"]
        save_settings(self.settings)

    def on_toggle_autostart(self, *_):
        set_autostart(not autostart_enabled())

    def on_quit(self, *_):
        self.stopping.set()
        self.wake.set()
        self.icon.stop()

    # background loop
    def worker(self):
        s = self.settings
        addon_dir = Path(s["wow"]) / "Interface" / "AddOns" / companion.DATA_ADDON
        state_file = companion.STATE_DIR / f"state-{s['region']}.json"
        api = companion.Blizzard(s["region"], Path(s["creds"]))
        companion.log(f"tray app started: region {s['region']}, writing to {addon_dir}")
        if not Path(s["wow"], "Interface").is_dir():
            companion.log(f"WoW folder not found: {s['wow']} (edit {SETTINGS_FILE})")
            self.set_status("WoW folder not found, see log", "error")
            return
        while not self.stopping.is_set():
            self.set_status("checking for new prices…", "busy")
            result = "unchanged"
            try:
                result = companion.run_once(api, s["region"], addon_dir, state_file)
                snap = companion.latest_snapshot(state_file)
                when = time.strftime("%H:%M", time.localtime(snap)) if snap else "?"
                if result == "pending":
                    self.set_status(f"prices from {when}, Blizzard is still publishing newer ones", "ok")
                else:
                    self.set_status(f"prices from {when}", "ok")
                if result == "updated" and s["notify"]:
                    self.icon.notify(f"New auction prices from {when}. /reload in game to see them.", APP)
            except SystemExit as e:  # bad credentials file
                companion.log(str(e))
                self.set_status("credentials problem, see log", "error")
            except Exception as e:  # network trouble, Blizzard hiccups, anything else
                companion.log(f"update failed, will retry: {e}")
                file_sink(traceback.format_exc().rstrip())
                self.set_status("update failed, retrying (see log)", "error")
            wait = min(120, s["interval"]) if result == "pending" else s["interval"]
            self.wake.wait(wait)
            self.wake.clear()

    def run(self):
        def setup(icon):
            icon.visible = True
            threading.Thread(target=self.worker, daemon=True).start()
        self.icon.run(setup=setup)


def already_running() -> bool:
    """Named mutex so a second copy (e.g. autostart + double-click) just exits."""
    global _mutex  # keep the handle open for the life of the process
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _mutex = k32.CreateMutexW(None, False, "Local\\ForeverPricesTray")
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


def main():
    companion.log_sink = file_sink
    if already_running():
        ctypes.windll.user32.MessageBoxW(None, "ForeverPrices is already running (look for the coin by the clock).", APP, 0x40)
        return
    TrayApp().run()


if __name__ == "__main__":
    main()
