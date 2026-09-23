//+------------------------------------------------------------------+
//|                                            HedgePyramidGold.mq5  |
//|   Hedged start, pyramid into the winning side, basket trailing SL |
//+------------------------------------------------------------------+
#property version     "1.00"
#property description "Hedged pyramid EA for gold (XAUUSD)."
#property description "Opens a BUY and a SELL together, adds equal lots only to the"
#property description "side that is winning, trails a basket SL on each side and closes"
#property description "the losing side once the winners' locked profit covers it."

#include <Trade/Trade.mqh>

CTrade trade;

//================= LOT SIZING =================//
input group "Lot sizing"
input double StartLot          = 0.01;   // Lot for every trade (used when AutoLot = false)
input bool   AutoLot           = false;  // Size the lot from balance
input double BalancePer001     = 1000.0; // Balance per 0.01 lot (AutoLot)
input double MaxLot            = 0.05;   // Hard cap for any single position

//================= GAP =================//
input group "Gap between trades"
input bool   UseATR            = true;   // Gap from ATR (false = fixed gap)
input ENUM_TIMEFRAMES ATRTimeframe = PERIOD_M15; // ATR timeframe
input int    ATRPeriod         = 14;     // ATR period
input double GapATRMult        = 0.5;    // Gap = ATR x this
input double GapFixed          = 1.50;   // Gap in price when UseATR = false ($1.50 on gold)
input double MinGap            = 1.50;   // Gap is never smaller than this (price)
input double MaxGap            = 2.50;   // Gap is never larger than this (price)
input int    MaxTradesPerSide  = 6;      // Max positions per side (including the first one)
input int    AddCooldownSec    = 5;      // Min seconds between adds on the same side

//================= STOPS =================//
input group "Stops and trailing (measured in gaps)"
input double HardSLGaps        = 2.0;    // Every trade opens with an SL this many gaps away (0 = none)
input double MaxHardSL         = 5.00;   // Hard SL is never further than this from entry (price, 0 = no cap)
input double TrailStartGaps    = 1.0;    // Start trailing when price is this many gaps beyond the side's average
input double TrailDistGaps     = 0.75;   // Trailing SL distance behind price (gaps)
input double TrailStepGaps     = 0.10;   // Min SL improvement before modifying (gaps)

//================= CYCLE =================//
input group "Cycle management"
input bool   CloseLoserWhenCovered = true; // Close the losing side once winners' locked profit covers it
input double CoverBufferMoney  = 1.0;    // Extra locked profit required on top of the loser's loss (money)
input double CycleTargetPer001 = 2.0;    // Close everything at this cycle profit per 0.01 lot (0 = off, let the trail run)
input double CycleStopPct      = 30.0;   // Close everything if the cycle loses this % of equity (0 = off)
input int    CycleStopPauseMin = 30;     // Pause this many minutes after a cycle stop
input int    RestartDelaySec   = 60;     // Wait this many seconds before starting a new cycle

//================= ACCOUNT PROTECTION =================//
input group "Account protection"
input double DailyLossPct      = 4.0;    // Close all and pause until tomorrow at this daily loss % (0 = off)
input double DailyProfitPct    = 0.0;    // Close all and pause until tomorrow at this daily gain % (0 = off)
input double MaxDrawdownPct    = 10.0;   // Close all and HALT at this drawdown % from peak equity (0 = off)
input bool   ResetHalt         = false;  // Set true once to clear a max-drawdown halt

//================= FILTERS =================//
input group "Filters"
input double MaxSpread         = 0.40;   // Max spread in price for new trades ($0.40 on gold)
input bool   UseNewsFilter     = true;   // No new cycles or adds around high-impact news (live only)
input string NewsCurrency      = "USD";  // News currency
input int    NewsMinBefore     = 30;     // Minutes before news
input int    NewsMinAfter      = 30;     // Minutes after news
input int    StartHour         = 1;      // Session start hour for new cycles (server time)
input int    EndHour           = 22;     // Session end hour for new cycles (server time)
input bool   NoNewTradesFriday = true;   // No new cycles late on Friday
input int    FridayCutoffHour  = 18;     // Friday cutoff hour (server time)

//================= GENERAL =================//
input group "General"
input ulong  Magic             = 20260923; // Magic number
input int    SlippagePoints    = 50;       // Max slippage in points

//---------------- GLOBALS ----------------//
struct SideState
{
   int    count;      // open positions on this side
   double lots;       // total volume
   double avgPrice;   // volume-weighted average open price
   double front;      // highest buy / lowest sell open price (pyramid edge)
   double profit;     // floating profit + swap
   double locked;     // profit guaranteed if every SL on this side is hit
   bool   protectedAll; // every position has an SL
};

