"""
키움증권 Open API+ 연동 모듈
─────────────────────────────────────────────────────────────────
⚠️  키움증권 Open API+는 Windows 전용입니다.
    PyQt5 기반으로 동작하며, 반드시 키움 HTS가 설치된
    Windows 환경에서 실행해야 합니다.

설치 요건:
    pip install PyQt5
    키움증권 Open API+ 설치 (32bit Python 필요!)
    → https://www.kiwoom.com/h/customer/download/VOpenApiInfoView

사용법:
    broker = KiwoomBroker()
    broker.login()
    balance = broker.get_balance()
    broker.place_order("005930", "BUY", qty=10, price=0)  # 시장가 매수
"""

import sys
import time
from typing import Optional


# ──────────────────────────────────────────────
# 플랫폼 체크 (키움은 Windows 전용)
# ──────────────────────────────────────────────
IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    try:
        from PyQt5.QAxContainer import QAxWidget
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtCore import QEventLoop
        KIWOOM_AVAILABLE = True
    except ImportError:
        KIWOOM_AVAILABLE = False
        print("[경고] PyQt5 미설치. pip install PyQt5")
else:
    KIWOOM_AVAILABLE = False


# ──────────────────────────────────────────────
# 주문 데이터 클래스
# ──────────────────────────────────────────────
class Order:
    def __init__(self, ticker: str, side: str, qty: int,
                 price: int = 0, order_type: str = "market"):
        """
        ticker    : 종목코드 (예: '005930')
        side      : 'BUY' 또는 'SELL'
        qty       : 수량
        price     : 지정가일 때 가격 (시장가=0)
        order_type: 'market' 또는 'limit'
        """
        self.ticker = ticker
        self.side = side
        self.qty = qty
        self.price = price
        self.order_type = order_type
        self.order_id = None
        self.status = "pending"

    def __repr__(self):
        return (f"Order({self.side} {self.qty}주 {self.ticker} "
                f"@ {'시장가' if self.price == 0 else f'{self.price:,}원'})")


