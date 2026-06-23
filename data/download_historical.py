"""
ApexSpreadator — Historical Data Downloader
Downloads price history from Yahoo Finance and VIX data, aligns them,
and outputs to data/{interval}/all_symbols.csv and individual csv files.
"""
import os
import sys
import argparse
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf

# Configure stdout encoding to avoid Windows console errors
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def parse_lookback_to_days(lookback_str: str) -> int:
    s = lookback_str.strip().lower()
    if s.endswith("d"):
        return int(s[:-1])
    elif s.endswith("y"):
        return int(s[:-1]) * 365
    elif s.endswith("w"):
        return int(s[:-1]) * 7
    elif s.endswith("mo"):
        return int(s[:-2]) * 30
    elif s.endswith("m"):
        return int(s[:-1]) * 30
    else:
        try:
            return int(s)
        except ValueError:
            return 365

def validate_and_truncate_lookback(interval: str, lookback_str: str) -> tuple[int, str, bool]:
    days = parse_lookback_to_days(lookback_str)
    interval = interval.strip().lower()
    truncated = False
    
    if interval == "15m":
        if days > 60:
            days = 60
            truncated = True
    elif interval in ["1h", "60m"]:
        if days > 730:
            days = 730
            truncated = True
            
    if truncated:
        final_str = f"{days}d"
    else:
        final_str = lookback_str
        
    return days, final_str, truncated

def download_symbol(symbol: str, start_date: str, end_date: str, interval: str = "1d") -> pd.DataFrame:
    print(f"Downloading price history for {symbol} ({interval})...")
    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start_date, end=end_date, interval=interval)
    if df.empty:
        raise ValueError(f"No data returned for {symbol}")
    return df

