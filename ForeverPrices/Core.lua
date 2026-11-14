-- ForeverPrices: auction prices in item tooltips.
-- Prices come from the ForeverPrices_Data addon, which the companion app rewrites
-- from Blizzard's auction data (new data shows up after /reload or relog).
local PREFIX = "|cff33ccffForeverPrices|r: "
local COMPANION_HINT = "Get the free companion app at github.com/Miaphys/ForeverPrices (it keeps prices up to date), then /reload."

local prices        -- itemID -> { min, market, quantity, avg } for the selected house
local houseID       -- auction house id the prices are for
local houseName     -- display name of the selected house
local houseUpdated  -- unix time the selected house's data was taken

local function Print(msg) print(PREFIX .. msg) end

local function Data()
  return ForeverPricesData and ForeverPricesData.houses
end

-- "item:min:market:qty:avg;..." -> table
local function ParseHouse(str)
  local t = {}
  for id, mn, mkt, qty, avg in str:gmatch("(%d+):(%d+):(%d+):(%d+):(%d+)") do
    t[tonumber(id)] = { tonumber(mn), tonumber(mkt), tonumber(qty), tonumber(avg) }
  end
  return t
end

local function SelectHouse(id)
  local houses = Data()
  local h = houses and houses[id]
  if not h then
    prices, houseID, houseName, houseUpdated = nil, nil, nil, nil
    return false
  end
  prices = ParseHouse(h.data)
  houseID = id
  houseName = h.name .. " #" .. id
  houseUpdated = h.updated
  return true
end

local function Money(copper)
  if GetMoneyString then return GetMoneyString(copper, true) end
  return GetCoinTextureString(copper)
end

local function Ago(t)
  local s = time() - t
  if s < 3600 then return math.max(1, math.floor(s / 60)) .. "m ago" end
  if s < 172800 then return math.floor(s / 3600) .. "h ago" end
  return math.floor(s / 86400) .. "d ago"
end

-------------------------------------------------------------------- public API
-- For other addons. All prices are in copper and for the auction house this
-- character uses. Everything returns nil until ForeverPrices knows that house
-- (after login, once it has been detected or set). Check ForeverPrices.IsReady().
--
--   ForeverPrices.GetPrice(item)      -> copper or nil
--       Market price while the item is listed, else its 3-day average.
--   ForeverPrices.GetPriceInfo(item)  -> table or nil
--       { market, lowest, average, quantity, listed, updated, house }
--       market/lowest are 0 and listed is false when none are listed right now.
--   ForeverPrices.IsReady()           -> true once prices are available
--   ForeverPrices.GetHouse()          -> houseID, houseName, updated (unix time)
--
-- `item` can be an item ID, an item link, or an "item:12345..." string.
ForeverPrices = ForeverPrices or {}
ForeverPrices.API_VERSION = 1

local function ItemID(item)
  if type(item) == "number" then return item end
  if type(item) == "string" then return tonumber(item:match("item:(%d+)") or item:match("^%d+$")) end
end

function ForeverPrices.IsReady()
  return prices ~= nil
end

function ForeverPrices.GetHouse()
  return houseID, houseName, houseUpdated
end

function ForeverPrices.GetPriceInfo(item)
  local id = prices and ItemID(item)
  local p = id and prices[id]
  if not p then return nil end
  return {
    market = p[2], lowest = p[1], average = p[4], quantity = p[3],
    listed = p[3] > 0, updated = houseUpdated, house = houseID,
  }
end

function ForeverPrices.GetPrice(item)
  local id = prices and ItemID(item)
  local p = id and prices[id]
  if not p then return nil end
  if p[3] > 0 then return p[2] end
  if p[4] > 0 then return p[4] end
  return nil
end

-------------------------------------------------------------------- tooltip
local function StackCount(tooltip)
  local info = tooltip.info or tooltip.processingInfo
  if info and info.getterName == "GetBagItem" and info.getterArgs and C_Container then
    local item = C_Container.GetContainerItemInfo(info.getterArgs[1], info.getterArgs[2])
    if item and item.stackCount and item.stackCount > 1 then return item.stackCount end
  end
  return 1
end

local function AddLines(tooltip, itemID)
  if not prices or not itemID then return end
  local p = prices[itemID]
  if not p then return end
  local mn, mkt, qty, avg = p[1], p[2], p[3], p[4]
  local n = StackCount(tooltip)
  local each = n > 1 and (" (x" .. n .. ")") or ""
  local mult = IsShiftKeyDown() and 1 or n
  if mult == 1 then each = "" end

  tooltip:AddLine(" ")
  if qty > 0 then
    tooltip:AddDoubleLine("AH market" .. each, Money(mkt * mult), 0.4, 0.8, 1, 1, 1, 1)
    tooltip:AddDoubleLine("AH lowest" .. each, Money(mn * mult), 0.4, 0.8, 1, 1, 1, 1)
  else
    tooltip:AddDoubleLine("AH market", "none listed", 0.4, 0.8, 1, 0.6, 0.6, 0.6)
  end
  if avg > 0 then
    tooltip:AddDoubleLine("AH 3-day avg" .. each, Money(avg * mult), 0.4, 0.8, 1, 1, 1, 1)
  end
  local meta = (qty > 0 and (qty .. " listed, ") or "") .. "updated " .. Ago(houseUpdated)
  tooltip:AddDoubleLine(" ", meta, 1, 1, 1, 0.55, 0.55, 0.55)
  tooltip:Show()
