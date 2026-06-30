"""
QR 코드 생성 모듈.
지점 코드 + 품목 코드 + 서버 URL 조합으로 입고(IN) / 출고(OUT) QR 이미지를 생성한다.
"""
import qrcode
from pathlib import Path
from typing import Dict


def generate_qr(
    server_url: str,
    branch_code: str,
    item_code: str,
    entry_no: str,
    scan_type: str,   # "IN" 또는 "OUT"
    output_dir: str = "./qr_codes"
) -> str:
    """
    QR 코드 이미지를 생성하고 저장 경로를 반환한다.
    QR에 인코딩되는 URL 형식:
      {server_url}/scan?branch_code={branch_code}
                       &item_code={item_code}
                       &entry_no={entry_no}
                       &scan_type={scan_type}
    저장 파일명: {branch_code}_{item_code}_{entry_no}_{scan_type}.png
    """
    # URL 구성
    url = f"{server_url}/scan?branch_code={branch_code}&item_code={item_code}&entry_no={entry_no}&scan_type={scan_type}"
    
    # 출력 디렉토리 생성
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # 파일명 생성
    filename = f"{branch_code}_{item_code}_{entry_no}_{scan_type}.png"
    file_path = output_path / filename
    
    # QR 코드 생성 및 저장
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(file_path)
    
    return str(file_path)


def generate_qr_pair(
    server_url: str,
    branch_code: str,
    item_code: str,
    entry_no: str,
    output_dir: str = "./qr_codes"
) -> Dict[str, str]:
    """
    IN / OUT QR을 한 쌍으로 생성한다.
    반환: {"IN": "저장경로", "OUT": "저장경로"}
    """
    in_path = generate_qr(server_url, branch_code, item_code, entry_no, "IN", output_dir)
    out_path = generate_qr(server_url, branch_code, item_code, entry_no, "OUT", output_dir)
    return {"IN": in_path, "OUT": out_path}