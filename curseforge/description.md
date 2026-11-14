# ForeverPrices

**Auction house prices in your item tooltips, always up to date. No scanning.**

Hover any item and see what it sells for on *your* auction house. Prices come from Blizzard's own auction
data and refresh every hour, for every Forever auction house, EU and US.

> ### ⚠ This addon needs its free companion app
> WoW addons can't use the internet, so a tiny companion app (a gold coin by your clock) downloads the
> latest prices for the addon.
> **Download it here: https://miaphys.github.io/ForeverPrices/**
> Without it, the addon has no prices to show.

## What you see in tooltips

- **AH market**: the typical price (average of the cheapest 25% listed, ignoring silly outliers)
- **AH lowest**: the cheapest single listing
- **AH 3-day avg**: the market price averaged over about 3 days, great for spotting bargains
- For stacks in your bags: the price of the whole stack (hold **Shift** for one)
- How many are listed, and how old the data is

## Setup (2 minutes)

1. Install this addon.
2. Download and run **ForeverPrices.exe** from https://miaphys.github.io/ForeverPrices/.
   It finds your WoW folder by itself. Right-click the coin for options (EU/US, start with Windows).
3. Log in. On **Normal** and **PvP** prices show up right away; on other rulesets, search the auction house once
   so the addon knows which house you use.

## FAQ

**Windows says "Windows protected your PC".**
The companion isn't code-signed yet (certificates cost money). Click *More info* → *Run anyway*. The code is open
source on GitHub, and each release lists the file's SHA-256 fingerprint.

**My prices didn't change while playing.**
WoW only loads addon data at login or `/reload`. The companion shows a notification when new prices are ready.

**Does it need my Battle.net login or touch the game?**
No. It only downloads public price data and writes one addon file (`ForeverPrices_Data`).

**Which auction house's prices do I see?**
Your own. Every Forever auction house (per ruleset and faction) is its own market.

## Commands

`/fp` status · `/fp houses` list houses · `/fp house <id>` pick one · `/fp detect` re-detect

## For addon authors

Read prices from your addon with `ForeverPrices.GetPrice(item)`, `ForeverPrices.GetPriceInfo(item)`,
`ForeverPrices.IsReady()`. Details: https://github.com/Miaphys/ForeverPrices#for-addon-authors-price-api

## Open source

MIT license. Code, issues and releases: https://github.com/Miaphys/ForeverPrices
Not affiliated with Blizzard Entertainment.