end

if TooltipDataProcessor and Enum and Enum.TooltipDataType then
  TooltipDataProcessor.AddTooltipPostCall(Enum.TooltipDataType.Item, function(tooltip, data)
    if tooltip:IsForbidden() or not data then return end
    AddLines(tooltip, data.id)
  end)
else
  GameTooltip:HookScript("OnTooltipSetItem", function(tooltip)
    local _, link = tooltip:GetItem()
    local id = link and tonumber(link:match("item:(%d+)"))
    AddLines(tooltip, id)
  end)
end

-------------------------------------------------------------------- house detection
-- Every auction house is its own market. While browsing the AH we compare the
-- lowest prices on screen with each same-faction house in the data and pick
-- the one that matches.
local seen, seenCount = {}, 0
local candidates -- houseID -> parsed prices (only while detecting)

local function Candidates()
  if candidates then return candidates end
  candidates = {}
  local faction = UnitFactionGroup("player")
  for id, h in pairs(Data() or {}) do
    if faction and h.name:find(faction, 1, true) then
      candidates[id] = ParseHouse(h.data)
    end
  end
  return candidates
end

local function TryDetect()
  if seenCount < 8 then return end
  local scores = {}
  for id, t in pairs(Candidates()) do
    local compared, close = 0, 0
    for itemID, seenMin in pairs(seen) do
      local p = t[itemID]
      if p and p[3] > 0 then
        compared = compared + 1
        local diff = math.abs(p[1] - seenMin) / math.max(seenMin, 1)
        if diff <= 0.10 then close = close + 1 end
      end
    end
    scores[#scores + 1] = { id = id, close = close, compared = compared }
  end
  table.sort(scores, function(a, b) return a.close > b.close end)
  local best, second = scores[1], scores[2]
  if not best or best.close < 5 or best.close < best.compared * 0.5 then return end
  if second and best.close < second.close * 1.5 + 2 then return end

  ForeverPricesCharDB.house = best.id
  candidates, seen, seenCount = nil, {}, 0
  SelectHouse(best.id)
  Print(("detected your auction house: %s (%d of %d prices matched). Prices are now in tooltips."):format(houseName, best.close, best.compared))
end

local function OnBrowseResults()
  if ForeverPricesCharDB.house or not C_AuctionHouse then return end
  for _, r in ipairs(C_AuctionHouse.GetBrowseResults() or {}) do
    local id = r.itemKey and r.itemKey.itemID
    if id and r.minPrice and r.minPrice > 0 then
      if not seen[id] then seenCount = seenCount + 1 end
      if not seen[id] or r.minPrice < seen[id] then seen[id] = r.minPrice end
    end
  end
  TryDetect()
end

-------------------------------------------------------------------- slash commands
local function ListHouses()
  local ids = {}
  for id in pairs(Data() or {}) do ids[#ids + 1] = id end
  table.sort(ids)
  for _, id in ipairs(ids) do
    local h = Data()[id]
    local _, n = h.data:gsub(";", "")
    Print(("  #%d %s - %d items, updated %s%s"):format(id, h.name, n + 1, Ago(h.updated),
      id == ForeverPricesCharDB.house and "  |cff00ff00(yours)|r" or ""))
  end
end

SLASH_FOREVERPRICES1 = "/fp"
SLASH_FOREVERPRICES2 = "/foreverprices"
SlashCmdList.FOREVERPRICES = function(msg)
  local cmd, arg = (msg or ""):lower():match("^%s*(%S*)%s*(.-)%s*$")
  if cmd == "houses" then
    ListHouses()
  elseif cmd == "house" and tonumber(arg) then
    if SelectHouse(tonumber(arg)) then
      ForeverPricesCharDB.house = tonumber(arg)
      Print("using " .. houseName .. ".")
    else
      Print("no data for house " .. arg .. ". See /fp houses.")
    end
  elseif cmd == "detect" then
    ForeverPricesCharDB.house = nil
    prices, candidates, seen, seenCount = nil, nil, {}, 0
    Print("open the auction house and browse or search a few times; your house will be detected.")
  else
    if not Data() then
      Print("no price data yet. " .. COMPANION_HINT)
    elseif houseName then
      Print(("using %s, data updated %s."):format(houseName, Ago(houseUpdated)))
    else
      Print("house not detected yet: open the AH and search for anything.")
    end
    Print("commands: /fp houses, /fp house <id>, /fp detect")
  end
end

-------------------------------------------------------------------- events
local f = CreateFrame("Frame")
f:RegisterEvent("PLAYER_LOGIN")
f:RegisterEvent("AUCTION_HOUSE_BROWSE_RESULTS_UPDATED")
f:RegisterEvent("AUCTION_HOUSE_BROWSE_RESULTS_ADDED")
f:SetScript("OnEvent", function(_, event)
  if event == "PLAYER_LOGIN" then
    ForeverPricesDB = ForeverPricesDB or {}
    ForeverPricesCharDB = ForeverPricesCharDB or {}
    if not Data() then
      Print("no price data yet. " .. COMPANION_HINT)
    elseif not (ForeverPricesCharDB.house and SelectHouse(ForeverPricesCharDB.house)) then
      ForeverPricesCharDB.house = nil
      Print("open the auction house and search for anything once, so I can tell which house you use.")
    end
  else
    OnBrowseResults()
  end
end)
