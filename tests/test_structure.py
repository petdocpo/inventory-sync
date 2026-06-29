"""
파일/구조 자동 검증 스크립트.
아래 항목을 검증한다:
- 위 폴더/파일 전부 존재하는지
- settings.env.example에 9개 키 전부 포함됐는지
- 각 .py 파일 크기가 0바이트가 아닌지
- qr_db_connector.py에 'class' 또는 'def' 키워드가 포함됐는지 (추상화 여부 확인)
"""
import os
from pathlib import Path

def test_structure():
    base_dir = Path(".")
    errors = []

    # 1. 폴더/파일 존재 확인
    required_paths = [
        "config/settings.env.example",
        "src/connectors/qr_db_connector.py",
        "src/connectors/raw_db_connector.py",
        "src/core/comparator.py",
        "src/core/scheduler.py",
        "src/notifier/teams_notifier.py",
        "src/adjuster/manual_adjuster.py",
        "src/qr_generator/qr_generator.py",
        "main.py",
        "README.md",
        "tests/test_structure.py"
    ]
    for p in required_paths:
        if not (base_dir / p).exists():
            errors.append(f"Missing file/directory: {p}")

    # 2. settings.env.example에 9개 키 포함 확인
    env_path = base_dir / "config/settings.env.example"
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
        required_keys = [
            "RAW_DB_HOST=",
            "RAW_DB_PORT=",
            "RAW_DB_NAME=",
            "RAW_DB_USER=",
            "RAW_DB_PASSWORD=",
            "QR_DB_PATH=",
            "POWER_AUTOMATE_WEBHOOK_URL=",
            "COMPARE_INTERVAL_MINUTES=",
            "SERVER_HOST="
        ]
        for key in required_keys:
            if key not in content:
                errors.append(f"Missing key in settings.env.example: {key}")
    else:
        errors.append("settings.env.example not found (already caught above)")

    # 3. 각 .py 파일 크기가 0바이트가 아닌지 확인
    py_files = list(base_dir.rglob("*.py"))
    for py_file in py_files:
        if py_file.stat().st_size == 0:
            errors.append(f"Empty .py file: {py_file}")

    # 4. qr_db_connector.py에 'class' 또는 'def' 키워드가 포함됐는지 확인
    qr_db_path = base_dir / "src/connectors/qr_db_connector.py"
    if qr_db_path.exists():
        qr_content = qr_db_path.read_text(encoding="utf-8")
        if "class" not in qr_content and "def" not in qr_content:
            errors.append("qr_db_connector.py does not contain 'class' or 'def' (abstraction check)")
    else:
        errors.append("qr_db_connector.py not found (already caught above)")

    if errors:
        print("FAILED:")
        for err in errors:
            print(f" - {err}")
        return False
    else:
        print("SUCCESS: All checks passed.")
        return True

if __name__ == "__main__":
    test_structure()