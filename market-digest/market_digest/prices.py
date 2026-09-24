"""Latest prices for the dashboard (Yahoo Finance via yfinance, cached for a minute)."""
import logging
import time

log = logging.getLogger(__name__)

# Commentators' shorthand -> Yahoo symbols.
YAHOO_SYMBOL = {"SPX": "^GSPC", "NDX": "^NDX", "VIX": "^VIX", "RUT": "^RUT", "DJI": "^DJI",
                "ES": "ES=F", "NQ": "NQ=F", "YM": "YM=F", "RTY": "RTY=F", "CL": "CL=F",
                "GC": "GC=F", "BTC": "BTC-USD", "ETH": "ETH-USD", "DXY": "DX-Y.NYB", "TNX": "^TNX"}

_cache: dict[str, tuple[float, float | None]] = {}
TTL_SECONDS = 60


def get_prices(tickers: list[str]) -> dict[str, float | None]:
    now = time.time()
    missing = [t for t in tickers if t not in _cache or now - _cache[t][0] > TTL_SECONDS]
    if missing:
        try:
            import yfinance as yf
            symbols = {t: YAHOO_SYMBOL.get(t, t) for t in missing}
            data = yf.Tickers(" ".join(symbols.values()))
            for t, sym in symbols.items():
                try:
                    _cache[t] = (now, float(data.tickers[sym].fast_info["last_price"]))
                except Exception:
                    _cache[t] = (now, None)
        except Exception as e:
            log.warning("price lookup failed: %s", e)
    return {t: _cache.get(t, (0, None))[1] for t in tickers}
