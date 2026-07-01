"""
Raw 재고 데이터베이스 연결을 담당하는 모듈.
추후 DB 종류가 변경될 수 있음을 고려해 추상화 인터페이스를 제공한다.
"""
import abc
from typing import Any, Dict, List

class RawDBConnector(abc.ABC):
    """RAW 재고 DB 연결을 위한 추상 클래스."""

    @abc.abstractmethod
    def connect(self) -> None:
        """RAW DB 연결 초기화."""
        pass

    @abc.abstractmethod
    def fetch_inventory(self) -> List[Dict[str, Any]]:
        """RAW 재고 데이터를 조회한다."""
        pass

    @abc.abstractmethod
    def close(self) -> None:
        """RAW DB 연결을 종료한다."""
        pass

class MockRawDBConnector(RawDBConnector):
    """테스트용 Mock 구현체, 고정된 재고 데이터를 반환한다."""

    def __init__(self):
        self._data = [
            {"item_id": "A001", "name": "Item A", "quantity": 10, "location": "Warehouse"},
            {"item_id": "B002", "name": "Item B", "quantity": 5, "location": "Store"},
        ]

    def connect(self) -> None:
        # Mock 연결, 실제 동작 없음
        pass

    def fetch_inventory(self) -> List[Dict[str, Any]]:
        return self._data

    def close(self) -> None:
        # Mock 종료, 실제 동작 없음
        pass