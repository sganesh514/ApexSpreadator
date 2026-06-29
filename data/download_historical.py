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

def download_symbol(symbol: str, start_date: str, end_date: str, interval: str = "1d") -> pd.DataFrame:
    print(f"Downloading price history for {symbol} (interval={interval})...")
    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start_date, end=end_date, interval=interval)
    if df.empty:
        raise ValueError(f"No data returned for {symbol}")
    return df

def main():
    parser = argparse.ArgumentParser(description="ApexSpreadator — Historical Data Downloader")
    parser.add_argument("--symbols", nargs="+", default=["SPY", "QQQ"], help="Symbols to download")
    parser.add_argument("--lookback", type=int, default=730, help="Lookback in integer days")
    parser.add_argument("--interval", type=str, default="1d", help="Data interval (e.g. 15m, 1d, 1y)")
    args = parser.parse_args()

    print("============================================================")
    print("  APEXSPREADATOR — Historical Data Downloader")
    print("============================================================")

    # Date range
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=args.lookback)
    start_date = start_dt.strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")

    print(f"  Period: {start_date} -> {end_date} ({args.lookback} days)")
    print(f"  Interval: {args.interval}")
    print(f"  Symbols: {', '.join(args.symbols)}")
    print("============================================================")

    # Create interval-specific output directory
    output_dir = os.path.join("data", args.interval)
    os.makedirs(output_dir, exist_ok=True)

    # 1. Download VIX data first for general IV proxy
    print("Downloading VIX index data...")
    try:
        vix_df = download_symbol("^VIX", start_date, end_date, interval=args.interval)
        vix_df = vix_df[["Close"]].rename(columns={"Close": "VIX"})
        # Strip timezone metadata
        if vix_df.index.tz is not None:
            vix_df.index = vix_df.index.tz_localize(None)
        vix_df.to_csv(os.path.join(output_dir, "vix.csv"))
        print("VIX data downloaded successfully.")
    except Exception as e:
        print(f"Warning: Failed to download VIX: {e}. Will use default IV=0.18.")
        vix_df = None

    VOL_INDEX_MAP = {
        "SPY": "^VIX",
        "QQQ": "^VXN",
        "IWM": "^RVX"
    }

    all_dfs = []
    failed_symbols = []

    for symbol in args.symbols:
        try:
            df = download_symbol(symbol, start_date, end_date, interval=args.interval)
            
            # Reset index to make Date/Datetime a column
            df = df.reset_index()
            # Clean column names
            df.columns = [c.strip() for c in df.columns]
            
            if "Datetime" in df.columns:
                df = df.rename(columns={"Datetime": "Date"})
                
            # Select required columns
            df = df[["Date", "Open", "High", "Low", "Close", "Volume"]]

            # Strip timezone metadata from Date column
            df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
            
            # Align with specific volatility index
            df = df.set_index("Date")
            vol_symbol = VOL_INDEX_MAP.get(symbol.upper(), "^VIX")
            print(f"Downloading volatility index {vol_symbol} for {symbol}...")
            
            try:
                vol_df = download_symbol(vol_symbol, start_date, end_date, interval=args.interval)
                vol_df = vol_df[["Close"]].rename(columns={"Close": "VolIndex"})
                if vol_df.index.tz is not None:
                    vol_df.index = vol_df.index.tz_localize(None)
                vol_series = vol_df["VolIndex"].reindex(df.index, method="ffill")
                df["VIX"] = vol_series.values
            except Exception as vol_err:
                print(f"Warning: Failed to download {vol_symbol} for {symbol}: {vol_err}. Falling back to standard VIX.")
                if vix_df is not None:
                    vix_series = vix_df["VIX"].reindex(df.index, method="ffill")
                    df["VIX"] = vix_series.values
                else:
                    df["VIX"] = 18.0

            df["IV"] = df["VIX"] / 100.0
            
            # Fill NaNs
            df["VIX"] = df["VIX"].fillna(18.0)
            df["IV"] = df["IV"].fillna(0.18)
            
            # Save individual symbol csv
            df.to_csv(os.path.join(output_dir, f"{symbol.lower()}.csv"))
            
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
        # Ensure Date column has no timezone metadata
        combined_df["Date"] = pd.to_datetime(combined_df["Date"]).dt.tz_localize(None)
        combined_path = os.path.join(output_dir, "all_symbols.csv")
        combined_df.to_csv(combined_path, index=False)
        print(f"\nAll downloads complete! Combined file saved to: {combined_path} ({len(all_dfs)} symbols succeeded)")
    else:
        print("\n❌ Error: No symbols were successfully downloaded.")
        sys.exit(1)

if __name__ == "__main__":
    main()
