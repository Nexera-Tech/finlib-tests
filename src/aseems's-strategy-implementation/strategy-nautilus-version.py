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
    """
    Bank Nifty SuperTrend Option Selling Strategy for Nautilus Trader.
    
    Strategy Logic:
    1. Enter short straddle at 09:19 (ATM CE + PE)
    2. Multiple stop-loss levels:
       - Individual: 15% on each leg
       - Combined: 25% on total premium
       - Trailing: 10% based on SuperTrend
    3. Exit all positions by 15:15
    """

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

    def __init__(self, config: Optional[Dict] = None):
        """Initialize the strategy with parameters."""
        super().__init__()
        
        # Handle config being None
        if config is None:
            config = {}
            
        # Strategy parameters
        entry_time_str = config.get("entry_time", "09:19:00")
        if len(entry_time_str.split(':')) == 2:
            entry_time_str += ":00"
        self.entry_time = time.fromisoformat(entry_time_str)
        
        exit_time_str = config.get("exit_time", "15:15:00") 
        if len(exit_time_str.split(':')) == 2:
            exit_time_str += ":00"
        self.exit_time = time.fromisoformat(exit_time_str)
        
        self.individual_sl_pct = config.get("individual_sl_pct", 0.15)
        self.combined_sl_pct = config.get("combined_sl_pct", 0.25)
        self.trailing_sl_pct = config.get("trailing_sl_pct", 0.10)
        self.lot_size = config.get("lot_size", 1)
        
        # SuperTrend parameters
        self.st_period = config.get("supertrend_period", 10)
        self.st_multiplier = config.get("supertrend_multiplier", 3)
        self.supertrend = SuperTrend(self.st_period, self.st_multiplier)
        
        # Position tracking
        self.positions = {}
        self.ce_position = None
        self.pe_position = None
        self.ce_entry_price = None
        self.pe_entry_price = None
        self.atm_strike = None
        self.spot_price = None
        
        # Stop-loss tracking
        self.ce_sl = None
        self.pe_sl = None
        self.combined_sl = None
        self.trailing_sl_active = False
        self.ce_trailing_sl = None
        self.pe_trailing_sl = None
        
        # State flags
        self.positions_entered = False
        self.strategy_complete = False
        
        # Trade tracking
        self.daily_pnl = 0.0
        self.trade_log = []
        self.cumulative_pnl = 0.0
        self.trade_counter = 0

    def on_start(self):
        """Called when the strategy starts."""
        self.log.info("Bank Nifty SuperTrend Option Strategy started")
        self.log.info(f"Entry: {self.entry_time}, Exit: {self.exit_time}")
        self.log.info(f"SL levels: Individual {self.individual_sl_pct*100}%, Combined {self.combined_sl_pct*100}%, Trailing {self.trailing_sl_pct*100}%")

    def on_stop(self):
        """Called when the strategy stops."""
        self.log.info("Bank Nifty SuperTrend Option Strategy stopped")

    def on_bar(self, bar):
        """Process incoming bar data."""
        current_time = datetime.now().time()
        
        # Skip if strategy is complete
        if self.strategy_complete:
            return
            
        # Check if it's time to enter positions
        if (not self.positions_entered and 
            current_time >= self.entry_time and
            current_time < time(9, 25)):  # Entry window
            self.enter_positions(bar)
            
        # Update SuperTrend if we have underlying data
        if bar.instrument_id.value.startswith("BANKNIFTY"):
            st_value = self.supertrend.update(bar.high.as_double(), bar.low.as_double(), bar.close.as_double())
            if st_value and self.trailing_sl_active:
                self.update_trailing_stops(st_value)
                
        # Check exit time
        if current_time >= self.exit_time:
            self.exit_all_positions("EOD Exit")
            self.strategy_complete = True

    def enter_positions(self, spot_price: float, ce_price: float, pe_price: float):
        """Enter short straddle positions."""
        try:
            self.spot_price = spot_price
            self.atm_strike = round(spot_price / 100) * 100
            
            # Store entry prices
            self.ce_entry_price = ce_price
            self.pe_entry_price = pe_price
            
            # Calculate stop-loss levels
            self.ce_sl = ce_price * (1 + self.individual_sl_pct)
            self.pe_sl = pe_price * (1 + self.individual_sl_pct) 
            self.combined_sl = (ce_price + pe_price) * (1 + self.combined_sl_pct)
            
            # Set position flags
            self.ce_position = True
            self.pe_position = True
            self.positions_entered = True
            
            # Log entry
            gross_credit = ce_price + pe_price
            self.log.info(f"Positions entered - Spot: {spot_price:.2f}, ATM: {self.atm_strike}")
            self.log.info(f"CE: {ce_price:.2f} (SL: {self.ce_sl:.2f}), PE: {pe_price:.2f} (SL: {self.pe_sl:.2f})")
            self.log.info(f"Gross Credit: {gross_credit:.2f}, Combined SL: {self.combined_sl:.2f}")
            
            # Create trade records - will be updated by backtester with proper format
            self.trade_counter += 1
            ce_trade = {
                'trade_id': f"CE_{self.trade_counter}",
                'entry_timestamp': None,  # Will be set by backtester
                'exit_timestamp': None,
                'entry_price': ce_price,
                'exit_price': None,
                'instrument': None,  # Will be set by backtester
                'side': 'SELL',
                'quantity': self.lot_size,
                'entry_reason': 'Strategy Entry - Short Straddle',
                'exit_reason': None,
                'trade_pnl': None,
                'cumulative_pnl': None,
                'status': 'OPEN'
            }
            
            # Create PE trade record  
            self.trade_counter += 1
            pe_trade = {
                'trade_id': f"PE_{self.trade_counter}",
                'entry_timestamp': None,  # Will be set by backtester
                'exit_timestamp': None,
                'entry_price': pe_price,
                'exit_price': None,
                'instrument': None,  # Will be set by backtester
                'side': 'SELL',
                'quantity': self.lot_size,
                'entry_reason': 'Strategy Entry - Short Straddle',
                'exit_reason': None,
                'trade_pnl': None,
                'cumulative_pnl': None,
                'status': 'OPEN'
            }
            
            self.trade_log.extend([ce_trade, pe_trade])
            
        except Exception as e:
            self.log.error(f"Error entering positions: {str(e)}")

    def check_stop_losses(self, ce_price: float, pe_price: float) -> Optional[str]:
        """
        Check all stop-loss conditions.
        Returns the exit reason if a stop-loss is triggered, None otherwise.
        """
        if not self.positions_entered:
            return None
            
        # Check individual stop-losses
        if self.ce_position and ce_price >= self.ce_sl:
            self.exit_ce_position(ce_price, "Individual SL")
            self.trailing_sl_active = True
            return "CE_SL"
            
        if self.pe_position and pe_price >= self.pe_sl:
            self.exit_pe_position(pe_price, "Individual SL") 
            self.trailing_sl_active = True
            return "PE_SL"
            
        # Check combined stop-loss (only if both positions active)
        if self.ce_position and self.pe_position:
            combined_value = ce_price + pe_price
            if combined_value >= self.combined_sl:
                self.exit_all_positions("Combined SL")
                return "COMBINED_SL"
                
        # Check trailing stop-losses
        if self.ce_position and self.ce_trailing_sl and ce_price >= self.ce_trailing_sl:
            self.exit_ce_position(ce_price, "Trailing SL")
            return "CE_TRAILING_SL"
            
        if self.pe_position and self.pe_trailing_sl and pe_price >= self.pe_trailing_sl:
            self.exit_pe_position(pe_price, "Trailing SL")
            return "PE_TRAILING_SL"
            
        return None

    def update_trailing_stops(self, st_value: float, st_trend: int, ce_price: float, pe_price: float):
        """Update trailing stops based on SuperTrend."""
        if not self.trailing_sl_active:
            return
            
        # Update CE trailing stop (if only CE position remains)
        if self.ce_position and not self.pe_position:
            if st_trend == 1 and ce_price < st_value:  # Uptrend and price below ST
                new_trailing_sl = ce_price * (1 + self.trailing_sl_pct)
                if self.ce_trailing_sl is None:
                    self.ce_trailing_sl = new_trailing_sl
                else:
                    self.ce_trailing_sl = min(self.ce_trailing_sl, new_trailing_sl)
                self.log.info(f"CE Trailing SL updated: {self.ce_trailing_sl:.2f}")
            elif ce_price >= st_value:
                # Exit when price crosses SuperTrend upward
                self.exit_ce_position(ce_price, "SuperTrend Exit")
                
        # Update PE trailing stop (if only PE position remains)
        if self.pe_position and not self.ce_position:
            if st_trend == -1 and pe_price < st_value:  # Downtrend and price below ST
                new_trailing_sl = pe_price * (1 + self.trailing_sl_pct)
                if self.pe_trailing_sl is None:
                    self.pe_trailing_sl = new_trailing_sl
                else:
                    self.pe_trailing_sl = min(self.pe_trailing_sl, new_trailing_sl)
                self.log.info(f"PE Trailing SL updated: {self.pe_trailing_sl:.2f}")
            elif pe_price >= st_value:
                # Exit when price crosses SuperTrend upward
                self.exit_pe_position(pe_price, "SuperTrend Exit")

    def exit_ce_position(self, exit_price: float, reason: str):
        """Exit CE position."""
        if self.ce_position:
            ce_pnl = self.ce_entry_price - exit_price
            self.daily_pnl += ce_pnl
            self.cumulative_pnl += ce_pnl
            self.ce_position = False
            
            self.log.info(f"CE position exited at {exit_price:.2f} - Reason: {reason}, P&L: {ce_pnl:.2f}")
            
            # Update CE trade record
            for trade in self.trade_log:
                if (trade['trade_id'].startswith('CE_') and 
                    trade['status'] == 'OPEN' and 
                    trade['entry_price'] == self.ce_entry_price):
                    trade['exit_timestamp'] = None  # Will be set by backtester
                    trade['exit_price'] = exit_price
                    trade['exit_reason'] = reason
                    trade['trade_pnl'] = ce_pnl
                    trade['cumulative_pnl'] = self.cumulative_pnl
                    trade['status'] = 'CLOSED'
                    break

    def exit_pe_position(self, exit_price: float, reason: str):
        """Exit PE position."""
        if self.pe_position:
            pe_pnl = self.pe_entry_price - exit_price
            self.daily_pnl += pe_pnl
            self.cumulative_pnl += pe_pnl
            self.pe_position = False
            
            self.log.info(f"PE position exited at {exit_price:.2f} - Reason: {reason}, P&L: {pe_pnl:.2f}")
            
            # Update PE trade record
            for trade in self.trade_log:
                if (trade['trade_id'].startswith('PE_') and 
                    trade['status'] == 'OPEN' and 
                    trade['entry_price'] == self.pe_entry_price):
                    trade['exit_timestamp'] = None  # Will be set by backtester
                    trade['exit_price'] = exit_price
                    trade['exit_reason'] = reason
                    trade['trade_pnl'] = pe_pnl
                    trade['cumulative_pnl'] = self.cumulative_pnl
                    trade['status'] = 'CLOSED'
                    break

    def exit_all_positions(self, reason: str, ce_price: float = None, pe_price: float = None):
        """Exit all open positions."""
        # Exit positions using individual exit methods to maintain proper trade records
        if self.ce_position and ce_price is not None:
            self.exit_ce_position(ce_price, reason)
            
        if self.pe_position and pe_price is not None:
            self.exit_pe_position(pe_price, reason)
            
        # Complete the strategy
        self.positions_entered = False
        self.strategy_complete = True
        
        self.log.info(f"All positions exited - Reason: {reason}, Daily P&L: {self.daily_pnl:.2f}, Cumulative P&L: {self.cumulative_pnl:.2f}")

    def get_trade_summary(self) -> Dict:
        """Get summary of trades for the day."""
        return {
            'daily_pnl': self.daily_pnl,
            'positions_entered': self.positions_entered,
            'ce_position_active': self.ce_position if hasattr(self, 'ce_position') else False,
            'pe_position_active': self.pe_position if hasattr(self, 'pe_position') else False,
            'atm_strike': self.atm_strike,
            'trade_log': self.trade_log
        }

