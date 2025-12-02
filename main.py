from decimal import Decimal
from datetime import datetime, timezone
import os
import time
import requests
import csv
from dotenv import load_dotenv

# Load .env / Replit secrets
load_dotenv()

# Parse starting balance with error handling
try:
    balance_str = os.getenv("SIM_START_BALANCE_USD", "1000").strip()
    SIM_START_BALANCE_USD = Decimal(balance_str)
except Exception as e:
    print(f"Warning: Invalid SIM_START_BALANCE_USD value. Using default $1000.")
    SIM_START_BALANCE_USD = Decimal("1000")

CANDLE_GRANULARITY = "60"  # 60 seconds = 1 minute
SLEEP_SECONDS = 60  # 1 minute loop

# Coinbase Public API (no authentication required)
PUBLIC_API_BASE = "https://api.exchange.coinbase.com"

# Multi-crypto scanner configuration
WATCHLIST = ["BTC-USD", "ETH-USD", "SOL-USD", "AVAX-USD", "LINK-USD", "ADA-USD"]

# Portfolio state
usd_balance = SIM_START_BALANCE_USD
crypto_balance = Decimal("0")  # Amount of whatever crypto we're holding
current_position = None  # None = USD only, or "BTC-USD", "ETH-USD", etc.
entry_price = None  # Track price we bought at for profit calculations
start_time = datetime.now(timezone.utc)
trade_count = 0
total_trades = {"BUY": 0, "SELL": 0}

# Initialize trade log file
TRADE_LOG = "trade_history.csv"

# Create CSV file if it doesn't exist
if not os.path.exists(TRADE_LOG):
    with open(TRADE_LOG, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["Timestamp", "Pair", "Action", "Price", "Amount", "USD_Balance", "Crypto_Balance", "Portfolio_Value", "Profit_Loss"])
    print(f"Created new trade history file")
else:
    print(f"Using existing trade history file: {TRADE_LOG}")

print("="*60)
print("PAPER TRADING BOT - 100% SIMULATED")
print("="*60)
print("This bot ONLY simulates trades - it does NOT:")
print("  - Access your real Coinbase account")
print("  - Use any real money")
print("  - Place actual trades")
print("  + Only fetches public market prices")
print("  + Simulates trades in memory")
print("="*60)


def get_latest_price(pair):
    """Fetch latest price for any pair using public API (no auth required)"""
    url = f"{PUBLIC_API_BASE}/products/{pair}/ticker"
    response = requests.get(url)
    response.raise_for_status()
    data = response.json()
    price = Decimal(data["price"])
    return price


def get_recent_candles(pair, limit=60):
    """Fetch recent candles for any pair using public API (no auth required)"""
    end_time = int(time.time())
    start_time = end_time - (limit * 60)
    
    # Convert to ISO format for public API
    start_iso = datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat()
    end_iso = datetime.fromtimestamp(end_time, tz=timezone.utc).isoformat()
    
    url = f"{PUBLIC_API_BASE}/products/{pair}/candles"
    params = {
        "start": start_iso,
        "end": end_iso,
        "granularity": CANDLE_GRANULARITY
    }
    
    response = requests.get(url, params=params)
    response.raise_for_status()
    candles = response.json()
    
    # Candles format: [timestamp, low, high, open, close, volume]
    closes = [Decimal(str(candle[4])) for candle in candles]
    closes.reverse()  # Put in chronological order
    return closes


def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / Decimal(period)


def rsi(values, period=14):
    if len(values) <= period:
        return None
    gains = []
    losses = []
    for i in range(1, period + 1):
        diff = values[-i] - values[-i - 1]
        if diff > 0:
            gains.append(diff)
        else:
            losses.append(-diff)
    if not gains and not losses:
        return Decimal(50)
    avg_gain = sum(gains) / Decimal(period) if gains else Decimal(0)
    avg_loss = sum(losses) / Decimal(period) if losses else Decimal(0)
    if avg_loss == 0:
        return Decimal(100)
    rs = avg_gain / avg_loss
    return Decimal(100) - (Decimal(100) / (Decimal(1) + rs))


