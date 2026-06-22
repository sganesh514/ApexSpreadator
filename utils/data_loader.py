"""
ApexSpreadator — Unified Data Loader
Provides a single interface for retrieving market history in BACKTEST and LIVE modes.
"""
import os
import sys
import time
import threading
import pandas as pd
from typing import Optional
from utils import get_logger

logger = get_logger("DataLoader")

# Moomoo Quote Context Singleton (Thread-safe)
_quote_ctx = None
_quote_ctx_lock = threading.Lock()


def get_quote_context():
    """Get or initialize the persistent Moomoo Quote Context synchronously and thread-safely."""
    global _quote_ctx
    
    # Check if context is active
    if _quote_ctx is not None:
        try:
            if not _quote_ctx.get_sync_conn_id():
                logger.warning("Moomoo Quote Context disconnected. Reconnecting...")
                try:
                    _quote_ctx.close()
                except Exception:
                    pass
                _quote_ctx = None
        except Exception:
            _quote_ctx = None

    if _quote_ctx is None:
        with _quote_ctx_lock:
            if _quote_ctx is None:
                try:
                    from moomoo import OpenQuoteContext
                except ImportError:
                    logger.error("Failed to import OpenQuoteContext from moomoo.")
                    return None
                
                from config import CONFIG
                host = CONFIG.connection.host or "127.0.0.1"
                port = 11111
                
                logger.info(f"Initializing Moomoo Quote Context for history loader at {host}:{port}...")
                
                # Connection retry loop (3 attempts)
                for attempt in range(3):
                    try:
                        ctx = OpenQuoteContext(host=host, port=port)
                        ctx.start()
                        
                        # Wait up to 5 seconds for handshake
                        for hs_attempt in range(5):
                            if ctx.get_sync_conn_id():
                                _quote_ctx = ctx
                                logger.info("✅ Moomoo Quote Context connected successfully for history loader.")
                                break
                            time.sleep(1)
                        
                        if _quote_ctx is not None:
                            break
                            
                        logger.warning(f"Connection attempt {attempt + 1} timed out waiting for handshake.")
                        try:
                            ctx.close()
                        except Exception:
                            pass
                    except Exception as conn_err:
                        logger.warning(f"Connection attempt {attempt + 1} failed: {conn_err}")
                        time.sleep(2)
                
                if _quote_ctx is None:
                    logger.error("❌ Failed to connect Moomoo Quote Context for history loader after retries.")
                    
    return _quote_ctx


