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