def main():
    parser = argparse.ArgumentParser(description="ApexSpreadator — Historical Data Downloader")
    parser.add_argument("--symbols", nargs="+", default=["SPY", "QQQ"], help="Symbols to download")
    parser.add_argument("--lookback", type=str, default="2y", help="Lookback period (e.g. '60d', '730d', '2y')")
    parser.add_argument("--interval", type=str, default="1d", help="Interval to download (15m, 1h, 1d)")
    args = parser.parse_args()

    # Lookback validation check and truncation
    days_requested, final_lookback, truncated = validate_and_truncate_lookback(args.interval, args.lookback)
    if truncated:
        print(f"⚠️ Warning: Requested lookback '{args.lookback}' exceeds the yfinance limit for interval '{args.interval}'.")
        print(f"  Truncating lookback to {days_requested} days.")
    
    args.lookback = final_lookback

    print("============================================================")
    print("  APEXSPREADATOR — Historical Data Downloader")
    print("============================================================")
    print(f"  Interval: {args.interval}")
    print(f"  Lookback: {args.lookback} ({days_requested} days)")
    print(f"  Symbols: {', '.join(args.symbols)}")
    print("============================================================")

    # Date range
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days_requested)
    start_date = start_dt.strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")

    os.makedirs(f"data/{args.interval}", exist_ok=True)

    # 1. Download VIX data first for general IV proxy and saving vix_daily.csv
    print("Downloading VIX index data...")
    try:
        vix_df = download_symbol("^VIX", start_date, end_date, "1d")
        vix_df.index = pd.to_datetime(vix_df.index)
        if vix_df.index.tz is None:
            vix_df.index = vix_df.index.tz_localize('UTC').tz_convert('America/New_York')
        else:
            vix_df.index = vix_df.index.tz_convert('America/New_York')
        vix_df.index = vix_df.index.tz_localize(None)
        vix_df = vix_df[["Close"]].rename(columns={"Close": "VIX"})
        vix_df.to_csv(f"data/{args.interval}/vix_daily.csv")
        print("VIX data downloaded successfully.")
    except Exception as e:
        print(f"Error downloading VIX: {e}")
        sys.exit(1)

    VOL_INDEX_MAP = {
        "SPY": "^VIX",
        "QQQ": "^VXN",
        "IWM": "^RVX"
    }

    all_dfs = []
    failed_symbols = []

    for symbol in args.symbols:
        try:
            df = download_symbol(symbol, start_date, end_date, args.interval)
            
            # Reset index to make Date a column
            df = df.reset_index()
            # Clean column names
            df.columns = [c.strip() for c in df.columns]
            
            if "Datetime" in df.columns:
                df = df.rename(columns={"Datetime": "Date"})
            
            # Select required columns
            df = df[["Date", "Open", "High", "Low", "Close", "Volume"]]
            
            # Align with specific volatility index
            df = df.set_index("Date")
            vol_symbol = VOL_INDEX_MAP.get(symbol.upper(), "^VIX")
            print(f"Downloading volatility index {vol_symbol} for {symbol}...")
            
            try:
                vol_df = download_symbol(vol_symbol, start_date, end_date, "1d")
                vol_df = vol_df[["Close"]].rename(columns={"Close": "VolIndex"})
                
                df.index = pd.to_datetime(df.index)
                if df.index.tz is None:
                    df.index = df.index.tz_localize('UTC').tz_convert('America/New_York')
                else:
                    df.index = df.index.tz_convert('America/New_York')
                df.index = df.index.tz_localize(None)
                
                vol_df.index = pd.to_datetime(vol_df.index)
                if vol_df.index.tz is None:
                    vol_df.index = vol_df.index.tz_localize('UTC').tz_convert('America/New_York')
                else:
                    vol_df.index = vol_df.index.tz_convert('America/New_York')
                vol_df.index = vol_df.index.tz_localize(None)
                
                df = df.sort_index()
                vol_df = vol_df.sort_index()
                
                df = pd.merge_asof(df, vol_df, left_index=True, right_index=True, direction='backward')
                df = df.rename(columns={"VolIndex": "VIX"})
            except Exception as vol_err:
                print(f"Warning: Failed to download {vol_symbol} for {symbol}: {vol_err}. Falling back to standard VIX.")
                if 'vix_df' in locals():
                    vix_df.index = pd.to_datetime(vix_df.index)
                    if vix_df.index.tz is None:
                        vix_df.index = vix_df.index.tz_localize('UTC').tz_convert('America/New_York')
                    else:
                        vix_df.index = vix_df.index.tz_convert('America/New_York')
                    vix_df.index = vix_df.index.tz_localize(None)
                    df = df.sort_index()
                    vix_df = vix_df.sort_index()
                    df = pd.merge_asof(df, vix_df, left_index=True, right_index=True, direction='backward')
                else:
                    df["VIX"] = 18.0

            # Fill NaNs
            if "VIX" in df.columns:
                df["VIX"] = df["VIX"].ffill().bfill().fillna(18.0)
            else:
                df["VIX"] = 18.0
                
            df["IV"] = df["VIX"] / 100.0
            df["IV"] = df["IV"].fillna(0.18)
            
            # Save individual symbol csv
            df.to_csv(f"data/{args.interval}/{symbol.lower()}.csv")
            
            # For the combined CSV
            df_reset = df.reset_index()
            df_reset["Symbol"] = symbol
            all_dfs.append(df_reset)
            print(f"Completed {symbol}")
        except Exception as e:
            print(f"Error downloading {symbol}: {e}")
            failed_symbols.append(symbol)
            continue

    if failed_symbols:
        print("\n⚠️ The following symbols failed to download and were skipped:")
        print(f"  {', '.join(failed_symbols)}")

    if all_dfs:
        combined_df = pd.concat(all_dfs, ignore_index=True)
        combined_df = combined_df[["Date", "Symbol", "Open", "High", "Low", "Close", "Volume", "VIX", "IV"]]
        combined_df.to_csv(f"data/{args.interval}/all_symbols.csv", index=False)
        print(f"\nAll downloads complete! Combined file saved to: data/{args.interval}/all_symbols.csv ({len(all_dfs)} symbols succeeded)")
    else:
        print("\n❌ Error: No symbols were successfully downloaded.")
        sys.exit(1)

if __name__ == "__main__":
    main()
