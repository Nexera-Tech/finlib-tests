"""
Configuration file for backtesting strategies
"""

import os

# Base paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
SRC_DIR = os.path.join(BASE_DIR, 'src')

# Data file paths
RAW_DATA_DIR = os.path.join(DATA_DIR, 'raw')
PROCESSED_DATA_DIR = os.path.join(DATA_DIR, 'processed')

# Sample data file
SAMPLE_DATA_FILE = os.path.join(RAW_DATA_DIR, 'sample_data.csv')

# Strategy parameters
STRATEGY_CONFIG = {
    'moving_average': {
        'fast_period': 5,
        'slow_period': 10,
        'initial_capital': 100000,
    },
    'supertrend': {
        'period': 10,
        'multiplier': 3.0,
        'individual_sl_pct': 0.15,
        'combined_sl_pct': 0.25,
    }
}

# Backtest settings
BACKTEST_CONFIG = {
    'start_date': '2020-10-30',
    'end_date': '2020-10-30',
    'commission': 0.001,  # 0.1%
    'slippage': 0.0001,   # 0.01%
}

# Output settings
RESULTS_DIR = os.path.join(BASE_DIR, 'results')
REPORTS_DIR = os.path.join(BASE_DIR, 'reports')

# Ensure directories exist
for directory in [RAW_DATA_DIR, PROCESSED_DATA_DIR, RESULTS_DIR, REPORTS_DIR]:
    os.makedirs(directory, exist_ok=True)
