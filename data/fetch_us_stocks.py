"""
미국 주식 데이터 수집 및 qlib 포맷 변환
yfinance를 사용해 NYSE/NASDAQ 데이터를 수집합니다.

사용법:
    python data/fetch_us_stocks.py --universe sp500 --start 2020-01-01 --end 2024-12-31
"""

import os
import argparse
import pandas as pd
import yfinance as yf
from pathlib import Path
from datetime import datetime

# ────────────────────────────────────────────────────────────────
# 1. 유니버스 정의
# ────────────────────────────────────────────────────────────────

# 관심 종목 예시 (직접 편집하세요)
NASDAQ_100_SAMPLE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AVGO",
    "ASML", "COST", "AMD", "NFLX", "ADBE", "QCOM", "INTC", "INTU",
    "AMAT", "MU", "LRCX", "KLAC", "MRVL", "PANW", "SNPS", "CDNS",
    "MNST", "ORLY", "FTNT", "ABNB", "MELI", "WDAY", "DDOG", "ZS",
]

SP500_SAMPLE = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "NVDA", "BRK-B", "META", "LLY",
    "JPM", "TSLA", "V", "UNH", "AVGO", "MA", "JNJ", "HD", "PG",
    "COST", "ABBV", "CVX", "MRK", "KO", "PEP", "ORCL", "BAC",
    "CRM", "TMO", "ACN", "MCD", "ABT", "WMT", "CSCO", "DHR", "NKE",
]

UNIVERSES = {
    "nasdaq100": NASDAQ_100_SAMPLE,
    "sp500": SP500_SAMPLE,
    "custom": [],  # config에서 직접 지정
}


# ────────────────────────────────────────────────────────────────
# 2. 데이터 수집
# ────────────────────────────────────────────────────────────────

def fetch_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
    """yfinance로 수정주가 OHLCV 수집"""
    try:
        t = yf.Ticker(ticker)
        df = t.history(start=start, end=end, auto_adjust=True)

        if df.empty or len(df) < 20:
            return None

        df = df.rename(columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        })

        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.index.name = "date"
        df["factor"] = 1.0  # auto_adjust=True이므로 이미 수정주가

        return df[["open", "high", "low", "close", "volume", "factor"]]
    except Exception as e:
        print(f"  [오류] {ticker}: {e}")
        return None


def fetch_batch(tickers: list[str], start: str, end: str) -> dict:
    """여러 종목 일괄 수집"""
    print(f"\nyfinance 일괄 다운로드: {len(tickers)}개 종목")
    try:
        raw = yf.download(
            tickers,
            start=start,
            end=end,
            auto_adjust=True,
            group_by="ticker",
            threads=True,
            progress=True,
        )
    except Exception as e:
        print(f"일괄 다운로드 실패: {e}, 개별 수집으로 전환...")
        return {}

    result = {}
    for ticker in tickers:
        try:
            if len(tickers) == 1:
                df = raw.copy()
            else:
                df = raw[ticker].copy()

            df = df.dropna()
            if len(df) < 20:
                continue

            df.columns = [c.lower() for c in df.columns]
            df.index = pd.to_datetime(df.index).tz_localize(None)
            df.index.name = "date"
            df["factor"] = 1.0
            result[ticker] = df[["open", "high", "low", "close", "volume", "factor"]]
        except Exception:
            continue

    return result


# ────────────────────────────────────────────────────────────────
# 3. qlib 포맷으로 저장
# ────────────────────────────────────────────────────────────────

def save_to_qlib_format(
    tickers: list[str],
    start: str,
    end: str,
    output_dir: str = "~/.qlib/qlib_data/us_data",
    universe_name: str = "nasdaq100"
):
    output_dir = Path(output_dir).expanduser()

    (output_dir / "instruments").mkdir(parents=True, exist_ok=True)
    (output_dir / "calendars").mkdir(parents=True, exist_ok=True)
    csv_dir = output_dir / "csv_raw"
    csv_dir.mkdir(exist_ok=True)

    # 일괄 수집
    ticker_dfs = fetch_batch(tickers, start, end)

    # 실패한 종목 개별 재시도
    failed = [t for t in tickers if t not in ticker_dfs]
    for ticker in failed:
        print(f"개별 재시도: {ticker}")
        df = fetch_ohlcv(ticker, start, end)
        if df is not None:
            ticker_dfs[ticker] = df

    print(f"\n수집 성공: {len(ticker_dfs)}/{len(tickers)}개 종목")

    # ── 캘린더 저장 ──
    all_dates = set()
    for df in ticker_dfs.values():
        all_dates.update(df.index.tolist())

    sorted_dates = sorted([d.strftime("%Y-%m-%d") for d in all_dates])
    with open(output_dir / "calendars" / "day.txt", "w") as f:
        f.write("\n".join(sorted_dates))

    # ── instruments 저장 ──
    with open(output_dir / "instruments" / f"{universe_name}.txt", "w") as f:
        for ticker, df in ticker_dfs.items():
            s = df.index.min().strftime("%Y-%m-%d")
            e = df.index.max().strftime("%Y-%m-%d")
            f.write(f"{ticker}\t{s}\t{e}\n")

    # ── CSV 저장 ──
    for ticker, df in ticker_dfs.items():
        df.to_csv(csv_dir / f"{ticker}.csv")

    print(f"""
{'='*60}
✅ 미국 주식 데이터 수집 완료!

다음 단계: qlib binary 포맷으로 변환
────────────────────────────────────────
python -m qlib.run.dump_bin dump_all \\
    --csv_path {csv_dir} \\
    --qlib_dir {output_dir} \\
    --freq day \\
    --date_field_name date \\
    --symbol_field_name symbol
{'='*60}
""")

    return str(output_dir)


# ────────────────────────────────────────────────────────────────
# 4. 메인
# ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="미국 주식 데이터 수집기")
    parser.add_argument("--universe", default="nasdaq100",
                        choices=list(UNIVERSES.keys()),
                        help="종목 유니버스")
    parser.add_argument("--tickers", nargs="+", help="직접 티커 지정 (--universe custom 일 때)")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=datetime.today().strftime("%Y-%m-%d"))
    parser.add_argument("--output_dir", default="~/.qlib/qlib_data/us_data")
    args = parser.parse_args()

    if args.universe == "custom" and args.tickers:
        tickers = args.tickers
    else:
        tickers = UNIVERSES[args.universe]

    save_to_qlib_format(
        tickers=tickers,
        start=args.start,
        end=args.end,
        output_dir=args.output_dir,
        universe_name=args.universe
    )


if __name__ == "__main__":
    main()
