import robin_stocks.robinhood as rh
from openai import OpenAI
from datetime import datetime
import time
import pandas as pd
import numpy as np
import json
import re
from pytz import timezone
from config import *


# Initialize session and login
openai_client = OpenAI(api_key=OPENAI_API_KEY)
rh.login(ROBINHOOD_USERNAME, ROBINHOOD_PASSWORD)


# Print log message
def log(level, msg):
    log_levels = {"DEBUG": 1, "INFO": 2, "WARNING": 3, "ERROR": 4}
    level_color_codes = {
        "DEBUG": "\033[94m",
        "INFO": "\033[92m",
        "WARNING": "\033[93m",
        "ERROR": "\033[91m"
    }
    timestamp_color_code = "\033[96m"
    reset_color_code = "\033[0m"
    if log_levels.get(level, 2) >= log_levels.get(LOG_LEVEL, 2):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        level_space = " " * (8 - len(level))
        print(f"{timestamp_color_code}[{timestamp}] {level_color_codes[level]}[{level}]{reset_color_code}{level_space}{msg}")


# Print debug log message
def log_debug(msg):
    log("DEBUG", msg)


# Print info log message
def log_info(msg):
    log("INFO", msg)


# Print warning log message
def log_warning(msg):
    log("WARNING", msg)


# Print error log message
def log_error(msg):
    log("ERROR", msg)


# Run a Robinhood function with retries and delay between attempts (to handle rate limits)
def rh_run_with_retries(func, *args, max_retries=3, delay=60, **kwargs):
    for attempt in range(max_retries):
        result = func(*args, **kwargs)
        log_debug(f"Function: {func.__name__}, Parameters: {args}, Attempt: {attempt + 1}, Result: {result}")
        if result is not None:
            return result
        log_debug(f"Function: {func.__name__}, Parameters: {args}, Attempt: {attempt + 1}, Retrying in {delay} seconds...")
        time.sleep(delay)
    return None


# Check if the market is open
def is_market_open():
    # market_hours = run_with_retries(rh.get_market_hours, MARKET_MIC, datetime.now().strftime('%Y-%m-%d'))
    eastern = timezone('US/Eastern')
    now = datetime.now(eastern)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


# Round money
def round_money(price, decimals=2):
    if price is None:
        return None
    return round(float(price), decimals)


# Round quantity
def round_quantity(quantity, decimals=6):
    if quantity is None:
        return None
    return round(float(quantity), decimals)


# Calculate moving averages for stock prices
def calculate_moving_averages(prices, short_window=50, long_window=200):
    short_mavg = pd.Series(prices).rolling(window=short_window).mean().iloc[-1]
    long_mavg = pd.Series(prices).rolling(window=long_window).mean().iloc[-1]
    return short_mavg, long_mavg


# Extract data from my stocks
def extract_my_stocks_data(stock_data):
    return {
        "price": round_money(stock_data['price']),
        "quantity": round_quantity(stock_data['quantity']),
        "average_buy_price": round_money(stock_data['average_buy_price']),
    }


# Extract data from watchlist stocks
def extract_watchlist_data(stock_data):
    return {
        "price": round_money(stock_data['price']),
    }


# Extract sell response data
def extract_sell_response_data(sell_resp):
    return {
        "quantity": round_quantity(sell_resp['quantity']),
        "price": round_money(sell_resp['price']),
    }


# Extract buy response data
def extract_buy_response_data(buy_resp):
    return {
        "quantity": round_quantity(buy_resp['quantity']),
        "price": round_money(buy_resp['price']),
    }


# Enrich stock data with moving averages
def enrich_with_moving_averages(stock_data, symbol):
    prices = get_historical_data(symbol)
    if len(prices) >= 200:
        moving_avg_50, moving_avg_200 = calculate_moving_averages(prices)
        stock_data["50_day_mavg_price"] = round_money(moving_avg_50)
        stock_data["200_day_mavg_price"] = round_money(moving_avg_200)
    return stock_data


