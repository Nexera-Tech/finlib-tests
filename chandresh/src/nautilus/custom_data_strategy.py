"""
Nautilus strategy for consuming custom CSV data format
Simple moving average crossover strategy for demonstration
"""

import sys
import os
from decimal import Decimal
from typing import List, Dict, Any

try:
    from nautilus_trader.core.correctness import PyCondition
    from nautilus_trader.core.datetime import dt_to_unix_nanos
    from nautilus_trader.indicators.average.sma import SimpleMovingAverage
    from nautilus_trader.model.data.bar import Bar, BarType
    from nautilus_trader.model.data.base import DataType
    from nautilus_trader.model.enums import BarAggregation, PriceType
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.objects import Price, Quantity
    from nautilus_trader.trading.strategy import Strategy
    from nautilus_trader.model.instruments import Equity
    from nautilus_trader.model.enums import AssetClass, OrderSide
    from nautilus_trader.model.orders import MarketOrder
    from nautilus_trader.core.uuid import UUID4
    
    NAUTILUS_AVAILABLE = True
except ImportError:
    NAUTILUS_AVAILABLE = False
    print("Nautilus Trader not available - creating mock strategy")

from src.common.data_parser import DataParser, data_file


class CustomDataNautilusStrategy:
    """Custom data strategy for Nautilus Trader"""
    
    def __init__(self, data_file: str):
        self.data_file = data_file
        self.fast_period = 5
        self.slow_period = 10
        self.indicators = {}
        self.position_size = Decimal('10000') if NAUTILUS_AVAILABLE else 10000
        
        # Load data
        self.parser = DataParser(data_file)
        self.data = self.parser.load_data()
        self.symbols = self.parser.get_symbols()
        
        print(f"Loaded data for symbols: {self.symbols}")
        
    def initialize_indicators(self, symbol: str):
        """Initialize indicators for a symbol"""
        if NAUTILUS_AVAILABLE:
            self.indicators[symbol] = {
                'fast_sma': SimpleMovingAverage(self.fast_period),
                'slow_sma': SimpleMovingAverage(self.slow_period)
            }
        else:
            # Mock indicators
            self.indicators[symbol] = {
                'fast_sma': MockSMA(self.fast_period),
                'slow_sma': MockSMA(self.slow_period)
            }
    
    def create_instrument(self, symbol: str):
        """Create instrument for the symbol"""
        if not NAUTILUS_AVAILABLE:
            return None
            
        # Clean symbol name
        clean_symbol = symbol.replace('.NSE_IDX', '')
        
        instrument_id = InstrumentId(
            symbol=Symbol(clean_symbol),
            venue=Venue("NSE")
        )
        
        # Create equity instrument
        instrument = Equity(
            instrument_id=instrument_id,
            raw_symbol=Symbol(clean_symbol),
            asset_class=AssetClass.EQUITY,
            price_precision=2,
            size_precision=0,
            price_increment=Price.from_str("0.01"),
            size_increment=Quantity.from_int(1),
            margin_init=Decimal('0.1'),
            margin_maint=Decimal('0.05'),
            currency="INR"
        )
        
        return instrument
    
    def process_bar_data(self, symbol: str, bar_data: Dict[str, Any]):
        """Process a single bar of data"""
        if symbol not in self.indicators:
            self.initialize_indicators(symbol)
        
        close_price = bar_data['close']
        
        # Update indicators
        fast_sma = self.indicators[symbol]['fast_sma']
        slow_sma = self.indicators[symbol]['slow_sma']
        
        fast_sma.update_raw(close_price)
        slow_sma.update_raw(close_price)
        
        # Check for signals
        if fast_sma.is_ready() and slow_sma.is_ready():
            fast_value = fast_sma.value
            slow_value = slow_sma.value
            
            if fast_value > slow_value:
                self.on_buy_signal(symbol, close_price, fast_value, slow_value)
            elif fast_value < slow_value:
                self.on_sell_signal(symbol, close_price, fast_value, slow_value)
    
    def on_buy_signal(self, symbol: str, price: float, fast_sma: float, slow_sma: float):
        """Handle buy signal"""
        print(f"BUY SIGNAL for {symbol} at {price:.2f} - Fast SMA: {fast_sma:.2f}, Slow SMA: {slow_sma:.2f}")
        
    def on_sell_signal(self, symbol: str, price: float, fast_sma: float, slow_sma: float):
        """Handle sell signal"""
        print(f"SELL SIGNAL for {symbol} at {price:.2f} - Fast SMA: {fast_sma:.2f}, Slow SMA: {slow_sma:.2f}")
    
    def run_backtest(self):
        """Run the backtest on loaded data"""
        print("Starting backtest...")
        
        for symbol in self.symbols[:1]:  # Test with first symbol only
            print(f"\nProcessing {symbol}...")
            
            # Get bars for this symbol
            bars = self.parser.to_nautilus_format(symbol)
            
            for i, bar in enumerate(bars):
                self.process_bar_data(symbol, bar)
                
                # Print progress every 5 bars
                if i % 5 == 0:
                    print(f"Processed {i+1}/{len(bars)} bars for {symbol}")
        
        print("\nBacktest completed!")


class MockSMA:
    """Mock Simple Moving Average for when Nautilus is not available"""
    
    def __init__(self, period: int):
        self.period = period
        self.values = []
        self.value = 0.0
    
    def update_raw(self, value: float):
        """Update with raw value"""
        self.values.append(value)
        if len(self.values) > self.period:
            self.values.pop(0)
        
        if len(self.values) > 0:
            self.value = sum(self.values) / len(self.values)
    
    def is_ready(self) -> bool:
        """Check if indicator is ready"""
        return len(self.values) >= self.period


def run_strategy_backtest():
    """Run the strategy backtest"""
    # data_file = '/Users/chandreshkumar/Desktop/code/fin-tests/data/raw/sample_data.csv'
    
    try:
        strategy = CustomDataNautilusStrategy(data_file)
        strategy.run_backtest()
    except Exception as e:
        print(f"Error running backtest: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_strategy_backtest()
