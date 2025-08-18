#!/usr/bin/env python3
"""
Bank Nifty SuperTrend Live Trading Script
=========================================
Main execution script for running the Bank Nifty options strategy
with AngelOne SmartAPI integration.

Usage:
    python run_live_trading.py [--config config.env] [--dry-run] [--verbose]

Example:
    python run_live_trading.py --config config.env --verbose
"""

import argparse
import logging
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Add the current directory to Python path
sys.path.append(str(Path(__file__).parent))

from angelone_live_trading import AngelOneConnection, BankNiftyLiveTrader


class TradingManager:
    """Manages the live trading session with proper error handling and logging."""
    
    def __init__(self, config_file: str = None, dry_run: bool = False, verbose: bool = False):
        self.config_file = config_file or 'config.env'
        self.dry_run = dry_run
        self.verbose = verbose
        self.trader = None
        self.connection = None
        
        # Setup logging
        self.setup_logging()
        
        # Load configuration
        self.config = self.load_config()
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
    def setup_logging(self):
        """Configure logging based on verbosity level."""
        log_level = logging.DEBUG if self.verbose else logging.INFO
        log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        
        # Create logs directory if it doesn't exist
        logs_dir = Path(__file__).parent / 'logs'
        logs_dir.mkdir(exist_ok=True)
        
        # Configure logging
        logging.basicConfig(
            level=log_level,
            format=log_format,
            handlers=[
                logging.FileHandler(logs_dir / f'trading_{datetime.now().strftime("%Y%m%d")}.log'),
                logging.StreamHandler(sys.stdout)
            ]
        )
        
        self.logger = logging.getLogger(__name__)
        
        if self.dry_run:
            self.logger.info("Running in DRY RUN mode - no actual trades will be placed")
            
    def load_config(self) -> dict:
        """Load configuration from environment file."""
        config_path = Path(__file__).parent / self.config_file
        
        if not config_path.exists():
            self.logger.error(f"Configuration file not found: {config_path}")
            self.logger.info("Please copy config.env.example to config.env and configure your credentials")
            sys.exit(1)
            
        # Load environment variables from file
        load_dotenv(config_path)
        
        # Validate required credentials
        required_keys = [
            'ANGELONE_API_KEY', 'ANGELONE_USERNAME', 
            'ANGELONE_PASSWORD', 'ANGELONE_TOTP_SECRET'
        ]
        
        missing_keys = [key for key in required_keys if not os.getenv(key)]
        
        if missing_keys:
            self.logger.error(f"Missing required configuration keys: {missing_keys}")
            sys.exit(1)
            
        # Build configuration dictionary
        config = {
            # API credentials
            'api_key': os.getenv('ANGELONE_API_KEY'),
            'username': os.getenv('ANGELONE_USERNAME'),
            'password': os.getenv('ANGELONE_PASSWORD'),
            'totp_secret': os.getenv('ANGELONE_TOTP_SECRET'),
            
            # Trading parameters
            'entry_time': os.getenv('ENTRY_TIME', '09:19:00'),
            'exit_time': os.getenv('EXIT_TIME', '15:15:00'),
            'individual_sl_pct': float(os.getenv('INDIVIDUAL_SL_PCT', '0.15')),
            'combined_sl_pct': float(os.getenv('COMBINED_SL_PCT', '0.25')),
            'trailing_sl_pct': float(os.getenv('TRAILING_SL_PCT', '0.10')),
            'lot_size': int(os.getenv('LOT_SIZE', '15')),
            
            # SuperTrend parameters
            'supertrend_period': int(os.getenv('SUPERTREND_PERIOD', '10')),
            'supertrend_multiplier': float(os.getenv('SUPERTREND_MULTIPLIER', '3')),
            
            # Risk management
            'max_daily_loss': float(os.getenv('MAX_DAILY_LOSS', '50000')),
            'max_positions': int(os.getenv('MAX_POSITIONS', '2')),
            
            # Dry run mode
            'dry_run': self.dry_run
        }
        
        self.logger.info("Configuration loaded successfully")
        self.logger.info(f"Entry time: {config['entry_time']}, Exit time: {config['exit_time']}")
        self.logger.info(f"Lot size: {config['lot_size']}, Max daily loss: ₹{config['max_daily_loss']:,.0f}")
        
        return config
        
    def pre_flight_checks(self) -> bool:
        """Perform pre-flight checks before starting trading."""
        self.logger.info("Performing pre-flight checks...")
        
        # Check market hours
        current_time = datetime.now()
        market_open = current_time.replace(hour=9, minute=15, second=0)
        market_close = current_time.replace(hour=15, minute=30, second=0)
        
        if current_time < market_open or current_time > market_close:
            self.logger.warning(f"Current time {current_time.time()} is outside market hours")
            if not self.dry_run:
                response = input("Continue anyway? (y/N): ")
                if response.lower() != 'y':
                    return False
                    
        # Check if it's a trading day (Monday-Friday, excluding holidays)
        if current_time.weekday() > 4:  # Saturday = 5, Sunday = 6
            self.logger.warning("Today appears to be a weekend")
            if not self.dry_run:
                response = input("Continue anyway? (y/N): ")
                if response.lower() != 'y':
                    return False
                    
        # Test API connection
        self.logger.info("Testing AngelOne API connection...")
        self.connection = AngelOneConnection(
            self.config['api_key'],
            self.config['username'], 
            self.config['password'],
            self.config['totp_secret']
        )
        
        if not self.connection.connect():
            self.logger.error("Failed to connect to AngelOne API")
            return False
            
        self.logger.info("✓ API connection successful")
        
        # Test market data access
        self.logger.info("Testing market data access...")
        spot_price = self.connection.get_ltp("NSE", "BANKNIFTY", "26009")
        
        if spot_price:
            self.logger.info(f"✓ Bank Nifty current price: ₹{spot_price:,.2f}")
        else:
            self.logger.error("Failed to fetch Bank Nifty price")
            return False
            
        self.logger.info("✓ All pre-flight checks passed")
        return True
        
    def run(self) -> bool:
        """Run the trading strategy."""
        try:
            # Perform pre-flight checks
            if not self.pre_flight_checks():
                self.logger.error("Pre-flight checks failed")
                return False
                
            # Initialize trader
            self.logger.info("Initializing trading strategy...")
            self.trader = BankNiftyLiveTrader(self.connection, self.config)
            
            # Display trading parameters
            self.logger.info("=" * 60)
            self.logger.info("BANK NIFTY SUPERTREND OPTION STRATEGY")
            self.logger.info("=" * 60)
            self.logger.info(f"Entry Time: {self.config['entry_time']}")
            self.logger.info(f"Exit Time: {self.config['exit_time']}")
            self.logger.info(f"Individual SL: {self.config['individual_sl_pct']*100}%")
            self.logger.info(f"Combined SL: {self.config['combined_sl_pct']*100}%")
            self.logger.info(f"Trailing SL: {self.config['trailing_sl_pct']*100}%")
            self.logger.info(f"Lot Size: {self.config['lot_size']} contracts")
            self.logger.info(f"SuperTrend: {self.config['supertrend_period']}, {self.config['supertrend_multiplier']}")
            self.logger.info("=" * 60)
            
            if self.dry_run:
                self.logger.info("DRY RUN MODE - No actual trades will be placed")
                
            # Start trading
            self.logger.info("Starting trading session...")
            self.trader.run_strategy()
            
            self.logger.info("Trading session completed successfully")
            return True
            
        except KeyboardInterrupt:
            self.logger.info("Trading interrupted by user")
            return True
            
        except Exception as e:
            self.logger.error(f"Trading session failed: {str(e)}")
            return False
            
        finally:
            self.cleanup()
            
    def cleanup(self):
        """Clean up resources and close connections."""
        self.logger.info("Cleaning up resources...")
        
        if self.trader:
            self.trader.stop()
            
        self.logger.info("Cleanup completed")
        
    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        
        if self.trader:
            self.trader.stop()
            
        sys.exit(0)


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Bank Nifty SuperTrend Live Trading",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python run_live_trading.py                    # Run with default config.env
    python run_live_trading.py --config prod.env  # Run with custom config file
    python run_live_trading.py --dry-run          # Run in simulation mode
    python run_live_trading.py --verbose          # Run with debug logging
        """
    )
    
    parser.add_argument(
        '--config',
        type=str,
        default='config.env',
        help='Configuration file path (default: config.env)'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Run in simulation mode without placing actual trades'
    )
    
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )
    
    args = parser.parse_args()
    
    # Create and run trading manager
    manager = TradingManager(
        config_file=args.config,
        dry_run=args.dry_run,
        verbose=args.verbose
    )
    
    success = manager.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()