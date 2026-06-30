"""
QR 재고 데이터베이스 연결을 담당하는 모듈.
SQLite와 Supabase 사이의 추상화를 통해 향후 데이터베이스 교체에 유연하게 대응한다.
"""
import abc
import sqlite3
from typing import Any, Dict, List
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

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

    @abc.abstractmethod
    def adjust_quantity(self, branch_code: str, item_code: str, entry_no: str, delta: int) -> int:
        """
        현재 수량에 delta를 더한다 (IN: +1, OUT: -1).
        해당 레코드가 없으면 quantity=delta로 신규 생성.
        변경 후 수량을 반환한다.
        """
        pass


class SQLiteQRDBConnector(QRDBConnector):
    """SQLite 기반 QR 재고 DB 구현체."""

    def __init__(self, db_path: str = "./data/qr_inventory.db"):
        self.db_path = Path(db_path)
        self.conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        """SQLite DB 연결을 초기화하고 테이블을 생성한다."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_table()

    def _create_table(self) -> None:
        """재고 테이블을 생성한다."""
        assert self.conn is not None
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                branch_code TEXT NOT NULL,
                item_code TEXT NOT NULL,
                entry_no TEXT NOT NULL,
                quantity INTEGER DEFAULT 0,
                last_updated TEXT,
                UNIQUE(branch_code, item_code, entry_no)
            )
        """)
        self.conn.commit()

    def fetch_inventory(self) -> List[Dict[str, Any]]:
        """전체 재고 데이터를 조회한다."""
        assert self.conn is not None
        cursor = self.conn.execute("SELECT * FROM inventory")
        return [dict(row) for row in cursor.fetchall()]

    def upsert_item(self, item_data: Dict[str, Any]) -> None:
        """아이템을 삽입하거나 업데이트한다."""
        assert self.conn is not None
        self.conn.execute("""
            INSERT INTO inventory (branch_code, item_code, entry_no, quantity, last_updated)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(branch_code, item_code, entry_no) DO UPDATE SET
                quantity=excluded.quantity,
                last_updated=CURRENT_TIMESTAMP
        """, (
            item_data["branch_code"],
            item_data["item_code"],
            item_data["entry_no"],
            item_data.get("quantity", 0)
        ))
        self.conn.commit()

    def adjust_quantity(self, branch_code: str, item_code: str, entry_no: str, delta: int) -> int:
        """
        현재 수량에 delta를 더한다 (IN: +1, OUT: -1).
        해당 레코드가 없으면 quantity=delta로 신규 생성.
        변경 후 수량을 반환한다.
        """
        assert self.conn is not None
        # 현재 수량 조회
        cursor = self.conn.execute(
            "SELECT quantity FROM inventory WHERE branch_code=? AND item_code=? AND entry_no=?",
            (branch_code, item_code, entry_no)
        )
        row = cursor.fetchone()
        current_qty = row["quantity"] if row else 0
        new_qty = current_qty + delta
        # Upsert
        self.conn.execute("""
            INSERT INTO inventory (branch_code, item_code, entry_no, quantity, last_updated)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(branch_code, item_code, entry_no) DO UPDATE SET
                quantity=excluded.quantity,
                last_updated=CURRENT_TIMESTAMP
        """, (branch_code, item_code, entry_no, new_qty))
        self.conn.commit()
        return new_qty

    def close(self) -> None:
        """DB 연결을 종료한다."""
        if self.conn:
            self.conn.close()
            self.conn = None