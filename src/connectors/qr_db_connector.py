"""
QR 재고 데이터베이스 연결을 담당하는 모듈.
SQLite와 Supabase 사이의 추상화를 통해 향후 데이터베이스 교체에 유연하게 대응한다.
"""
import abc
from typing import Any, Dict, List

class QRDBConnector(abc.ABC):
    """SQLite 및 Supabase를 위한 추상 DB 커넥터 인터페이스."""

    @abc.abstractmethod
    def connect(self) -> None:
        """DB 연결 초기화."""
        pass

    @abc.abstractmethod
    def fetch_inventory(self) -> List[Dict[str, Any]]:
        """전체 재고 데이터를 조회한다."""
        pass

    @abc.abstractmethod
    def upsert_item(self, item_data: Dict[str, Any]) -> None:
        """아이템을 삽입하거나 업데이트한다."""
        pass

    @abc.abstractmethod
    def close(self) -> None:
        """DB 연결을 종료한다."""
        pass