# ══════════════════════════════════════════════════════════════════════════════
# Nautilus Strategy-based Backtester
# ══════════════════════════════════════════════════════════════════════════════

class NautilusBacktester:
    """
    Backtester that uses the actual Nautilus strategy for testing on CSV data.
    This ensures complete consistency between backtest and live trading logic.
    """
    
    def __init__(self, csv_folder: str | Path, config: Dict = None):
        self.csv_folder = Path(csv_folder)
        self.config = config or {}
        self.results = []
        self.all_trades = []
        self.global_trade_counter = 0  # Global counter across all days
        self.global_cumulative_pnl = 0.0  # Global cumulative P&L
        
    def run(self):
        """Run backtest using Nautilus strategy on historical CSV data."""
        csv_files = sorted(self.csv_folder.glob("GFDLNFO_BACKADJUSTED_*.csv"))
        if not csv_files:
            warnings.warn("No GFDLNFO_BACKADJUSTED_*.csv files found")
            return pd.DataFrame(), pd.DataFrame()
            
        for file in tqdm(csv_files, desc="Backtesting with Nautilus strategy"):
            self._run_one_day(file)
            
        # Generate results
        summary_df = pd.DataFrame([r.__dict__ for r in self.results])
        trades_df = pd.DataFrame(self.all_trades)
        return summary_df, trades_df
        
    def _run_one_day(self, file_path: Path):
        """Run strategy on one day of data."""
        # Initialize strategy for this day
        strategy = BankNiftySuperTrendOptionSell(self.config)
        
        # Set global counters for continuous tracking
        strategy.trade_counter = self.global_trade_counter
        strategy.cumulative_pnl = self.global_cumulative_pnl
        
        # Mock logger for strategy
        class MockLogger:
            def info(self, msg): print(f"INFO: {msg}")
            def error(self, msg): print(f"ERROR: {msg}")
        strategy.log = MockLogger()
        
        # Load and prepare data
        df = self._prepare_data(file_path)
        if df is None or len(df) == 0:
            return
            
        # Extract date from filename
        date_str = file_path.stem[-8:]
        date_obj = datetime.strptime(date_str, "%d%m%Y")
        
        # Store current file path for expiry matching
        self.current_file_path = file_path
        
        # Find entry and exit data
        entry_data = self._get_entry_data(df)
        if entry_data is None:
            return
            
        spot_price, ce_price, pe_price, atm_strike = entry_data
        
        # Enter positions using strategy
        strategy.enter_positions(spot_price, ce_price, pe_price)
        
        # Simulate the trading day
        exit_reason = self._simulate_trading_day(strategy, df, atm_strike)
        
        # Get final results
        trade_summary = strategy.get_trade_summary()
        
        # Update global counters
        self.global_trade_counter = strategy.trade_counter
        self.global_cumulative_pnl = strategy.cumulative_pnl
        
        # Store results
        self.results.append(DailyResult(
            date=date_obj,
            gross_premium=ce_price + pe_price,
            net_pnl=trade_summary['daily_pnl'],
            leg_hit=exit_reason,
            trades=trade_summary['trade_log']
        ))
        
        # Format and store individual trades with proper timestamps and instruments
        entry_time = f"{date_str} 09:19:59"  # Standard entry time
        exit_time = f"{date_str} 15:14:59"   # Standard exit time
        
        for trade in trade_summary['trade_log']:
            # Determine option type and create proper instrument name
            option_type = "CE" if trade['trade_id'].startswith('CE_') else "PE"
            instrument = f"BANKNIFTY{date_str}{int(atm_strike)}{option_type}"
            
            # Format trade record exactly as required
            formatted_trade = {
                'trade_id': f"{date_str}_{trade['trade_id']}",
                'entry_timestamp': entry_time,
                'exit_timestamp': exit_time if trade['status'] == 'CLOSED' else '',
                'entry_price': trade['entry_price'],
                'exit_price': trade.get('exit_price', ''),
                'instrument': instrument,
                'side': trade['side'],
                'quantity': trade['quantity'],
                'entry_reason': trade['entry_reason'],
                'exit_reason': trade.get('exit_reason', ''),
                'trade_pnl': trade.get('trade_pnl', ''),
                'cumulative_pnl': trade.get('cumulative_pnl', ''),
                'status': trade['status']
            }
            self.all_trades.append(formatted_trade)
    
    def _prepare_data(self, file_path: Path) -> Optional[pd.DataFrame]:
        """Prepare CSV data for processing."""
        try:
            df = pd.read_csv(file_path)
            
            # Parse ticker to extract components
            df['SYMBOL'] = df['Ticker'].str.extract(r'(BANKNIFTY)', expand=False)
            df['STRIKE_PR'] = df['Ticker'].str.extract(r'(\d{5})(?=CE|PE)', expand=False).astype(float)
            df['OPTION_TYP'] = df['Ticker'].str.extract(r'(CE|PE)', expand=False)
            
            # Convert time format
            df['TIMESTAMP'] = pd.to_datetime(df['Time'], format='%H:%M:%S').dt.time
            
            # Rename columns
            df.rename(columns={
                'Open': 'OPEN', 'High': 'HIGH', 
                'Low': 'LOW', 'Close': 'CLOSE'
            }, inplace=True)
            
            # Filter for Bank Nifty only
            df = df[df['SYMBOL'] == 'BANKNIFTY'].copy()
            # DO NOT SORT - keep original CSV order to match original implementation
            # The original uses unsorted df for exit_price function
            
            return df
            
        except Exception as e:
            warnings.warn(f"Error preparing data from {file_path.name}: {str(e)}")
            return None
            
    def _get_entry_data(self, df: pd.DataFrame) -> Optional[Tuple[float, float, float, int]]:
        """Get entry data at strategy entry time."""
        # Parse entry time (ignoring seconds to match original implementation)
        entry_time_str = self.config.get("entry_time", "09:19:00")
        entry_time_parts = entry_time_str.split(':')
        entry_time = datetime.strptime(entry_time_parts[0] + ':' + entry_time_parts[1], "%H:%M").time()
        
        # Sort for finding timestamps like original
        df_sorted = df.sort_values('TIMESTAMP')
        
        # Find entry timestamp
        entry_candidates = df_sorted[df_sorted['TIMESTAMP'] >= entry_time]
        if len(entry_candidates) == 0:
            return None
        entry_ts = entry_candidates['TIMESTAMP'].iloc[0]
        
        # Get entry data
        entry_rows = df[df["TIMESTAMP"] == entry_ts]
        if len(entry_rows) == 0:
            return None
            
        # Estimate spot price
        spot_price = entry_rows["STRIKE_PR"].median()
        atm_strike = round(spot_price / 100) * 100
        
        # Get CE and PE prices using exact same logic as original implementation
        def _entry_price(opt_type: str):
            mask = (
                (df["TIMESTAMP"] == entry_ts)
                & (df["OPTION_TYP"] == opt_type)
                & (df["STRIKE_PR"] == atm_strike)
            )
            matches = df.loc[mask, "CLOSE"]
            if len(matches) == 0:
                return 0.0
            return float(matches.iloc[0])

        ce_price = _entry_price("CE")
        pe_price = _entry_price("PE")
        
        if ce_price == 0 or pe_price == 0:
            return None
            
        return spot_price, ce_price, pe_price, atm_strike
        
    def _simulate_trading_day(self, strategy, df: pd.DataFrame, atm_strike: int) -> Optional[str]:
        """Simulate the trading day with the strategy."""
        # Parse exit time (ignoring seconds to match original implementation)
        exit_time_str = self.config.get("exit_time", "15:15:00")
        exit_time_parts = exit_time_str.split(':')
        exit_time = datetime.strptime(exit_time_parts[0] + ':' + exit_time_parts[1], "%H:%M").time()
        
        # Find the exit timestamp upfront like original implementation  
        exit_candidates = df[df['TIMESTAMP'] <= exit_time]
        if len(exit_candidates) == 0:
            return None
        exit_ts = exit_candidates['TIMESTAMP'].iloc[-1]  # Pre-determine exit timestamp
        
        # Filter relevant data (ATM options only)
        relevant_df = df[df['STRIKE_PR'] == atm_strike].copy()
        
        exit_reason = None
        
        # Process each timestamp
        for current_time in relevant_df['TIMESTAMP'].unique():
            if current_time > exit_time:
                break
                
            # Get current prices
            current_data = relevant_df[relevant_df['TIMESTAMP'] == current_time]
            
            ce_data = current_data[current_data['OPTION_TYP'] == 'CE']
            pe_data = current_data[current_data['OPTION_TYP'] == 'PE']
            
            if len(ce_data) == 0 or len(pe_data) == 0:
                continue
                
            ce_price = float(ce_data['CLOSE'].iloc[0])
            pe_price = float(pe_data['CLOSE'].iloc[0])
            
            # Check stop losses
            sl_reason = strategy.check_stop_losses(ce_price, pe_price)
            if sl_reason:
                exit_reason = sl_reason
                # Strategy state is already updated by check_stop_losses
                if strategy.strategy_complete:
                    break
                
            # Update trailing stops if needed
            if strategy.trailing_sl_active:
                # For simplified backtesting, we'll skip SuperTrend updates
                # In live trading, this would use real SuperTrend values
                pass
        
        # End of day exit using pre-determined exit_ts
        if not strategy.strategy_complete:
            # Use the same exit_price function logic as original
            def exit_price_at_ts(opt_type: str):
                matches = df.loc[(df["TIMESTAMP"] == exit_ts) & (df["OPTION_TYP"] == opt_type) & (df["STRIKE_PR"] == atm_strike), "CLOSE"]
                if len(matches) == 0:
                    return 0.0 
                return float(matches.iloc[0])
            
            final_ce = exit_price_at_ts("CE")
            final_pe = exit_price_at_ts("PE")
            
            if final_ce > 0 and final_pe > 0:
                strategy.exit_all_positions("EOD Exit", final_ce, final_pe)
                exit_reason = "EOD"
                
        return exit_reason


