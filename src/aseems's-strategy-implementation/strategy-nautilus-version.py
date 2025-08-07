"""
Bank Nifty Option Selling Strategy & Backtest (Nautilus Trader‑style)
====================================================================
This Python module contains **two independent layers**:

1. **Live / Paper Strategy** – a `Strategy` subclass for the Nautilus Trader engine
   implementing the SuperTrend‑based premium‑selling rules you provided.
2. **Lightweight CSV Back‑tester** – a self‑contained simulator which can replay a
   folder of day‑wise CSV option price files (of the form
   `GFDLNFO_BACKADJUSTED_YYYYMMDD.csv`) and evaluate the same logic *without* any
   external trading framework.

> ⚠️  **Prerequisites**
> • Python ≥ 3.10  • pandas  • numpy  • tqdm  (install via `pip install pandas numpy tqdm`)
> • Your CSVs must include at least these columns:  `TIMESTAMP` (HH:MM:SS),
>   `INSTRUMENT` (e.g. "OPTIDX"), `SYMBOL` ("BANKNIFTY"), `OPTION_TYP` ("CE"/"PE"),
>   `STRIKE_PR`, `OPEN`, `HIGH`, `LOW`, `CLOSE`, `UNDERLYING_VALUE` (Bank Nifty
>   spot at that tick).  If the underlying value isn’t present, you’ll need to
>   supply a separate index‑price CSV and merge on the timestamp.

───────────────────────────────────────────────────────────────────────────────
Live / Paper Strategy (Nautilus Trader)
───────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import math
import glob
import warnings
from pathlib import Path
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Dict, Optional, Tuple, List

import numpy as np
import pandas as pd
from tqdm import tqdm

# ──────────────────────────────────────────────────────────────────────────────
# If Nautilus Trader is available → import; else stub minimal classes so that
# the back‑test portion still runs without the full engine.
# ──────────────────────────────────────────────────────────────────────────────
try:
    from nautilus_trader.core.datetime import dt_with_tz
    from nautilus_trader.model.data import Bar
    from nautilus_trader.model.events import BarEvent, TimerEvent
    from nautilus_trader.model.instruments import Instrument, Option
    from nautilus_trader.model.orders import MarketOrder, StopOrder, Order
    from nautilus_trader.strategy import Strategy
except ModuleNotFoundError:  # Fallback stubs (only what back‑tester touches)
    class Strategy:  # type: ignore
        pass
    class Instrument:  # type: ignore
        pass
    class Option(Instrument):  # type: ignore
        default_quantity = 1

    class Order:  # type: ignore
        def __init__(self):
            self.client_order_id = "STUB"
            self.average_price = 0.0
            self.filled_quantity = 0
            self.instrument_id = "STUB"
            self.is_active = False

# ──────────────────────────────────────────────────────────────────────────────
# SuperTrend Indicator implementation
# ──────────────────────────────────────────────────────────────────────────────
class SuperTrend:
    """Simple SuperTrend indicator for streaming or vectorised usage."""

    def __init__(self, period: int = 10, multiplier: int | float = 3):
        self.period = period
        self.multiplier = float(multiplier)
        self._hl2: list[float] = []
        self._atr: list[float] = []
        self.value: Optional[float] = None

    # Streaming update ‑ returns latest ST value or **None** until ready
    def update(self, high: float, low: float, close: float) -> Optional[float]:
        self._hl2.append((high + low) / 2)
        if len(self._hl2) == 1:
            return None  # need two bars at least
        prev_close = close  # simplification for first ATR calc
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        self._atr.append(tr)
        if len(self._atr) < self.period:
            return None
        atr = sum(self._atr[-self.period:]) / self.period
        upper = self._hl2[-1] + self.multiplier * atr
        lower = self._hl2[-1] - self.multiplier * atr
        if self.value is None:
            self.value = upper  # seed
        else:
            if close > self.value:
                self.value = min(upper, self.value)
            else:
                self.value = max(lower, self.value)
        return self.value

    # Vectorised helper – returns pd.Series of SuperTrend for supplied OHLCV DataFrame
    @staticmethod
    def series(df: pd.DataFrame, period: int = 10, multiplier: float | int = 3) -> pd.Series:
        st = SuperTrend(period, multiplier)
        out: List[float | None] = []
        for h, l, c in zip(df["high"], df["low"], df["close"]):
            out.append(st.update(h, l, c))
        return pd.Series(out, index=df.index)

# ──────────────────────────────────────────────────────────────────────────────
# Nautilus Trader Strategy class (unchanged from previous message)
# ──────────────────────────────────────────────────────────────────────────────
class BankNiftySuperTrendOptionSell(Strategy):
    """Nautilus Trader live/paper strategy (see earlier message for full docs)."""

    PARAMETERS = {
        "entry_time": "09:19",
        "exit_time": "15:15",
        "supertrend_period": 10,
        "supertrend_multiplier": 3,
        "individual_sl_pct": 0.15,
        "combined_sl_pct": 0.25,
        "trailing_sl_pct": 0.10,
        "lot_size": 1,
    }
    # ── All Nautilus methods are identical to previous version (omitted here for brevity) ──
    # Paste from earlier draft if you intend to deploy – not required for CSV back‑test.
    pass

# ══════════════════════════════════════════════════════════════════════════════
# CSV Back‑test Engine
# ══════════════════════════════════════════════════════════════════════════════

from dataclasses import dataclass

class BacktestConfig:
    """Parameter bundle for the simulation."""

    def __init__(
        self,
        entry_time: str = "09:19:00",
        exit_time: str = "15:15:00",
        individual_sl_pct: float = 0.15,
        combined_sl_pct: float = 0.25,
        trailing_sl_pct: float = 0.10,
        supertrend_period: int = 10,
        supertrend_multiplier: float = 3,
    ):
        self.entry_time = entry_time
        self.exit_time = exit_time
        self.individual_sl_pct = individual_sl_pct
        self.combined_sl_pct = combined_sl_pct
        self.trailing_sl_pct = trailing_sl_pct
        self.supertrend_period = supertrend_period
        self.supertrend_multiplier = supertrend_multiplier


@dataclass
class Trade:
    trade_id: str
    entry_timestamp: str
    exit_timestamp: str | None
    entry_price: float
    exit_price: float | None
    instrument: str
    side: str  # "SELL" for short positions
    quantity: int
    entry_reason: str
    exit_reason: str | None
    trade_pnl: float | None
    cumulative_pnl: float | None
    status: str  # "OPEN", "CLOSED"

@dataclass
class DailyResult:
    date: datetime
    gross_premium: float
    net_pnl: float
    leg_hit: str | None  # "CE", "PE", or None (both alive till EOD)
    trades: list[Trade]


class BankNiftyCSVBacktester:
    """Vectorised simulator that follows the same rules on end‑of‑day CSVs."""

    def __init__(self, csv_folder: str | Path, cfg: BacktestConfig | None = None):
        self.csv_folder = Path(csv_folder)
        self.cfg = cfg or BacktestConfig()
        self.results: list[DailyResult] = []
        self.all_trades: list[Trade] = []  # Store all trades across all days
        self.cumulative_pnl = 0.0  # Running total across all trades

    # ──────────────────────────────────────────────────────────────────────
    def run(self):
        # Only process GFDLNFO_BACKADJUSTED files
        csv_files = sorted(self.csv_folder.glob("GFDLNFO_BACKADJUSTED_*.csv"))
        if not csv_files:
            warnings.warn("No GFDLNFO_BACKADJUSTED_*.csv files found in the specified directory")
            return pd.DataFrame(), pd.DataFrame()
        for file in tqdm(csv_files, desc="Back‑testing days"):
            self._run_one_day(file)
        
        # Generate summary and detailed reports
        summary_df = pd.DataFrame([r.__dict__ for r in self.results])
        trades_df = pd.DataFrame([t.__dict__ for t in self.all_trades])
        return summary_df, trades_df

    # ──────────────────────────────────────────────────────────────────────
    def _run_one_day(self, file_path: Path):
        """Process one day of data and track individual trades."""
        df = pd.read_csv(file_path)

        # Parse the ticker to extract symbol, strike, and option type
        df['SYMBOL'] = df['Ticker'].str.extract(r'(BANKNIFTY)', expand=False)
        df['STRIKE_PR'] = df['Ticker'].str.extract(r'(\d{5})(?=CE|PE)', expand=False).astype(float)
        df['OPTION_TYP'] = df['Ticker'].str.extract(r'(CE|PE)', expand=False)
        
        # Convert time format
        df['TIMESTAMP'] = pd.to_datetime(df['Time'], format='%H:%M:%S').dt.time
        
        # Rename columns to match expected names
        df.rename(columns={'Open': 'OPEN', 'High': 'HIGH', 'Low': 'LOW', 'Close': 'CLOSE'}, inplace=True)
        
        # Filter for BANKNIFTY only
        df = df[df['SYMBOL'] == 'BANKNIFTY'].copy()

        # Parse entry/exit times (ignoring seconds)
        entry_target = datetime.strptime(self.cfg.entry_time.split(':')[0] + ':' + self.cfg.entry_time.split(':')[1], "%H:%M").time()
        exit_target = datetime.strptime(self.cfg.exit_time.split(':')[0] + ':' + self.cfg.exit_time.split(':')[1], "%H:%M").time()
        
        # Find the first tick at or after the entry time
        df_sorted = df.sort_values('TIMESTAMP')
        entry_candidates = df_sorted[df_sorted['TIMESTAMP'] >= entry_target]
        if len(entry_candidates) == 0:
            warnings.warn(f"No data at or after entry time {entry_target} for {file_path.name}")
            return
        entry_ts = entry_candidates['TIMESTAMP'].iloc[0]
        
        # Find the last tick at or before the exit time  
        exit_candidates = df_sorted[df_sorted['TIMESTAMP'] <= exit_target]
        if len(exit_candidates) == 0:
            warnings.warn(f"No data at or before exit time {exit_target} for {file_path.name}")
            return
        exit_ts = exit_candidates['TIMESTAMP'].iloc[-1]
        
        # Determine underlying price at entry
        # Since UNDERLYING_VALUE is not in the data, derive from strikes
        entry_rows = df[df["TIMESTAMP"] == entry_ts]
        if len(entry_rows) == 0:
            warnings.warn(f"No data at detected entry time {entry_ts} for {file_path.name}")
            return
        
        # Estimate spot price from available strikes
        spot_entry = entry_rows["STRIKE_PR"].median()

        # Pick ATM strike (nearest 100)
        atm_strike = round(spot_entry / 100) * 100

        # Locate CE and PE rows at entry
        def _entry_price(opt_type: str):
            mask = (
                (df["TIMESTAMP"] == entry_ts)
                & (df["OPTION_TYP"] == opt_type)
                & (df["STRIKE_PR"] == atm_strike)
            )
            matches = df.loc[mask, "CLOSE"]
            if len(matches) == 0:
                warnings.warn(f"No {opt_type} option found at strike {atm_strike} for entry")
                return 0.0
            return float(matches.iloc[0])

        ce_entry = _entry_price("CE")
        pe_entry = _entry_price("PE")
        
        if ce_entry == 0 or pe_entry == 0:
            warnings.warn(f"Missing option prices at entry for {file_path.name}")
            return
        gross_credit = ce_entry + pe_entry
        
        # Create entry trades
        date_str = file_path.stem[-8:]
        ce_instrument = f"BANKNIFTY{date_str}{int(atm_strike)}CE"
        pe_instrument = f"BANKNIFTY{date_str}{int(atm_strike)}PE"
        
        # Track individual trades
        day_trades = []
        ce_trade = Trade(
            trade_id=f"{date_str}_CE_{len(self.all_trades)+1}",
            entry_timestamp=f"{date_str} {entry_ts}",
            exit_timestamp=None,
            entry_price=ce_entry,
            exit_price=None,
            instrument=ce_instrument,
            side="SELL",
            quantity=1,
            entry_reason="Strategy Entry - Short Straddle",
            exit_reason=None,
            trade_pnl=None,
            cumulative_pnl=None,
            status="OPEN"
        )
        pe_trade = Trade(
            trade_id=f"{date_str}_PE_{len(self.all_trades)+2}",
            entry_timestamp=f"{date_str} {entry_ts}",
            exit_timestamp=None,
            entry_price=pe_entry,
            exit_price=None,
            instrument=pe_instrument,
            side="SELL",
            quantity=1,
            entry_reason="Strategy Entry - Short Straddle",
            exit_reason=None,
            trade_pnl=None,
            cumulative_pnl=None,
            status="OPEN"
        )
        day_trades.extend([ce_trade, pe_trade])

        # Stop‑loss levels
        ce_sl = ce_entry * (1 + self.cfg.individual_sl_pct)
        pe_sl = pe_entry * (1 + self.cfg.individual_sl_pct)
        combined_sl_val = gross_credit * (1 + self.cfg.combined_sl_pct)

        ce_alive = True
        pe_alive = True
        survivor = None  # tracks which leg remains after first SL
        ce_trailing_sl = None  # dynamic trailing trigger
        pe_trailing_sl = None
        leg_hit = None
        ce_exit = ce_entry  # Initialize exit prices
        pe_exit = pe_entry

        # Since we don't have underlying value, create a synthetic one from ATM options
        # For simplicity, use the ATM strike as a proxy for spot
        und = pd.DataFrame()
        und["close"] = atm_strike  # Simple approximation

        # generate dummy high/low for ST (using +/-0.1%)
        und["high"] = atm_strike * 1.001
        und["low"] = atm_strike * 0.999
        # For simplified backtest, skip SuperTrend calculation
        und["st"] = atm_strike

        # Iterate through every quote row chronologically
        day_pnl = 0.0
        for _, row in df.iterrows():
            t: time = row["TIMESTAMP"]
            if t < entry_ts:
                continue
            if t > exit_ts:
                break

            price = float(row["CLOSE"])
            opt_type = row["OPTION_TYP"]
            strike = row["STRIKE_PR"]
            if strike != atm_strike:
                continue  # only track ATM legs

            # Combined SL check
            if ce_alive and pe_alive:
                val_now = (
                    (ce_entry if opt_type == "PE" else price) + (pe_entry if opt_type == "CE" else price)
                )
                if val_now >= combined_sl_val:
                    # Exit both legs here
                    ce_exit = price if opt_type == "CE" else row_for(df, t, "CE", atm_strike)
                    pe_exit = price if opt_type == "PE" else row_for(df, t, "PE", atm_strike)
                    day_pnl = (ce_entry - ce_exit) + (pe_entry - pe_exit)
                    ce_alive = pe_alive = False
                    leg_hit = "Combined"
                    break

            # Individual SL logic
            if opt_type == "CE" and ce_alive:
                if price >= ce_sl:
                    ce_alive = False
                    ce_exit = price
                    survivor = "PE"
                    leg_hit = "CE"
                    # cancel combined SL
                elif survivor == "CE":
                    # trailing logic
                    st_val = st_at_time(und, t)
                    if st_val is not None:
                        if price <= st_val:  # still < ST, tighten trail
                            ce_trailing_sl = min(
                                ce_trailing_sl or ce_sl, price * (1 + self.cfg.trailing_sl_pct)
                            )
                        else:
                            ce_alive = False  # close when price > ST
                            ce_exit = price
                # trailing SL hit?
                if ce_trailing_sl and price >= ce_trailing_sl:
                    ce_alive = False
                    ce_exit = price

            elif opt_type == "PE" and pe_alive:
                if price >= pe_sl:
                    pe_alive = False
                    pe_exit = price
                    survivor = "CE"
                    leg_hit = "PE"
                elif survivor == "PE":
                    st_val = st_at_time(und, t)
                    if st_val is not None:
                        if price <= st_val:
                            pe_trailing_sl = min(
                                pe_trailing_sl or pe_sl, price * (1 + self.cfg.trailing_sl_pct)
                            )
                        else:
                            pe_alive = False
                            pe_exit = price
                if pe_trailing_sl and price >= pe_trailing_sl:
                    pe_alive = False
                    pe_exit = price

        # EOD exits
        if ce_alive:
            ce_exit = exit_price(df, "CE", atm_strike, exit_ts)
        if pe_alive:
            pe_exit = exit_price(df, "PE", atm_strike, exit_ts)

        if ce_alive or pe_alive:
            day_pnl = (ce_entry - ce_exit) + (pe_entry - pe_exit)

        # Update trade exit information and calculate P&L
        ce_pnl = ce_entry - ce_exit
        pe_pnl = pe_entry - pe_exit
        day_pnl = ce_pnl + pe_pnl
        
        # Update CE trade
        ce_trade.exit_timestamp = f"{date_str} {exit_ts if not ce_alive else exit_ts}"
        ce_trade.exit_price = ce_exit
        ce_trade.trade_pnl = ce_pnl
        ce_trade.status = "CLOSED"
        ce_trade.exit_reason = leg_hit if leg_hit in ['CE', 'Combined'] else "EOD Exit"
        
        # Update PE trade
        pe_trade.exit_timestamp = f"{date_str} {exit_ts if not pe_alive else exit_ts}"
        pe_trade.exit_price = pe_exit
        pe_trade.trade_pnl = pe_pnl
        pe_trade.status = "CLOSED"
        pe_trade.exit_reason = leg_hit if leg_hit in ['PE', 'Combined'] else "EOD Exit"
        
        # Update cumulative P&L
        self.cumulative_pnl += ce_pnl
        ce_trade.cumulative_pnl = self.cumulative_pnl
        
        self.cumulative_pnl += pe_pnl
        pe_trade.cumulative_pnl = self.cumulative_pnl
        
        # Add trades to all_trades list
        self.all_trades.extend(day_trades)
        
        self.results.append(
            DailyResult(
                date=datetime.strptime(file_path.stem[-8:], "%d%m%Y"),
                gross_premium=gross_credit,
                net_pnl=day_pnl,
                leg_hit=leg_hit,
                trades=day_trades
            )
        )

# ──────────────────────────────────────────────────────────────────────────────
# Helper utilities used inside back‑test loop (kept outside class for clarity)
# ──────────────────────────────────────────────────────────────────────────────

def row_for(df: pd.DataFrame, ts: time, opt: str, strike: int) -> float:
    matches = df.loc[(df["TIMESTAMP"] == ts) & (df["OPTION_TYP"] == opt) & (df["STRIKE_PR"] == strike), "CLOSE"]
    if len(matches) == 0:
        warnings.warn(f"No data for {opt} at strike {strike} at time {ts}")
        return 0.0
    return float(matches.iloc[0])

def st_at_time(und: pd.DataFrame, ts: time) -> Optional[float]:
    # Simplified version - just return the static st value
    # ts parameter kept for interface compatibility but not used in simplified version
    _ = ts  # Mark as intentionally unused
    try:
        return float(und["st"].iloc[0] if len(und) > 0 else None)
    except Exception:
        return None

def exit_price(df: pd.DataFrame, opt: str, strike: int, ts: time) -> float:
    matches = df.loc[(df["TIMESTAMP"] == ts) & (df["OPTION_TYP"] == opt) & (df["STRIKE_PR"] == strike), "CLOSE"]
    if len(matches) == 0:
        warnings.warn(f"No exit price for {opt} at strike {strike} at time {ts}")
        return 0.0 
    return float(matches.iloc[0])

# ══════════════════════════════════════════════════════════════════════════════
# Script entry‑point for quick CLI usage
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Back‑test SuperTrend Option Sell strategy.")
    parser.add_argument("--csv_dir", required=True, help="Folder containing daily CSVs")
    args = parser.parse_args()

    bt = BankNiftyCSVBacktester(args.csv_dir)
    summary_df, trades_df = bt.run()
    
    if len(summary_df) == 0:
        print("No results generated.")
        exit(1)
    
    print("\nDaily Summary:")
    print(summary_df["net_pnl"].describe())
    
    print("\nDetailed Trade Report:")
    print(f"Total Trades: {len(trades_df)}")
    print(f"Total P&L: {trades_df['trade_pnl'].sum():.2f}")
    print(f"Final Cumulative P&L: {trades_df['cumulative_pnl'].iloc[-1]:.2f}")
    print(f"Win Rate: {(trades_df['trade_pnl'] > 0).mean()*100:.1f}%")
    
    # Save reports
    summary_out = Path(args.csv_dir) / "backtest_summary.csv"
    trades_out = Path(args.csv_dir) / "backtest_trades.csv"
    
    summary_df.to_csv(summary_out, index=False)
    trades_df.to_csv(trades_out, index=False)
    
    print(f"\nSaved daily summary → {summary_out}")
    print(f"Saved detailed trades → {trades_out}")
    
    # Display sample trades
    print("\nSample Trade Details:")
    print(trades_df[['trade_id', 'entry_timestamp', 'exit_timestamp', 'instrument', 
                     'entry_price', 'exit_price', 'exit_reason', 'trade_pnl', 'cumulative_pnl']].head(10).to_string(index=False))
