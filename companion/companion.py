"""ForeverPrices companion (personal version).

Downloads WoW Forever auction house data from Blizzard's Game Data API, works out
prices per item for every auction house, and writes them into the
ForeverPrices_Data addon so the ForeverPrices addon can show them in tooltips.

Usage:
    python -I companion.py --once          # one update, then exit
    python -I companion.py                 # keep running, check every 10 minutes

Only uses the Python standard library so it can be packed into a single .exe later.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

VERSION = 1
DEFAULT_WOW = Path(r"D:\World of Warcraft\_forever_")
DEFAULT_CREDS = Path.home() / ".bnet-credentials.txt"
STATE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ForeverPrices"
DATA_ADDON = "ForeverPrices_Data"
INTERFACE = "16001"

# Market value: quantity-weighted mean of the cheapest 25% of units, ignoring
# units more than 1.5x that first estimate (keeps one silly listing from skewing it).
MARKET_SHARE = 0.25
OUTLIER_FACTOR = 1.5
# 3-day average: exponential moving average, one step per new snapshot (~hourly).
AVG_ALPHA = 2 / (72 + 1)
# Forget items not seen on the AH for this long.
FORGET_AFTER = 14 * 86400


def _print_sink(line: str) -> None:
    print(line, flush=True)


log_sink = _print_sink  # the tray app swaps this for a log file


def log(msg: str) -> None:
    log_sink(f"{time.strftime('%H:%M:%S')} {msg}")


def latest_snapshot(state_file: Path) -> int:
    """Unix time of the newest snapshot we have prices for (0 if none)."""
    state = load_state(state_file)
    return max((h.get("updated", 0) for h in state["houses"].values() if h.get("items")), default=0)


# ---------------------------------------------------------------- Blizzard API
class Blizzard:
    def __init__(self, region: str, creds_file: Path):
        self.region = region
        self.creds_file = creds_file
        self._token = None
        self._token_expires = 0.0

    def _read_creds(self) -> tuple[str, str]:
        # The server job gets them as environment variables (GitHub Actions secrets).
        if os.environ.get("BNET_ID") and os.environ.get("BNET_SECRET"):
            return os.environ["BNET_ID"], os.environ["BNET_SECRET"]
        cred = {}
        for line in self.creds_file.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                cred[k.strip().replace("-", "_").upper()] = v.strip()
        try:
            return cred["BNET_ID"], cred["BNET_SECRET"]
        except KeyError:
            sys.exit(f"{self.creds_file} needs BNET_ID=... and BNET_SECRET=... lines")

    def token(self) -> str:
        if self._token and time.time() < self._token_expires - 300:
            return self._token
        cid, secret = self._read_creds()
        basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
        req = urllib.request.Request(
            "https://oauth.battle.net/token",
            data=b"grant_type=client_credentials",
            headers={"Authorization": f"Basic {basic}"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.load(r)
        self._token = tok["access_token"]
        self._token_expires = time.time() + tok.get("expires_in", 3600)
        return self._token

    def get(self, path: str):
        """Returns (json, Last-Modified header).

        Blizzard marks auction responses cacheable for a day, and conditional
        requests (If-Modified-Since) were answered "unchanged" from a stale copy
        after new data was out. So: plain requests, no-cache, compare timestamps ourselves.
        """
        url = f"https://{self.region}.api.blizzard.com{path}?namespace=dynamic-forever-{self.region}"
        headers = {
            "Authorization": f"Bearer {self.token()}",
            "Accept-Encoding": "gzip",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=120) as r:
            lm = r.headers.get("Last-Modified")
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return json.loads(body), lm


# ---------------------------------------------------------------- pricing
def summarize(auctions: list, commodities: list) -> dict[int, tuple[int, int, int]]:
    """item id -> (min unit price, market value, quantity available), in copper."""
    units: dict[int, list[tuple[int, int]]] = {}
    for a in auctions:
        buyout = a.get("buyout")
        if not buyout:
            continue  # bid-only auctions don't tell us a price
        q = max(1, a.get("quantity", 1))
        units.setdefault(a["item"]["id"], []).append((buyout // q, q))
    for c in commodities:
        units.setdefault(c["item"]["id"], []).append((c["unit_price"], c.get("quantity", 1)))

    out = {}
    for item, lst in units.items():
        lst.sort()
        total = sum(q for _, q in lst)
        want = max(1, int(total * MARKET_SHARE))
        picked, n = [], 0
        for price, q in lst:
            take = min(q, want - n)
            picked.append((price, take))
            n += take
            if n >= want:
                break
        est = sum(p * q for p, q in picked) / n
        kept = [(p, q) for p, q in picked if p <= est * OUTLIER_FACTOR] or picked
        market = round(sum(p * q for p, q in kept) / sum(q for _, q in kept))
        out[item] = (lst[0][0], market, total)
    return out


# ---------------------------------------------------------------- state
def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log(f"state file unreadable, starting fresh: {path}")
    return {"houses": {}}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def update_house(hstate: dict, snapshot: dict, updated: int) -> None:
    """Merge a new snapshot into a house's stored items (keeps the 3-day average)."""
    items = hstate.setdefault("items", {})
    for item, (mn, mkt, qty) in snapshot.items():
        key = str(item)
        old = items.get(key)
        avg = mkt if not old or not old[3] else round(old[3] + AVG_ALPHA * (mkt - old[3]))
        items[key] = [mn, mkt, qty, avg, updated]
    for key, row in list(items.items()):
        if int(key) not in snapshot:
            if updated - row[4] > FORGET_AFTER:
                del items[key]
            else:
                row[0] = row[1] = row[2] = 0  # not listed right now; keep the average
    hstate["updated"] = updated


