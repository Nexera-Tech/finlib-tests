"""
AngelOne SmartAPI Live Trading Integration for Bank Nifty Options Strategy
==========================================================================
This module provides live trading capabilities for the SuperTrend-based option
selling strategy using AngelOne's SmartAPI.

Requirements:
    pip install smartapi-python websocket-client pandas numpy pyotp

Features:
    - Real-time market data via WebSocket
    - Automatic order placement and management
    - Stop-loss and trailing stop-loss implementation
    - Position tracking and P&L monitoring
"""

import json
import logging
import threading
import time
from datetime import datetime, time as dt_time, timedelta
from typing import Dict, List, Optional, Tuple
import os
import sys

import numpy as np
import pandas as pd
import pyotp
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

# Import the working SuperTrend and Nautilus strategy from the backtester
from strategy_nautilus_version import SuperTrend, BankNiftySuperTrendOptionSell

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SuperTrendIndicator:
    """SuperTrend indicator for real-time calculation."""
    
    def __init__(self, period: int = 10, multiplier: float = 3):
        self.period = period
        self.multiplier = multiplier
        self.high_buffer = []
        self.low_buffer = []
        self.close_buffer = []
        self.atr_buffer = []
        self.supertrend_value = None
        self.trend = None  # 1 for uptrend, -1 for downtrend
        
    def update(self, high: float, low: float, close: float) -> Tuple[Optional[float], Optional[int]]:
        """Update indicator with new OHLC data."""
        self.high_buffer.append(high)
        self.low_buffer.append(low)
        self.close_buffer.append(close)
        
        # Keep only required history
        if len(self.high_buffer) > self.period:
            self.high_buffer.pop(0)
            self.low_buffer.pop(0)
            self.close_buffer.pop(0)
        
        if len(self.close_buffer) < 2:
            return None, None
            
        # Calculate True Range
        tr = max(
            high - low,
            abs(high - self.close_buffer[-2]) if len(self.close_buffer) > 1 else high - low,
            abs(low - self.close_buffer[-2]) if len(self.close_buffer) > 1 else 0
        )
        
        self.atr_buffer.append(tr)
        if len(self.atr_buffer) > self.period:
            self.atr_buffer.pop(0)
            
        if len(self.atr_buffer) < self.period:
            return None, None
            
        # Calculate ATR
        atr = sum(self.atr_buffer) / self.period
        
        # Calculate basic bands
        hl_avg = (high + low) / 2
        upper_band = hl_avg + (self.multiplier * atr)
        lower_band = hl_avg - (self.multiplier * atr)
        
        # Determine trend
        if self.supertrend_value is None:
            if close > hl_avg:
                self.supertrend_value = lower_band
                self.trend = 1
            else:
                self.supertrend_value = upper_band
                self.trend = -1
        else:
            if self.trend == 1:  # Uptrend
                self.supertrend_value = max(lower_band, self.supertrend_value)
                if close <= self.supertrend_value:
                    self.supertrend_value = upper_band
                    self.trend = -1
            else:  # Downtrend
                self.supertrend_value = min(upper_band, self.supertrend_value)
                if close >= self.supertrend_value:
                    self.supertrend_value = lower_band
                    self.trend = 1
                    
        return self.supertrend_value, self.trend


