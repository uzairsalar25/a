//+------------------------------------------------------------------+
//|                                               SmartGridGold.mq5  |
//|        Risk-managed grid EA for XAUUSD with basket trailing stop |
//+------------------------------------------------------------------+
#property version     "2.00"
#property description "Risk-managed grid EA for gold (XAUUSD)."
#property description "Basket trailing stop, ATR grid, basket stop loss,"
#property description "trend / ADX / news / spread / session filters,"
#property description "daily loss limit and max drawdown halt."

#include <Trade/Trade.mqh>

CTrade trade;

//================= LOT SIZING =================//
input group "Lot sizing"
input double StartLot          = 0.01;   // Start lot (used when AutoLot = false)
input bool   AutoLot           = false;  // Size start lot from balance
input double BalancePer001     = 2000.0; // Balance per 0.01 lot (AutoLot)
input double LotStep           = 0.0;    // Lot added every LevelsPerStep levels (0 = flat lots)
input int    LevelsPerStep     = 3;      // Levels before the lot increases
input double MaxLot            = 0.05;   // Hard cap for any single position

//================= GRID =================//
input group "Grid"
input bool   TradeBuys         = true;   // Allow buy grid
input bool   TradeSells        = true;   // Allow sell grid
input bool   UseATR            = true;   // Size distances from ATR (false = fixed price distances)
input ENUM_TIMEFRAMES ATRTimeframe = PERIOD_H1; // ATR / ADX timeframe
input int    ATRPeriod         = 14;     // ATR period
input double GridATRMult       = 1.0;    // Grid step = ATR x this
input double GridFixed         = 5.0;    // Grid step in price when UseATR = false ($5 on gold)
input double MinGridStep       = 2.0;    // Grid step is never tighter than this (price)
input int    MaxLevels         = 6;      // Max positions per side
input int    EntryCooldownSec  = 10;     // Min seconds between entries on the same side

//================= EXITS =================//
input group "Exits: basket trailing stop and basket stop loss"
input double TrailStartATR     = 0.6;    // Start trailing when price is this x ATR beyond basket average
input double TrailDistATR      = 0.35;   // Trailing distance behind price (x ATR)
input double TrailStepATR      = 0.05;   // Min stop improvement before modifying (x ATR)
input double TrailStartFixed   = 3.0;    // Trail start in price when UseATR = false
input double TrailDistFixed    = 1.8;    // Trail distance in price when UseATR = false
input double TrailStepFixed    = 0.3;    // Trail step in price when UseATR = false
input double BasketRiskPct     = 2.0;    // Close a side when its loss reaches this % of equity (0 = off)
input int    StopCooldownMin   = 60;     // Pause a side for this many minutes after its basket stop loss

//================= ACCOUNT PROTECTION =================//
input group "Account protection"
input double DailyLossPct      = 4.0;    // Close all and pause until tomorrow at this daily loss % (0 = off)
input double DailyProfitPct    = 0.0;    // Close all and pause until tomorrow at this daily gain % (0 = off)
input double MaxDrawdownPct    = 10.0;   // Close all and HALT at this drawdown % from peak equity (0 = off)
input bool   ResetHalt         = false;  // Set true once to clear a max-drawdown halt

//================= FILTERS =================//
input group "Filters"
input bool   UseTrendFilter    = true;   // Buys only above EMA, sells only below
input ENUM_TIMEFRAMES TrendTimeframe = PERIOD_H4; // Trend EMA timeframe
input int    TrendEMAPeriod    = 200;    // Trend EMA period
input bool   UseADXFilter      = true;   // Stop adding grid levels in strong moves
input int    ADXPeriod         = 14;     // ADX period
input double ADXMax            = 30.0;   // No extra grid levels while ADX is above this
input bool   UseNewsFilter     = true;   // Block new trades around high-impact news (live only)
input string NewsCurrency      = "USD";  // News currency
input int    NewsMinBefore     = 30;     // Minutes before news
input int    NewsMinAfter      = 30;     // Minutes after news
input double MaxSpread         = 0.60;   // Max spread in price for new trades ($0.60 on gold)
input int    StartHour         = 1;      // Session start hour (server time)
input int    EndHour           = 22;     // Session end hour (server time)
input bool   NoNewTradesFriday = true;   // No new trades late on Friday
input int    FridayCutoffHour  = 18;     // Friday cutoff hour (server time)

