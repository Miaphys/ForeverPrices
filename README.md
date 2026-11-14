# ForeverPrices

Auction house prices in your item tooltips for **World of Warcraft: Forever**, kept up to date
automatically. No scanning, no setup screens: install the addon, run the companion app, done.

### ⬇ [Download](https://miaphys.github.io/ForeverPrices/) · [All releases](https://github.com/Miaphys/ForeverPrices/releases)

1. Extract the addon zip into `World of Warcraft\_forever_\Interface\AddOns`.
2. Run `ForeverPrices.exe` (Windows may warn because it isn't code-signed: **More info → Run anyway**).
   A gold coin appears by your clock; it finds WoW and keeps prices up to date.
3. Log in. On Normal and PvP, prices are in your tooltips right away; elsewhere search the AH once.

![status](https://img.shields.io/badge/status-early%20testing-orange) ![license](https://img.shields.io/badge/license-MIT-blue)

## How it works

```
Blizzard auction API ──hourly──▶ GitHub Actions (server/) ──▶ GitHub Pages: eu.json, us.json
                                                                     │
                       companion app on your PC (companion/) ◀───────┘ every 10 min
                                      │
                                      ▼
            Interface/AddOns/ForeverPrices_Data  ──▶  ForeverPrices addon shows the prices
```

- **ForeverPrices** (addon): adds *AH market*, *AH lowest*, *AH 3-day avg* and the number listed
  to item tooltips. It figures out which auction house you use the first time you search the AH.
  Commands: `/fp`, `/fp houses`, `/fp house <id>`, `/fp detect`.
- **Companion app**: a small tray app (gold coin by the clock) that downloads the latest prices and
  writes them into the `ForeverPrices_Data` addon. WoW reads it at login or `/reload`.
- **Server**: a GitHub Actions job that pulls Blizzard's auction data every hour (both EU and US),
  works out the prices, and publishes them on GitHub Pages.

Prices per auction house: every Forever auction house (Alliance, Horde and Blackwater, per ruleset
group) is its own market, so you see prices from *your* house.

## Price lines

| Line | Meaning |
|---|---|
| AH market | typical price: average of the cheapest 25% of everything listed, ignoring silly outliers |
| AH lowest | the single cheapest listing |
| AH 3-day avg | moving average of the market price over roughly 3 days |

## For addon authors: price API

Other addons can read ForeverPrices' prices. Add `## OptionalDeps: ForeverPrices` to your `.toc` so it
loads first. All prices are in copper, for the auction house the player uses.

```lua
if ForeverPrices and ForeverPrices.IsReady and ForeverPrices.IsReady() then
  local copper = ForeverPrices.GetPrice(itemLinkOrID)   -- market price, or 3-day avg if none listed; nil if unknown
  local info = ForeverPrices.GetPriceInfo(itemLinkOrID)
  -- info = { market, lowest, average, quantity, listed, updated, house }  (nil if unknown)
  local houseID, houseName, updated = ForeverPrices.GetHouse()
end
```

- `item` can be an item ID, an item link, or an `"item:12345..."` string.
- `IsReady()` is false until the player's auction house is known (right after login, once detected).
- `ForeverPrices.API_VERSION` is `1`; it only goes up if something changes incompatibly.

## Repository layout

- `ForeverPrices/`: the addon
- `companion/`: companion core (`companion.py`), tray app (`tray.py`), `build.ps1` to make the .exe
- `server/build_prices.py` + `.github/workflows/prices.yml`: the hourly price job
