# Bank Nifty SuperTrend Live Trading with AngelOne

This implementation provides live trading capabilities for the Bank Nifty SuperTrend options strategy using AngelOne's SmartAPI.

## Features

- **Real-time market data** via AngelOne WebSocket
- **Automatic order placement** for short straddles
- **Multi-level stop-loss system** (individual, combined, trailing)
- **SuperTrend-based** position management
- **Risk management** with configurable parameters
- **Comprehensive logging** and monitoring
- **Dry-run mode** for testing without real trades

## Setup Instructions

### 1. Install Dependencies

```bash
pip install -r requirements_live.txt
```

### 2. Configure API Credentials

1. Copy the example configuration file:
```bash
cp config.env.example config.env
```

2. Edit `config.env` with your AngelOne credentials:
```bash
# AngelOne API Credentials
ANGELONE_API_KEY=your_actual_api_key
ANGELONE_USERNAME=your_client_code
ANGELONE_PASSWORD=your_password
ANGELONE_TOTP_SECRET=your_totp_secret_key
```

### 3. Get AngelOne API Credentials

1. **API Key**: Login to AngelOne > Profile > My Profile > API
2. **Username**: Your AngelOne client code
3. **Password**: Your AngelOne login password
4. **TOTP Secret**: Enable 2FA and get the secret key for programmatic access

## Strategy Configuration

### Trading Parameters
```bash
ENTRY_TIME=09:19:00          # Strategy entry time
EXIT_TIME=15:15:00           # Strategy exit time (EOD)
INDIVIDUAL_SL_PCT=0.15       # 15% stop-loss on individual legs
COMBINED_SL_PCT=0.25         # 25% stop-loss on combined premium
TRAILING_SL_PCT=0.10         # 10% trailing stop-loss
LOT_SIZE=15                  # Bank Nifty lot size
```

### SuperTrend Parameters
```bash
SUPERTREND_PERIOD=10         # SuperTrend period
SUPERTREND_MULTIPLIER=3      # SuperTrend multiplier
```

## Usage

### Basic Usage
```bash
python run_live_trading.py
```

### Test Mode (Dry Run)
```bash
python run_live_trading.py --dry-run
```

### Verbose Logging
```bash
python run_live_trading.py --verbose
```

### Custom Configuration
```bash
python run_live_trading.py --config production.env
```

## Strategy Logic

### Entry Conditions
1. **Time**: Between 09:19 AM (after market opening volatility)
2. **Position**: Sell ATM Call and Put options (short straddle)
3. **Strike Selection**: Nearest to Bank Nifty spot price (rounded to 100)

### Exit Conditions

#### Individual Stop-Loss
- **CE Stop-Loss**: Triggered when CE premium rises by 15%
- **PE Stop-Loss**: Triggered when PE premium rises by 15%
- **Action**: Exit the triggered leg, activate trailing SL for remaining leg

#### Combined Stop-Loss
- **Trigger**: When total premium (CE + PE) rises by 25%
- **Action**: Exit both legs immediately

#### Trailing Stop-Loss
- **Activation**: After one leg is stopped out
- **Logic**: Trail the remaining position based on SuperTrend signals
- **Exit**: When price crosses SuperTrend or trailing SL is hit

#### End-of-Day Exit
- **Time**: 15:15 PM (before market close)
- **Action**: Exit all remaining positions

## File Structure

```
src/aseems's-strategy-implementation/
├── angelone_live_trading.py      # Main trading engine
├── run_live_trading.py           # Execution script
├── config.env.example           # Configuration template
├── requirements_live.txt         # Python dependencies
├── README_LIVE_TRADING.md       # This documentation
└── logs/                        # Trading logs (auto-created)
    └── trading_YYYYMMDD.log     # Daily log files
```

## Classes and Components

### AngelOneConnection
- Handles API authentication and connection
- Manages TOTP-based login
- Provides market data access

### SuperTrendIndicator
- Real-time SuperTrend calculation
- Streaming update method for live data
- Trend detection (uptrend/downtrend)

### BankNiftyLiveTrader
- Main strategy execution engine
- Position and order management
- Stop-loss monitoring and execution
- WebSocket data processing

### TradingManager
- Orchestrates the trading session
- Pre-flight checks and validation
- Error handling and graceful shutdown
- Configuration management

## Risk Management

### Position Limits
- Maximum 2 positions (1 CE + 1 PE)
- Fixed lot size per trade
- No position scaling or averaging

### Stop-Loss System
1. **Individual**: 15% on each leg
2. **Combined**: 25% on total premium
3. **Trailing**: 10% dynamic trail based on SuperTrend

### Daily Limits
- Configurable maximum daily loss
- Automatic shutdown on breach
- Position-wise P&L tracking

## Logging and Monitoring

### Log Files
- Daily log files in `logs/` directory
- Timestamp-based file naming
- Configurable log levels (INFO/DEBUG)

### Log Contents
- Strategy signals and decisions
- Order placements and executions
- Stop-loss triggers and exits
- Market data updates
- Error messages and warnings

## Important Notes

### Market Hours
- Strategy runs during NSE hours: 09:15 AM - 03:30 PM
- Entry restricted to: 09:19 AM - 09:25 AM window
- Mandatory exit by: 03:15 PM

### Weekly Expiry
- Uses current week's Thursday expiry
- Automatically calculates next expiry if Thursday passed
- Symbol format: BANKNIFTYXXMMMYYCE/PE

### Risk Warnings
1. **Real Money Trading**: This system places actual trades with real money
2. **Market Risk**: Options trading involves significant risk of loss
3. **System Risk**: Technology failures can result in losses
4. **Backtesting vs Live**: Live results may differ from backtests

### Recommended Usage
1. **Test First**: Always use `--dry-run` mode for testing
2. **Small Size**: Start with minimum lot size
3. **Monitor Closely**: Supervise the system during operation
4. **Have Backup**: Manual override capability should be available

## Troubleshooting

### Common Issues

#### Authentication Failures
- Verify API credentials in config.env
- Check TOTP secret key accuracy
- Ensure 2FA is enabled on AngelOne account

#### WebSocket Connection Issues
- Check internet connectivity
- Verify AngelOne API service status
- Restart the application if connection drops

#### Order Placement Failures
- Verify sufficient account balance
- Check margin requirements for options
- Ensure market is open and liquid

#### Data Feed Issues
- Validate instrument tokens
- Check if contracts are actively traded
- Verify market data subscriptions

### Support
For issues related to:
- **AngelOne API**: Contact AngelOne support
- **Strategy Logic**: Review the original backtesting code
- **System Issues**: Check logs for detailed error messages

## Disclaimer

This software is for educational and research purposes. Trading involves substantial risk of loss. Users are responsible for:
- Understanding the strategy and its risks
- Verifying the code before live usage
- Managing their trading capital responsibly
- Compliance with applicable regulations

The authors are not responsible for any financial losses incurred through the use of this software.