# Get analyst ratings for a stock by symbol
def enrich_with_analyst_ratings(stock_data, symbol):
    ratings = get_ratings(symbol)
    if 'ratings' in ratings and len(ratings['ratings']) > 0:
        last_sell_rating = next((rating for rating in ratings['ratings'] if rating['type'] == "sell"), None)
        last_buy_rating = next((rating for rating in ratings['ratings'] if rating['type'] == "buy"), None)
        if last_sell_rating:
            stock_data["robinhood_analyst_sell_opinion"] = last_sell_rating['text'].decode('utf-8')
        if last_buy_rating:
            stock_data["robinhood_analyst_buy_opinion"] = last_buy_rating['text'].decode('utf-8')
    if 'summary' in ratings and ratings['summary']:
        summary = ratings['summary']
        total_ratings = sum([summary['num_buy_ratings'], summary['num_hold_ratings'], summary['num_sell_ratings']])
        if total_ratings > 0:
            buy_percent = summary['num_buy_ratings'] / total_ratings * 100
            sell_percent = summary['num_sell_ratings'] / total_ratings * 100
            hold_percent = summary['num_hold_ratings'] / total_ratings * 100
            stock_data["robinhood_analyst_summary_distribution"] = f"sell: {sell_percent:.0f}%, buy: {buy_percent:.0f}%, hold: {hold_percent:.0f}%"
    return stock_data


# Get my buying power
def get_buying_power():
    resp = rh_run_with_retries(rh.profiles.load_account_profile)
    if resp is None or 'buying_power' not in resp:
        raise Exception("Error getting profile data: No response")
    buying_power = round_money(resp['buying_power'])
    return buying_power


# Get my stocks
def get_my_stocks():
    resp = rh_run_with_retries(rh.build_holdings)
    if resp is None:
        raise Exception("Error getting holdings data: No response")
    return resp


# Get watchlist stocks by name
def get_watchlist_stocks(name):
    resp = rh_run_with_retries(rh.get_watchlist_by_name, name)
    if resp is None or 'results' not in resp:
        raise Exception(f"Error getting watchlist {name}: No response")
    return resp['results']


# Get analyst ratings for a stock by symbol
def get_ratings(symbol):
    resp = rh_run_with_retries(rh.stocks.get_ratings, symbol)
    if resp is None:
        raise Exception(f"Error getting ratings for {symbol}: No response")
    return resp


# Get historical stock data by symbol
def get_historical_data(symbol, interval="day", span="year"):
    resp = rh_run_with_retries(rh.stocks.get_stock_historicals, symbol, interval=interval, span=span)
    if resp is None:
        raise Exception(f"Error getting historical data for {symbol}: No response")
    prices = [round_money(day['close_price']) for day in resp]
    return prices


# Sell a stock by symbol and amount
def sell_stock(symbol, amount):
    if MODE == "demo":
        return {"id": "demo"}

    if MODE == "manual":
        confirm = input(f"Confirm sell for {symbol} of ${amount}? (yes/no): ")
        if confirm.lower() != "yes":
            return {"id": "cancelled"}

    sell_resp = rh_run_with_retries(rh.orders.order_sell_fractional_by_price, symbol, amount)
    if sell_resp is None:
        raise Exception(f"Error selling {symbol}: No response")
    return sell_resp


# Buy a stock by symbol and amount
def buy_stock(symbol, amount):
    if MODE == "demo":
        return {"id": "demo"}

    if MODE == "manual":
        confirm = input(f"Confirm buy for {symbol} of ${amount}? (yes/no): ")
        if confirm.lower() != "yes":
            return {"id": "cancelled"}

    buy_resp = rh_run_with_retries(rh.orders.order_buy_fractional_by_price, symbol, amount)
    if buy_resp is None:
        raise Exception(f"Error buying {symbol}: No response")
    return buy_resp


# Make AI request to OpenAI API
def make_ai_request(prompt):
    ai_resp = openai_client.chat.completions.create(
        model=OPENAI_MODEL_NAME,
        messages=[
            {"role": "system", "content": "You are a precise trading robot that only responds in valid json."},
            {"role": "user", "content": prompt}
        ]
    )
    return ai_resp


# Parse AI response
def parse_ai_response(ai_response):
    try:
        ai_content = re.sub(r'```json|```', '', ai_response.choices[0].message.content.strip())
        decisions = json.loads(ai_content)
    except json.JSONDecodeError as e:
        raise Exception("Invalid JSON response from OpenAI: " + ai_response.choices[0].message.content.strip())
    return decisions