def calculate_volatility(closes, period=20):
    """
    Calculate price volatility as percentage of price range.
    Higher volatility = more price movement (better for trading).
    Returns None if not enough data, or volatility % otherwise.
    """
    if len(closes) < period:
        return None
    
    recent_prices = closes[-period:]
    high = max(recent_prices)
    low = min(recent_prices)
    avg = sum(recent_prices) / Decimal(period)
    
    # Volatility as percentage of average price
    volatility_pct = ((high - low) / avg) * 100
    return volatility_pct


def scan_best_opportunity():
    """
    Scan all pairs in watchlist and find the best trading opportunity.
    Returns: (best_pair, price, closes, score) or (None, None, None, None) if no data
    """
    opportunities = []
    
    for pair in WATCHLIST:
        try:
            closes = get_recent_candles(pair)
            price = get_latest_price(pair)
            
            short_ma = sma(closes, 9)
            long_ma = sma(closes, 21)
            current_rsi = rsi(closes, 14)
            
            if not short_ma or not long_ma or not current_rsi:
                continue
            
            # Score based on trend strength and RSI positioning
            # Higher score = better buy opportunity
            trend_strength = ((short_ma - long_ma) / long_ma) * 100  # % difference
            rsi_score = Decimal(60) - current_rsi  # Prefer lower RSI (more room to grow)
            
            # Combined score: strong uptrend + low RSI = best opportunity
            opportunity_score = trend_strength + (rsi_score / 10)
            
            opportunities.append({
                'pair': pair,
                'price': price,
                'closes': closes,
                'score': opportunity_score,
                'rsi': current_rsi,
                'trend': trend_strength
            })
            
        except Exception as e:
            print(f"Error scanning {pair}: {e}")
            continue
    
    if not opportunities:
        return None, None, None, None
    
    # Sort by score (highest = best opportunity)
    opportunities.sort(key=lambda x: x['score'], reverse=True)
    best = opportunities[0]
    
    print(f"\nSCANNER: Top 3 opportunities:")
    for i, opp in enumerate(opportunities[:3]):
        print(f"  {i+1}. {opp['pair']}: Score={float(opp['score']):.2f} | RSI={float(opp['rsi']):.1f} | Trend={float(opp['trend']):.2f}%")
    
    return best['pair'], best['price'], best['closes'], best['score']


def decide_action(price, closes):
    """
    Decide whether to BUY, SELL or HOLD based on:
    - 9 / 21 period moving averages (trend)
    - 14-period RSI (momentum)
    - Volatility filter (avoid choppy markets)
    - 3% quick profit target
    - 2% stop-loss (cut losses fast)
    - What we actually hold (USD or crypto)
    """

    # Use the global balances so our decisions depend on our position
    global usd_balance, crypto_balance, entry_price

    short_ma = sma(closes, 9)
    long_ma = sma(closes, 21)
    current_rsi = rsi(closes, 14)
    volatility = calculate_volatility(closes, 20)

    # If we don't have enough data yet, just do nothing
    if not short_ma or not long_ma or not current_rsi or volatility is None:
        return "HOLD"

    has_usd_only = usd_balance > Decimal("10") and crypto_balance < Decimal("0.000001")
    has_crypto = crypto_balance >= Decimal("0.000001")

    # ENTRY: from USD -> Crypto
    # Requirements:
    # 1. Uptrend (short MA above long MA)
    # 2. RSI < 65 (not overbought)
    # 3. Volatility > 1.5% (enough price movement to trade)
    if has_usd_only and short_ma > long_ma and current_rsi < Decimal("65"):
        # Volatility filter: only trade if there's enough price movement
        if volatility >= Decimal("1.5"):
            return "BUY"

    # EXIT: from Crypto -> USD
    # Four exit conditions (protective and profit-taking):
    # 1. Stop-loss: Down 2% from entry (cut losses fast)
    # 2. Quick profit target: Up 3% from entry
    # 3. Trend weakening: Short MA crosses below long MA
    # 4. Overbought: RSI above 65 (take profits sooner)
    if has_crypto:
        # Check stop-loss and profit target first
        if entry_price is not None:
            profit_pct = ((price - entry_price) / entry_price) * 100
            
            # Stop-loss: cut losses at -2%
            if profit_pct <= Decimal("-2"):
                return "SELL"
            
            # Profit target: take profits at +3%
            if profit_pct >= Decimal("3"):
                return "SELL"
        
        # Check technical indicators
        if short_ma < long_ma or current_rsi > Decimal("65"):
            return "SELL"

    # Otherwise, just sit tight
    return "HOLD"



