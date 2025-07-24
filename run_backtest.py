"""
Backtest runner for the existing Lean strategy using custom data format
"""

import sys
import os
import pandas as pd
from datetime import datetime

# Add the common module to path
from src.common.data_parser import DataParser, data_file


class LeanBacktestRunner:
    """Run backtest for Lean-style strategies with custom data"""
    
    def __init__(self, data_file: str):
        self.data_file = data_file
        self.parser = DataParser(data_file)
        self.results = {}
        
    def run_banknifty_option_strategy(self):
        """Run a simplified version of the BankNifty option strategy"""
        print("=== BankNifty Option Strategy Backtest ===")
        
        # Load data
        data = self.parser.load_data()
        symbols = self.parser.get_symbols()
        
        # Look for BankNifty data
        banknifty_symbols = [s for s in symbols if 'BANKNIFTY' in s]
        if not banknifty_symbols:
            print("No BankNifty data found")
            return
            
        symbol = banknifty_symbols[0]
        print(f"Using symbol: {symbol}")
        
        # Get BankNifty data
        bn_data = self.parser.get_symbol_data(symbol)
        
        # Simulate option strategy
        # For demo purposes, we'll use the underlying price movements
        # In real implementation, you'd have actual option data
        
        results = {
            'symbol': symbol,
            'total_bars': len(bn_data),
            'start_price': bn_data['Close'].iloc[0],
            'end_price': bn_data['Close'].iloc[-1],
            'price_change': bn_data['Close'].iloc[-1] - bn_data['Close'].iloc[0],
            'price_change_pct': ((bn_data['Close'].iloc[-1] / bn_data['Close'].iloc[0]) - 1) * 100,
            'volatility': bn_data['Close'].std(),
            'max_price': bn_data['Close'].max(),
            'min_price': bn_data['Close'].min(),
        }
        
        # Simulate individual stop losses (15% from entry)
        individual_sl_pct = 0.15
        entry_price = results['start_price']
        
        # For a short straddle, we're short both CE and PE
        # We'll simulate this with price movements
        ce_sl_price = entry_price * (1 + individual_sl_pct)  # CE stop loss
        pe_sl_price = entry_price * (1 - individual_sl_pct)  # PE stop loss (simplified)
        
        # Check if SL was hit
        max_price = results['max_price']
        min_price = results['min_price']
        
        ce_sl_hit = max_price > ce_sl_price
        pe_sl_hit = min_price < pe_sl_price
        
        results.update({
            'entry_price': entry_price,
            'ce_sl_price': ce_sl_price,
            'pe_sl_price': pe_sl_price,
            'ce_sl_hit': ce_sl_hit,
            'pe_sl_hit': pe_sl_hit,
            'individual_sl_pct': individual_sl_pct,
        })
        
        # Print results
        print(f"\nBacktest Results for {symbol}:")
        print(f"Entry Price: {results['entry_price']:.2f}")
        print(f"End Price: {results['end_price']:.2f}")
        print(f"Price Change: {results['price_change']:.2f} ({results['price_change_pct']:.2f}%)")
        print(f"Volatility: {results['volatility']:.2f}")
        print(f"Price Range: {results['min_price']:.2f} - {results['max_price']:.2f}")
        
        print(f"\nStop Loss Analysis:")
        print(f"CE SL Price: {results['ce_sl_price']:.2f} - {'HIT' if ce_sl_hit else 'NOT HIT'}")
        print(f"PE SL Price: {results['pe_sl_price']:.2f} - {'HIT' if pe_sl_hit else 'NOT HIT'}")
        
        if ce_sl_hit or pe_sl_hit:
            print("⚠️  Individual stop loss triggered - Strategy would apply supertrend logic")
        else:
            print("✅ No individual stop losses hit - Strategy continues normally")
        
        # Simulate supertrend calculation (dummy implementation)
        supertrend_val = results['end_price'] * 0.97  # 3% below current price
        
        print(f"\nSupertrend Analysis:")
        print(f"Current Price: {results['end_price']:.2f}")
        print(f"Supertrend Value: {supertrend_val:.2f}")
        
        if results['end_price'] > supertrend_val:
            print("Price above supertrend - Continue with position")
        else:
            print("Price below supertrend - Tighten stop loss")
        
        self.results['banknifty_strategy'] = results
        return results


def run_custom_data_backtest():
    """Run backtest with custom data format"""
    # data_file = '/Users/chandreshkumar/Desktop/code/fin-tests/data/raw/sample_data.csv'
    
    # Check if data file exists
    if not os.path.exists(data_file):
        print(f"Data file not found: {data_file}")
        return
    
    # Create and run backtest
    runner = LeanBacktestRunner(data_file)
    
    # Test data parsing
    print("=== Data Format Validation ===")
    parser = DataParser(data_file)
    data = parser.load_data()
    symbols = parser.get_symbols()
    
    print(f"✓ Loaded {len(data)} rows")
    print(f"✓ Found {len(symbols)} symbols: {symbols}")
    
    for symbol in symbols:
        symbol_data = parser.get_symbol_data(symbol)
        lean_format = parser.to_lean_format(symbol)
        print(f"✓ {symbol}: {len(symbol_data)} bars, Lean format ready")
    
    print("\n" + "="*50)
    
    # Run BankNifty strategy
    runner.run_banknifty_option_strategy()
    
    print("\n" + "="*50)
    print("✅ Backtest completed successfully!")
    print("\nTo run with your own data:")
    print("1. Replace sample_data.csv with your CSV file")
    print("2. Ensure it follows the format: Ticker,Date,Time,Open,High,Low,Close,Volume,Open Interest")
    print("3. Update the data file path in this script")
    print("4. Run: python run_backtest.py")


if __name__ == "__main__":
    run_custom_data_backtest()
