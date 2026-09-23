//+------------------------------------------------------------------+
//|                                               FiboHedgeGold.mq5  |
//|   Hedged start, Fibonacci recovery lots against the move,        |
//|   round profit target with trailing, round stop loss             |
//+------------------------------------------------------------------+
#property version     "1.00"
#property description "Fibonacci hedge EA for gold (XAUUSD)."
#property description "Opens a BUY and a SELL together. When price moves against a side,"
#property description "adds to that side every fixed gap with Fibonacci lots (capped)."
#property description "Closes everything at the round profit target, or at the round stop loss."

#include <Trade/Trade.mqh>

CTrade trade;

//================= LOT SIZING =================//
input group "Lot sizing"
input double StartLot          = 0.01;   // First lot of each side (used when AutoLot = false)
input bool   AutoLot           = false;  // Size the first lot from balance
input double BalancePer001     = 12000.0;// Balance per 0.01 first lot (AutoLot)
input int    MaxFibMultiplier  = 8;      // Lot multiplier never grows past this (8 = 0.08 max at 0.01 start)

//================= GRID =================//
input group "Grid"
input double Gap               = 2.00;   // Fixed gap between trades on a side (price, $2 on gold)
input int    MaxTradesPerSide  = 10;     // Max positions per side (including the first one)
input int    AddCooldownSec    = 3;      // Min seconds between adds on the same side

//================= PROFIT =================//
input group "Profit booking (money per 0.01 first lot)"
input double ProfitStartPer001 = 4.0;    // Round profit that starts profit booking
input bool   UseProfitTrailing = true;   // true = trail the round profit, false = close at ProfitStart
input double GiveBackPct       = 30.0;   // Close when round profit falls this % from its peak
input double MinLockPer001     = 3.0;    // ...but never lock less than this

//================= LOSS =================//
input group "Loss protection"
input double StopBeyondLast    = 4.00;   // With a side full, close the round this far beyond its last entry (price)
input double DailyLossPct      = 10.0;   // Close all and stop until tomorrow at this daily loss % (0 = off)
input double MaxDrawdownPct    = 0.0;    // Close all and HALT at this drawdown % from peak equity (0 = off)
input bool   ResetHalt         = false;  // Set true once to clear a max-drawdown halt

//================= RESTART =================//
input group "Restart"
input int    ProfitRestartSec  = 30;     // Seconds before a new round after a profit close
input int    StopRestartSec    = 60;     // Seconds before a new round after a stop loss

//================= FILTERS =================//
input group "Filters"
input double MaxSpread         = 0.40;   // Max spread in price for new rounds and adds ($0.40 on gold)
input bool   UseNewsFilter     = true;   // No new rounds around high-impact news (live only)
input string NewsCurrency      = "USD";  // News currency
input int    NewsMinBefore     = 30;     // Minutes before news
input int    NewsMinAfter      = 30;     // Minutes after news
input int    StartHour         = 1;      // Session start hour for new rounds (server time)
input int    EndHour           = 22;     // Session end hour for new rounds (server time)
input bool   NoNewTradesFriday = true;   // No new rounds late on Friday
input int    FridayCutoffHour  = 18;     // Friday cutoff hour (server time)

//================= GENERAL =================//
input group "General"
input ulong  Magic             = 20260924; // Magic number
input int    SlippagePoints    = 50;       // Max slippage in points

//---------------- GLOBALS ----------------//
struct SideState
{
   int    count;      // open positions on this side
   double lots;       // total volume
   double firstLot;   // smallest volume = the side's first lot (restart-safe base)
   double worst;      // lowest buy / highest sell open price (last recovery entry)
   double profit;     // floating profit + swap
};

double   peakEquity     = 0;
double   dayStartEquity = 0;
int      currentDay     = -1;
bool     dailyPaused    = false;
bool     ddHalted       = false;
datetime roundStart     = 0;
double   roundPeak      = 0;
datetime nextRoundAt    = 0;
datetime lastBuyAdd     = 0;
datetime lastSellAdd    = 0;

//+------------------------------------------------------------------+
//| Helpers                                                          |
//+------------------------------------------------------------------+
string HaltKey()  { return "FHG_HALT_"  + _Symbol + "_" + (string)Magic; }
string RoundKey() { return "FHG_ROUND_" + _Symbol + "_" + (string)Magic; }
string PeakKey()  { return "FHG_PEAK_"  + _Symbol + "_" + (string)Magic; }

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
   if(step > 0) lot = MathFloor(lot / step + 1e-9) * step;
   lot = MathMax(mn, MathMin(mx, lot));
   int digits = (step > 0) ? (int)MathMax(0, MathCeil(-MathLog10(step))) : 2;
   return NormalizeDouble(lot, digits);
}

