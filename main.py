"""
FastAPI 애플리케이션 진입점.
재고 불일치 알림 시스템의 메인 서버를 시작한다.
"""
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
import uvicorn
import os
from pathlib import Path
from src.connectors.sqlite_qr_db_connector import SQLiteQRDBConnector
from src.connectors.raw_db_connector import MockRawDBConnector
from src.core.comparator import InventoryComparator
from src.core.scheduler import Scheduler
from src.notifier.teams_notifier import TeamsNotifier
from src.adjuster.manual_adjuster import ManualAdjuster
from src.qr_generator.qr_generator import generate_qr_pair
import json

app = FastAPI(title="Inventory Sync System", version="0.1.0")

# 템플릿 및 정적 파일 설정
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/qr_codes", StaticFiles(directory="qr_codes"), name="qr_codes")

# 컴포넌트 초기화
qr_db = SQLiteQRDBConnector()
qr_db.connect()  # Add database connection initialization
raw_db = MockRawDBConnector()
comparator = InventoryComparator(qr_db, raw_db)
notifier = TeamsNotifier()
adjuster = ManualAdjuster(qr_db, raw_db)
scheduler = Scheduler(comparator, notifier)

# 스케줄러 시작
scheduler.start()

@app.get("/health")
async def health_check() -> dict:
    """헬스 체크 엔드포인트."""
    return {"status": "ok"}

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """대시보드 페이지."""
    inventory = qr_db.get_inventory()
    comparison = comparator.compare()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "inventory": inventory,
            "comparison": comparison,
        },
    )

@app.get("/scan", response_class=HTMLResponse)
async def scan_get(request: Request):
    """QR 스캔 폼 (GET)."""
    return templates.TemplateResponse(
        "scan.html",
        {"request": request},
    )

@app.post("/scan")
async def scan_post(
    branch_code: str = Form(...),
    item_code: str = Form(...),
    entry_no: str = Form(...),
    scan_type: str = Form(...),  # IN or OUT
):
    """QR 스캔 처리 (POST)."""
    if scan_type == "IN":
        # 입고 처리
        qr_db.adjust_quantity(branch_code, item_code, entry_no, 1)
    else:
        # 출고 처리
        qr_db.adjust_quantity(branch_code, item_code, entry_no, -1)
    return RedirectResponse(url="/", status_code=303)

@app.get("/inventory")
async def get_inventory():
    """현재 재고 조회 API."""
    inventory = qr_db.get_inventory()
    return {"inventory": inventory}

@app.get("/adjust", response_class=HTMLResponse)
async def adjust_get(request: Request):
    """재고 조정 폼 (GET)."""
    logs = adjuster.get_adjustment_logs()
    return templates.TemplateResponse(
        "adjust.html",
        {"request": request, "logs": logs},
    )

@app.post("/adjust")
async def adjust_post(
    branch_code: str = Form(...),
    item_code: str = Form(...),
    entry_no: str = Form(...),
    delta: int = Form(...),
):
    """재고 조정 처리 (POST)."""
    adjuster.adjust_inventory(branch_code, item_code, entry_no, delta)
    return RedirectResponse(url="/adjust", status_code=303)

@app.get("/qr", response_class=HTMLResponse)
async def qr_get(request: Request):
    """QR 코드 생성 폼 (GET)."""
    return templates.TemplateResponse(
        "qr_generate.html",
        {"request": request},
    )

@app.post("/qr/download")
async def qr_download_post(
    branch_code: str = Form(...),
    item_code: str = Form(...),
    entry_no: str = Form(...),
    server_url: str = Form(...),
):
    """QR 코드 생성 및 다운로드 (POST)."""
    # QR 코드 생성
    qr_paths = generate_qr_pair(server_url, branch_code, item_code, entry_no, "./qr_codes")
    
    # ZIP 파일 생성 및 반환 (간단히 첫 번째 파일 반환으로 대체)
    # 실제 구현에서는 ZIP을 만들어야 하지만, 여기서는 예시로 첫 번째 이미지 반환
    file_path = qr_paths["IN"]
    if os.path.exists(file_path):
        return StreamingResponse(
            open(file_path, "mb"),
            media_type="image/png",
            headers={"Content-Disposition": f"attachment; filename={os.path.basename(file_path)}"},
        )
    raise HTTPException(status_code=404, detail="QR code not found")

@app.get("/compare/now")
async def compare_now():
    """즉시 비교 실행 및 결과 반환."""
    try:
        result = comparator.compare()
        # 알림 전송
        notifier.notify_discrepancy(result)
        return {"status": "completed", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def main() -> None:
    """메인 진입점."""
    # Use environment variable for port, default to 8000 if not set
    port = int(os.getenv("SERVER_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()