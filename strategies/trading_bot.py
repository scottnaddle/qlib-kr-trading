"""
qlib 예측 신호 → 실거래 주문 연동 메인 트레이딩 봇
─────────────────────────────────────────────────────────────────
qlib의 TopkDropoutStrategy로 매일 리밸런싱 신호를 생성하고,
KIS 또는 키움증권 API로 실제 주문을 실행합니다.

실행 방법:
    # 모의투자 (기본)
    python strategies/trading_bot.py --market KR --broker kis

    # 실거래
    python strategies/trading_bot.py --market KR --broker kis --live

    # 미국 주식
    python strategies/trading_bot.py --market US --broker kis --live
"""

import os
import sys
import time
import argparse
import logging
import json
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

# ── 로깅 설정 ─────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/trading_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# 1. qlib 초기화
# ────────────────────────────────────────────────────────────────

def init_qlib(data_dir: str, market: str = "KR"):
    """qlib 초기화"""
    import qlib
    from qlib.config import REG_CN, REG_US

    data_path = Path(data_dir).expanduser()

    if market == "KR":
        qlib.init(
            provider_uri=str(data_path / "kr_data"),
            region="cn",  # KR은 cn 설정 재사용 (커스텀 데이터)
        )
    else:
        qlib.init(
            provider_uri=str(data_path / "us_data"),
            region=REG_US,
        )
    logger.info(f"qlib 초기화 완료: {market} 시장")


# ────────────────────────────────────────────────────────────────
# 2. 모델 로딩 및 예측 신호 생성
# ────────────────────────────────────────────────────────────────

class SignalGenerator:
    """
    qlib LightGBM 모델 기반 예측 신호 생성기
    """

    def __init__(self, model_path: str = None, market: str = "KR"):
        self.model_path = model_path
        self.market = market
        self.model = None
        self.dataset = None

    def load_model(self):
        """저장된 모델 로딩"""
        import pickle
        from qlib.workflow import R

        if self.model_path and Path(self.model_path).exists():
            with open(self.model_path, "rb") as f:
                self.model = pickle.load(f)
            logger.info(f"모델 로딩 완료: {self.model_path}")
        else:
            logger.warning("저장된 모델 없음. 기본 LightGBM 모델 학습 필요.")

    def train_model(
        self,
        start_train: str = "2020-01-01",
        end_train: str = "2023-12-31",
        instruments: str = "kospi100",
    ):
        """LightGBM 모델 학습 (최초 1회)"""
        from qlib.contrib.model.gbdt import LGBModel
        from qlib.contrib.data.handler import Alpha158
        from qlib.data.dataset import DatasetH
        from qlib.data.dataset.handler import DataHandlerLP

        logger.info(f"모델 학습 시작: {start_train} ~ {end_train}")

        # 데이터셋 구성
        handler = Alpha158(
            instruments=instruments,
            start_time=start_train,
            end_time=end_train,
        )

        self.dataset = DatasetH(
            handler=handler,
            segments={
                "train": (start_train, end_train),
                "valid": ("2023-01-01", "2023-12-31"),
                "test":  ("2024-01-01", "2024-12-31"),
            }
        )

        # LightGBM 학습
        self.model = LGBModel(
            loss="mse",
            colsample_bytree=0.8879,
            learning_rate=0.2,
            subsample=0.8,
            lambda_l1=205.6999,
            lambda_l2=580.9768,
            max_depth=8,
            num_leaves=210,
            num_threads=4,
        )

        self.model.fit(self.dataset)
        logger.info("모델 학습 완료")

        # 모델 저장
        import pickle
        save_path = "models/lgbm_model.pkl"
        Path("models").mkdir(exist_ok=True)
        with open(save_path, "wb") as f:
            pickle.dump(self.model, f)
        logger.info(f"모델 저장: {save_path}")

    def predict(self, target_date: str = None) -> pd.Series:
        """
        예측 신호 생성
        Returns: 종목별 예측 점수 Series (index=ticker, value=score)
        """
        if self.model is None:
            raise ValueError("모델을 먼저 로딩하거나 학습하세요.")

        if target_date is None:
            target_date = date.today().strftime("%Y-%m-%d")

        try:
            pred = self.model.predict(self.dataset)
            # 오늘 날짜 예측값 추출
            if isinstance(pred, pd.DataFrame):
                if target_date in pred.index.get_level_values(0):
                    pred = pred.loc[target_date].squeeze()
                else:
                    # 가장 최근 거래일 예측값 사용
                    pred = pred.iloc[-1] if len(pred) > 0 else pd.Series()
            return pred.sort_values(ascending=False)
        except Exception as e:
            logger.error(f"예측 실패: {e}")
            return pd.Series()