// First lot of a new round
double BaseLot()
{
   if(!AutoLot) return NormLot(StartLot);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   return NormLot(0.01 * MathFloor(balance / MathMax(BalancePer001, 1.0)));
}

// Fibonacci multiplier for trade number n (1-based): 1, 2, 3, 5, 8, 13 ... capped
int FibMultiplier(const int n)
{
   int a = 1, b = 2;
   if(n <= 1) return 1;
   for(int i = 2; i < n; i++)
   {
      int c = a + b;
      a = b;
      b = c;
      if(b >= MaxFibMultiplier) break;
   }
   return MathMin(b, MathMax(MaxFibMultiplier, 1));
}

// Money amounts are given per 0.01 first lot and scale with the round's lot
double PerLot(const double moneyPer001, const double firstLot)
{
   return moneyPer001 * firstLot / 0.01;
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
   s.count = 0; s.lots = 0; s.firstLot = 0; s.worst = 0; s.profit = 0;
   bool isBuy = (type == POSITION_TYPE_BUY);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);   // also selects the position
      if(ticket == 0 || !IsOurs(type)) continue;

      double vol  = PositionGetDouble(POSITION_VOLUME);
      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      s.count++;
      s.lots   += vol;
      s.profit += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
      if(s.firstLot == 0 || vol < s.firstLot) s.firstLot = vol;
      if(s.worst == 0 || (isBuy ? open < s.worst : open > s.worst))
         s.worst = open;
   }
}

//+------------------------------------------------------------------+
//| Realized P/L of our deals since the round started                |
//+------------------------------------------------------------------+
double RealizedSince(const datetime from)
{
   if(from == 0) return 0;
   if(!HistorySelect(from, TimeCurrent() + 60)) return 0;

   double sum = 0;
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0) continue;
      if(HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol) continue;
      if((ulong)HistoryDealGetInteger(deal, DEAL_MAGIC) != Magic) continue;
      sum += HistoryDealGetDouble(deal, DEAL_PROFIT) +
             HistoryDealGetDouble(deal, DEAL_SWAP) +
             HistoryDealGetDouble(deal, DEAL_COMMISSION);
   }
   return sum;
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
}

void CloseAllSides(const string reason)
{
   CloseSide(POSITION_TYPE_BUY, reason);
   CloseSide(POSITION_TYPE_SELL, reason);
   Print("Closed all: ", reason);
}

//+------------------------------------------------------------------+
//| Round bookkeeping (a round = one BUY+SELL start until all closed)|
//+------------------------------------------------------------------+
void BeginRound()
{
   roundStart = TimeCurrent();
   roundPeak  = 0;
   GlobalVariableSet(RoundKey(), (double)roundStart);
   GlobalVariableSet(PeakKey(), 0);
}

void EndRound(const string reason, const int restartSec)
{
   if(roundStart != 0)
      PrintFormat("Round ended (%s): P/L %.2f", reason, RealizedSince(roundStart));
   roundStart  = 0;
   roundPeak   = 0;
   nextRoundAt = TimeCurrent() + restartSec;
   GlobalVariableDel(RoundKey());
   GlobalVariableDel(PeakKey());
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
   return AccountInfoDouble(ACCOUNT_MARGIN_FREE) > margin * 1.5;   // keep a buffer
}

//+------------------------------------------------------------------+
//| Open one trade                                                   |
//+------------------------------------------------------------------+
bool OpenTrade(const ENUM_POSITION_TYPE type, const double lot, const double sl, const int number)
{
   bool   isBuy = (type == POSITION_TYPE_BUY);
   double price = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);

   // Cooldown starts on every attempt so a rejected order is not spammed each tick
   if(isBuy) lastBuyAdd = TimeCurrent(); else lastSellAdd = TimeCurrent();

   if(!EnoughMargin(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, lot, price))
   {
      Print("Not enough free margin for ", lot, " lots - trade skipped");
      return false;
   }

   string comment = StringFormat("FHG %s #%d", isBuy ? "BUY" : "SELL", number);
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
//| Round stop price for a full side, and a broker-side backup SL    |
//+------------------------------------------------------------------+
double StopPrice(const ENUM_POSITION_TYPE type, const SideState &s)
{
   return NormPrice(type == POSITION_TYPE_BUY ? s.worst - StopBeyondLast : s.worst + StopBeyondLast);
}

