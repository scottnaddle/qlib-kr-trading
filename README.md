# qlib 한국/미국 주식 실거래 연동 시스템

Microsoft qlib AI 퀀트 플랫폼을 한국투자증권(KIS) 및 키움증권 API와 연동하여
실제 주식 거래를 자동화하는 시스템입니다.

---

## 📁 프로젝트 구조

```
qlib-kr-trading/
├── data/
│   ├── fetch_kr_stocks.py    # pykrx → qlib 포맷 (한국 주식)
│   └── fetch_us_stocks.py    # yfinance → qlib 포맷 (미국 주식)
├── brokers/
│   ├── kis_broker.py         # 한국투자증권 REST API (KR+US, 크로스플랫폼)
│   └── kiwoom_broker.py      # 키움증권 Open API+ (KR, Windows 전용)
├── strategies/
│   └── trading_bot.py        # 메인 트레이딩 봇 (qlib → 실거래)
├── scripts/
│   └── run_backtest.py       # 백테스팅 실행 스크립트
├── config/
│   ├── workflow_kr.yaml      # 한국 주식 백테스팅 설정
│   └── workflow_us.yaml      # 미국 주식 백테스팅 설정
├── .env.example              # 환경변수 예시
└── requirements.txt          # 의존성 패키지
```

---

## 🚀 빠른 시작

### 1단계: 설치

```bash
# qlib 소스 설치
git clone https://github.com/microsoft/qlib.git
cd qlib && pip install -e .

# 이 프로젝트 의존성 설치
cd qlib-kr-trading
pip install -r requirements.txt
```

### 2단계: API 키 설정

```bash
# .env 파일 생성
cp .env.example .env

# .env 파일 편집 (본인 API 키 입력)
# KIS_APP_KEY=...
# KIS_APP_SECRET=...
# KIS_ACCOUNT_NO=12345678-01
# KIS_MOCK=true  ← 처음엔 반드시 모의투자!
```

**KIS API 신청:** https://apiportal.koreainvestment.com/

### 3단계: 데이터 수집

```bash
# 한국 주식 (KOSPI 상위 100종목, 2020~현재)
python data/fetch_kr_stocks.py --market KOSPI --top_n 100 --start 20200101

# 미국 주식 (NASDAQ 100)
python data/fetch_us_stocks.py --universe nasdaq100 --start 2020-01-01

# qlib binary 포맷으로 변환 (한국)
python -m qlib.run.dump_bin dump_all \
    --csv_path ~/.qlib/qlib_data/kr_data/csv_raw \
    --qlib_dir ~/.qlib/qlib_data/kr_data \
    --freq day
```

### 4단계: 백테스팅

```bash
# 한국 주식 백테스팅
python scripts/run_backtest.py --market KR

# 또는 YAML 설정으로 실행
qrun config/workflow_kr.yaml
```

### 5단계: 모의투자 실행

```bash
# 한국 주식 - KIS 모의투자 (1회 실행)
python strategies/trading_bot.py --market KR --broker kis --once

# 매일 09:05 자동 실행 (모의투자)
python strategies/trading_bot.py --market KR --broker kis

# 미국 주식 - KIS 모의투자
python strategies/trading_bot.py --market US --broker kis --once
```

### 6단계: 실거래 전환 (3~6개월 모의투자 후!)

```bash
# .env에서 KIS_MOCK=false 로 변경 후
python strategies/trading_bot.py --market KR --broker kis --live
```

---

## 🔑 브로커 선택 가이드

| 기능 | 한국투자증권 (KIS) | 키움증권 |
|------|------------------|---------|
| OS | ✅ 윈도우/맥/리눅스 | ⚠️ 윈도우 전용 |
| 한국 주식 | ✅ | ✅ |
| 미국 주식 | ✅ | ❌ |
| API 방식 | REST | OCX (PyQt5) |
| 서버 운영 | ✅ 가능 | ❌ PC 필요 |
| 모의투자 | ✅ | ✅ |

**→ 한국+미국 주식, 서버 자동화를 원하면 KIS 추천**

---

## ⚙️ 전략 파라미터 튜닝

`strategies/trading_bot.py`의 `PortfolioManager` 초기화 값:

```python
PortfolioManager(
    topk=20,              # 보유 종목 수 (10~30 권장)
    n_drop=3,             # 매 리밸런싱 교체 수 (topk의 10~20%)
    min_cash_pct=0.05,    # 최소 현금 비율 (5%)
    max_position_pct=0.10 # 단일 종목 최대 비중 (10%)
)
```

---

## ⚠️ 중요 주의사항

1. **반드시 모의투자 먼저** — 최소 3개월 이상 검증 후 실거래 전환
2. **거래비용 댄한** — YAML 설정의 `open_cost`, `close_cost` 실제 수수료로 설정
3. **손절 규칙** — 봇 외부에서 별도 손절 로직 구현 권장 (MDD -20% 자동 중단 등)
4. **API 장애 대비** — 브로커 API 서버 점검 시간 확인, 장애 시 수동 대응 필수
5. **법적 책임** — 이 코드는 교육 목적으로 제공되며, 투자 손실에 대한 책임은 사용자에게 있습니다

---

## 📞 API 문서

- **KIS Developers:** https://apiportal.koreainvestment.com/
- **키움 Open API+:** https://www.kiwoom.com/h/customer/download/VOpenApiInfoView
- **qlib 공식 문서:** https://qlib.readthedocs.io/
- **pykrx:** https://github.com/sharebook-kr/pykrx