int      atrHandle      = INVALID_HANDLE;
double   peakEquity     = 0;
double   dayStartEquity = 0;
int      currentDay     = -1;
bool     dailyPaused    = false;
bool     ddHalted       = false;
datetime cycleStart     = 0;
datetime nextCycleAt    = 0;
datetime lastBuyAdd     = 0;
datetime lastSellAdd    = 0;

//+------------------------------------------------------------------+
//| Helpers                                                          |
//+------------------------------------------------------------------+
string HaltKey()  { return "HPG_HALT_"  + _Symbol + "_" + (string)Magic; }
string CycleKey() { return "HPG_CYCLE_" + _Symbol + "_" + (string)Magic; }

double ATRValue()
{
   double v[1];
   if(atrHandle == INVALID_HANDLE || CopyBuffer(atrHandle, 0, 1, 1, v) != 1)
      return 0;
   return v[0];
}

double Gap(const double atr)
{
   double g = (UseATR && atr > 0) ? atr * GapATRMult : GapFixed;
   g = MathMax(g, MinGap);
   if(MaxGap > 0) g = MathMin(g, MaxGap);
   return g;
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

// Equal lots on every trade - no martingale
double TradeLot()
{
   if(!AutoLot) return NormLot(StartLot);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   return NormLot(0.01 * MathFloor(balance / MathMax(BalancePer001, 1.0)));
}

// Broker minimum distance between price and SL
double MinStopGap()
{
   return (SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) +
           SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL) + 1) * _Point;
}

double Spread()
{
   return SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID);
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
   s.count = 0; s.lots = 0; s.avgPrice = 0; s.front = 0; s.profit = 0;
   s.locked = 0; s.protectedAll = true;

   bool   isBuy    = (type == POSITION_TYPE_BUY);
   double weighted = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);   // also selects the position
      if(ticket == 0 || !IsOurs(type)) continue;

      double vol  = PositionGetDouble(POSITION_VOLUME);
      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl   = PositionGetDouble(POSITION_SL);
      double swap = PositionGetDouble(POSITION_SWAP);
      s.count++;
      s.lots   += vol;
      weighted += vol * open;
      s.profit += PositionGetDouble(POSITION_PROFIT) + swap;
      if(s.front == 0 || (isBuy ? open > s.front : open < s.front))
         s.front = open;

      // Money this position is guaranteed to make (or lose) at its SL
      double atSL = 0;
      if(sl <= 0 || !OrderCalcProfit(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, vol, open, sl, atSL))
         s.protectedAll = false;
      else
         s.locked += atSL + swap;
   }
   if(s.lots > 0) s.avgPrice = weighted / s.lots;
}

//+------------------------------------------------------------------+
//| Realized P/L of our deals since the cycle started                |
//+------------------------------------------------------------------+
double RealizedSince(const datetime from)
{
   if(from == 0) return 0;

   static datetime lastCalc = 0;
   static datetime lastFrom = 0;
   static double   cached   = 0;
   if(TimeCurrent() == lastCalc && from == lastFrom) return cached;   // at most once a second
   lastCalc = TimeCurrent();
   lastFrom = from;
   cached   = 0;

   if(!HistorySelect(from, TimeCurrent() + 60)) return 0;
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0) continue;
      if(HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol) continue;
      if((ulong)HistoryDealGetInteger(deal, DEAL_MAGIC) != Magic) continue;
      cached += HistoryDealGetDouble(deal, DEAL_PROFIT) +
                HistoryDealGetDouble(deal, DEAL_SWAP) +
                HistoryDealGetDouble(deal, DEAL_COMMISSION);
   }
   return cached;
}

//+------------------------------------------------------------------+
//| Close positions                                                  |
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
//| Cycle bookkeeping (a cycle = one hedged start until fully flat)  |
//+------------------------------------------------------------------+
void BeginCycle()
{
   cycleStart = TimeCurrent();
   GlobalVariableSet(CycleKey(), (double)cycleStart);
}

void EndCycle(const string reason, const int pauseSec)
{
   if(cycleStart != 0)
      PrintFormat("Cycle ended (%s): P/L %.2f", reason, RealizedSince(cycleStart));
   cycleStart  = 0;
   nextCycleAt = TimeCurrent() + pauseSec;
   GlobalVariableDel(CycleKey());
}

