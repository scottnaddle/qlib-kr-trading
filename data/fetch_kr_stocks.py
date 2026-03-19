"""
한국 주식 데이터 수집 및 qlib 포맷 변환
pykrx를 사용해 KRX(한국거래소) 데이터를 수집합니다.

사용법:
    python data/fetch_kr_stocks.py --market KOSPI --start 20200101 --end 20241231
"""

import os
import argparse
import pandas as pd
from pathlib import Path
from datetime import datetime

try:
    from pykrx import stock as krx
except ImportError:
    raise ImportError("pip install pykrx 를 먼저 실행하세요")


# ────────────────────────────────────────────────────────────────
# 1. 종목 리스트 가져오기
# ────────────────────────────────────────────────────────────────

def get_stock_list(market: str = "KOSPI") -> list[str]:
    """KOSPI 또는 KOSDAQ 종목 코드 리스트 반환"""
    tickers = krx.get_market_ticker_list(market=market)
    print(f"[{market}] 종목 수: {len(tickers)}")
    return tickers


def get_top_n_by_cap(market: str = "KOSPI", n: int = 200, date: str = None) -> list[str]:
    """시가총액 상위 N개 종목 코드 반환"""
    if date is None:
        date = datetime.today().strftime("%Y%m%d")
    df = krx.get_market_cap(date, market=market)
    df = df.sort_values("시가총액", ascending=False)
    tickers = df.index[:n].tolist()
    print(f"[{market}] 시총 상위 {n}종목 선택")
    return tickers


# ────────────────────────────────────────────────────────────────
# 2. OHLCV 데이터 수집
# ────────────────────────────────────────────────────────────────

def fetch_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
    """단일 종목 OHLCV 수집 (일봉)"""
    try:
        df = krx.get_market_ohlcv(start, end, ticker)
        if df.empty:
            return None

        df = df.rename(columns={
            "시가": "open",
            "고가": "high",
            "저가": "low",
            "종가": "close",
            "거래량": "volume",
            "거래대금": "amount",
            "등락률": "change"
        })

        # qlib 필수 컬럼
        df.index.name = "date"
        df["factor"] = 1.0          # 수정주가 factor (pykrx는 이미 수정주가)
        df["$close"] = df["close"]  # qlib 표준 네이밍

        return df[["open", "high", "low", "close", "volume", "amount", "factor"]]
    except Exception as e:
        print(f"  [오류] {ticker}: {e}")
        return None


# ────────────────────────────────────────────────────────────────
# 3. qlib 포맷으로 저장
# ────────────────────────────────────────────────────────────────

def save_to_qlib_format(
    tickers: list[str],
    start: str,
    end: str,
    output_dir: str = "~/.qlib/qlib_data/kr_data",
    market: str = "KOSPI"
):
    """
    qlib binary 포맷으로 저장
    최종 경로: ~/.qlib/qlib_data/kr_data/instruments/
                                         calendars/
                                         features/{ticker}/
    """
    output_dir = Path(output_dir).expanduser()

    # 디렉토리 생성
    (output_dir / "instruments").mkdir(parents=True, exist_ok=True)
    (output_dir / "calendars").mkdir(parents=True, exist_ok=True)

    all_dates = set()
    valid_tickers = []
    ticker_dfs = {}

    print(f"\n{'='*50}")
    print(f"데이터 수집 시작: {len(tickers)}개 종목 ({start} ~ {end})")
    print(f"{'='*50}")

    for i, ticker in enumerate(tickers):
        print(f"[{i+1}/{len(tickers)}] {ticker} 수집 중...", end="")
        df = fetch_ohlcv(ticker, start, end)

        if df is None or len(df) < 20:
            print(" 스킵 (데이터 부족)")
            continue

        # 종목명 가져오기
        try:
            name = krx.get_market_ticker_name(ticker)
        except:
            name = ticker

        ticker_dfs[ticker] = (df, name)
        all_dates.update(df.index.tolist())
        valid_tickers.append((ticker, name))
        print(f" 완료 ({len(df)}일)")

    # ── 캘린더 저장 ──
    calendar_path = output_dir / "calendars" / "day.txt"
    sorted_dates = sorted([d.strftime("%Y-%m-%d") for d in all_dates])
    with open(calendar_path, "w") as f:
        f.write("\n".join(sorted_dates))
    print(f"\n캘린더 저장: {len(sorted_dates)}일 → {calendar_path}")

    # ── instruments 저장 ──
    instruments_path = output_dir / "instruments" / f"{market.lower()}.txt"
    with open(instruments_path, "w") as f:
        for ticker, name in valid_tickers:
            f.write(f"{ticker}\t{start[:4]}-{start[4:6]}-{start[6:]}\t{end[:4]}-{end[4:6]}-{end[6:]}\t{name}\n")
    print(f"instruments 저장: {len(valid_tickers)}종목 → {instruments_path}")

    # ── CSV 저장 (백업용 / qlib dump 입력용) ──
    csv_dir = output_dir / "csv_raw"
    csv_dir.mkdir(exist_ok=True)

    for ticker, (df, name) in ticker_dfs.items():
        csv_path = csv_dir / f"{ticker}.csv"
        df.to_csv(csv_path)

    print(f"CSV 저장 완료: {csv_dir}")

    # ── qlib dump 명령 안내 ──
    print(f"""
{'='*60}
✅ 데이터 수집 완료!

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
    parser = argparse.ArgumentParser(description="KRX 데이터 수집기")
    parser.add_argument("--market", default="KOSPI", choices=["KOSPI", "KOSDAQ"], help="시장 선택")
    parser.add_argument("--top_n", type=int, default=100, help="시총 상위 N개 종목")
    parser.add_argument("--start", default="20210101", help="시작일 (YYYYMMDD)")
    parser.add_argument("--end", default=datetime.today().strftime("%Y%m%d"), help="종료일 (YYYYMMDD)")
    parser.add_argument("--output_dir", default="~/.qlib/qlib_data/kr_data", help="저장 경로")
    args = parser.parse_args()

    # 시총 상위 종목 선택
    tickers = get_top_n_by_cap(market=args.market, n=args.top_n)

    # 데이터 수집 및 저장
    save_to_qlib_format(
        tickers=tickers,
        start=args.start,
        end=args.end,
        output_dir=args.output_dir,
        market=args.market
    )


if __name__ == "__main__":
    main()
