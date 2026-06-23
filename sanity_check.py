import os
import sys
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

def run_sanity_check():
    print("============================================================")
    print("  RUNNING SANITY CHECK ON SINGLE SYMBOL DOWNLOAD")
    print("============================================================")
    
    symbol = "SPY"
    interval = "15m"
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=5)
    
    start_date = start_dt.strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")
    
    print(f"Downloading {symbol} ({interval}) from {start_date} to {end_date}...")
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start_date, end=end_date, interval=interval)
        if df.empty:
            print("❌ Error: No data returned from yfinance.")
            return
            
        print("Raw yfinance index timezone:", df.index.tz)
        print("First 3 index timestamps (raw):")
        for idx in df.index[:3]:
            print(f"  {idx}")
            
        # Apply the America/New_York conversion and tz_localize(None) logic
        print("\nApplying conversion to America/New_York and stripping offset...")
        df.index = pd.to_datetime(df.index)
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC').tz_convert('America/New_York')
        else:
            df.index = df.index.tz_convert('America/New_York')
        df.index = df.index.tz_localize(None)
        
        print("First 3 index timestamps (processed):")
        for idx in df.index[:3]:
            print(f"  {idx}")
            
        # Verify first timestamp of the trading day is 09:30:00
        first_ts = str(df.index[0])
        print("\nFirst row index timestamp string:", first_ts)
        
        if "09:30:00" in first_ts:
            print("✅ Success: The index starts at 09:30:00 native Eastern time!")
        else:
            print("⚠️ Warning: The index did not start at 09:30:00. Time is:", first_ts.split()[-1])
            
    except Exception as e:
        print(f"❌ Error during sanity check: {e}")

if __name__ == "__main__":
    run_sanity_check()
