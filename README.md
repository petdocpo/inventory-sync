# Inventory Sync System

## 개요
QR 재고 시스템과 RAW 재고 시스템의 불일치 알림 시스템입니다.

## 실행 방법
1. 설정 파일 생성:
   `config/settings.env.example`을 복사하여 `config/settings.env`로 생성
2. 필요한 환경 변수 설정:
   - RAW DB 설정
   - QR DB 경로
   - Power Automate Webhook URL
3. 서버 실행:
   `python main.py`

## API 엔드포인트
- `GET /health`: 시스템 상태 확인

## TODO
- [ ] 테스트 스크립트 작성
- [ ] 테스트 실행
- [ ] 최종 보고서 생성