def log_trade(timestamp, pair, action, price, amount, usd_bal, crypto_bal, pv, profit_loss):
    """Log trade to CSV file for historical analysis"""
    with open(TRADE_LOG, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([timestamp, pair, action, price, amount, usd_bal, crypto_bal, pv, profit_loss])
    
    # Trade history saved to local CSV file


def execute_paper_trade(action, price, pair):
    global usd_balance, crypto_balance, current_position, entry_price, trade_count, total_trades
    max_risk_fraction = Decimal("0.2")  # use 20% of USD per buy
    
    crypto_symbol = pair.split('-')[0]  # Extract "BTC" from "BTC-USD"

    if action == "BUY" and usd_balance > 10:
        usd_to_spend = usd_balance * max_risk_fraction
        crypto_to_buy = (usd_to_spend / price).quantize(Decimal("0.00000001"))
        if crypto_to_buy > 0:
            usd_balance -= usd_to_spend
            crypto_balance += crypto_to_buy
            current_position = pair
            entry_price = price  # Track entry price for profit calculation
            trade_count += 1
            total_trades["BUY"] += 1
            return f"BUY {crypto_to_buy} {crypto_symbol} @ {price}", crypto_to_buy

    if action == "SELL" and crypto_balance > Decimal("0.00001"):
        crypto_to_sell = (crypto_balance * Decimal("0.5")).quantize(Decimal("0.00000001"))
        if crypto_to_sell > 0:
            usd_gained = (crypto_to_sell * price).quantize(Decimal("0.01"))
            crypto_balance -= crypto_to_sell
            usd_balance += usd_gained
            if crypto_balance < Decimal("0.000001"):
                crypto_balance = Decimal("0")  # Zero out dust
                current_position = None  # Back to USD only
                entry_price = None  # Reset entry price when fully out
            trade_count += 1
            total_trades["SELL"] += 1
            return f"SELL {crypto_to_sell} {crypto_symbol} @ {price}", crypto_to_sell

    return None, None


def portfolio_value(price):
    return usd_balance + crypto_balance * price


def print_daily_summary(price, pair):
    """Print daily performance summary"""
    pv = portfolio_value(price)
    profit_loss = pv - SIM_START_BALANCE_USD
    profit_pct = (profit_loss / SIM_START_BALANCE_USD) * 100
    runtime = datetime.now(timezone.utc) - start_time
    
    position_str = "USD only"
    if current_position:
        crypto_symbol = current_position.split('-')[0]
        position_str = f"{crypto_balance:.8f} {crypto_symbol}"
    
    print("\n" + "="*70)
    print(f"DAILY SUMMARY - {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    print("="*70)
    print(f"Runtime: {runtime.days} days, {runtime.seconds//3600} hours")
    print(f"Starting Balance: ${SIM_START_BALANCE_USD}")
    print(f"Current Value: ${pv:.2f}")
    print(f"Profit/Loss: ${profit_loss:.2f} ({profit_pct:+.2f}%)")
    print(f"Total Trades: {trade_count} (Buy: {total_trades['BUY']}, Sell: {total_trades['SELL']})")
    print(f"Holdings: ${usd_balance:.2f} USD + {position_str}")
    print(f"Current Position: {current_position or 'None (all USD)'}")
    print(f"Watching: {', '.join(WATCHLIST)}")
    print("="*70 + "\n")


def main_loop():
    print(f"[INIT] Multi-crypto scanner starting with balance ${usd_balance}")
    print(f"Watching {len(WATCHLIST)} pairs: {', '.join(WATCHLIST)}")
    print(f"Trade history will be logged to: {TRADE_LOG}")
    print(f"You can track performance over time in this file.\n")
    
    last_summary_day = None
    
    while True:
