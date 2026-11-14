"""ForeverPrices tray app: keeps the ForeverPrices_Data addon up to date, with an icon by the clock.

By default it downloads the hourly price file from the ForeverPrices server (GitHub Pages).
People with their own Blizzard API credentials can switch to fetching from Blizzard directly.
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
REPO_URL = "https://github.com/Miaphys/ForeverPrices"
DEFAULTS = {
    "wow": "",              # found automatically on first start
    "region": "eu",
    "source": "server",     # "server" or "blizzard" (own API credentials)
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
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > 1_000_000:  # keep the log small
        LOG_FILE.replace(LOG_FILE.with_suffix(".old.log"))
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d')} {line}\n")


def pick_folder(prompt: str) -> str:
    """Windows folder picker (via PowerShell, so no extra GUI library is needed)."""
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
        f"$d.Description = '{prompt}';"
        "if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath }"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script],
                             capture_output=True, text=True, timeout=600,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def message(text: str, error: bool = False) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, APP, 0x10 if error else 0x40)


# ---------------------------------------------------------------- icon
def make_icon(state: str = "ok", size: int = 64) -> Image.Image:
    """A gold coin; a blue dot while working, a red dot on errors."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 64
    d.ellipse((2 * s, 2 * s, 62 * s, 62 * s), fill=(150, 98, 18), outline=(90, 55, 5), width=max(1, int(2 * s)))
    d.ellipse((8 * s, 8 * s, 56 * s, 56 * s), fill=(242, 190, 50))
    d.ellipse((14 * s, 12 * s, 34 * s, 26 * s), fill=(255, 231, 140))  # shine
    d.ellipse((20 * s, 20 * s, 44 * s, 44 * s), outline=(176, 120, 22), width=max(1, int(4 * s)))
    dot = {"error": (215, 40, 40), "busy": (60, 150, 235)}.get(state)
    if dot:
        d.ellipse((38 * s, 38 * s, 63 * s, 63 * s), fill=dot, outline=(255, 255, 255), width=max(1, int(2 * s)))
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
        def region_item(code, label):
            return pystray.MenuItem(label, lambda *_: self.set_region(code), radio=True,
                                    checked=lambda _: self.settings["region"] == code)
        return pystray.Menu(
            pystray.MenuItem(lambda _: self.status, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Update now", self.on_update_now, default=True),
            pystray.MenuItem("Region", pystray.Menu(region_item("eu", "Europe (EU)"), region_item("us", "Americas (US)"))),
            pystray.MenuItem("Choose WoW folder…", self.on_choose_wow),
            pystray.MenuItem("Open log", self.on_open_log),
            pystray.MenuItem("Notifications", self.on_toggle_notify, checked=lambda _: self.settings["notify"]),
            pystray.MenuItem("Start with Windows", self.on_toggle_autostart, checked=lambda _: autostart_enabled()),
            pystray.MenuItem("Use my own Blizzard API key", self.on_toggle_source,
                             checked=lambda _: self.settings["source"] == "blizzard",
                             visible=lambda _: Path(self.settings["creds"]).exists()),
            pystray.MenuItem("About / help", lambda *_: os.startfile(REPO_URL)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self.on_quit),
        )

    def set_status(self, text: str, state: str = "ok") -> None:
        self.status = text
        self.icon.icon = make_icon(state)
        self.icon.title = f"{APP}: {text}"[:127]
        self.icon.update_menu()

    def change(self, key, value):
        self.settings[key] = value
        save_settings(self.settings)
        self.wake.set()

    # menu actions
    def on_update_now(self, *_):
        self.wake.set()

    def set_region(self, code):
        self.change("region", code)

    def on_choose_wow(self, *_):
        threading.Thread(target=self.ask_wow_folder, daemon=True).start()

    def ask_wow_folder(self) -> bool:
        path = pick_folder("Select your World of Warcraft _forever_ folder")
        if not path:
            return False
        p = Path(path)
        if not companion.is_wow_forever(p) and companion.is_wow_forever(p / "_forever_"):
            p = p / "_forever_"
        if not companion.is_wow_forever(p):
            message(f"That doesn't look like a WoW Forever folder:\n{path}\n\n"
                    "Pick the _forever_ folder inside your World of Warcraft folder (the one with Wow.exe).", error=True)
            return False
        self.change("wow", str(p))
        return True

    def on_open_log(self, *_):
        if not LOG_FILE.exists():
            file_sink("(log is empty)")
        os.startfile(LOG_FILE)

    def on_toggle_notify(self, *_):
        self.change("notify", not self.settings["notify"])

    def on_toggle_autostart(self, *_):
        set_autostart(not autostart_enabled())

    def on_toggle_source(self, *_):
        self.change("source", "server" if self.settings["source"] == "blizzard" else "blizzard")

    def on_quit(self, *_):
        self.stopping.set()
        self.wake.set()
        self.icon.stop()

    # background loop
    def wow_folder(self) -> Path | None:
        s = self.settings
        if s["wow"] and companion.is_wow_forever(Path(s["wow"])):
            return Path(s["wow"])
        found = companion.find_wow()
        if found:
            companion.log(f"found WoW Forever at {found}")
            self.change("wow", str(found))
            return found
        return None

    def update_once(self) -> str:
        s = self.settings
        wow = self.wow_folder()
        if not wow:
            self.set_status("WoW folder not found: right-click > Choose WoW folder…", "error")
            if not getattr(self, "_asked_folder", False):
                self._asked_folder = True
                message("ForeverPrices couldn't find World of Warcraft: Forever.\n\n"
                        "Please select your _forever_ folder (inside your World of Warcraft folder).")
                self.ask_wow_folder()
            return "error"
        addon_dir = wow / "Interface" / "AddOns" / companion.DATA_ADDON
        region = s["region"]
        if s["source"] == "blizzard" and Path(s["creds"]).exists():
            api = companion.Blizzard(region, Path(s["creds"]))
            state_file = companion.STATE_DIR / f"state-{region}.json"
            result = companion.run_once(api, region, addon_dir, state_file)
            newest = companion.latest_snapshot(state_file)
        else:
            result, newest = companion.run_from_server(region, addon_dir)
        when = time.strftime("%H:%M", time.localtime(newest)) if newest else "?"
        tag = region.upper()
        if result == "pending":
            self.set_status(f"{tag} prices from {when}, newer ones on the way")
        else:
            self.set_status(f"{tag} prices from {when}")
        if result == "updated" and s["notify"]:
            self.icon.notify(f"New {tag} auction prices from {when}. /reload in game to see them.", APP)
        return result

    def worker(self):
        companion.log(f"tray app started (companion v{companion.VERSION})")
        while not self.stopping.is_set():
            self.set_status("checking for new prices…", "busy")
            result = "unchanged"
            try:
                result = self.update_once()
            except SystemExit as e:  # bad credentials file (own-key mode)
                companion.log(str(e))
                self.set_status("credentials problem, see log", "error")
            except Exception as e:  # network trouble, server hiccups, anything else
                companion.log(f"update failed, will retry: {e}")
                file_sink(traceback.format_exc().rstrip())
                self.set_status("update failed, retrying (see log)", "error")
            wait = min(120, self.settings["interval"]) if result == "pending" else self.settings["interval"]
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
        message("ForeverPrices is already running (look for the gold coin by the clock).")
        return
    TrayApp().run()


if __name__ == "__main__":
    main()
