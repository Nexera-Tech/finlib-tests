"""
Test script to verify data parsing and run basic backtests
"""

import sys
import os

# Add the common module to path

from src.common.data_parser import DataParser, data_file


def test_data_parsing():
    """Test the data parsing functionality"""
    print("=== Testing Data Parser ===")
    
    # data_file = '/Users/chandreshkumar/Desktop/code/fin-tests/data/raw/sample_data.csv'
    
    try:
        parser = DataParser(data_file)
        data = parser.load_data()
        
        print(f"✓ Loaded {len(data)} rows of data")
        print(f"✓ Available symbols: {parser.get_symbols()}")
        
        # Test each symbol
        for symbol in parser.get_symbols():
            symbol_data = parser.get_symbol_data(symbol)
            print(f"\n--- {symbol} ---")
            print(f"Rows: {len(symbol_data)}")
            print(f"Date range: {symbol_data.index.min()} to {symbol_data.index.max()}")
            print(f"Price range: {symbol_data['Close'].min():.2f} - {symbol_data['Close'].max():.2f}")
            
            # Show first few rows
            print("\nFirst 3 rows:")
            print(symbol_data[['Open', 'High', 'Low', 'Close', 'Volume']].head(3))
            
            # Test Lean format
            lean_format = parser.to_lean_format(symbol)
            print(f"\n✓ Lean format: {lean_format.shape}")
            
            # Test Nautilus format
            nautilus_format = parser.to_nautilus_format(symbol)
            print(f"✓ Nautilus format: {len(nautilus_format)} bars")
            print(f"  Sample bar: {nautilus_format[0]}")
            
    except Exception as e:
        print(f"✗ Error: {str(e)}")
        import traceback
        traceback.print_exc()


def simple_moving_average_backtest():
    """Run a simple moving average crossover backtest"""
    print("\n\n=== Simple Moving Average Backtest ===")
    
    # data_file = '/Users/chandreshkumar/Desktop/code/fin-tests/data/raw/sample_data.csv'
    
    try:
        parser = DataParser(data_file)
        
        # Use first symbol for testing
        symbols = parser.get_symbols()
        if not symbols:
            print("No symbols found")
            return
            
        symbol = symbols[0]
        print(f"Running backtest for: {symbol}")
        
        # Get data
        data = parser.get_symbol_data(symbol)
        
        # Calculate moving averages
        fast_period = 3
        slow_period = 5
        
        data['SMA_Fast'] = data['Close'].rolling(window=fast_period).mean()
        data['SMA_Slow'] = data['Close'].rolling(window=slow_period).mean()
        
        # Generate signals
        data['Signal'] = 0
        data.loc[data['SMA_Fast'] > data['SMA_Slow'], 'Signal'] = 1  # Buy
        data.loc[data['SMA_Fast'] < data['SMA_Slow'], 'Signal'] = -1  # Sell
        
        # Find signal changes
        data['Position'] = data['Signal'].diff()
        
        # Print results
        print(f"\nBacktest Results:")
        print(f"Total bars: {len(data)}")
        print(f"Buy signals: {len(data[data['Position'] == 2])}")  # 1 to 1 = 2
        print(f"Sell signals: {len(data[data['Position'] == -2])}")  # 1 to -1 = -2
        
        # Show signal points
        signals = data[data['Position'].abs() == 2].copy()
        if len(signals) > 0:
            print(f"\nSignal Details:")
            for idx, row in signals.iterrows():
                signal_type = "BUY" if row['Position'] > 0 else "SELL"
                print(f"{idx}: {signal_type} at {row['Close']:.2f} "
                      f"(Fast: {row['SMA_Fast']:.2f}, Slow: {row['SMA_Slow']:.2f})")
        
        # Simple P&L calculation (assuming we buy/sell 1 unit each time)
        if len(signals) >= 2:
            trades = []
            position = 0
            entry_price = 0
            
            for idx, row in signals.iterrows():
                if row['Position'] > 0 and position == 0:  # Buy signal
                    position = 1
                    entry_price = row['Close']
                    print(f"ENTER LONG at {entry_price:.2f}")
                elif row['Position'] < 0 and position == 1:  # Sell signal
                    exit_price = row['Close']
                    pnl = exit_price - entry_price
                    trades.append(pnl)
                    position = 0
                    print(f"EXIT LONG at {exit_price:.2f}, P&L: {pnl:.2f}")
            
            if trades:
                total_pnl = sum(trades)
                avg_pnl = total_pnl / len(trades)
                print(f"\nTotal P&L: {total_pnl:.2f}")
                print(f"Average P&L per trade: {avg_pnl:.2f}")
                print(f"Number of completed trades: {len(trades)}")
        
    except Exception as e:
        print(f"✗ Error: {str(e)}")
        import traceback
        traceback.print_exc()


def run_nautilus_mock_backtest():
    """Run the Nautilus mock backtest"""
    print("\n\n=== Nautilus Mock Backtest ===")
    
    try:
        # Import and run the Nautilus strategy
        from src.nautilus.custom_data_strategy import run_strategy_backtest
        
        run_strategy_backtest()
        
    except Exception as e:
        print(f"✗ Error: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # Run all tests
    test_data_parsing()
    simple_moving_average_backtest()
    run_nautilus_mock_backtest()
