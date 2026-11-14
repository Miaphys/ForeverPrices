"""ForeverPrices server job: build the public price files.

Runs hourly on GitHub Actions. For each region it loads the previous published
file (that is our state, so the 3-day averages carry over between runs), fetches
every auction house Blizzard has republished, and writes site/<region>.json for
GitHub Pages. Credentials come from the BNET_ID / BNET_SECRET environment variables.

    python server/build_prices.py --out site --previous https://<user>.github.io/<repo>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "companion"))
import companion as core  # noqa: E402  (shared pricing code)

FORMAT = 1
REGIONS = ["eu", "us"]
PENDING_RETRY = 120      # seconds between retries while big houses are still publishing
PENDING_MAX_WAIT = 900   # give up waiting after this; the next run picks them up


def load_previous(base: str | None, region: str, out: Path) -> dict:
    """Previous published state: from the live site, else a local file, else empty."""
    if base:
        url = f"{base.rstrip('/')}/{region}.json?nocache={int(time.time())}"
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                state = json.load(r)
            core.log(f"[{region}] previous state loaded from {base} ({len(state.get('houses', {}))} houses)")
            return state
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise  # don't silently reset everyone's 3-day averages on a hiccup
            core.log(f"[{region}] no previous file online (404), starting fresh")
    local = out / f"{region}.json"
    if local.exists():
        return json.loads(local.read_text(encoding="utf-8"))
    return {"houses": {}}


def refresh(api: core.Blizzard, state: dict, region: str) -> tuple[int, list[str]]:
    """Fetch houses whose Blizzard snapshot is newer than ours. Returns (changed, still_behind)."""
    index, _ = api.get("/data/wow/ruleset/2/auctions/")
    names = {str(h["id"]): h["name"]["en_US"] for h in index["auctions"]}
    houses = state.setdefault("houses", {})
    changed, newest = 0, 0
    for hid, name in names.items():
        hs = houses.setdefault(hid, {})
        hs["name"] = name
        auctions, lm = api.get(f"/data/wow/ruleset/2/auctions/{hid}")
        updated = int(parsedate_to_datetime(lm).timestamp()) if lm else int(time.time())
        newest = max(newest, updated)
        if lm and lm == hs.get("lm"):
            continue
        commodities, _ = api.get(f"/data/wow/ruleset/2/auctions/{hid}/commodities")
        snap = core.summarize(auctions.get("auctions", []), (commodities or {}).get("auctions", []))
        core.update_house(hs, snap, updated)
        hs["lm"] = lm
        changed += 1
        core.log(f"[{region}] house {hid} ({name}): {len(snap)} items, snapshot {lm}")
    behind = [h for h in names if houses[h].get("updated", 0) < newest - 600]
    return changed, behind


def write_site(out: Path, region: str, state: dict) -> int:
    state["format"] = FORMAT
    state["region"] = region
    state["generated"] = int(time.time())
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{region}.json"
    path.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    return path.stat().st_size


def write_index(out: Path, states: dict) -> None:
    rows = []
    for region, st in states.items():
        newest = max((h.get("updated", 0) for h in st.get("houses", {}).values() if h.get("items")), default=0)
        items = sum(len(h.get("items", {})) for h in st.get("houses", {}).values())
        rows.append(f"<tr><td>{region.upper()}</td><td>{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(newest))}</td>"
                    f"<td>{items}</td><td><a href='{region}.json'>{region}.json</a></td></tr>")
    (out / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>ForeverPrices data</title>"
        "<style>body{font:15px system-ui;margin:2em;background:#111;color:#ddd}td,th{padding:4px 12px;text-align:left}"
        "a{color:#6cf}</style><h1>ForeverPrices price data</h1>"
        "<p>Auction prices for WoW Forever, refreshed hourly from Blizzard's API. Used by the "
        "<a href='https://github.com/Miaphys/ForeverPrices'>ForeverPrices</a> companion app.</p>"
        "<table><tr><th>Region</th><th>Newest snapshot</th><th>Item rows</th><th>File</th></tr>"
        + "".join(rows) + "</table>", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("site"))
    ap.add_argument("--previous", help="base URL of the live site, to carry state over")
    ap.add_argument("--regions", default=",".join(REGIONS))
    args = ap.parse_args()
    core.log_sink = lambda line: print(line, flush=True)

    states, total_changed = {}, 0
    for region in args.regions.split(","):
        api = core.Blizzard(region, core.DEFAULT_CREDS)
        state = load_previous(args.previous, region, args.out)
        deadline = time.time() + PENDING_MAX_WAIT
        while True:
            changed, behind = refresh(api, state, region)
            total_changed += changed
            if not behind or time.time() > deadline:
                break
            core.log(f"[{region}] {len(behind)} house(s) not republished yet ({', '.join(behind)}), retrying in {PENDING_RETRY}s")
            time.sleep(PENDING_RETRY)
        size = write_site(args.out, region, state)
        core.log(f"[{region}] wrote {region}.json ({size / 1024:.0f} KB)")
        states[region] = state
    write_index(args.out, states)

    # Tell the workflow whether anything changed, so it can skip deploying.
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if total_changed else 'false'}\n")
    core.log(f"done: {total_changed} house(s) updated")


if __name__ == "__main__":
    main()