# Make AI-based decisions on stock portfolio and watchlist
def make_ai_decisions(buying_power, portfolio_overview, watchlist_overview):
    ai_prompt = (
        f"Analyze the stock portfolio and watchlist to make investment decisions. "
        f"Suggest which stocks to sell first from the portfolio to increase buying power, "
        f"and then determine if any stock from either the portfolio or the watchlist is worth buying. "
        f"Return sell decisions in the order they should be executed to maximize buying power, "
        f"and then provide buy decisions based on the resulting buying power.\n\n"
        f"Portfolio overview:\n{json.dumps(portfolio_overview, indent=1)}\n\n"
        f"Watchlist overview:\n{json.dumps(watchlist_overview, indent=1)}\n\n"
        f"Total buying power in USD: {buying_power}\n\n"
        f"Guidelines for sell and buy amounts in USD:\n"
        f"- Min sell: {round_money(MIN_SELLING_AMOUNT_USD)}\n"
        f"- Max sell: {round_money(MAX_SELLING_AMOUNT_USD)}\n"
        f"- Min buy: {round_money(MIN_BUYING_AMOUNT_USD)}\n"
        f"- Max buy: {round_money(MAX_BUYING_AMOUNT_USD)}\n\n"
        f"Provide a JSON response in this format:\n"
        '[{"symbol": "<symbol>", "decision": "<decision>", "amount": <amount>}, ...]\n'
        "Decision options: buy, sell, hold\n"
        "Amount is the suggested amount to buy or sell in USD.\n"
        "Return only the JSON array, without explanation or extra text. "
        "If no decisions are made, return an empty array."
    )
    log_debug(f"AI making-decisions prompt:\n{ai_prompt}")
    ai_response = make_ai_request(ai_prompt)
    log_debug(f"AI making-decisions response:\n{ai_response.choices[0].message.content.strip()}")
    decisions = parse_ai_response(ai_response)
    return decisions


# Make post-decisions adjustment based on trading results
def make_ai_post_decisions_adjustment(buying_power, trading_results):
    ai_prompt = (
        "Analyze the trading results based on your previous decisions. "
        "Make adjustments if needed. "
        "Return sell decisions in the order they should be executed to maximize buying power, "
        "and then provide buy decisions based on the resulting buying power.\n\n"
        f"Trading results:\n{json.dumps(trading_results, indent=1)}\n\n"
        f"Total buying power in USD: {buying_power}\n\n"
        "Guidelines for sell and buy amounts in USD:\n"
        f"- Min sell: {round_money(MIN_SELLING_AMOUNT_USD)}\n"
        f"- Max sell: {round_money(MAX_SELLING_AMOUNT_USD)}\n"
        f"- Min buy: {round_money(MIN_BUYING_AMOUNT_USD)}\n"
        f"- Max buy: {round_money(MAX_BUYING_AMOUNT_USD)}\n\n"
        "Provide a JSON response in this format:\n"
        '[{"symbol": "<symbol>", "decision": "<decision>", "amount": <amount>}, ...]\n'
        "Decision options: buy, sell, hold\n"
        "Amount is the suggested amount to buy or sell in USD.\n"
        "Return only the JSON array, without explanation or extra text. "
        "If no decisions are made, return an empty array."
    )
    log_debug(f"AI post-decisions-adjustment prompt:\n{ai_prompt}")
    ai_response = make_ai_request(ai_prompt)
    log_debug(f"AI post-decisions-adjustment response:\n{ai_response.choices[0].message.content.strip()}")
    decisions = parse_ai_response(ai_response)
    return decisions


# Adjust decisions based on trading parameters
def adjust_decisions(decisions):
    sell_decisions = [decision for decision in decisions if decision['decision'] == "sell"]
    buy_decisions = [decision for decision in decisions if decision['decision'] == "buy"]
    hold_decisions = [decision for decision in decisions if decision['decision'] == "hold"]
    for decision in sell_decisions:
        decision['amount'] = round_money(max(MIN_SELLING_AMOUNT_USD, min(MAX_SELLING_AMOUNT_USD, decision['amount'])))
    for decision in buy_decisions:
        decision['amount'] = round_money(max(MIN_BUYING_AMOUNT_USD, min(MAX_BUYING_AMOUNT_USD, decision['amount'])))
    return sell_decisions + buy_decisions + hold_decisions