class AngelOneConnection:
    """Manages AngelOne SmartAPI connection and authentication."""
    
    def __init__(self, api_key: str, username: str, password: str, totp_secret: str):
        self.api_key = api_key
        self.username = username
        self.password = password
        self.totp_secret = totp_secret
        self.smart_api = None
        self.auth_token = None
        self.refresh_token = None
        self.feed_token = None
        self.user_profile = None
        
    def connect(self) -> bool:
        """Establish connection to AngelOne."""
        try:
            self.smart_api = SmartConnect(api_key=self.api_key)
            
            # Generate TOTP
            totp = pyotp.TOTP(self.totp_secret)
            totp_code = totp.now()
            
            # Login
            data = self.smart_api.generateSession(
                clientCode=self.username,
                password=self.password,
                totp=totp_code
            )
            
            if data['status']:
                self.auth_token = data['data']['jwtToken']
                self.refresh_token = data['data']['refreshToken']
                self.feed_token = self.smart_api.getfeedToken()
                self.user_profile = self.smart_api.getProfile(self.refresh_token)
                
                logger.info(f"Successfully connected to AngelOne for user: {self.username}")
                return True
            else:
                logger.error(f"Failed to connect: {data['message']}")
                return False
                
        except Exception as e:
            logger.error(f"Connection error: {str(e)}")
            return False
            
    def get_ltp(self, exchange: str, symbol: str, token: str) -> Optional[float]:
        """Get Last Traded Price for a symbol."""
        try:
            ltp_data = self.smart_api.ltpData(exchange, symbol, token)
            if ltp_data['status']:
                return float(ltp_data['data']['ltp'])
        except Exception as e:
            logger.error(f"Error fetching LTP: {str(e)}")
        return None


