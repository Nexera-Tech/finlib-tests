"""
Common data parser for CSV files with the format:
Ticker,Date,Time,Open,High,Low,Close,Volume,Open Interest
"""

import pandas as pd
from datetime import datetime
from typing import Dict, List, Any

data_file = '/Users/chandreshkumar/Downloads/OCT_2020/GFDLCM_INDICES_30102020.csv'

class DataParser:
    """Parses CSV data files in the specified format"""
    
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.data = None
        
    def load_data(self) -> pd.DataFrame:
        """Load and parse the CSV data"""
        self.data = pd.read_csv(self.file_path)
        
        # Combine Date and Time columns into a single datetime
        self.data['DateTime'] = pd.to_datetime(
            self.data['Date'] + ' ' + self.data['Time'],
            format='%d/%m/%Y %H:%M:%S'
        )
        
        # Set DateTime as index
        self.data.set_index('DateTime', inplace=True)
        
        # Convert numeric columns
        numeric_cols = ['Open', 'High', 'Low', 'Close', 'Volume', 'Open Interest']
        for col in numeric_cols:
            self.data[col] = pd.to_numeric(self.data[col], errors='coerce')
        
        return self.data
    
    def get_symbols(self) -> List[str]:
        """Get unique symbols from the data"""
        if self.data is None:
            self.load_data()
        return self.data['Ticker'].unique().tolist()
    
    def get_symbol_data(self, symbol: str) -> pd.DataFrame:
        """Get data for a specific symbol"""
        if self.data is None:
            self.load_data()
        return self.data[self.data['Ticker'] == symbol].copy()
    
    def to_lean_format(self, symbol: str) -> pd.DataFrame:
        """Convert to format suitable for Lean"""
        symbol_data = self.get_symbol_data(symbol)
        
        # Lean expects columns: open, high, low, close, volume
        lean_data = symbol_data[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
        lean_data.columns = ['open', 'high', 'low', 'close', 'volume']
        
        return lean_data
    
    def to_nautilus_format(self, symbol: str) -> List[Dict[str, Any]]:
        """Convert to format suitable for Nautilus"""
        symbol_data = self.get_symbol_data(symbol)
        
        bars = []
        for idx, row in symbol_data.iterrows():
            bar = {
                'timestamp': idx.timestamp() * 1_000_000_000,  # Nautilus expects nanoseconds
                'open': float(row['Open']),
                'high': float(row['High']),
                'low': float(row['Low']),
                'close': float(row['Close']),
                'volume': int(row['Volume']) if row['Volume'] > 0 else 1,  # Avoid zero volume
            }
            bars.append(bar)
        
        return bars


def test_parser():
    """Test the data parser with sample data"""
    # data_file = '/Users/chandreshkumar/Desktop/code/fin-tests/data/raw/sample_data.csv'
    parser = DataParser(data_file)
    
    # Load data
    data = parser.load_data()
    print(f"Loaded {len(data)} rows")
    print(f"Symbols: {parser.get_symbols()}")
    
    # Test symbol-specific data
    for symbol in parser.get_symbols()[:2]:  # Test first 2 symbols
        symbol_data = parser.get_symbol_data(symbol)
        print(f"\n{symbol}: {len(symbol_data)} rows")
        print(symbol_data.head(3))
        
        # Test Lean format
        lean_format = parser.to_lean_format(symbol)
        print(f"\nLean format for {symbol}:")
        print(lean_format.head(2))
        
        # Test Nautilus format
        nautilus_format = parser.to_nautilus_format(symbol)
        print(f"\nNautilus format for {symbol} (first 2 bars):")
        for bar in nautilus_format[:2]:
            print(bar)


if __name__ == "__main__":
    test_parser()
