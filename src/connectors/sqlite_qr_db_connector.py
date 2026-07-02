"""
SQLite 기반 QR 재고 데이터베이스 구현체.
QRDBConnector 추상 클래스를 구현한다.
"""
import sqlite3
from typing import Any, Dict, List
from pathlib import Path

from src.connectors.qr_db_connector import QRDBConnector


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
                item_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                location TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def fetch_inventory(self) -> List[Dict[str, Any]]:
        """전체 재고 데이터를 조회한다."""
        assert self.conn is not None
        cursor = self.conn.execute("SELECT * FROM inventory")
        return [dict(row) for row in cursor.fetchall()]

    def get_inventory(self) -> List[Dict[str, Any]]:
        """호환성을 위해 fetch_inventory를 래핑한다."""
        return self.fetch_inventory()

    def upsert_item(self, item_data: Dict[str, Any]) -> None:
        """아이템을 삽입하거나 업데이트한다."""
        assert self.conn is not None
        self.conn.execute("""
            INSERT INTO inventory (item_id, name, quantity, location, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(item_id) DO UPDATE SET
                name=excluded.name,
                quantity=excluded.quantity,
                location=excluded.location,
                updated_at=CURRENT_TIMESTAMP
        """, (
            item_data["item_id"],
            item_data["name"],
            item_data["quantity"],
            item_data.get("location", "")
        ))
        self.conn.commit()

    def close(self) -> None:
        """DB 연결을 종료한다."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def adjust_quantity(self, branch_code: str, item_code: str, entry_no: str, delta: int) -> int:
        """
        현재 수량에 delta를 더한다 (IN: +1, OUT: -1).
        해당 레코드가 없으면 quantity=delta로 신규 생성.
        변경 후 수량을 반환한다.
        """
        assert self.conn is not None
        cursor = self.conn.execute(
            "SELECT quantity FROM inventory WHERE branch_code=? AND item_code=? AND entry_no=?",
            (branch_code, item_code, entry_no)
        )
        row = cursor.fetchone()
        current_qty = row["quantity"] if row else 0
        new_qty = current_qty + delta
        self.conn.execute(
            """
            INSERT INTO inventory (branch_code, item_code, entry_no, quantity, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(branch_code, item_code, entry_no) DO UPDATE SET
                quantity=excluded.quantity,
                updated_at=CURRENT_TIMESTAMP
            """,
            (branch_code, item_code, entry_no, new_qty)
        )
        self.conn.commit()
        return new_qty