class BankNiftyLiveTrader:
    """Live trading implementation using the Nautilus strategy."""
    
    def __init__(self, connection: AngelOneConnection, config: Dict):
        self.connection = connection
        self.config = config
        self.ws = None
        self.ws_thread = None
        
        # Initialize the Nautilus strategy
        self.strategy = BankNiftySuperTrendOptionSell(config)
        
        # Mock logger for strategy
        class LiveLogger:
            def info(self, msg): logger.info(f"[STRATEGY] {msg}")
            def error(self, msg): logger.error(f"[STRATEGY] {msg}")
        self.strategy.log = LiveLogger()
        
        # Market data and tokens
        self.spot_price = None
        self.atm_strike = None
        self.ce_token = None
        self.pe_token = None
        self.ce_symbol = None
        self.pe_symbol = None
        
        # Current market prices
        self.ce_ltp = None
        self.pe_ltp = None
        
        # Threading
        self.trading_active = False
        self.stop_flag = threading.Event()
        
    def get_atm_strike(self, spot_price: float) -> int:
        """Calculate ATM strike price."""
        return round(spot_price / 100) * 100
        
    def get_option_tokens(self, strike: int, expiry: str) -> Tuple[Optional[str], Optional[str]]:
        """Get instrument tokens for CE and PE options."""
        try:
            # Search for instruments
            # Format: BANKNIFTY28NOV24C54000
            ce_symbol = f"BANKNIFTY{expiry}C{strike}"
            pe_symbol = f"BANKNIFTY{expiry}P{strike}"
            
            # Get instrument list (this would need to be cached in production)
            instruments = self.connection.smart_api.searchScrip("NFO", ce_symbol)
            ce_token = instruments['data'][0]['symboltoken'] if instruments['status'] else None
            
            instruments = self.connection.smart_api.searchScrip("NFO", pe_symbol)
            pe_token = instruments['data'][0]['symboltoken'] if instruments['status'] else None
            
            return ce_token, pe_token
            
        except Exception as e:
            logger.error(f"Error getting option tokens: {str(e)}")
            return None, None
            
    def place_order(self, symbol: str, token: str, transaction_type: str, 
                   quantity: int, price: float = 0, order_type: str = "MARKET") -> Optional[str]:
        """Place an order with AngelOne."""
        try:
            order_params = {
                "variety": "NORMAL",
                "tradingsymbol": symbol,
                "symboltoken": token,
                "transactiontype": transaction_type,  # BUY or SELL
                "exchange": "NFO",
                "producttype": "INTRADAY",
                "ordertype": order_type,  # MARKET, LIMIT, SL, SL-M
                "quantity": quantity,
                "price": price if order_type == "LIMIT" else 0,
                "triggerprice": 0,
                "duration": "DAY"
            }
            
            response = self.connection.smart_api.placeOrder(order_params)
            
            if response['status']:
                order_id = response['data']['orderid']
                logger.info(f"Order placed successfully: {order_id} for {symbol}")
                return order_id
            else:
                logger.error(f"Order placement failed: {response['message']}")
                return None
                
        except Exception as e:
            logger.error(f"Error placing order: {str(e)}")
            return None
            
    def start_websocket(self):
        """Initialize and start WebSocket connection for live data."""
        try:
            correlation_id = "abc123"
            action = 1  # Subscribe
            mode = 3    # Full mode (LTP + market depth)
            
            # Token list for Bank Nifty spot and options
            token_list = [
                {"exchangeType": 2, "tokens": ["26009"]}  # Bank Nifty index
            ]
            
            # Add option tokens if available
            if self.ce_token:
                token_list.append({"exchangeType": 2, "tokens": [self.ce_token]})
            if self.pe_token:
                token_list.append({"exchangeType": 2, "tokens": [self.pe_token]})
            
            # Initialize WebSocket
            self.ws = SmartWebSocketV2(
                self.connection.auth_token,
                self.api_key,
                self.connection.username,
                self.connection.feed_token
            )
            
            # Set callbacks
            self.ws.on_open = self.on_ws_open
            self.ws.on_data = self.on_ws_data
            self.ws.on_error = self.on_ws_error
            self.ws.on_close = self.on_ws_close
            
            # Connect
            self.ws.connect()
            
            # Subscribe to tokens
            self.ws.subscribe(correlation_id, mode, token_list)
            
            logger.info("WebSocket connection established")
            
        except Exception as e:
            logger.error(f"WebSocket initialization error: {str(e)}")
            
    def on_ws_open(self, ws):
        """WebSocket open callback."""
        logger.info("WebSocket opened")
        
    def on_ws_data(self, ws, data):
        """Process incoming market data."""
        try:
            # Parse market data
            parsed_data = json.loads(data)
            token = parsed_data.get('token')
            ltp = float(parsed_data.get('ltp', 0))
            
            # Update spot price if Bank Nifty data
            if token == '26009':
                self.spot_price = ltp
                
                # Update SuperTrend if we have OHLC data
                if all(k in parsed_data for k in ['high', 'low', 'close']):
                    st_value = self.strategy.supertrend.update(
                        float(parsed_data['high']),
                        float(parsed_data['low']),
                        float(parsed_data['close'])
                    )
                    
                    # Update trailing stops if needed
                    if (self.strategy.trailing_sl_active and st_value and 
                        self.ce_ltp is not None and self.pe_ltp is not None):
                        # Determine trend (simplified)
                        trend = 1 if ltp > st_value else -1
                        self.strategy.update_trailing_stops(st_value, trend, self.ce_ltp, self.pe_ltp)
                        
            # Update option prices
            elif token == self.ce_token:
                self.ce_ltp = ltp
            elif token == self.pe_token:
                self.pe_ltp = ltp
                
            # Check stop losses if we have both option prices and positions are active
            if (self.strategy.positions_entered and 
                self.ce_ltp is not None and self.pe_ltp is not None):
                self.check_stop_losses()
                
        except Exception as e:
            logger.error(f"Error processing market data: {str(e)}")
            
    def on_ws_error(self, ws, error):
        """WebSocket error callback."""
        logger.error(f"WebSocket error: {error}")
        
    def on_ws_close(self, ws):
        """WebSocket close callback."""
        logger.info("WebSocket closed")
        
    def check_stop_losses(self):
        """Monitor and trigger stop-losses using Nautilus strategy."""
        current_time = datetime.now().time()
        
        # Skip if outside trading hours
        if current_time < self.strategy.entry_time or current_time > self.strategy.exit_time:
            return
            
        # Use strategy's stop-loss logic
        exit_reason = self.strategy.check_stop_losses(self.ce_ltp, self.pe_ltp)
        
        if exit_reason:
            logger.info(f"Stop-loss triggered: {exit_reason}")
            
            # Execute the actual orders based on what the strategy decided
            if exit_reason == "CE_SL":
                self.execute_ce_exit(self.ce_ltp, "Individual SL")
            elif exit_reason == "PE_SL":
                self.execute_pe_exit(self.pe_ltp, "Individual SL")
            elif exit_reason == "COMBINED_SL":
                self.execute_all_exit(self.ce_ltp, self.pe_ltp, "Combined SL")
            elif exit_reason == "CE_TRAILING_SL":
                self.execute_ce_exit(self.ce_ltp, "Trailing SL")
            elif exit_reason == "PE_TRAILING_SL":
                self.execute_pe_exit(self.pe_ltp, "Trailing SL")
                    
    def enter_positions(self):
        """Enter short straddle positions at entry time."""
        try:
            # Get current spot price
            self.spot_price = self.connection.get_ltp("NSE", "BANKNIFTY", "26009")
            if not self.spot_price:
                logger.error("Could not fetch Bank Nifty spot price")
                return
                
            # Calculate ATM strike
            self.atm_strike = self.get_atm_strike(self.spot_price)
            logger.info(f"Bank Nifty Spot: {self.spot_price}, ATM Strike: {self.atm_strike}")
            
            # Get current expiry (weekly - every Thursday)
            today = datetime.now()
            days_until_thursday = (3 - today.weekday()) % 7
            if days_until_thursday == 0 and today.hour >= 15:
                days_until_thursday = 7
            expiry_date = today + timedelta(days=days_until_thursday)
            expiry = expiry_date.strftime("%d%b%y").upper()
            
            # Get option tokens
            self.ce_token, self.pe_token = self.get_option_tokens(self.atm_strike, expiry)
            
            if not self.ce_token or not self.pe_token:
                logger.error("Could not fetch option tokens")
                return
                
            # Build option symbols
            self.ce_symbol = f"BANKNIFTY{expiry}C{self.atm_strike}"
            self.pe_symbol = f"BANKNIFTY{expiry}P{self.atm_strike}"
            
            # Get option prices
            ce_price = self.connection.get_ltp("NFO", self.ce_symbol, self.ce_token)
            pe_price = self.connection.get_ltp("NFO", self.pe_symbol, self.pe_token)
            
            if not ce_price or not pe_price:
                logger.error("Could not fetch option prices")
                return
                
            # Initialize strategy with current prices
            self.strategy.enter_positions(self.spot_price, ce_price, pe_price)
            
            # Place actual sell orders
            lot_size = self.config.get('lot_size', 15)
            
            ce_order_id = self.place_order(
                self.ce_symbol, self.ce_token, "SELL", 
                lot_size, ce_price
            )
            
            pe_order_id = self.place_order(
                self.pe_symbol, self.pe_token, "SELL",
                lot_size, pe_price
            )
            
            if ce_order_id and pe_order_id:
                logger.info(f"Orders placed successfully - CE: {ce_order_id}, PE: {pe_order_id}")
                
                # Store current prices for monitoring
                self.ce_ltp = ce_price
                self.pe_ltp = pe_price
                
                # Start WebSocket for live monitoring
                self.start_websocket()
                
        except Exception as e:
            logger.error(f"Error entering positions: {str(e)}")
            
    def execute_ce_exit(self, exit_price: float, reason: str):
        """Execute CE position exit order."""
        if self.strategy.ce_position:
            lot_size = self.config.get('lot_size', 15)
            order_id = self.place_order(
                self.ce_symbol,
                self.ce_token,
                "BUY",
                lot_size
            )
            
            if order_id:
                logger.info(f"CE exit order placed: {order_id} at {exit_price:.2f} - {reason}")
                
    def execute_pe_exit(self, exit_price: float, reason: str):
        """Execute PE position exit order."""
        if self.strategy.pe_position:
            lot_size = self.config.get('lot_size', 15)
            order_id = self.place_order(
                self.pe_symbol,
                self.pe_token,
                "BUY",
                lot_size
            )
            
            if order_id:
                logger.info(f"PE exit order placed: {order_id} at {exit_price:.2f} - {reason}")
                
    def execute_all_exit(self, ce_price: float, pe_price: float, reason: str):
        """Execute exit orders for all positions."""
        self.execute_ce_exit(ce_price, reason)
        self.execute_pe_exit(pe_price, reason)
        logger.info(f"All exit orders placed - {reason}")
        
    def exit_all_positions(self):
        """Exit all open positions at current market prices."""
        if self.ce_ltp and self.pe_ltp:
            self.strategy.exit_all_positions("Manual Exit", self.ce_ltp, self.pe_ltp)
            self.execute_all_exit(self.ce_ltp, self.pe_ltp, "Manual Exit")
        
    def run_strategy(self):
        """Main strategy execution loop using Nautilus strategy."""
        logger.info("Starting Bank Nifty SuperTrend strategy with Nautilus implementation...")
        self.trading_active = True
        
        # Start the strategy
        self.strategy.on_start()
        
        while self.trading_active and not self.stop_flag.is_set():
            current_time = datetime.now().time()
            
            # Check if it's time to enter positions
            if (current_time >= self.strategy.entry_time and 
                current_time < dt_time(9, 25) and
                not self.strategy.positions_entered):
                logger.info("Entry time reached - placing orders")
                self.enter_positions()
                
            # Check if it's exit time
            elif current_time >= self.strategy.exit_time:
                logger.info("Exit time reached - closing all positions")
                if self.ce_ltp and self.pe_ltp:
                    self.strategy.exit_all_positions("EOD Exit", self.ce_ltp, self.pe_ltp)
                    self.execute_all_exit(self.ce_ltp, self.pe_ltp, "EOD Exit")
                self.trading_active = False
                break
                
            # Check if strategy is complete
            elif self.strategy.strategy_complete:
                logger.info("Strategy completed")
                self.trading_active = False
                break
                
            # Sleep for a short interval
            time.sleep(1)
            
        # Stop the strategy
        self.strategy.on_stop()
        
        # Get and log trade summary
        trade_summary = self.strategy.get_trade_summary()
        logger.info(f"Final P&L: ₹{trade_summary['daily_pnl']:.2f}")
        logger.info(f"Total trades: {len(trade_summary['trade_log'])}")
        
        logger.info("Strategy execution completed")
        
    def stop(self):
        """Stop the trading strategy."""
        logger.info("Stopping strategy...")
        self.stop_flag.set()
        self.trading_active = False
        
        # Close all positions
        self.exit_all_positions()
        
        # Close WebSocket
        if self.ws:
            self.ws.close_connection()
            
        logger.info("Strategy stopped")


