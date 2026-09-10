# MatMan — 자재관리 시스템

Python/Flask 기반 자재관리 웹 애플리케이션

## 실행 방법

```bash
pip install flask
python app.py
# → http://localhost:5000 접속
```

## 화면 목록 (23개)

| 경로 | 화면 |
|------|------|
| / | 메인 대시보드 |
| /products | 자재 목록 |
| /lot | LOT 상세 |
| /bom | BOM 관리 |
| /safety-stock | 안전재고 |
| /abc | ABC 분석 |
| /inbound | 입고 처리 |
| /disburse | 불출 처리 |
| /tx-history | 입출고 이력 |
| /picking | 피킹리스트 ★ |
| /approval | 불출 승인 워크플로우 |
| /scanner | 바코드/QR 스캐너 |
| /purchase | 구매 발주 |
| /suppliers | 협력사 |
| /calendar | 발주 캘린더 |
| /production | 생산 실적 |
| /stock-map | 재고 현황 지도 |
| /risk-radar | 납기 리스크 레이더 ★ |
| /forecast | 수요 예측 |
| /simulator | 발주 시뮬레이터 ★ |
| /report | 월간 리포트 |
| /wizard | 안전재고 일괄 갱신 |
| /users | 사용자 관리 |

★ = 완전 인터랙티브 (4단계)

## 프로젝트 구조
```
matman/
├── app.py              Flask 앱 · 25개 라우트
├── requirements.txt
├── static/
│   ├── css/main.css    전역 스타일 (사이드바/헤더/카드/테이블)
│   └── js/main.js      사이드바 토글 · 검색 · Toast
└── templates/
    ├── base.html       공통 레이아웃
    ├── dashboard.html  메인 대시보드
    ├── *.html (22개)   각 화면
    └── embed/ (22개)   iframe 내부 콘텐츠
```
