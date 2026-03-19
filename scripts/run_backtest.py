"""
백테스팅 실행 및 성과 분석 스크립트
─────────────────────────────────────────────────────────────────
실행:
    python scripts/run_backtest.py --market KR
    python scripts/run_backtest.py --market US
"""

import argparse
import qlib
from qlib.config import REG_CN, REG_US
from qlib.utils import init_instance_by_config
from qlib.workflow import R
from qlib.workflow.record_temp import SignalRecord, PortAnaRecord, SigAnaRecord
from qlib.contrib.report import analysis_model, analysis_position
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path


def run_backtest(market: str = "KR"):
    """백테스팅 전체 파이프라인 실행"""

    # ── 1. qlib 초기화 ──
    if market == "KR":
        qlib.init(
            provider_uri="~/.qlib/qlib_data/kr_data",
            region=REG_CN,
        )
        instruments = "kospi100"
        benchmark   = "SH000300"
    else:
        qlib.init(
            provider_uri="~/.qlib/qlib_data/us_data",
            region=REG_US,
        )
        instruments = "nasdaq100"
        benchmark   = "^NDX"

    print(f"[백테스팅] 시장: {market} | 유니버스: {instruments}")

    # ── 2. 데이터셋 준비 ──
    from qlib.contrib.data.handler import Alpha158
    from qlib.data.dataset import DatasetH

    handler_config = {
        "start_time": "2020-01-01",
        "end_time": "2024-12-31",
        "fit_start_time": "2020-01-01",
        "fit_end_time": "2022-12-31",
        "instruments": instruments,
        "infer_processors": [
            {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}},
            {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
        ],
        "learn_processors": [
            {"class": "DropnaLabel"},
            {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}},
        ],
        "label": ["Ref($close, -5) / $close - 1"],
    }

    handler  = Alpha158(**handler_config)
    dataset  = DatasetH(
        handler=handler,
        segments={
            "train": ("2020-01-01", "2022-12-31"),
            "valid": ("2023-01-01", "2023-06-30"),
            "test":  ("2023-07-01", "2024-12-31"),
        }
    )

    # ── 3. 모델 학습 ──
    from qlib.contrib.model.gbdt import LGBModel

    model = LGBModel(
        loss="mse",
        colsample_bytree=0.8879,
        learning_rate=0.2,
        subsample=0.8,
        lambda_l1=205.6999,
        lambda_l2=580.9768,
        max_depth=8,
        num_leaves=210,
        num_threads=4,
        verbose=-1,
    )

    print("[백테스팅] LightGBM 모델 학습 중...")
    with R.start(experiment_name=f"qlib_kr_{market.lower()}"):
        model.fit(dataset)

        # ── 4. 백테스팅 ──
        from qlib.contrib.strategy import TopkDropoutStrategy
        from qlib.backtest import backtest, executor as exec_

        strategy = TopkDropoutStrategy(
            model=model,
            dataset=dataset,
            topk=20,
            n_drop=3,
            hold_thresh=1,
        )

        executor_config = {
            "class": "SimulatorExecutor",
            "module_path": "qlib.backtest.executor",
            "kwargs": {
                "time_per_step": "day",
                "generate_portfolio_metrics": True,
            },
        }

        # 거래비용 설정
        if market == "KR":
            trade_exchange = {
                "freq": "day",
                "limit_threshold": 0.095,
                "deal_price": "close",
                "open_cost": 0.001,
                "close_cost": 0.0015,
                "min_cost": 5,
            }
            initial_capital = 10_000_000  # 1천만원
        else:
            trade_exchange = {
                "freq": "day",
                "deal_price": "close",
                "open_cost": 0.0,
                "close_cost": 0.0025,
                "min_cost": 0,
            }
            initial_capital = 10_000  # $10,000

        print("[백테스팅] 백테스팅 실행 중...")
        portfolio_metric_dict, indicator_dict = backtest(
            start_time="2023-07-01",
            end_time="2024-12-31",
            strategy=strategy,
            executor=init_instance_by_config(executor_config),
            account=initial_capital,
            benchmark=benchmark,
            exchange_kwargs=trade_exchange,
        )

        # ── 5. 성과 분석 ──
        print("\n" + "="*60)
        print("백테스팅 성과 요약")
        print("="*60)

        analysis_df = pd.DataFrame(portfolio_metric_dict)
        print(analysis_df)

        # 성과 지표 출력
        if "1day" in portfolio_metric_dict:
            metrics = portfolio_metric_dict["1day"]
            print(f"\n📊 주요 성과 지표:")
            print(f"  연환산 수익률 (AR):    {metrics.get('annualized_return', 0)*100:.2f}%")
            print(f"  최대 낙폭 (MDD):       {metrics.get('max_drawdown', 0)*100:.2f}%")
            print(f"  정보비율 (IR):         {metrics.get('information_ratio', 0):.3f}")
            print(f"  샤프비율:              {metrics.get('sharpe', 0):.3f}")
            print(f"  벤치마크 대비 초과:    {metrics.get('excess_return_with_cost', 0)*100:.2f}%")

        # ── 6. 차트 저장 ──
        save_dir = Path("backtest_results")
        save_dir.mkdir(exist_ok=True)

        try:
            fig = analysis_position.report_graph(
                portfolio_metric_dict,
                show_notebook=False
            )
            if fig:
                fig.savefig(save_dir / f"performance_{market}.png", dpi=150, bbox_inches="tight")
                print(f"\n📈 성과 차트 저장: backtest_results/performance_{market}.png")
        except Exception as e:
            print(f"차트 저장 실패: {e}")

        # ── 7. 모델 저장 ──
        import pickle
        Path("models").mkdir(exist_ok=True)
        model_path = f"models/lgbm_{market.lower()}.pkl"
        with open(model_path, "wb") as f:
            pickle.dump(model, f)
        print(f"💾 모델 저장: {model_path}")

    return portfolio_metric_dict


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", default="KR", choices=["KR", "US"])
    args = parser.parse_args()
    run_backtest(args.market)
