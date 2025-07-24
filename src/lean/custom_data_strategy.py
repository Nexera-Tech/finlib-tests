"""
Lean strategy for consuming custom CSV data format
Simple moving average crossover strategy for demonstration
"""

from AlgorithmImports import *
import sys
import os

# Add the common module to path
from src.common.data_parser import DataParser, data_file

class CustomDataStrategy(QCAlgorithm):
    
    def initialize(self):
        """Initialize the algorithm"""
        self.set_start_date(2020, 10, 30)
        self.set_end_date(2020, 10, 30)
        self.set_cash(100000)
        
        # Strategy parameters
        self.fast_period = 5
        self.slow_period = 10
        
        # Data file path
        # self.data_file = '/Users/chandreshkumar/Desktop/code/fin-tests/data/raw/sample_data.csv'
        self.data_file = data_file
        
        # Load and prepare data
        self.load_custom_data()
        
        # Initialize indicators
        self.indicators = {}
        
        # Schedule daily operations
        self.schedule.on(
            self.date_rules.every_day(),
            self.time_rules.at(9, 15),
            self.on_market_open
        )
    
    def load_custom_data(self):
        """Load custom CSV data"""
        try:
            parser = DataParser(self.data_file)
            data = parser.load_data()
            
            # Get available symbols
            symbols = parser.get_symbols()
            self.debug(f"Available symbols: {symbols}")
            
            # Add symbols to universe (for demonstration, we'll use the first symbol)
            if symbols:
                # Use the first available symbol
                symbol_name = symbols[0].replace('.NSE_IDX', '')  # Clean symbol name
                
                # Add custom data (this is a simplified approach)
                # In a real implementation, you'd create a custom data class
                self.symbol = self.add_equity(symbol_name, Resolution.MINUTE).symbol
                
                # Store the parsed data for later use
                self.symbol_data = parser.to_lean_format(symbols[0])
                self.debug(f"Loaded data for {symbol_name}: {len(self.symbol_data)} bars")
                
        except Exception as e:
            self.debug(f"Error loading data: {str(e)}")
            self.symbol = None
            self.symbol_data = None
    
    def on_data(self, data: Slice):
        """Main data handler"""
        if not self.symbol or self.symbol not in data:
            return
        
        # Initialize indicators if not done
        if self.symbol not in self.indicators:
            self.indicators[self.symbol] = {
                'fast_sma': SimpleMovingAverage(self.fast_period),
                'slow_sma': SimpleMovingAverage(self.slow_period)
            }
        
        # Update indicators
        price = data[self.symbol].close
        fast_sma = self.indicators[self.symbol]['fast_sma']
        slow_sma = self.indicators[self.symbol]['slow_sma']
        
        fast_sma.update(self.time, price)
        slow_sma.update(self.time, price)
        
        # Skip if indicators not ready
        if not fast_sma.is_ready or not slow_sma.is_ready:
            return
        
        # Simple crossover strategy
        if fast_sma.current.value > slow_sma.current.value and not self.portfolio.invested:
            self.set_holdings(self.symbol, 1.0)
            self.debug(f"BUY at {price:.2f} - Fast SMA: {fast_sma.current.value:.2f}, Slow SMA: {slow_sma.current.value:.2f}")
        
        elif fast_sma.current.value < slow_sma.current.value and self.portfolio.invested:
            self.liquidate(self.symbol)
            self.debug(f"SELL at {price:.2f} - Fast SMA: {fast_sma.current.value:.2f}, Slow SMA: {slow_sma.current.value:.2f}")
    
    def on_market_open(self):
        """Called at market open"""
        self.debug(f"Market opened at {self.time}")
    
    def on_end_of_algorithm(self):
        """Called at end of backtest"""
        self.debug(f"Final portfolio value: ${self.portfolio.total_portfolio_value:,.2f}")


class CustomDataReader:
    """Helper class to simulate feeding custom data to Lean"""
    
    def __init__(self, file_path: str):
        self.parser = DataParser(file_path)
        self.data = self.parser.load_data()
    
    def get_bars_for_symbol(self, symbol: str):
        """Get bars for a specific symbol"""
        return self.parser.to_lean_format(symbol)
