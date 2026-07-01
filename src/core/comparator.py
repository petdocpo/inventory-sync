"""
QR 재고와 RAW 재고를 비교하는 코어 엔진 모듈.
재고 불일치 여부를 판단하고, 필요 시 조정 제안을 생성한다.
"""
from typing import Any, Dict, List


def compare_inventories(qr_inventory: List[Dict[str, Any]], raw_inventory: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    QR 재고와 RAW 재고를 비교하여 차이점을 분석한다.

    Args:
        qr_inventory: QR DB에서 조회한 재고 목록
        raw_inventory: RAW DB에서 조회한 재고 목록

    Returns:
        비교 결과 딕셔너리 (차이점, 일치점 등 포함)
    """
    result: Dict[str, Any] = {
        "qr_items": qr_inventory,
        "raw_items": raw_inventory,
        "differences": [],
        "matches": [],
        "summary": {}
    }

    # TODO: 실제 비교 로직 구현 (추후)
    return result


class InventoryComparator:
    """
    `main.py`에서 사용되는 래퍼 클래스.
    `qr_db`와 `raw_db` 인스턴스를 받아 `compare_inventories`를 호출한다.
    """
    def __init__(self, qr_db, raw_db):
        self.qr_db = qr_db
        self.raw_db = raw_db

    def compare(self) -> Dict[str, Any]:
        """QR DB와 RAW DB의 재고를 조회하고 비교한다."""
        qr_inventory = self.qr_db.fetch_inventory()
        raw_inventory = self.raw_db.fetch_inventory()
        return compare_inventories(qr_inventory, raw_inventory)