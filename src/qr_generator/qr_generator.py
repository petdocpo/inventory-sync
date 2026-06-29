"""
지점별 품목 QR 이미지 생성 모듈.
qrcode 라이브러리를 사용해 QR 코드 이미지를 생성하고 저장.
"""
import qrcode
from PIL import Image
from typing import Dict, Any


def generate_qr_image(item_data: Dict[str, Any], save_path: str) -> None:
    """
    QR 코드 이미지를 생성하고 지정된 경로에 저장합니다.

    Args:
        item_data: 품목 정보 (item_id, name 등 포함)
        save_path: 이미지 저장 경로
    """
    pass