# ────────────────────────────────────────────────────────────────
# 3. 포트폴리오 리밸런싱 로직
# ────────────────────────────────────────────────────────────────

class PortfolioManager:
    """
    qlib 신호 기반 포트폴리오 관리
    TopkDropout 전략: 상위 K종목 보유, 하위 종목 교체
    """

    def __init__(
        self,
        broker,
        topk: int = 20,           # 보유 최대 종목 수
        n_drop: int = 3,          # 매 리밸런싱 교체 종목 수
        total_capital: float = None,   # 운용 자금 (None=전체 잔고)
        min_cash_pct: float = 0.05,    # 최소 현금 비율
        max_position_pct: float = 0.10, # 종목당 최대 비중
        market: str = "KR",
    ):
        self.broker          = broker
        self.topk            = topk
        self.n_drop          = n_drop
        self.total_capital   = total_capital
        self.min_cash_pct    = min_cash_pct
        self.max_position_pct = max_position_pct
        self.market          = market

    def calc_target_portfolio(self, pred_scores: pd.Series) -> dict:
        """예측 점수 → 목표 포트폴리오 비중 계산"""
        if pred_scores.empty:
            return {}

        # 상위 K 종목 선택
        top_tickers = pred_scores.nlargest(self.topk).index.tolist()

        # 동일 비중 (Equal Weight)
        weight_per_stock = min(
            1.0 / self.topk,
            self.max_position_pct
        )
        return {ticker: weight_per_stock for ticker in top_tickers}

    def calc_rebalance_orders(
        self,
        target_weights: dict,
        current_positions: list[dict],
        available_cash: float,
        prices: dict
    ) -> list[dict]:
        """
        목표 포트폴리오와 현재 포지션 비교 → 주문 리스트 생성

        Returns: [{"ticker": .., "side": "BUY"/"SELL", "qty": .., "price": ..}]
        """
        orders = []

        # 현재 포지션 딕셔너리화
        current_pos = {
            p["ticker"]: p for p in current_positions
        }

        # 총 자산 계산
        total_value = available_cash
        for pos in current_positions:
            ticker = pos["ticker"]
            price  = prices.get(ticker, pos.get("current_price", 0))
            total_value += pos["qty"] * price

        if self.total_capital:
            total_value = min(total_value, self.total_capital)

        logger.info(f"운용 자산: {total_value:,.0f}원")

        # ── 매도 먼저: 목표에 없거나 비중 초과 종목 ──
        for ticker, pos in current_pos.items():
            target_weight = target_weights.get(ticker, 0)
            target_value  = total_value * target_weight
            price         = prices.get(ticker, pos.get("current_price", 1))
            target_qty    = int(target_value / price) if price > 0 else 0
            current_qty   = pos["qty"]

            if current_qty > target_qty:
                sell_qty = current_qty - target_qty
                orders.append({
                    "ticker": ticker,
                    "side":   "SELL",
                    "qty":    sell_qty,
                    "price":  price,
                })
                logger.info(f"  SELL {sell_qty}주 {ticker} (현재 {current_qty} → 목표 {target_qty})")

        # ── 매수: 목표에 있지만 보유 미달 종목 ──
        for ticker, weight in target_weights.items():
            target_value = total_value * weight
            price        = prices.get(ticker, 0)

            if price <= 0:
                logger.warning(f"  {ticker} 현재가 조회 실패, 스킵")
                continue

            target_qty  = int(target_value / price)
            current_qty = current_pos.get(ticker, {}).get("qty", 0)

            if target_qty > current_qty:
                buy_qty = target_qty - current_qty
                # 현금 체크
                cost = buy_qty * price
                if cost > available_cash * (1 - self.min_cash_pct):
                    buy_qty = int(available_cash * (1 - self.min_cash_pct) / price)

                if buy_qty > 0:
                    orders.append({
                        "ticker": ticker,
                        "side":   "BUY",
                        "qty":    buy_qty,
                        "price":  price,
                    })
                    available_cash -= buy_qty * price
                    logger.info(f"  BUY  {buy_qty}주 {ticker} (현재 {current_qty} → 목표 {target_qty})")

        return orders

    def execute_rebalance(self, pred_scores: pd.Series):
        """전체 리밸런싱 실행"""
        logger.info("="*50)
        logger.info(f"리밸런싱 시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # 1. 현재 잔고 조회
        balance   = self.broker.get_balance(self.market)
        positions = balance.get("positions", [])
        deposit   = balance.get("deposit", 0)

        logger.info(f"예수금: {deposit:,.0f}원 | 보유종목: {len(positions)}개")

        # 2. 목표 포트폴리오 계산
        target_weights = self.calc_target_portfolio(pred_scores)
        logger.info(f"목표 포트폴리오: {len(target_weights)}종목")

        # 3. 현재가 조회
        all_tickers = list(target_weights.keys()) + [p["ticker"] for p in positions]
        prices = {}
        for ticker in set(all_tickers):
            try:
                if self.market == "KR":
                    info = self.broker.get_kr_price(ticker)
                else:
                    info = self.broker.get_us_price(ticker)
                prices[ticker] = info.get("price", 0)
                time.sleep(0.1)  # API 레이트 리밋
            except Exception as e:
                logger.warning(f"  {ticker} 현재가 조회 실패: {e}")

        # 4. 주문 계산
        orders = self.calc_rebalance_orders(
            target_weights, positions, deposit, prices
        )

        logger.info(f"생성된 주문: {len(orders)}건")

        # 5. 주문 실행
        results = []
        for order in orders:
            logger.info(f"  주문 실행: {order['side']} {order['qty']}주 {order['ticker']}")
            try:
                result = self.broker.place_order(
                    ticker     = order["ticker"],
                    side       = order["side"],
                    qty        = order["qty"],
                    price      = order.get("price", 0),
                    market     = self.market,
                    order_type = "market",
                )
                results.append(result)
                time.sleep(0.3)  # API 레이트 리밋
            except Exception as e:
                logger.error(f"  주문 실패: {order['ticker']} - {e}")

        logger.info(f"리밸런싱 완료: {sum(1 for r in results if r.get('success'))} / {len(results)} 성공")
        return results


# ────────────────────────────────────────────────────────────────
# 4. 메인 트레이딩 봇
# ────────────────────────────────────────────────────────────────

class TradingBot:
    """
    메인 트레이딩 봇 클래스
    매일 장 시작 전 qlib 신호 생성 → 리밸런싱 실행
    """

    def __init__(
        self,
        broker,
        market:     str = "KR",
        topk:       int = 20,
        data_dir:   str = "~/.qlib/qlib_data",
        model_path: str = "models/lgbm_model.pkl",
    ):
        self.broker       = broker
        self.market       = market
        self.data_dir     = data_dir
        self.model_path   = model_path

        self.signal_gen   = SignalGenerator(model_path, market)
        self.portfolio_mgr = PortfolioManager(broker, topk=topk, market=market)

        # 실행 로그
        self.run_history = []

    def initialize(self):
        """초기화: qlib + 모델 로딩"""
        init_qlib(self.data_dir, self.market)
        self.signal_gen.load_model()

        # 모델 없으면 학습
        if self.signal_gen.model is None:
            logger.info("저장된 모델이 없습니다. 새로 학습합니다...")
            instruments = "kospi100" if self.market == "KR" else "nasdaq100"
            self.signal_gen.train_model(instruments=instruments)

    def run_daily(self, target_date: str = None):
        """
        일일 트레이딩 실행 (메인 루틴)
        보통 장 시작 직후 (09:05) 또는 전날 야간에 실행
        """
        start_time = datetime.now()
        logger.info(f"\n{'='*60}")
        logger.info(f"일일 트레이딩 실행: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")

        try:
            # 1. 예측 신호 생성
            logger.info("▶ 예측 신호 생성 중...")
            pred_scores = self.signal_gen.predict(target_date)
            logger.info(f"  상위 5종목: {pred_scores.head().to_dict()}")

            # 2. 리밸런싱 실행
            logger.info("▶ 포트폴리오 리밸런싱 중...")
            results = self.portfolio_mgr.execute_rebalance(pred_scores)

            # 3. 결과 저장
            run_result = {
                "date":          start_time.strftime("%Y-%m-%d"),
                "time":          start_time.strftime("%H:%M:%S"),
                "pred_count":    len(pred_scores),
                "order_count":   len(results),
                "success_count": sum(1 for r in results if r.get("success")),
                "elapsed_sec":   (datetime.now() - start_time).seconds,
            }
            self.run_history.append(run_result)
            self._save_run_log(run_result)

            logger.info(f"▶ 완료! 소요시간: {run_result['elapsed_sec']}초")

        except Exception as e:
            logger.error(f"일일 트레이딩 오류: {e}", exc_info=True)

    def _save_run_log(self, result: dict):
        """실행 결과 JSON 저장"""
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"run_{result['date']}.json"
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    def run_scheduled(self, run_time: str = "09:05"):
        """
        스케줄러 모드: 매 거래일 지정 시각에 자동 실행
        run_time: "HH:MM" 형식 (기본: 장 시작 5분 후)
        """
        import schedule

        logger.info(f"스케줄러 시작: 매일 {run_time}에 실행")

        schedule.every().monday.at(run_time).do(self.run_daily)
        schedule.every().tuesday.at(run_time).do(self.run_daily)
        schedule.every().wednesday.at(run_time).do(self.run_daily)
        schedule.every().thursday.at(run_time).do(self.run_daily)
        schedule.every().friday.at(run_time).do(self.run_daily)

        while True:
            schedule.run_pending()
            time.sleep(30)


# ────────────────────────────────────────────────────────────────
# 5. CLI 실행
# ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="qlib 트레이딩 봇")
    parser.add_argument("--market",     default="KR",   choices=["KR", "US"])
    parser.add_argument("--broker",     default="kis",  choices=["kis", "kiwoom"])
    parser.add_argument("--topk",       type=int, default=20, help="보유 최대 종목 수")
    parser.add_argument("--live",       action="store_true", help="실거래 (기본: 모의투자)")
    parser.add_argument("--once",       action="store_true", help="1회 실행 후 종료")
    parser.add_argument("--run_time",   default="09:05", help="일일 실행 시각 (HH:MM)")
    parser.add_argument("--data_dir",   default="~/.qlib/qlib_data")
    parser.add_argument("--model_path", default="models/lgbm_model.pkl")
    args = parser.parse_args()

    # 브로커 초기화
    is_mock = not args.live
    if args.broker == "kis":
        from brokers.kis_broker import KISBroker
        broker = KISBroker(mock=is_mock)
    else:
        from brokers.kiwoom_broker import KiwoomBroker
        broker = KiwoomBroker(mock=is_mock)

    # 로그인
    logger.info(f"브로커 로그인: {broker}")
    broker.login()

    # 봇 초기화
    bot = TradingBot(
        broker=broker,
        market=args.market,
        topk=args.topk,
        data_dir=args.data_dir,
        model_path=args.model_path,
    )
    bot.initialize()

    # 실행
    if args.once:
        bot.run_daily()
    else:
        bot.run_scheduled(args.run_time)


if __name__ == "__main__":
    # logs 디렉토리 생성
    Path("logs").mkdir(exist_ok=True)
    main()
