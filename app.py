import os
from flask import Flask, jsonify, request, send_from_directory
import yfinance as yf
from datetime import datetime

app = Flask(__name__, static_folder="static", static_url_path="")

@app.route("/")
def index():
    # Serves the single-page frontend HTML
    return app.send_static_file("index.html")

@app.route("/api/strategy", methods=["GET"])
def get_strategy():
    ticker_symbol = request.args.get("ticker", "SPY").upper().strip()
    target_date = request.args.get("date", None)
    
    try:
        ticker = yf.Ticker(ticker_symbol)
        
        # Get real-time spot price
        # Try fast Info lookup; fallback to history if Info fails (common in sandbox/free environments)
        try:
            spot_price = ticker.info.get("regularMarketPrice") or ticker.info.get("currentPrice")
        except Exception:
            spot_price = None
            
        if not spot_price:
            hist = ticker.history(period="1d")
            if not hist.empty:
                spot_price = hist["Close"].iloc[-1]
            else:
                return jsonify({"error": f"Could not retrieve stock price for {ticker_symbol}."}), 400

        # Get available option maturity dates
        expirations = ticker.options
        if not expirations:
            return jsonify({"error": f"No options chain found for ticker {ticker_symbol}."}), 404

        # If no specific date is provided, find the maturity closest to 1 year (365 days) out
        if not target_date:
            today = datetime.now()
            parsed_dates = []
            for d_str in expirations:
                try:
                    parsed_dates.append((datetime.strptime(d_str, "%Y-%m-%d"), d_str))
                except ValueError:
                    continue
            
            if parsed_dates:
                # Find date with difference closest to 365 days
                target_date = min(parsed_dates, key=lambda x: abs((x[0] - today).days - 365))[1]
            else:
                target_date = expirations[0]

        # Fetch options chain for selected expiration date
        opt_chain = ticker.option_chain(target_date)
        calls = opt_chain.calls
        puts = opt_chain.puts

        if calls.empty or puts.empty:
            return jsonify({"error": "Empty call or put chain for selected date."}), 400

        # --- STRATEGY FORMULATION (100% PROTECTED BULLISH COLLAR) ---
        
        # Convert DataFrames to dictionary lists for easier processing
        calls_list = calls.to_dict(orient="records")
        puts_list = puts.to_dict(orient="records")

        # 1. At-The-Money Protective Put (Strike closest to spot price)
        atm_put = min(puts_list, key=lambda p: abs(p["strike"] - spot_price))
        put_strike = atm_put["strike"]
        put_cost = atm_put.get("ask") or atm_put.get("lastPrice") or 0.0

        # 2. Deep In-the-Money Call (representing asset replacement for Synthetic Replicator)
        deep_call = min(calls_list, key=lambda c: c["strike"])
        deep_call_strike = deep_call["strike"]
        deep_call_cost = deep_call.get("ask") or deep_call.get("lastPrice") or 0.0

        # 3. Out-of-the-Money Funding Call (Strike > Spot, premium credit closest to ATM Put cost)
        otm_calls = [c for c in calls_list if c["strike"] > spot_price]
        if otm_calls:
            otm_call = min(otm_calls, key=lambda c: abs((c.get("bid") or c.get("lastPrice") or 0.0) - put_cost))
            short_call_strike = otm_call["strike"]
            short_call_credit = otm_call.get("bid") or otm_call.get("lastPrice") or 0.0
        else:
            short_call_strike = spot_price * 1.10
            short_call_credit = 0.0

        # Calculations
        net_hedging_premium = put_cost - short_call_credit
        traditional_collar_cost = spot_price + net_hedging_premium
        synthetic_collar_cost = deep_call_cost + net_hedging_premium

        # Protection boundaries
        max_traditional_return = ((short_call_strike - traditional_collar_cost) / spot_price) * 100
        max_traditional_risk = ((put_strike - traditional_collar_cost) / spot_price) * 100
        gross_cap_percent = ((short_call_strike - spot_price) / spot_price) * 100

        return jsonify({
            "ticker": ticker_symbol,
            "spotPrice": round(spot_price, 2),
            "selectedDate": target_date,
            "allDates": expirations,
            "strategy": {
                "putStrike": put_strike,
                "putCost": round(put_cost, 2),
                "deepCallStrike": deep_call_strike,
                "deepCallCost": round(deep_call_cost, 2),
                "shortCallStrike": short_call_strike,
                "shortCallCredit": round(short_call_credit, 2),
                "netHedgingPremium": round(net_hedging_premium, 2),
                "traditionalCollarCost": round(traditional_collar_cost, 2),
                "syntheticCollarCost": round(synthetic_collar_cost, 2),
                "maxProfitTraditional": round(max_traditional_return, 2),
                "maxLossTraditional": round(max_traditional_risk, 2),
                "grossCapPercent": round(gross_cap_percent, 2)
            }
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # Render binds to process.env.PORT, default to 3000
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=False)