//================= GENERAL =================//
input group "General"
input ulong  Magic             = 20260922; // Magic number
input int    SlippagePoints    = 50;       // Max slippage in points

//---------------- GLOBALS ----------------//
struct SideState
{
   int    count;      // open positions on this side
   double lots;       // total volume
   double avgPrice;   // volume-weighted average open price
   double extreme;    // lowest buy / highest sell open price (grid edge)
   double profit;     // floating profit + swap
};

int      atrHandle      = INVALID_HANDLE;
int      emaHandle      = INVALID_HANDLE;
int      adxHandle      = INVALID_HANDLE;
double   peakEquity     = 0;
double   dayStartEquity = 0;
int      currentDay     = -1;
bool     dailyPaused    = false;
bool     ddHalted       = false;
datetime lastBuyEntry   = 0;
datetime lastSellEntry  = 0;
datetime buyPauseUntil  = 0;
datetime sellPauseUntil = 0;

//+------------------------------------------------------------------+
//| Helpers                                                          |
//+------------------------------------------------------------------+
string HaltKey() { return "SGG_HALT_" + _Symbol + "_" + (string)Magic; }

double Indicator(const int handle, const int buffer)
{
   double v[1];
   if(handle == INVALID_HANDLE || CopyBuffer(handle, buffer, 1, 1, v) != 1)
      return 0;
   return v[0];
}

// ATR-based distance, or the fixed fallback when ATR is off / not ready
double Dist(const double atrMult, const double fixedDist, const double atr)
{
   return (UseATR && atr > 0) ? atr * atrMult : fixedDist;
}

double GridStep(const double atr)
{
   return MathMax(Dist(GridATRMult, GridFixed, atr), MinGridStep);
}

double NormPrice(const double price)
{
   double tick = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick <= 0) return NormalizeDouble(price, _Digits);
   return NormalizeDouble(MathRound(price / tick) * tick, _Digits);
}

double NormLot(double lot)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double mn   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double mx   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   lot = MathMin(lot, MaxLot);
   if(step > 0) lot = MathFloor(lot / step + 1e-9) * step;
   lot = MathMax(mn, MathMin(mx, lot));
   int digits = (step > 0) ? (int)MathMax(0, MathCeil(-MathLog10(step))) : 2;
   return NormalizeDouble(lot, digits);
}

double BaseLot()
{
   if(!AutoLot) return StartLot;
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   return 0.01 * MathFloor(balance / MathMax(BalancePer001, 1.0));
}

double LotFor(const int level)
{
   int steps = (LevelsPerStep > 0) ? level / LevelsPerStep : 0;
   return NormLot(BaseLot() + steps * LotStep);
}

bool IsOurs(const ENUM_POSITION_TYPE type)
{
   return PositionGetString(POSITION_SYMBOL) == _Symbol &&
          (ulong)PositionGetInteger(POSITION_MAGIC) == Magic &&
          (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == type;
}

//+------------------------------------------------------------------+
//| Rebuild a side's state from real open positions (restart-safe)   |
//+------------------------------------------------------------------+
void ScanSide(const ENUM_POSITION_TYPE type, SideState &s)
{
   s.count = 0; s.lots = 0; s.avgPrice = 0; s.extreme = 0; s.profit = 0;
   double weighted = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);   // also selects the position
      if(ticket == 0 || !IsOurs(type)) continue;

      double vol  = PositionGetDouble(POSITION_VOLUME);
      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      s.count++;
      s.lots   += vol;
      weighted += vol * open;
      s.profit += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
      if(s.extreme == 0 || (type == POSITION_TYPE_BUY ? open < s.extreme : open > s.extreme))
         s.extreme = open;
   }
   if(s.lots > 0) s.avgPrice = weighted / s.lots;
}

//+------------------------------------------------------------------+
//| Close every position on one side                                 |
//+------------------------------------------------------------------+
void CloseSide(const ENUM_POSITION_TYPE type, const string reason)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !IsOurs(type)) continue;
      if(!trade.PositionClose(ticket))
         PrintFormat("Close %I64u failed (%s): %u %s", ticket, reason,
                     trade.ResultRetcode(), trade.ResultRetcodeDescription());
   }
   PrintFormat("Closed %s side: %s", type == POSITION_TYPE_BUY ? "BUY" : "SELL", reason);
}