# Main trading bot function
def trading_bot():
    log_info("Getting my stocks to proceed...")
    my_stocks = get_my_stocks()

    log_info(f"Total stocks in portfolio: {len(my_stocks)}")

    log_info("Prepare portfolio overview for AI analysis...")
    portfolio_overview = {}
    for symbol, stock_data in my_stocks.items():
        portfolio_overview[symbol] = extract_my_stocks_data(stock_data)
        portfolio_overview[symbol] = enrich_with_moving_averages(portfolio_overview[symbol], symbol)
        portfolio_overview[symbol] = enrich_with_analyst_ratings(portfolio_overview[symbol], symbol)

    log_info("Getting watchlist stocks to proceed...")
    watchlist_stocks = []
    for watchlist_name in WATCHLIST_NAMES:
        try:
            watchlist_stocks.extend(get_watchlist_stocks(watchlist_name))
            watchlist_stocks = [stock for stock in watchlist_stocks if stock['symbol'] not in my_stocks.keys()]
        except Exception as e:
            log_error(f"Error getting watchlist stocks for {watchlist_name}: {e}")

    log_info(f"Total watchlist stocks: {len(watchlist_stocks)}")

    if len(watchlist_stocks) > WATCHLIST_OVERVIEW_LIMIT:
        log_info(f"Limiting watchlist stocks to overview limit of {WATCHLIST_OVERVIEW_LIMIT} (random selection)...")
        watchlist_stocks = np.random.choice(watchlist_stocks, WATCHLIST_OVERVIEW_LIMIT, replace=False)

    log_info("Prepare watchlist overview for AI analysis...")
    watchlist_overview = {}
    for stock_data in watchlist_stocks:
        symbol = stock_data['symbol']
        watchlist_overview[symbol] = extract_watchlist_data(stock_data)
        watchlist_overview[symbol] = enrich_with_moving_averages(watchlist_overview[symbol], symbol)
        watchlist_overview[symbol] = enrich_with_analyst_ratings(watchlist_overview[symbol], symbol)

    if len(portfolio_overview) == 0 and len(watchlist_overview) == 0:
        log_info("No stocks to analyze, skipping AI-based decision-making...")
        return {}

    decisions_data = []
    trading_results = {}
    post_decisions_adjustment_count = 0

    try:
        log_info("Making AI-based decision...")
        buying_power = round_money(get_buying_power())
        decisions_data = make_ai_decisions(buying_power, portfolio_overview, watchlist_overview)
    except Exception as e:
        log_error(f"Error making AI-based decision: {e}")

    while len(decisions_data) > 0:
        log_info(f"Total decisions: {len(decisions_data)}")
        log_info("Adjusting decisions based on trading parameters...")
        decisions_data = adjust_decisions(decisions_data)
        log_debug(f"Adjusted decisions:\n{json.dumps(decisions_data, indent=1)}")
        log_info("Executing decisions...")
        for decision_data in decisions_data:
            symbol = decision_data['symbol']
            decision = decision_data['decision']
            amount = decision_data['amount']
            log_info(f"{symbol} > Decision: {decision} with amount ${amount}")

            if decision == "sell":
                try:
                    sell_resp = sell_stock(symbol, amount)
                    if sell_resp and 'id' in sell_resp:
                        if sell_resp['id'] == "demo":
                            trading_results[symbol] = {"symbol": symbol, "amount": amount, "decision": "sell", "result": "success", "details": "Demo mode"}
                            log_info(f"{symbol} > Demo > Sold ${amount} worth of stock")
                        elif sell_resp['id'] == "cancelled":
                            trading_results[symbol] = {"symbol": symbol, "amount": amount, "decision": "sell", "result": "cancelled", "details": "Cancelled by user"}
                            log_info(f"{symbol} > Sell cancelled by user")
                        else:
                            details = extract_sell_response_data(sell_resp)
                            trading_results[symbol] = {"symbol": symbol, "amount": amount, "decision": "sell", "result": "success", "details": details}
                            log_info(f"{symbol} > Sold ${amount} worth of stock")
                    else:
                        details = sell_resp['detail'] if 'detail' in sell_resp else sell_resp
                        trading_results[symbol] = {"symbol": symbol, "amount": amount, "decision": "sell", "result": "error", "details": details}
                        log_error(f"{symbol} > Error selling: {details}")
                except Exception as e:
                    trading_results[symbol] = {"symbol": symbol, "amount": amount, "decision": "sell", "result": "error", "details": str(e)}
                    log_error(f"{symbol} > Error selling: {e}")

            if decision == "buy":
                try:
                    buy_resp = buy_stock(symbol, amount)
                    if buy_resp and 'id' in buy_resp:
                        if buy_resp['id'] == "demo":
                            trading_results[symbol] = {"symbol": symbol, "amount": amount, "decision": "buy", "result": "success", "details": "Demo mode"}
                            log_info(f"{symbol} > Demo > Bought ${amount} worth of stock")
                        elif buy_resp['id'] == "cancelled":
                            trading_results[symbol] = {"symbol": symbol, "amount": amount, "decision": "buy", "result": "cancelled", "details": "Cancelled by user"}
                            log_info(f"{symbol} > Buy cancelled by user")
                        else:
                            details = extract_buy_response_data(buy_resp)
                            trading_results[symbol] = {"symbol": symbol, "amount": amount, "decision": "buy", "result": "success", "details": details}
                            log_info(f"{symbol} > Bought ${amount} worth of stock")
                    else:
                        details = buy_resp['detail'] if 'detail' in buy_resp else buy_resp
                        trading_results[symbol] = {"symbol": symbol, "amount": amount, "decision": "buy", "result": "error", "details": details}
                        log_error(f"{symbol} > Error buying: {details}")
                except Exception as e:
                    trading_results[symbol] = {"symbol": symbol, "amount": amount, "decision": "buy", "result": "error", "details": str(e)}
                    log_error(f"{symbol} > Error buying: {e}")

        if post_decisions_adjustment_count >= MAX_POST_DECISIONS_ADJUSTMENTS:
            break

        try:
            log_info("Making AI-based post-decision analysis...")
            buying_power = round_money(get_buying_power())
            decisions_data = make_ai_post_decisions_adjustment(buying_power, trading_results)
            post_decisions_adjustment_count += 1
        except Exception as e:
            log_error(f"Error making post-decision analysis: {e}")
            break

    return trading_results


