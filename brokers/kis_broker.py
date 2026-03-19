"""
한국투자증권 KIS Developers API 연동 모듈
────────────────────────────────────────────────────────────────
✅  REST API 기반으로 Windows/Mac/Linux 모두 동작합니다.
    키움증권과 달리 크로스플랫폼 서버 실행 가능.

API 신청:
    https://apiportal.koreainvestment.com/

환경변수 설정 (.env 파일):
    KIS_APP_KEY=your_app_key
    KIS_APP_SECRET=your_app_secret
    KIS_ACCOUNT_NO=12345678-01     # 계좌번호-계좌 뒤 2자리
    KIS_MOCK=true                   # 모의투자: true / 실거래: false

설치:
    pip install requests python-dotenv

사용법:
    from brokers.kis_broker import KISBroker
    broker = KISBroker()
    broker.login()
    balance = broker.get_balance()
    broker.place_order("005930", "BUY", qty=10)  # 시장가 매수
"""

import os
import time
import json
import hashlib
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()


class KISBroker:
    """
    한국투자증권 KIS Developers REST API 래퍼

    주요 엔드포인트:
        POST /oauth2/tokenP          - 접근토큰 발급
        GET  /uapi/domestic-stock/v1/quotations/inquire-price   - 현재가
        POST /uapi/domestic-stock/v1/trading/order-cash          - 국내주식 주문
        GET  /uapi/domestic-stock/v1/trading/inquire-balance     - 잔고조회
        POST /uapi/overseas-stock/v1/trading/order               - 해외주식 주문
        GET  /uapi/overseas-stock/v1/trading/inquire-balance     - 해외잔고
    """

    # API 서버 주소
    REAL_URL  = "https://openapi.koreainvestment.com:9443"
    MOCK_URL  = "https://openapivts.koreainvestment.com:29443"

    # TR ID (국내)
    KR_BUY_TRCD   = {"real": "TTTC0802U", "mock": "VTTC0802U"}
    KR_SELL_TRCD  = {"real": "TTTC0801U", "mock": "VTTC0801U"}
    KR_BALANCE    = {"real": "TTTC8434R", "mock": "VTTC8434R"}
    KR_PRICE      = "FHKST01010100"

    # TR ID (해외 - 미국)
    US_BUY_TRCD   = {"real": "TTTT1002U", "mock": "VTTT1002U"}
    US_SELL_TRCD  = {"real": "TTTT1006U", "mock": "VTTT1006U"}
    US_BALANCE    = {"real": "TTTS3012R", "mock": "VTTS3012R"}
    US_PRICE      = "HHDFS00000300"

    def __init__(
        self,
        app_key:    str = None,
        app_secret: str = None,
        account_no: str = None,
        mock:       bool = None,
    ):
        self.app_key    = app_key    or os.getenv("KIS_APP_KEY", "")
        self.app_secret = app_secret or os.getenv("KIS_APP_SECRET", "")
        self.account_no = account_no or os.getenv("KIS_ACCOUNT_NO", "")
        self.mock       = mock if mock is not None else (
            os.getenv("KIS_MOCK", "true").lower() == "true"
        )

        self.base_url   = self.MOCK_URL if self.mock else self.REAL_URL
        self.access_token  = None
        self.token_expires = None

        mode = "모의투자" if self.mock else "실거래"
        print(f"[KIS] 초기화 완료 ({mode})")

        if not self.app_key or not self.app_secret:
            print("[KIS] ⚠️  API 키 미설정. .env 파일을 확인하세요.")

    # ── 인증 ──────────────────────────────────

    def login(self) -> bool:
        """접근 토큰 발급"""
        url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey":     self.app_key,
            "appsecret":  self.app_secret,
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            self.access_token  = data["access_token"]
            expires_in = int(data.get("expires_in", 86400))
            self.token_expires = datetime.now() + timedelta(seconds=expires_in - 60)

            print(f"[KIS] 로그인 성공. 토큰 만료: {self.token_expires.strftime('%H:%M:%S')}")
            return True

        except Exception as e:
            print(f"[KIS] 로그인 실패: {e}")
            return False

    def _ensure_token(self):
        """토큰 유효성 확인 및 자동 갱신"""
        if not self.access_token or datetime.now() >= self.token_expires:
            print("[KIS] 토큰 만료, 재발급...")
            self.login()

    def _headers(self, tr_id: str, is_hash: bool = False) -> dict:
        """공통 헤더 생성"""
        self._ensure_token()
        h = {
            "Content-Type":  "application/json",
            "authorization": f"Bearer {self.access_token}",
            "appkey":        self.app_key,
            "appsecret":     self.app_secret,
            "tr_id":         tr_id,
            "custtype":      "P",
        }
        return h

    def _hashkey(self, data: dict) -> str:
        """주문 시 필요한 hashkey 생성"""
        url = f"{self.base_url}/uapi/hashkey"
        resp = requests.post(url, json=data, headers={
            "Content-Type": "application/json",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        })
        return resp.json().get("HASH"().get("HASH", "")

    # ── 국내 주식 ─────────────────────────────

    def get_kr_price(self, ticker: str) -> dict:
        """국내 주식 현재가 조회"""
        self._ensure_token()
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}

        resp = requests.get(url, headers=self._headers(self.KR_PRICE), params=params)
        data = resp.json()

        if data.get("rt_cd") == "0":
            output = data["output"]
            return {
                "ticker":       ticker,
                "price":        int(output.get("stck_prpr", 0)),
                "open":         int(output.get("stck_oprc", 0)),
                "high":         int(output.get("stck_hgpr", 0)),
                "low":          int(output.get("stck_lwpr", 0)),
                "volume":       int(output.get("acml_vol", 0)),
                "change_rate":  float(output.get("prdy_ctrt", 0)),
                "name":         output.get("hts_kor_isnm", ""),
            }
        else:
            print(f"[KIS] 현재가 조회 실패: {data.get('msg1')}")
            return {}

    def place_kr_order(
        self,
        ticker:     str,
        side:       str,
        qty:        int,
        price:      int = 0,
        order_type: str = "market"
    ) -> dict:
        """
        국내 주식 주문

        Parameters
        ----------
        ticker     : 종목코드 (예: '005930')
        side       : 'BUY' 또는 'SELL'
        qty        : 주문 수량
        price      : 지정가 가격 (시장가=0)
        order_type : 'market' 또는 'limit'
        """
        mode_key = "mock" if self.mock else "real"
        tr_id    = self.KR_BUY_TRCD[mode_key] if side == "BUY" else self.KR_SELL_TRCD[mode_key]

        # 주문구분: 01=시장가, 00=지정가
        ord_dvsn = "01" if order_type == "market" else "00"
        ord_price = "0" if order_type == "market" else str(price)

        # 계좌번호 분리 (12345678-01 → 12345678, 01)
        acnt_parts = self.account_no.replace("-", "")
        cano    = acnt_parts[:8]
        acnt_prdt_cd = acnt_parts[8:] if len(acnt_parts) > 8 else "01"

        payload = {
            "CANO":            cano,
            "ACNT_PRDT_CD":    acnt_prdt_cd,
            "PDNO":            ticker,
            "ORD_DVSN":        ord_dvsn,
            "ORD_QTY":         str(qty),
            "ORD_UNPR":        ord_price,
        }

        headers = self._headers(tr_id)
        headers["hashkey"] = self._hashkey(payload)

        url  = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        resp = requests.post(url, headers=headers, json=payload)
        data = resp.json()

        if data.get("rt_cd") == "0":
            order_id = data["output"].get("ODNO", "")
            print(f"[KIS] 국내 주문 접수 ✅  {side} {qty}주 {ticker} (주문번호: {order_id})")
            return {"success": True, "order_id": order_id, "data": data["output"]}
        else:
            msg = data.get("msg1", "알 수 없는 오류")
            print(f"[KIS] 국내 주문 실패 ❌  {msg}")
            return {"success": False, "message": msg, "data": data}

    def get_kr_balance(self) -> dict:
        """국내 계좌 잔고 조회"""
        self._ensure_token()
        mode_key = "mock" if self.mock else "real"

        acnt_parts = self.account_no.replace("-", "")
        cano = acnt_parts[:8]
        acnt_prdt_cd = acnt_parts[8:] if len(acnt_parts) > 8 else "01"

        params = {
            "CANO":          cano,
            "ACNT_PRDT_CD":  acnt_prdt_cd,
            "AFHR_FLPR_YN":  "N",
            "OFL_YN":        "",
            "INQR_DVSN":     "02",
            "UNPR_DVSN":     "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN":     "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }

        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        resp = requests.get(url, headers=self._headers(self.KR_BALANCE[mode_key]), params=params)
        data = resp.json()

        if data.get("rt_cd") == "0":
            summary = data.get("output2", [{}])[0]
            positions = []
            for item in data.get("output1", []):
                if int(item.get("hldg_qty", "0")) > 0:
                    positions.append({
                        "ticker":        item.get("pdno"),
                        "name":          item.get("prdt_name"),
                        "qty":           int(item.get("hldg_qty", 0)),
                        "avg_price":     int(float(item.get("pchs_avg_pric", 0))),
                        "current_price": int(item.get("prpr", 0)),
                        "profit_loss":   int(item.get("evlu_pfls_amt", 0)),
                        "profit_rate":   float(item.get("evlu_pfls_rt", 0)),
                    })

            return {
                "deposit":           int(summary.get("dnca_tot_amt", 0)),
                "total_eval":        int(summary.get("tot_evlu_amt", 0)),
                "total_profit_loss": int(summary.get("evlu_pfls_smtl_amt", 0)),
                "total_profit_rate": float(summary.get("asst_icdc_rt", 0)),
                "positions":         positions,
            }
        else:
            print(f"[KIS] 잔고 조회 실패: {data.get('msg1')}")
            return {}

    # ── 미국 주식 ─────────────────────────────

    def get_us_price(self, ticker: str, exchange: str = "NAS") -> dict:
        """미국 주식 현재가 조회 (장 외 시간에는 전일 종가)"""
        self._ensure_token()

        # exchange: NAS(나스닥), NYS(뉴욕), AMS(아멕스)
        params = {
            "AUTH":     "",
            "EXCD":     exchange,
            "SYMB":     ticker,
        }

        url = f"{self.base_url}/uapi/overseas-stock/v1/quotations/price"
        resp = requests.get(url, headers=self._headers(self.US_PRICE), params=params)
        data = resp.json()

        if data.get("rt_cd") == "0":
            output = data["output"]
            return {
                "ticker":      ticker,
                "price":       float(output.get("last", 0)),
                "change_rate": float(output.get("rate", 0)),
                "volume":      int(output.get("tvol", 0)),
                "exchange":    exchange,
            }
        else:
            print(f"[KIS] 미국 현재가 조회 실패: {data.get('msg1')}")
            return {}

    def place_us_order(
        self,
        ticker:     str,
        side:       str,
        qty:        int,
        price:      float = 0,
        exchange:   str = "NAS",
        order_type: str = "market"
    ) -> dict:
        """
        미국 주식 주문

        Parameters
        ----------
        ticker   : 티커 (예: 'AAPL')
        side     : 'BUY' 또는 'SELL'
        qty      : 수량
        price    : 지정가 (시장가=0)
        exchange : 'NAS'(나스닥) / 'NYS'(뉴욕) / 'AMS'(아멕스)
        """
        mode_key = "mock" if self.mock else "real"
        tr_id    = self.US_BUY_TRCD[mode_key] if side == "BUY" else self.US_SELL_TRCD[mode_key]

        # 미국 시장가: SLD(매수시장가), SLS(매도시장가)
        # 지정가: LMT
        if order_type == "market":
            sll_type = "SLD" if side == "BUY" else "SLS"
            ord_dvsn = ""
        else:
            sll_type = ""
            ord_dvsn = "LMT"

        acnt_parts = self.account_no.replace("-", "")
        cano = acnt_parts[:8]
        acnt_prdt_cd = acnt_parts[8:] if len(acnt_parts) > 8 else "01"

        payload = {
            "CANO":         cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": exchange,
            "PDNO":         ticker,
            "ORD_QTY":      str(qty),
            "OVRS_ORD_UNPR": str(price) if order_type == "limit" else "0",
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN":     sll_type or ord_dvsn,
        }

        headers = self._headers(tr_id)
        headers["hashkey"] = self._hashkey(payload)

        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order"
        resp = requests.post(url, headers=headers, json=payload)
        data = resp.json()

        if data.get("rt_cd") == "0":
            order_id = data["output"].get("ODNO", "")
            print(f"[KIS] 미국 주문 접수 ✅  {side} {qty}주 {ticker}/{exchange} (주문번호: {order_id})")
            return {"success": True, "order_id": order_id, "data": data["output"]}
        else:
            msg = data.get("msg1", "알 수 없는 오류")
            print(f"[KIS] 미국 주문 실패 ❌  {msg}")
            return {"success": False, "message": msg}

    # ── 공통 인터페이스 ───────────────────────

    def place_order(
        self,
        ticker: str,
        side: str,
        qty: int,
        price: float = 0,
        market: str = "KR",
        order_type: str = "market",
        exchange: str = "NAS"
    ) -> dict:
        """
        통합 주문 인터페이스 (qlib 전략에서 호출)

        market : 'KR' 또는 'US'
        """
        if market == "KR":
            return self.place_kr_order(ticker, side, qty, int(price), order_type)
        else:
            return self.place_us_order(ticker, side, qty, price, exchange, order_type)

    def get_balance(self, market: str = "KR") -> dict:
        """통합 잔고 조회"""
        if market == "KR":
            return self.get_kr_balance()
        else:
            # 미국 잔고는 별도 구현
            return self.get_kr_balance()  # 기본 국내 잔고

    def get_positions(self, market: str = "KR") -> list[dict]:
        """보유 종목 리스트"""
        return self.get_balance(market).get("positions", [])

    def __repr__(self):
        mode = "모의투자" if self.mock else "실거래"
        return f"KISBroker({mode}, 계좌: {self.account_no})"