void CloseAllSides(const string reason)
{
   CloseSide(POSITION_TYPE_BUY, reason);
   CloseSide(POSITION_TYPE_SELL, reason);
}

//+------------------------------------------------------------------+
//| Basket trailing stop: once price is TrailStart beyond the basket |
//| average, move a broker-side SL on every position of the side.    |
//| The SL lives on the server, so it still works if MT5 goes down.  |
//+------------------------------------------------------------------+
void TrailSide(const ENUM_POSITION_TYPE type, const SideState &s, const double atr)
{
   if(s.count == 0) return;

   double start = Dist(TrailStartATR, TrailStartFixed, atr);
   double dist  = Dist(TrailDistATR,  TrailDistFixed,  atr);
   double step  = Dist(TrailStepATR,  TrailStepFixed,  atr);
   double bid   = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask   = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   // Broker minimum distance between price and SL
   double minGap = (SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) +
                    SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL) + 1) * _Point;
   dist = MathMax(dist, minGap);

   bool   isBuy = (type == POSITION_TYPE_BUY);
   double newSL;
   if(isBuy)
   {
      if(bid < s.avgPrice + start) return;   // not far enough in profit yet
      newSL = NormPrice(bid - dist);
   }
   else
   {
      if(ask > s.avgPrice - start) return;
      newSL = NormPrice(ask + dist);
   }

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !IsOurs(type)) continue;

      double curSL = PositionGetDouble(POSITION_SL);
      double curTP = PositionGetDouble(POSITION_TP);
      bool better  = isBuy ? (curSL == 0 || newSL >= curSL + step)
                           : (curSL == 0 || newSL <= curSL - step);
      if(!better) continue;

      if(!trade.PositionModify(ticket, newSL, curTP))
         PrintFormat("Trail modify %I64u failed: %u %s", ticket,
                     trade.ResultRetcode(), trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Filters                                                          |
//+------------------------------------------------------------------+
bool InSession()
{
   MqlDateTime t;
   TimeToStruct(TimeCurrent(), t);
   if(NoNewTradesFriday && t.day_of_week == 5 && t.hour >= FridayCutoffHour) return false;
   if(StartHour <= EndHour) return t.hour >= StartHour && t.hour < EndHour;
   return t.hour >= StartHour || t.hour < EndHour;   // window that crosses midnight
}

bool NewsBlocked()
{
   // The economic calendar is not available in the Strategy Tester
   if(!UseNewsFilter || MQLInfoInteger(MQL_TESTER)) return false;

   static datetime lastCheck = 0;
   static bool     blocked   = false;
   datetime now = TimeTradeServer();
   if(now - lastCheck < 60) return blocked;   // query the calendar at most once a minute
   lastCheck = now;
   blocked   = false;

   MqlCalendarValue vals[];
   if(CalendarValueHistory(vals, now - NewsMinAfter * 60, now + NewsMinBefore * 60, NULL, NewsCurrency))
   {
      for(int i = 0; i < ArraySize(vals); i++)
      {
         MqlCalendarEvent ev;
         if(CalendarEventById(vals[i].event_id, ev) && ev.importance == CALENDAR_IMPORTANCE_HIGH)
         {
            blocked = true;
            break;
         }
      }
   }
   return blocked;
}

bool SpreadOk()
{
   return SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID) <= MaxSpread;
}

bool EnoughMargin(const ENUM_ORDER_TYPE type, const double lot, const double price)
{
   double margin = 0;
   if(!OrderCalcMargin(type, _Symbol, lot, price, margin)) return false;
   return AccountInfoDouble(ACCOUNT_MARGIN_FREE) > margin * 2.0;   // keep a 2x buffer
}

//+------------------------------------------------------------------+
//| Open one grid level                                              |
//+------------------------------------------------------------------+
void OpenLevel(const ENUM_POSITION_TYPE type, const int level)
{
   bool   isBuy = (type == POSITION_TYPE_BUY);
   double lot   = LotFor(level);
   double price = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);

   // Cooldown starts on every attempt so a rejected order is not spammed each tick
   if(isBuy) lastBuyEntry = TimeCurrent(); else lastSellEntry = TimeCurrent();

   if(!EnoughMargin(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, lot, price))
   {
      Print("Not enough free margin for ", lot, " lots - level skipped");
      return;
   }

   string comment = StringFormat("SGG %s L%d", isBuy ? "BUY" : "SELL", level + 1);
   bool ok = isBuy ? trade.Buy(lot, _Symbol, 0, 0, 0, comment)
                   : trade.Sell(lot, _Symbol, 0, 0, 0, comment);
   uint rc = trade.ResultRetcode();
   if(!ok || (rc != TRADE_RETCODE_DONE && rc != TRADE_RETCODE_PLACED))
      PrintFormat("%s failed: %u %s", comment, rc, trade.ResultRetcodeDescription());
}