// The SL lives on the server, so the side is still protected if MT5 goes down
void ArmBackupSL(const ENUM_POSITION_TYPE type, const SideState &s)
{
   double sl = StopPrice(type, s);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !IsOurs(type)) continue;
      if(MathAbs(PositionGetDouble(POSITION_SL) - sl) < _Point) continue;
      if(!trade.PositionModify(ticket, sl, PositionGetDouble(POSITION_TP)))
         PrintFormat("Backup SL %I64u failed: %u %s", ticket,
                     trade.ResultRetcode(), trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Daily limit and max drawdown                                     |
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

   if(!dailyPaused && DailyLossPct > 0 && dayStartEquity > 0 &&
      (eq - dayStartEquity) / dayStartEquity * 100.0 <= -DailyLossPct)
   {
      dailyPaused = true;
      CloseAllSides("daily loss limit");
   }
}

//+------------------------------------------------------------------+
//| Status panel                                                     |
//+------------------------------------------------------------------+
void ShowPanel(const SideState &b, const SideState &s, const double net,
               const double start, const string blockReason)
{
   static datetime last = 0;
   if(TimeCurrent() == last) return;
   last = TimeCurrent();

   string status = ddHalted    ? "HALTED (max drawdown) - set ResetHalt = true to resume"
                 : dailyPaused ? "STOPPED until tomorrow (daily loss limit)"
                 : blockReason != "" ? "WAITING: " + blockReason
                 : (b.count + s.count > 0) ? "IN ROUND"
                 : "READY";

   string booking = "";
   if(roundPeak >= start && start > 0)
      booking = StringFormat("  peak %.2f  (trailing)", roundPeak);

   double eq     = AccountInfoDouble(ACCOUNT_EQUITY);
   double dayPct = dayStartEquity > 0 ? (eq - dayStartEquity) / dayStartEquity * 100.0 : 0;

   Comment(StringFormat(
      "Fibo Hedge Gold v1\n"
      "Status: %s\n"
      "Gap: %.2f   Spread: %.2f\n"
      "BUY : %d/%d pos  %.2f lots  last %.2f  P/L %.2f\n"
      "SELL: %d/%d pos  %.2f lots  last %.2f  P/L %.2f\n"
      "Round P/L: %.2f  (booking starts %.2f)%s\n"
      "Today: %+.2f%%  (limit -%.1f%%)",
      status, Gap, Spread(),
      b.count, MaxTradesPerSide, b.lots, b.worst, b.profit,
      s.count, MaxTradesPerSide, s.lots, s.worst, s.profit,
      net, start, booking, dayPct, DailyLossPct));
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
   if(StartLot <= 0 || Gap <= 0 || MaxTradesPerSide < 1 || MaxFibMultiplier < 1 || StopBeyondLast <= 0)
   {
      Print("Invalid inputs: StartLot, Gap, MaxTradesPerSide, MaxFibMultiplier and StopBeyondLast must be positive.");
      return INIT_PARAMETERS_INCORRECT;
   }

   trade.SetExpertMagicNumber(Magic);
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);

   if(ResetHalt) GlobalVariableDel(HaltKey());
   ddHalted = GlobalVariableCheck(HaltKey());   // a halt survives restarts until reset

   // Resume a round that was running before a restart
   roundStart = GlobalVariableCheck(RoundKey()) ? (datetime)GlobalVariableGet(RoundKey()) : 0;
   roundPeak  = GlobalVariableCheck(PeakKey())  ? GlobalVariableGet(PeakKey()) : 0;

   peakEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   currentDay = -1;
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
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

   bool inRound = (buys.count + sells.count > 0);
   if(inRound && roundStart == 0) BeginRound();   // positions found without a round record

   double firstLot = buys.firstLot > 0 ? buys.firstLot : (sells.firstLot > 0 ? sells.firstLot : BaseLot());
   double start    = PerLot(ProfitStartPer001, firstLot);
   double net      = inRound ? RealizedSince(roundStart) + buys.profit + sells.profit : 0;

   // 3. Halted / stopped for the day: stay flat (retry any close that failed)
   if(ddHalted || dailyPaused)
   {
      if(inRound) CloseAllSides(ddHalted ? "halted" : "daily loss limit");
      else if(roundStart != 0) EndRound(ddHalted ? "halted" : "daily loss limit", 0);
      ShowPanel(buys, sells, net, start, "");
      return;
   }

   datetime now = TimeCurrent();
   double   bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double   ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   if(inRound)
   {
      // 4. One side gone while the other is open = its backup SL was hit on the server
      if(buys.count == 0 || sells.count == 0)
      {
         CloseAllSides("stop loss (server SL hit)");
         EndRound("stop loss", StopRestartSec);
         ShowPanel(buys, sells, net, start, "");
         return;
      }

      // 5. Round stop loss: a full side has moved StopBeyondLast past its last entry
      bool buyStop  = buys.count  >= MaxTradesPerSide && bid <= StopPrice(POSITION_TYPE_BUY,  buys);
      bool sellStop = sells.count >= MaxTradesPerSide && ask >= StopPrice(POSITION_TYPE_SELL, sells);
      if(buyStop || sellStop)
      {
         CloseAllSides("round stop loss");
         EndRound("stop loss", StopRestartSec);
         ShowPanel(buys, sells, net, start, "");
         return;
      }

      // 6. Profit booking
      if(net > roundPeak)
      {
         roundPeak = net;
         GlobalVariableSet(PeakKey(), roundPeak);
      }
      bool takeProfit = false;
      if(!UseProfitTrailing)
         takeProfit = (net >= start);
      else if(roundPeak >= start)
      {
         double lockLevel = MathMax(roundPeak * (1.0 - GiveBackPct / 100.0), PerLot(MinLockPer001, firstLot));
         takeProfit = (net <= lockLevel);
      }
      if(takeProfit)
      {
         CloseAllSides(StringFormat("profit %.2f", net));
         EndRound("profit", ProfitRestartSec);
         ShowPanel(buys, sells, net, start, "");
         return;
      }

      // 7. Backup server-side SL on any full side
      if(buys.count  >= MaxTradesPerSide) ArmBackupSL(POSITION_TYPE_BUY,  buys);
      if(sells.count >= MaxTradesPerSide) ArmBackupSL(POSITION_TYPE_SELL, sells);

      // 8. Recovery adds: bigger Fibonacci lot on the side price moved against
      string block = (Spread() > MaxSpread) ? "spread too wide for adds" : "";
      if(block == "")
      {
         if(buys.count < MaxTradesPerSide && ask <= buys.worst - Gap && now - lastBuyAdd >= AddCooldownSec)
         {
            int    n  = buys.count + 1;
            double sl = (n >= MaxTradesPerSide) ? NormPrice(ask - StopBeyondLast) : 0;
            OpenTrade(POSITION_TYPE_BUY, NormLot(buys.firstLot * FibMultiplier(n)), sl, n);
         }
         if(sells.count < MaxTradesPerSide && bid >= sells.worst + Gap && now - lastSellAdd >= AddCooldownSec)
         {
            int    n  = sells.count + 1;
            double sl = (n >= MaxTradesPerSide) ? NormPrice(bid + StopBeyondLast) : 0;
            OpenTrade(POSITION_TYPE_SELL, NormLot(sells.firstLot * FibMultiplier(n)), sl, n);
         }
      }

      ShowPanel(buys, sells, net, start, block);
      return;
   }

   // 9. Flat: the previous round was closed outside this tick (e.g. manually or by the server)
   if(roundStart != 0)
   {
      double result = RealizedSince(roundStart);
      EndRound("all positions closed", result >= 0 ? ProfitRestartSec : StopRestartSec);
   }

   // 10. Start a new round: one BUY and one SELL together
   string block = "";
   if(now < nextRoundAt)         block = StringFormat("new round in %ds", (int)(nextRoundAt - now));
   else if(!InSession())         block = "outside session";
   else if(Spread() > MaxSpread) block = "spread too wide";
   else if(NewsBlocked())        block = "high-impact news";

   if(block == "")
   {
      double lot = BaseLot();
      BeginRound();
      bool b = OpenTrade(POSITION_TYPE_BUY,  lot, 0, 1);
      bool s = b && OpenTrade(POSITION_TYPE_SELL, lot, 0, 1);
      if(!s)
      {
         // Never run a round with only one side: undo and retry shortly
         if(b) CloseSide(POSITION_TYPE_BUY, "pair open failed");
         EndRound("open failed", 10);
         block = "open failed, retrying";
      }
   }

   ShowPanel(buys, sells, 0, start, block);
}
//+------------------------------------------------------------------+