def _fetch_backtest_history(symbol: str, timeframe: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Offline historical data loading for backtesting mode."""
    clean_sym = symbol.replace("US.", "").lower()
    path = f"data/{clean_sym}_daily.csv"
    
    df = pd.DataFrame()
    if os.path.exists(path):
        try:
            df = pd.read_csv(path)
        except Exception as e:
            logger.error(f"Error reading backtest CSV for {symbol}: {e}")
            
    if df.empty:
        # Try all_symbols_daily.csv if individual file does not exist
        path_all = "data/all_symbols_daily.csv"
        if os.path.exists(path_all):
            try:
                full_df = pd.read_csv(path_all)
                # Symbol in all_symbols_daily.csv is uppercase without prefix, e.g. "AAPL"
                clean_sym_upper = symbol.replace("US.", "").upper()
                df = full_df[full_df["Symbol"] == clean_sym_upper].copy()
            except Exception as e:
                logger.error(f"Error reading combined CSV for {symbol}: {e}")

    if df.empty:
        logger.warning(f"No offline historical data found for {symbol}")
        return pd.DataFrame()

    try:
        if "Date" not in df.columns and "date" in df.columns:
            df = df.rename(columns={"date": "Date"})
        elif "Date" not in df.columns:
            df = df.reset_index()
            
        # Ensure standard columns are present and named correctly
        rename_map = {
            "time_key": "Date",
            "time": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume"
        }
        df = df.rename(columns=rename_map)
        
        # Ensure we have all required columns
        for col in ["Date", "Open", "High", "Low", "Close", "Volume"]:
            if col not in df.columns:
                # Try finding it case-insensitively
                for c in df.columns:
                    if c.lower() == col.lower():
                        df = df.rename(columns={c: col})
                        break
                else:
                    df[col] = 0.0

        # Filter columns
        df = df[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
        
        # Convert Date to pandas Timestamp objects to match downstream strftime expectations
        df["Date"] = pd.to_datetime(df["Date"])
        
        # Convert numeric columns
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
            
        # Filter by date range
        if start_date:
            df = df[df["Date"] >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df["Date"] <= pd.to_datetime(end_date)]
            
        df = df.sort_values("Date").reset_index(drop=True)
        return df
    except Exception as e:
        logger.error(f"Error processing backtest data for {symbol}: {e}")
        return pd.DataFrame()


def _fetch_live_history(symbol: str, timeframe: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Live historical data fetching via Moomoo Open API."""
    ctx = get_quote_context()
    if ctx is None:
        logger.error(f"Cannot fetch live data for {symbol} - Moomoo Quote Context not available.")
        return pd.DataFrame()
        
    code = symbol
    if "." not in symbol:
        code = f"US.{symbol}"
        
    try:
        from moomoo import KLType, AuType
    except ImportError:
        logger.error("Failed to import KLType, AuType from moomoo.")
        return pd.DataFrame()
        
    # Map timeframe string to Moomoo KLType
    ktype = KLType.K_DAY
    if timeframe == "1d":
        ktype = KLType.K_DAY
    elif timeframe == "1h":
        ktype = KLType.K_60M
    elif timeframe == "15m":
        ktype = KLType.K_15M
    elif timeframe == "5m":
        ktype = KLType.K_5M
        
    # Implement retry loop for Moomoo request
    data = None
    ret = -1
    for attempt in range(3):
        try:
            # Check if ctx has get_history_klData, else use request_history_kline
            if hasattr(ctx, "get_history_klData"):
                logger.debug(f"Calling get_history_klData for {code} (attempt {attempt + 1})...")
                ret, data = ctx.get_history_klData(
                    code=code,
                    start=start_date,
                    end=end_date,
                    ktype=ktype,
                    autype=AuType.QFQ
                )
            else:
                logger.debug(f"Calling request_history_kline for {code} (attempt {attempt + 1})...")
                ret, data, page_key = ctx.request_history_kline(
                    code=code,
                    start=start_date,
                    end=end_date,
                    ktype=ktype,
                    autype=AuType.QFQ
                )
            
            if ret == 0:
                break
            
            logger.warning(f"Moomoo kline query returned code {ret}: {data} (attempt {attempt + 1})")
            time.sleep(1)
        except Exception as query_err:
            logger.warning(f"Moomoo kline query error (attempt {attempt + 1}): {query_err}")
            time.sleep(1)
            
    if ret != 0 or data is None:
        logger.error(f"❌ Failed to fetch history for {code} from Moomoo after retries.")
        return pd.DataFrame()
        
    # Convert Moomoo nested lists/dicts/dfs to standard pandas DataFrame
    df = pd.DataFrame()
    try:
        if isinstance(data, dict):
            if "data_list" in data:
                df = pd.DataFrame(data["data_list"])
            elif "kline_list" in data:
                df = pd.DataFrame(data["kline_list"])
            else:
                df = pd.DataFrame(data)
        elif isinstance(data, list):
            df = pd.DataFrame(data)
        elif isinstance(data, pd.DataFrame):
            df = data
            
        if df.empty:
            logger.warning(f"Empty data returned from Moomoo for {code}")
            return pd.DataFrame()
            
        # Format and clean columns to match downstream expectation
        rename_map = {
            "time_key": "Date",
            "time": "Date",
            "date": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume"
        }
        df = df.rename(columns=rename_map)
        
        # Ensure all standard columns are present
        for col in ["Date", "Open", "High", "Low", "Close", "Volume"]:
            if col not in df.columns:
                # Try finding it case-insensitively
                for c in df.columns:
                    if c.lower() == col.lower():
                        df = df.rename(columns={c: col})
                        break
                else:
                    df[col] = 0.0
                    
        df = df[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
        
        # Convert Date to pandas Timestamp objects to match downstream strftime expectations
        df["Date"] = pd.to_datetime(df["Date"])
        
        # Convert numeric columns
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
            
        df = df.sort_values("Date").reset_index(drop=True)
        return df
        
    except Exception as e:
        logger.error(f"Error converting live Moomoo data for {symbol}: {e}", exc_info=True)
        return pd.DataFrame()


def get_market_history(
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    mode: str
) -> pd.DataFrame:
    """
    Exposes a unified interface for retrieving market history.
    If mode is 'BACKTEST', reads from local files, ensuring complete offline isolation.
    If mode is 'LIVE', fetches from Moomoo Open API Gateway.
    """
    if mode.upper() == "BACKTEST":
        return _fetch_backtest_history(symbol, timeframe, start_date, end_date)
    else:
        return _fetch_live_history(symbol, timeframe, start_date, end_date)