//+------------------------------------------------------------------+
//| Daily limits and max drawdown                                    |
//+------------------------------------------------------------------+
void UpdateAccountGuards()
{
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);

   MqlDateTime t;
   TimeToStruct(TimeCurrent(), t);
   int today = t.year * 1000 + t.day_of_year;
   if(today != currentDay)
   {
      currentDay     = today;
      dayStartEquity = eq;
      dailyPaused    = false;
   }

   if(eq > peakEquity) peakEquity = eq;

   if(!ddHalted && MaxDrawdownPct > 0 && peakEquity > 0 &&
      (peakEquity - eq) / peakEquity * 100.0 >= MaxDrawdownPct)
   {
      ddHalted = true;
      GlobalVariableSet(HaltKey(), (double)TimeCurrent());
      CloseAllSides("max drawdown");
      Print("MAX DRAWDOWN HIT - EA halted. Set ResetHalt = true to resume.");
   }

   if(!dailyPaused && dayStartEquity > 0)
   {
      double dayPct = (eq - dayStartEquity) / dayStartEquity * 100.0;
      if(DailyLossPct > 0 && dayPct <= -DailyLossPct)
      {
         dailyPaused = true;
         CloseAllSides("daily loss limit");
      }
      else if(DailyProfitPct > 0 && dayPct >= DailyProfitPct)
      {
         dailyPaused = true;
         CloseAllSides("daily profit target");
      }
   }
}