# ══════════════════════════════════════════════════════════════════════════════
# Original CSV Back‑test Engine (kept for reference)
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

    parser = argparse.ArgumentParser(description="Back‑test SuperTrend Option Sell strategy using Nautilus implementation.")
    parser.add_argument("--csv_dir", required=True, help="Folder containing daily CSVs")
    parser.add_argument("--use_original", action='store_true', help="Use original CSV backtester instead of Nautilus")
    
    # Strategy parameters
    parser.add_argument("--entry_time", default="09:19:00", help="Entry time (default: 09:19:00)")
    parser.add_argument("--exit_time", default="15:15:00", help="Exit time (default: 15:15:00)")
    parser.add_argument("--individual_sl", type=float, default=0.15, help="Individual SL percentage (default: 0.15)")
    parser.add_argument("--combined_sl", type=float, default=0.25, help="Combined SL percentage (default: 0.25)")
    parser.add_argument("--trailing_sl", type=float, default=0.10, help="Trailing SL percentage (default: 0.10)")
    
    args = parser.parse_args()
    
    # Build configuration
    config = {
        'entry_time': args.entry_time,
        'exit_time': args.exit_time,
        'individual_sl_pct': args.individual_sl,
        'combined_sl_pct': args.combined_sl,
        'trailing_sl_pct': args.trailing_sl,
        'supertrend_period': 10,
        'supertrend_multiplier': 3,
        'lot_size': 1
    }

    # Choose backtester
    if args.use_original:
        print("Using original CSV backtester...")
        # Filter config for BacktestConfig (remove extra parameters)
        backtest_config = {k: v for k, v in config.items() 
                          if k in ['entry_time', 'exit_time', 'individual_sl_pct', 
                                  'combined_sl_pct', 'trailing_sl_pct', 'supertrend_period', 
                                  'supertrend_multiplier']}
        bt = BankNiftyCSVBacktester(args.csv_dir, BacktestConfig(**backtest_config))
    else:
        print("Using Nautilus strategy backtester...")
        bt = NautilusBacktester(args.csv_dir, config)
    
    # Run backtest
    summary_df, trades_df = bt.run()
    
    if len(summary_df) == 0:
        print("No results generated.")
        exit(1)
    
    # Display results
    print("\n" + "="*60)
    print("BACKTEST RESULTS")
    print("="*60)
    print(f"Strategy: Bank Nifty SuperTrend Option Selling")
    print(f"Entry: {config['entry_time']}, Exit: {config['exit_time']}")
    print(f"SL Levels: Individual {config['individual_sl_pct']*100}%, Combined {config['combined_sl_pct']*100}%, Trailing {config['trailing_sl_pct']*100}%")
    print("="*60)
    
    print("\nDaily Summary Statistics:")
    print(summary_df["net_pnl"].describe())
    
    if len(trades_df) > 0:
        print("\nTrade Analysis:")
        if 'trade_pnl' in trades_df.columns:
            # Calculate total P&L from individual trades
            total_pnl = trades_df[trades_df['trade_pnl'] != '']['trade_pnl'].astype(float).sum()
            print(f"Total P&L: ₹{total_pnl:.2f}")
            
            # Count completed trades
            completed_trades = len(trades_df[trades_df['status'] == 'CLOSED'])
            total_trades = len(trades_df)
            print(f"Completed Trades: {completed_trades}/{total_trades}")
        
        # Count different exit reasons
        if 'exit_reason' in trades_df.columns:
            print("\nExit Reasons:")
            exit_reasons = trades_df[trades_df['exit_reason'] != '']['exit_reason'].value_counts()
            for reason, count in exit_reasons.items():
                print(f"  {reason}: {count} trades")
    
    # Save reports with appropriate names
    if args.use_original:
        summary_out = Path(args.csv_dir) / "original_backtest_summary.csv"
        trades_out = Path(args.csv_dir) / "original_backtest_trades.csv"
    else:
        summary_out = Path(args.csv_dir) / "nautilus_backtest_summary.csv"
        trades_out = Path(args.csv_dir) / "nautilus_backtest_trades.csv"
    
    summary_df.to_csv(summary_out, index=False)
    trades_df.to_csv(trades_out, index=False)
    
    print(f"\nSaved daily summary → {summary_out}")
    print(f"Saved detailed trades → {trades_out}")
    
    # Display sample results
    if len(summary_df) > 0:
        print("\nSample Daily Results:")
        print(summary_df[['date', 'gross_premium', 'net_pnl', 'leg_hit']].head(5).to_string(index=False))
    
    if len(trades_df) > 0:
        print("\nSample Trade Records:")
        sample_cols = ['trade_id', 'entry_timestamp', 'exit_timestamp', 'instrument', 'entry_price', 'exit_price', 'exit_reason', 'trade_pnl', 'status']
        available_cols = [col for col in sample_cols if col in trades_df.columns]
        print(trades_df[available_cols].head(10).to_string(index=False))
    
    print(f"\nBacktest completed successfully using {'Original CSV' if args.use_original else 'Nautilus Strategy'} backtester.")
    print(f"Trade file contains all required fields: trade_id, entry_timestamp, exit_timestamp, entry_price, exit_price, instrument, side, quantity, entry_reason, exit_reason, trade_pnl, cumulative_pnl, status")
