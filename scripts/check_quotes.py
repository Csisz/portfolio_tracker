import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.stocks import get_last_price


TICKERS = ["OTP.BD", "MOL.BD", "ANY.BD", "AAPL", "EURHUF=X", "USDHUF=X"]


def _age_seconds(quote_time):
    if not quote_time:
        return None
    try:
        dt = datetime.fromisoformat(str(quote_time).replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        return round((now - dt).total_seconds())
    except Exception:
        return None


def main():
    rows = []
    for ticker in TICKERS:
        try:
            quote = get_last_price(ticker, force_refresh=True)
            rows.append({
                "ticker": ticker,
                "price": quote.get("price"),
                "currency": quote.get("currency"),
                "source": quote.get("source"),
                "quote_time": quote.get("quote_time"),
                "received_at": quote.get("received_at"),
                "age_seconds": _age_seconds(quote.get("quote_time")),
                "stale": quote.get("stale"),
                "delayed": quote.get("delayed"),
                "error": None if quote.get("price") is not None else "no price",
            })
        except Exception as exc:
            rows.append({
                "ticker": ticker,
                "price": None,
                "currency": None,
                "source": None,
                "quote_time": None,
                "received_at": None,
                "age_seconds": None,
                "stale": None,
                "delayed": None,
                "error": str(exc),
            })
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