//+------------------------------------------------------------------+
//| Status panel                                                     |
//+------------------------------------------------------------------+
void ShowPanel(const SideState &b, const SideState &s, const double atr, const string blockReason)
{
   static datetime last = 0;
   if(TimeCurrent() == last) return;
   last = TimeCurrent();

   string status = ddHalted    ? "HALTED (max drawdown) - set ResetHalt = true to resume"
                 : dailyPaused ? "PAUSED until tomorrow (daily limit hit)"
                 : blockReason != "" ? "WAITING: " + blockReason
                 : "ACTIVE";

   double eq     = AccountInfoDouble(ACCOUNT_EQUITY);
   double dayPct = dayStartEquity > 0 ? (eq - dayStartEquity) / dayStartEquity * 100.0 : 0;
   double ddPct  = peakEquity > 0 ? (peakEquity - eq) / peakEquity * 100.0 : 0;

   Comment(StringFormat(
      "SmartGrid Gold v2\n"
      "Status: %s\n"
      "ATR: %.2f   Grid step: %.2f   Spread: %.2f\n"
      "BUY : %d pos  %.2f lots  avg %.2f  P/L %.2f\n"
      "SELL: %d pos  %.2f lots  avg %.2f  P/L %.2f\n"
      "Today: %+.2f%%  (limit -%.1f%%)\n"
      "Drawdown from peak: %.2f%%  (halt at %.1f%%)",
      status, atr, GridStep(atr),
      SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID),
      b.count, b.lots, b.avgPrice, b.profit,
      s.count, s.lots, s.avgPrice, s.profit,
      dayPct, DailyLossPct, ddPct, MaxDrawdownPct));
}

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   if(AccountInfoInteger(ACCOUNT_MARGIN_MODE) != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
   {
      Print("This EA needs a HEDGING account (a netting account merges buys and sells).");
      return INIT_FAILED;
   }
   if(StartLot <= 0 || MaxLevels < 1 || MaxLot <= 0)
   {
      Print("Invalid inputs: StartLot, MaxLot and MaxLevels must be positive.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(TrailStartATR <= TrailDistATR || TrailStartFixed <= TrailDistFixed)
      Print("Warning: trail start should be larger than trail distance, otherwise the first stop may be below break-even.");

   trade.SetExpertMagicNumber(Magic);
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);

   atrHandle = iATR(_Symbol, ATRTimeframe, ATRPeriod);
   emaHandle = iMA(_Symbol, TrendTimeframe, TrendEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
   adxHandle = iADX(_Symbol, ATRTimeframe, ADXPeriod);
   if(atrHandle == INVALID_HANDLE || emaHandle == INVALID_HANDLE || adxHandle == INVALID_HANDLE)
   {
      Print("Failed to create indicator handles.");
      return INIT_FAILED;
   }

   if(ResetHalt) GlobalVariableDel(HaltKey());
   ddHalted = GlobalVariableCheck(HaltKey());   // a halt survives restarts until reset

   peakEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   currentDay = -1;
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(atrHandle != INVALID_HANDLE) IndicatorRelease(atrHandle);
   if(emaHandle != INVALID_HANDLE) IndicatorRelease(emaHandle);
   if(adxHandle != INVALID_HANDLE) IndicatorRelease(adxHandle);
   Comment("");
}

//+------------------------------------------------------------------+
//| Main loop                                                        |
//+------------------------------------------------------------------+
void OnTick()
{
   // 1. Account-level protection
   UpdateAccountGuards();

   // 2. Real state from open positions
   SideState buys, sells;
   ScanSide(POSITION_TYPE_BUY,  buys);
   ScanSide(POSITION_TYPE_SELL, sells);

   double atr = Indicator(atrHandle, 0);

   // 3. Halted / paused: stay flat (retry any close that failed)
   if(ddHalted || dailyPaused)
   {
      if(buys.count + sells.count > 0) CloseAllSides(ddHalted ? "halted" : "paused");
      ShowPanel(buys, sells, atr, "");
      return;
   }

   // 4. Basket stop loss (per side, % of equity)
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(BasketRiskPct > 0)
   {
      double maxLoss = equity * BasketRiskPct / 100.0;
      if(buys.count > 0 && buys.profit <= -maxLoss)
      {
         CloseSide(POSITION_TYPE_BUY, "basket stop loss");
         buys.count    = 0;
         buyPauseUntil = TimeCurrent() + StopCooldownMin * 60;
      }
      if(sells.count > 0 && sells.profit <= -maxLoss)
      {
         CloseSide(POSITION_TYPE_SELL, "basket stop loss");
         sells.count    = 0;
         sellPauseUntil = TimeCurrent() + StopCooldownMin * 60;
      }
   }

   // 5. Basket trailing stop (this is the take-profit)
   TrailSide(POSITION_TYPE_BUY,  buys,  atr);
   TrailSide(POSITION_TYPE_SELL, sells, atr);

   // 6. Entry filters (exits above always run, filters only block NEW trades)
   string block = "";
   if(UseATR && atr <= 0)  block = "ATR not ready";
   else if(!InSession())   block = "outside session";
   else if(!SpreadOk())    block = "spread too wide";
   else if(NewsBlocked())  block = "high-impact news";

   if(block == "")
   {
      double bid     = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask     = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double step    = GridStep(atr);
      double ema     = Indicator(emaHandle, 0);
      bool   adxHigh = UseADXFilter && Indicator(adxHandle, 0) > ADXMax;
      bool   trendUp = !UseTrendFilter || (ema > 0 && bid > ema);
      bool   trendDn = !UseTrendFilter || (ema > 0 && bid < ema);
      datetime now   = TimeCurrent();

      // BUY grid
      if(TradeBuys && trendUp && now >= buyPauseUntil && now - lastBuyEntry >= EntryCooldownSec)
      {
         if(buys.count == 0)
            OpenLevel(POSITION_TYPE_BUY, 0);
         else if(buys.count < MaxLevels && !adxHigh && ask <= buys.extreme - step)
            OpenLevel(POSITION_TYPE_BUY, buys.count);
      }

      // SELL grid
      if(TradeSells && trendDn && now >= sellPauseUntil && now - lastSellEntry >= EntryCooldownSec)
      {
         if(sells.count == 0)
            OpenLevel(POSITION_TYPE_SELL, 0);
         else if(sells.count < MaxLevels && !adxHigh && bid >= sells.extreme + step)
            OpenLevel(POSITION_TYPE_SELL, sells.count);
      }
   }

   ShowPanel(buys, sells, atr, block);
}
//+------------------------------------------------------------------+