//+------------------------------------------------------------------+
//| Basket trailing stop: once price is TrailStart gaps beyond the   |
//| side's average, move a broker-side SL on every position of the   |
//| side. The SL lives on the server, so it works if MT5 goes down.  |
//+------------------------------------------------------------------+
void TrailSide(const ENUM_POSITION_TYPE type, const SideState &s, const double gap)
{
   if(s.count == 0) return;

   double start = TrailStartGaps * gap;
   double dist  = MathMax(TrailDistGaps * gap, MinStopGap());
   double step  = TrailStepGaps * gap;
   double bid   = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask   = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

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

bool EnoughMargin(const ENUM_ORDER_TYPE type, const double lot, const double price)
{
   double margin = 0;
   if(!OrderCalcMargin(type, _Symbol, lot, price, margin)) return false;
   return AccountInfoDouble(ACCOUNT_MARGIN_FREE) > margin * 2.0;   // keep a 2x buffer
}

//+------------------------------------------------------------------+
//| Open one trade with its hard SL                                  |
//+------------------------------------------------------------------+
bool OpenTrade(const ENUM_POSITION_TYPE type, const double gap, const string tag)
{
   bool   isBuy = (type == POSITION_TYPE_BUY);
   double lot   = TradeLot();
   double price = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);

   // Cooldown starts on every attempt so a rejected order is not spammed each tick
   if(isBuy) lastBuyAdd = TimeCurrent(); else lastSellAdd = TimeCurrent();

   if(!EnoughMargin(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, lot, price))
   {
      Print("Not enough free margin for ", lot, " lots - trade skipped");
      return false;
   }

   double sl = 0;
   if(HardSLGaps > 0)
   {
      double d = HardSLGaps * gap;
      if(MaxHardSL > 0) d = MathMin(d, MaxHardSL);
      d = MathMax(d, MinStopGap());
      sl = NormPrice(isBuy ? price - d : price + d);
   }

   string comment = StringFormat("HPG %s %s", isBuy ? "BUY" : "SELL", tag);
   bool ok = isBuy ? trade.Buy(lot, _Symbol, 0, sl, 0, comment)
                   : trade.Sell(lot, _Symbol, 0, sl, 0, comment);
   uint rc = trade.ResultRetcode();
   if(!ok || (rc != TRADE_RETCODE_DONE && rc != TRADE_RETCODE_PLACED))
   {
      PrintFormat("%s failed: %u %s", comment, rc, trade.ResultRetcodeDescription());
      return false;
   }
   return true;
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
void ShowPanel(const SideState &b, const SideState &s, const double atr, const double gap,
               const double cycleNet, const string blockReason)
{
   static datetime last = 0;
   if(TimeCurrent() == last) return;
   last = TimeCurrent();

   string status = ddHalted    ? "HALTED (max drawdown) - set ResetHalt = true to resume"
                 : dailyPaused ? "PAUSED until tomorrow (daily limit hit)"
                 : blockReason != "" ? "WAITING: " + blockReason
                 : (b.count + s.count > 0) ? "IN CYCLE"
                 : "READY";

   double eq     = AccountInfoDouble(ACCOUNT_EQUITY);
   double dayPct = dayStartEquity > 0 ? (eq - dayStartEquity) / dayStartEquity * 100.0 : 0;
   double ddPct  = peakEquity > 0 ? (peakEquity - eq) / peakEquity * 100.0 : 0;

   Comment(StringFormat(
      "Hedge Pyramid Gold v1\n"
      "Status: %s\n"
      "ATR: %.2f   Gap: %.2f   Spread: %.2f\n"
      "BUY : %d pos  %.2f lots  avg %.2f  P/L %.2f  locked %.2f\n"
      "SELL: %d pos  %.2f lots  avg %.2f  P/L %.2f  locked %.2f\n"
      "Cycle P/L (closed + open): %.2f  (target %.2f)\n"
      "Today: %+.2f%%  (limit -%.1f%%)\n"
      "Drawdown from peak: %.2f%%  (halt at %.1f%%)",
      status, atr, gap, Spread(),
      b.count, b.lots, b.avgPrice, b.profit, b.protectedAll ? b.locked : 0.0,
      s.count, s.lots, s.avgPrice, s.profit, s.protectedAll ? s.locked : 0.0,
      cycleNet, CycleTargetPer001 * TradeLot() / 0.01, dayPct, DailyLossPct, ddPct, MaxDrawdownPct));
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
   if(StartLot <= 0 || MaxLot <= 0 || MaxTradesPerSide < 1 || MinGap <= 0)
   {
      Print("Invalid inputs: StartLot, MaxLot, MaxTradesPerSide and MinGap must be positive.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(TrailStartGaps <= TrailDistGaps)
      Print("Warning: TrailStartGaps should be larger than TrailDistGaps, otherwise the first trailing SL may be below break-even.");

   trade.SetExpertMagicNumber(Magic);
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);

   atrHandle = iATR(_Symbol, ATRTimeframe, ATRPeriod);
   if(atrHandle == INVALID_HANDLE)
   {
      Print("Failed to create ATR handle.");
      return INIT_FAILED;
   }

   if(ResetHalt) GlobalVariableDel(HaltKey());
   ddHalted = GlobalVariableCheck(HaltKey());   // a halt survives restarts until reset

   // Resume a cycle that was running before a restart
   cycleStart = GlobalVariableCheck(CycleKey()) ? (datetime)GlobalVariableGet(CycleKey()) : 0;

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

   double atr = ATRValue();
   double gap = Gap(atr);
   bool   inCycle = (buys.count + sells.count > 0);
   if(inCycle && cycleStart == 0) BeginCycle();   // positions found without a cycle record
   double cycleNet = inCycle ? RealizedSince(cycleStart) + buys.profit + sells.profit : 0;

   // 3. Halted / paused: stay flat (retry any close that failed)
   if(ddHalted || dailyPaused)
   {
      if(inCycle) CloseAllSides(ddHalted ? "halted" : "paused");
      else if(cycleStart != 0) EndCycle(ddHalted ? "halted" : "paused", 0);
      ShowPanel(buys, sells, atr, gap, cycleNet, "");
      return;
   }

   datetime now = TimeCurrent();

   if(inCycle)
   {
      // 4. Cycle stop loss and optional cycle target
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      if(CycleStopPct > 0 && cycleNet <= -equity * CycleStopPct / 100.0)
      {
         CloseAllSides("cycle stop loss");
         EndCycle("cycle stop loss", CycleStopPauseMin * 60);
         ShowPanel(buys, sells, atr, gap, cycleNet, "");
         return;
      }
      double target = CycleTargetPer001 * TradeLot() / 0.01;   // scales with lot size
      if(target > 0 && cycleNet >= target)
      {
         CloseAllSides("cycle target");
         EndCycle("cycle target", RestartDelaySec);
         ShowPanel(buys, sells, atr, gap, cycleNet, "");
         return;
      }

      // 5. Close the losing side once the winners' locked profit pays for it
      if(CloseLoserWhenCovered && buys.count > 0 && sells.count > 0)
      {
         if(sells.profit < 0 && buys.protectedAll && buys.locked >= -sells.profit + CoverBufferMoney)
         {
            CloseSide(POSITION_TYPE_SELL, "covered by locked BUY profit");
            sells.count = 0;
         }
         else if(buys.profit < 0 && sells.protectedAll && sells.locked >= -buys.profit + CoverBufferMoney)
         {
            CloseSide(POSITION_TYPE_BUY, "covered by locked SELL profit");
            buys.count = 0;
         }
      }

      // 6. Basket trailing stop on each side (this is the take-profit)
      TrailSide(POSITION_TYPE_BUY,  buys,  gap);
      TrailSide(POSITION_TYPE_SELL, sells, gap);

      // 7. Pyramid: add only to the side price is moving in favour of
      string block = "";
      if(UseATR && atr <= 0)       block = "ATR not ready";
      else if(Spread() > MaxSpread) block = "spread too wide";
      else if(NewsBlocked())        block = "high-impact news";

      if(block == "")
      {
         double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

         if(buys.count > 0 && buys.count < MaxTradesPerSide &&
            ask >= buys.front + gap && now - lastBuyAdd >= AddCooldownSec)
            OpenTrade(POSITION_TYPE_BUY, gap, StringFormat("L%d", buys.count + 1));

         if(sells.count > 0 && sells.count < MaxTradesPerSide &&
            bid <= sells.front - gap && now - lastSellAdd >= AddCooldownSec)
            OpenTrade(POSITION_TYPE_SELL, gap, StringFormat("L%d", sells.count + 1));
      }

      ShowPanel(buys, sells, atr, gap, cycleNet, block);
      return;
   }

   // 8. Flat: the previous cycle finished (trailing SL / hard SL on the server)
   if(cycleStart != 0) EndCycle("all positions closed", RestartDelaySec);

   // 9. Start a new hedged cycle: one BUY and one SELL together
   string block = "";
   if(UseATR && atr <= 0)       block = "ATR not ready";
   else if(now < nextCycleAt)    block = StringFormat("restart in %ds", (int)(nextCycleAt - now));
   else if(!InSession())         block = "outside session";
   else if(Spread() > MaxSpread) block = "spread too wide";
   else if(NewsBlocked())        block = "high-impact news";

   if(block == "")
   {
      BeginCycle();
      bool b = OpenTrade(POSITION_TYPE_BUY,  gap, "L1");
      bool s = OpenTrade(POSITION_TYPE_SELL, gap, "L1");
      if(!b && !s)
      {
         EndCycle("open failed", RestartDelaySec);
         block = "open failed, retrying";
      }
   }

   ShowPanel(buys, sells, atr, gap, 0, block);
}
//+------------------------------------------------------------------+