# ---------------------------------------------------------------- Lua output
def write_addon(addon_dir: Path, region: str, houses: dict, state: dict) -> int:
    addon_dir.mkdir(parents=True, exist_ok=True)
    toc = (
        f"## Interface: {INTERFACE}\n"
        "## Title: ForeverPrices Data\n"
        "## Notes: Auction prices written by the ForeverPrices companion app. Don't edit.\n"
        "## Author: ForeverPrices companion\n"
        f"## Version: {VERSION}\n"
        "Data.lua\n"
    )
    lines = [
        "-- Written by the ForeverPrices companion app. Changes are overwritten.",
        "ForeverPricesData = {",
        f"  version = {VERSION},",
        f'  region = "{region if region in ("eu", "us", "kr", "tw") else "eu"}",',
        f"  generated = {int(time.time())},",
        "  houses = {",
    ]
    # Everything below is forced to plain integers / safe text, because in server mode
    # the state comes from a download: nothing in it may end up as Lua code.
    for hid in sorted(houses, key=int):
        h = state["houses"].get(str(hid))
        if not h or not h.get("items"):
            continue
        # one compact string per house: "item:min:market:qty:avg;" (addon parses only its own house)
        rows = ";".join(
            f"{int(k)}:{int(r[0])}:{int(r[1])}:{int(r[2])}:{int(r[3])}"
            for k, r in sorted(h["items"].items(), key=lambda kv: int(kv[0]))
        )
        name = "".join(ch for ch in str(houses[hid]) if ch.isalnum() or ch in " '-")[:60]
        lines.append(f'    [{int(hid)}] = {{ name = "{name}", updated = {int(h["updated"])}, data = "{rows}" }},')
    lines += ["  },", "}", ""]

    for fname, content in (("Data.lua", "\n".join(lines)), (f"{DATA_ADDON}.toc", toc)):
        tmp = addon_dir / (fname + ".tmp")
        tmp.write_text(content, encoding="utf-8", newline="\n")
        os.replace(tmp, addon_dir / fname)
    return (addon_dir / "Data.lua").stat().st_size


# ---------------------------------------------------------------- main loop
def local_time(lm: str | None) -> str:
    if not lm:
        return "?"
    return parsedate_to_datetime(lm).astimezone().strftime("%H:%M")