# Run trading bot in a loop
def main():
    while True:
        try:
            if is_market_open():
                run_interval_seconds = RUN_INTERVAL_SECONDS
                log_info(f"Market is open, running trading bot in {MODE} mode...")

                trading_results = trading_bot()

                sold_stocks = [f"{result['symbol']} (${result['amount']})" for result in trading_results.values() if result['decision'] == "sell" and result['result'] == "success"]
                bought_stocks = [f"{result['symbol']} (${result['amount']})" for result in trading_results.values() if result['decision'] == "buy" and result['result'] == "success"]
                errors = [f"{result['symbol']} ({result['details']})" for result in trading_results.values() if result['result'] == "error"]
                log_info(f"Sold stocks: {"None" if len(sold_stocks) == 0 else ', '.join(sold_stocks)}")
                log_info(f"Bought stocks: {"None" if len(bought_stocks) == 0 else ', '.join(bought_stocks)}")
                log_info(f"Errors: {"None" if len(errors) == 0 else ', '.join(errors)}")

                log_info("Getting portfolio and buying power...")
                my_stocks = get_my_stocks()
                my_stocks_value = 0
                for stock in my_stocks.values():
                    my_stocks_value += float(stock['price']) * float(stock['quantity'])
                portfolio = [f"{symbol} ({round(float(stock['price']) * float(stock['quantity']) / my_stocks_value * 100, 2)}%)" for symbol, stock in my_stocks.items()]
                buy_power = get_buying_power()

                log_info(f"Portfolio: {"None" if len(portfolio) == 0 else ', '.join(portfolio)}")
                log_info(f"Portfolio value: ${round_money(my_stocks_value)}")
                log_info(f"Buying power: ${buy_power}")
            else:
                run_interval_seconds = 60
                log_info("Market is closed, waiting for next run...")
        except Exception as e:
            run_interval_seconds = 60
            log_error(f"Trading bot error: {e}")

        log_info(f"Waiting for {run_interval_seconds} seconds...")
        time.sleep(run_interval_seconds)


# Run the main function
if __name__ == '__main__':
    confirm = input(f"Are you sure you want to run the bot in {MODE} mode? (yes/no): ")
    if confirm.lower() != "yes":
        log_warning("Exiting the bot...")
        exit()
    main()
