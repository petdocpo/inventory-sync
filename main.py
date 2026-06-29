"""
FastAPI 애플리케이션 진입점.
재고 불일치 알림 시스템의 메인 서버를 시작한다.
"""
from fastapi import FastAPI

app = FastAPI(title="Inventory Sync System", version="0.1.0")


@app.get("/health")
async def health_check() -> dict:
    """헬스 체크 엔드포인트."""
    return {"status": "ok"}


def main() -> None:
    """메인 진입점."""
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()