def run_once(api: Blizzard, region: str, addon_dir: Path, state_file: Path) -> str:
    """Returns "updated", "unchanged", or "pending" (new snapshot only partly published)."""
    state = load_state(state_file)
    index, _ = api.get("/data/wow/ruleset/2/auctions/")
    houses = {str(h["id"]): h["name"]["en_US"] for h in index["auctions"]}

    # Cheap check first: fetch only the smallest house (usually an empty one, a few
    # bytes). All houses get a new snapshot each hour, so if its timestamp moved, fetch
    # the rest. Empty houses are published first; big ones can show up several minutes
    # later (seen: still old at :23, new at :24), so any house more than 10 minutes
    # behind the newest snapshot counts as pending and is retried soon.
    # Safety net: also fetch everything if our newest data is over 90 minutes old.
    stored = state["houses"]
    probe = min(houses, key=lambda h: (len(stored.get(h, {}).get("items", {})), int(h)))
    _, probe_lm = api.get(f"/data/wow/ruleset/2/auctions/{probe}")
    probe_time = int(parsedate_to_datetime(probe_lm).timestamp()) if probe_lm else int(time.time())
    newest = max([probe_time] + [h.get("updated", 0) for h in stored.values()])
    if time.time() - newest > 90 * 60:
        todo = list(houses)  # safety net: something's off, refresh everything
    else:
        todo = [h for h in houses if not stored.get(h, {}).get("lm") or stored[h].get("updated", 0) < newest - 600]
    if not todo:
        log(f"no new data yet (have the {local_time(probe_lm)} snapshot)")
        return "unchanged"

    changed = 0
    for hid in todo:
        name = houses[hid]
        hs = stored.setdefault(hid, {})
        auctions, lm_a = api.get(f"/data/wow/ruleset/2/auctions/{hid}")
        if lm_a and lm_a == hs.get("lm"):
            continue
        commodities, _ = api.get(f"/data/wow/ruleset/2/auctions/{hid}/commodities")
        updated = int(parsedate_to_datetime(lm_a).timestamp()) if lm_a else int(time.time())
        snap = summarize(auctions.get("auctions", []), (commodities or {}).get("auctions", []))
        has_prices = bool(snap or hs.get("items"))
        update_house(hs, snap, updated)
        hs["lm"] = lm_a
        if has_prices:  # an empty house moving forward changes nothing in the addon
            changed += 1
            log(f"house {hid} ({name}): {len(snap)} items listed, snapshot from {local_time(lm_a)}")
    save_state(state_file, state)
    if changed:
        size = write_addon(addon_dir, region, houses, state)
        log(f"wrote {addon_dir / 'Data.lua'} ({size / 1024:.0f} KB). /reload in game to see new prices.")
    behind = [h for h in houses if stored.get(h, {}).get("updated", 0) < newest - 600]
    if behind:
        log(f"the {local_time(probe_lm)} snapshot isn't published for {len(behind)} house(s) yet "
            f"({', '.join(behind)}); checking again in 2 minutes")
        return "pending"
    return "updated" if changed else "unchanged"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wow", type=Path, default=DEFAULT_WOW, help="WoW Forever folder (the one with Interface\\)")
    ap.add_argument("--region", default="eu", choices=["eu", "us", "kr", "tw"])
    ap.add_argument("--creds", type=Path, default=DEFAULT_CREDS, help="file with BNET_ID= and BNET_SECRET=")
    ap.add_argument("--interval", type=int, default=600, help="seconds between checks (default 600)")
    ap.add_argument("--once", action="store_true", help="update once and exit")
    args = ap.parse_args()

    addon_dir = args.wow / "Interface" / "AddOns" / DATA_ADDON
    state_file = STATE_DIR / f"state-{args.region}.json"
    api = Blizzard(args.region, args.creds)
    log(f"ForeverPrices companion v{VERSION}: region {args.region}, writing to {addon_dir}")
    while True:
        result = "unchanged"
        try:
            result = run_once(api, args.region, addon_dir, state_file)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            log(f"update failed, will retry: {e}")
        if args.once:
            return
        time.sleep(min(120, args.interval) if result == "pending" else args.interval)


if __name__ == "__main__":
    main()