def main():
    """Main execution function."""
    
    # Load configuration from environment or config file
    config = {
        'api_key': os.getenv('ANGELONE_API_KEY', 'your_api_key'),
        'username': os.getenv('ANGELONE_USERNAME', 'your_client_code'),
        'password': os.getenv('ANGELONE_PASSWORD', 'your_password'),
        'totp_secret': os.getenv('ANGELONE_TOTP_SECRET', 'your_totp_secret'),
        
        # Trading parameters
        'entry_time': '09:19:00',
        'exit_time': '15:15:00',
        'individual_sl_pct': 0.15,
        'combined_sl_pct': 0.25,
        'trailing_sl_pct': 0.10,
        'lot_size': 15,
        
        # SuperTrend parameters
        'supertrend_period': 10,
        'supertrend_multiplier': 3
    }
    
    # Initialize connection
    connection = AngelOneConnection(
        config['api_key'],
        config['username'],
        config['password'],
        config['totp_secret']
    )
    
    # Connect to AngelOne
    if not connection.connect():
        logger.error("Failed to connect to AngelOne")
        return
        
    # Initialize trader
    trader = BankNiftyLiveTrader(connection, config)
    
    try:
        # Run strategy
        trader.run_strategy()
    except KeyboardInterrupt:
        logger.info("Strategy interrupted by user")
    except Exception as e:
        logger.error(f"Strategy error: {str(e)}")
    finally:
        trader.stop()
        

if __name__ == "__main__":
    main()