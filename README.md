# ForeverPrices

Auction house prices in your item tooltips for **World of Warcraft: Forever**, kept up to date
automatically. No scanning, no setup screens: install the addon, run the companion app, done.

![tooltip](https://img.shields.io/badge/status-early%20testing-orange)

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

## Repository layout

- `ForeverPrices/`: the addon
- `companion/`: companion core (`companion.py`), tray app (`tray.py`), `build.ps1` to make the .exe
- `server/build_prices.py` + `.github/workflows/prices.yml`: the hourly price job
