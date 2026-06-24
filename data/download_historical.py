"""
ApexSpreadator — Historical Data Downloader
Downloads price history from Yahoo Finance and VIX data, aligns them,
and outputs to data/all_symbols_daily.csv and individual csv files.
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
    print(f"Downloading price history for {symbol} (interval: {interval})...")
    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start_date, end=end_date, interval=interval)
    if df.empty:
        raise ValueError(f"No data returned for {symbol}")
    return df

def main():
    parser = argparse.ArgumentParser(description="ApexSpreadator — Historical Data Downloader")
    parser.add_argument("--symbols", nargs="+", default=["SPY", "QQQ"], help="Symbols to download")
    parser.add_argument("--days", type=int, default=1095, help="Days of data to download")
    parser.add_argument("--interval", choices=['15m', '1h', '1d'], default="1d", help="Data interval")
    args = parser.parse_args()

    print("============================================================")
    print("  APEXSPREADATOR — Historical Data Downloader")
    print("============================================================")

    # Date range
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=args.days)
    start_date = start_dt.strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")

    print(f"  Period: {start_date} -> {end_date} ({args.days} days, interval: {args.interval})")
    print(f"  Symbols: {', '.join(args.symbols)}")
    print("============================================================")

    data_dir = os.path.join("data", args.interval)
    os.makedirs(data_dir, exist_ok=True)

    # 1. Download VIX data first for general IV proxy and saving baseline vix_data.csv
    print("Downloading VIX index data...")
    try:
        vix_df = download_symbol("^VIX", start_date, end_date, args.interval)
        vix_df = vix_df[["Close"]].rename(columns={"Close": "VIX"})
        vix_df.to_csv(os.path.join(data_dir, "vix_data.csv"))
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
            
            # Select required columns
            df = df[["Date", "Open", "High", "Low", "Close", "Volume"]]
            
            # Align with specific volatility index
            df = df.set_index("Date")
            vol_symbol = VOL_INDEX_MAP.get(symbol.upper(), "^VIX")
            print(f"Downloading volatility index {vol_symbol} for {symbol}...")
            
            try:
                vol_df = download_symbol(vol_symbol, start_date, end_date, args.interval)
                vol_df = vol_df[["Close"]].rename(columns={"Close": "VolIndex"})
                vol_series = vol_df["VolIndex"].reindex(df.index, method="ffill")
                df["VIX"] = vol_series.values
            except Exception as vol_err:
                print(f"Warning: Failed to download {vol_symbol} for {symbol}: {vol_err}. Falling back to standard VIX.")
                if 'vix_df' in locals():
                    vix_series = vix_df["VIX"].reindex(df.index, method="ffill")
                    df["VIX"] = vix_series.values
                else:
                    df["VIX"] = 18.0

            df["IV"] = df["VIX"] / 100.0
            
            # Fill NaNs
            df["VIX"] = df["VIX"].fillna(18.0)
            df["IV"] = df["IV"].fillna(0.18)
            
            # Save individual symbol csv
            df.to_csv(os.path.join(data_dir, f"{symbol.lower()}_data.csv"))
            
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
        combined_file = os.path.join(data_dir, "all_symbols.csv")
        combined_df.to_csv(combined_file, index=False)
        print(f"\nAll downloads complete! Combined file saved to: {combined_file} ({len(all_dfs)} symbols succeeded)")
    else:
        print("\n❌ Error: No symbols were successfully downloaded.")
        sys.exit(1)

if __name__ == "__main__":
    main()
