"""
수동 재고 조정 UI 엔드포인트를 담당하는 모듈.
재고 수동 조정 요청을 처리하고, 조정 결과를 저장.
"""
from typing import Any, Dict
from src.connectors.sqlite_qr_db_connector import SQLiteQRDBConnector
from src.connectors.raw_db_connector import RawDBConnector


class ManualAdjuster:
    """
    수동 재고 조정을 처리하는 클래스.
    QR DB와 RAW DB를 받아 조정 작업을 수행한다.
    """
    def __init__(self, qr_db: SQLiteQRDBConnector, raw_db: RawDBConnector):
        self.qr_db = qr_db
        self.raw_db = raw_db

    def adjust_inventory(self, branch_code: str, item_code: str, entry_no: str, delta: int) -> None:
        """
        재고 수량을 조정한다.
        """
        # TODO: 실제 조정 로직 구현 (추후)
        pass

    def get_adjustment_logs(self) -> list:
        """
        조정 로그를 반환한다.
        """
        # TODO: 실제 로그 구현 (추후)
        return []