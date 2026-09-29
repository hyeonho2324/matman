# MatMan — 자재관리 시스템

Python/Flask 기반 자재관리 웹 애플리케이션

## 실행 방법

```bash
pip install -r requirements.txt
python app.py
# → http://127.0.0.1:5000 접속
```

> 윈도우에서 `localhost` 로 붙으면 요청마다 2초쯤 더 걸립니다. `127.0.0.1` 을 쓰세요.
> `requirements.txt` 의 waitress 가 로컬 서버로 쓰입니다 — 개발 서버는 큰 응답을
> 간헐적으로 멈춰서 화면이 빈 채로 굳습니다.

## 화면 목록 (24개)

| 경로 | 화면 |
|------|------|
| / | 메인 대시보드 |
| /products | 자재 목록 |
| /lot | LOT 상세 |
| /bom | BOM 관리 |
| /safety-stock | 안전재고 |
| /abc | ABC 분석 |
| /inbound | 입고 처리 ✎ |
| /disburse-request | 불출 요청 ✎ |
| /disburse | 불출 처리 ✎ |
| /tx-history | 입출고 이력 |
| /picking | 피킹리스트 ★ |
| /approval | 불출 승인 ✎ |
| /scanner | 바코드/QR 스캐너 |
| /purchase | 구매 발주 ✎ |
| /suppliers | 협력사 |
| /calendar | 발주 캘린더 |
| /production | 생산 실적 |
| /stock-map | 재고 현황 지도 |
| /risk-radar | 납기 리스크 레이더 ★ |
| /forecast | 수요 예측 |
| /simulator | 발주 시뮬레이터 ★ |
| /report | 월간 리포트 |
| /wizard | 안전재고 일괄 갱신 ✎ |
| /users | 사용자 관리 |

★ = 완전 인터랙티브 (4단계)
✎ = DB 쓰기 — 발주 등록 · 입고 등록(`Lot_tb`·`Transaction_tb`) · 불출 요청(`Disburse_Req_tb`) · 불출 승인(`Appr_Qty`·`Status`) · 불출 등록(`Transaction_tb`) · 안전재고 갱신(`Safe_tb`·`Update_Log_tb`). 발주 → 입고 → 불출 요청 → 승인 → 불출 한 바퀴가 실제로 돌아갑니다. 나머지는 조회 전용입니다.

> 승인되지 않은 요청은 불출 처리 화면에 내려가지 않습니다. 요청자 본인은 승인할 수 없고, 직급별 금액 한도(대리 300만 / 과장 1,000만 / 차장 3,000만 / 부장 무제한)를 넘으면 윗선이 처리합니다.

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