# ──────────────────────────────────────────────
# 키움 API 래퍼
# ──────────────────────────────────────────────
class KiwoomBroker:
    """
    키움증권 Open API+ 래퍼

    키움 TR 코드 참고:
        OPT10001 - 주식기본정보요청
        OPT10003 - 체결정보요청
        OPTKWFID - 관심종목정보요청 (복수 조회)
        OPW00001 - 예수금상세현황요청
        OPW00018 - 계좌평가잔고내역요청
    """

    # 주문 유형 코드
    ORDER_TYPE = {
        ("BUY",  "market"): 1,   # 시장가 매수
        ("BUY",  "limit"):  1,   # 지정가 매수 (fid_cond=00)
        ("SELL", "market"): 2,   # 시장가 매도
        ("SELL", "limit"):  2,   # 지정가 매도
    }

    PRICE_TYPE = {
        "market": "03",   # 시장가
        "limit":  "00",   # 지정가
    }

    def __init__(self, account_no: str = "", mock: bool = True):
        """
        account_no : 증권 계좌번호 (예: '1234567890')
        mock       : True = 모의투자 서버, False = 실거래 서버
        """
        self.account_no = account_no
        self.mock = mock
        self.ocx = None
        self.event_loop = None
        self.tr_data = {}
        self._connected = False

        if not IS_WINDOWS:
            print("[모드] 비-Windows 환경: 시뮬레이션 모드로 실행")

    # ── 연결 ──────────────────────────────────

    def login(self):
        """키움 API 로그인 (자동 팝업 뜸)"""
        if not KIWOOM_AVAILABLE:
            print("[시뮬레이션] 로그인 성공 (Mock)")
            self._connected = True
            return True

        app = QApplication(sys.argv) if not QApplication.instance() else QApplication.instance()
        self.ocx = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")

        # 이벤트 연결
        self.ocx.OnEventConnect.connect(self._on_connect)
        self.ocx.OnReceiveTrData.connect(self._on_receive_tr)
        self.ocx.OnReceiveChejanData.connect(self._on_chejan)

        # 서버 설정 (모의투자)
        if self.mock:
            self.ocx.dynamicCall("KOASetProperty(QString, QString)", "SIMULATION", "1")

        self.ocx.dynamicCall("CommConnect()")

        self.event_loop = QEventLoop()
        self.event_loop.exec_()

        return self._connected

    def _on_connect(self, err_code):
        """연결 이벤트"""
        if err_code == 0:
            print(f"[키움] 로그인 성공 ({'모의투자' if self.mock else '실거래'})")
            self._connected = True
        else:
            print(f"[키움] 로그인 실패 (코드: {err_code})")
        if self.event_loop:
            self.event_loop.quit()

    # ── 계좌 조회 ─────────────────────────────

    def get_balance(self) -> dict:
        """예수금 및 평가잔고 조회"""
        if not KIWOOM_AVAILABLE:
            # 시뮬레이션 응답
            return {
                "deposit": 10_000_000,           # 예수금
                "total_eval": 15_000_000,         # 총평가금액
                "total_profit_loss": 500_000,     # 총손익
                "total_profit_rate": 3.45,        # 수익률(%)
                "positions": self._mock_positions()
            }

        self.ocx.dynamicCall(
            "SetInputValue(QString, QString)", "계좌번호", self.account_no
        )
        self.ocx.dynamicCall(
            "SetInputValue(QString, QString)", "비밀번호", ""
        )
        self.ocx.dynamicCall(
            "SetInputValue(QString, QString)", "비밀번호입력매체구분", "00"
        )
        self.ocx.dynamicCall(
            "SetInputValue(QString, QString)", "조회구분", "2"
        )
        self.ocx.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "계좌평가잔고내역요청", "OPW00018", 0, "9999"
        )

        self.event_loop = QEventLoop()
        self.event_loop.exec_()
        return self.tr_data.get("OPW00018", {})

    def get_positions(self) -> list[dict]:
        """현재 보유 종목 리스트"""
        balance = self.get_balance()
        return balance.get("positions", [])

    # ── 주문 ──────────────────────────────────

    def place_order(self, ticker: str, side: str, qty: int,
                    price: int = 0, order_type: str = "market") -> Order:
        """
        주식 주문 실행

        Parameters
        ----------
        ticker     : 종목코드 (6자리, 예: '005930')
        side       : 'BUY' 또는 'SELL'
        qty        : 주문 수량
        price      : 지정가 (시장가=0)
        order_type : 'market' 또는 'limit'

        Returns
        -------
        Order 객체
        """
        order = Order(ticker, side, qty, price, order_type)

        if not KIWOOM_AVAILABLE:
            print(f"[시뮬레이션] {order} 접수")
            order.order_id = f"SIM_{int(time.time())}"
            order.status = "filled"
            return order

        order_no = 1 if side == "BUY" else 2

        result = self.ocx.dynamicCall(
            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
            [
                "주식매매",                          # 사용자구분명
                "0101",                              # 화면번호
                self.account_no,                     # 계좌번호
                order_no,                            # 주문유형 (1:매수, 2:매도)
                ticker,                              # 종목코드
                qty,                                 # 주문수량
                price,                               # 주문가격 (시장가=0)
                self.PRICE_TYPE[order_type],          # 거래구분
                ""                                   # 원주문번호
            ]
        )

        if result == 0:
            print(f"[키움] 주문 접수: {order}")
            order.status = "submitted"
        else:
            print(f"[키움] 주문 실패 (코드: {result}): {order}")
            order.status = "failed"

        return order

    def cancel_order(self, order_id: str, ticker: str, qty: int):
        """주문 취소"""
        if not KIWOOM_AVAILABLE:
            print(f"[시뮬레이션] 주문 취소: {order_id}")
            return True

        result = self.ocx.dynamicCall(
            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
            ["주문취소", "0101", self.account_no, 3, ticker, qty, 0, "00", order_id]
        )
        return result == 0

    # ── 시세 조회 ─────────────────────────────

    def get_current_price(self, ticker: str) -> dict:
        """현재가 조회"""
        if not KIWOOM_AVAILABLE:
            import random
            base = {"005930": 72000, "000660": 130000}.get(ticker, 50000)
            return {
                "ticker": ticker,
                "price": base + random.randint(-1000, 1000),
                "change_rate": round(random.uniform(-3, 3), 2),
                "volume": random.randint(100000, 1000000)
            }

        self.ocx.dynamicCall("SetInputValue(QString, QString)", "종목코드", ticker)
        self.ocx.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "주식기본정보요청", "OPT10001", 0, "9999"
        )
        self.event_loop = QEventLoop()
        self.event_loop.exec_()
        return self.tr_data.get("OPT10001", {})

    # ── 이벤트 핸들러 ─────────────────────────

    def _on_receive_tr(self, screen_no, rqname, trcode, recordname,
                       prev_next, data_len, err_code, msg1, msg2):
        """TR 데이터 수신"""
        # 실제 구현에서는 trcode에 따라 데이터 파싱
        if self.event_loop:
            self.event_loop.quit()

    def _on_chejan(self, gubun, item_cnt, fid_list):
        """체결 및 잔고 이벤트"""
        if gubun == "0":
            print(f"[키움] 주문 체결 이벤트")
        elif gubun == "1":
            print(f"[키움] 잔고 변경 이벤트")

    # ── 유틸 ──────────────────────────────────

    def _mock_positions(self) -> list[dict]:
        return [
            {"ticker": "005930", "name": "삼성전자", "qty": 10,
             "avg_price": 70000, "current_price": 72000,
             "profit_loss": 20000, "profit_rate": 2.86},
            {"ticker": "000660", "name": "SK하이닉스", "qty": 5,
             "avg_price": 125000, "current_price": 130000,
             "profit_loss": 25000, "profit_rate": 4.00},
        ]

    def __repr__(self):
        status = "연결됨" if self._connected else "미연결"
        mode = "모의투자" if self.mock else "실거래"
        return f"KiwoomBroker({status}, {mode}, 계좌: {self.account_no})"
