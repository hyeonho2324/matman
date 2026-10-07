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

데이터베이스(`data/erp.db`, 25테이블 8,355행)는 받은 그대로 들어 있어 따로 만들 필요가
없습니다. `build_db.py` 는 원본 `ERP.accdb` · CSV 에서 이 파일을 다시 만드는 스크립트라
원본(`../ProductManager`, `../DB_csv`)이 있어야 돌아갑니다.

## 화면 목록 (27개)

| 경로 | 화면 |
|------|------|
| / | 메인 대시보드 |
| /products | 자재 목록 ✎ |
| /lot | LOT 상세 |
| /bom | BOM 관리 |
| /safety-stock | 안전재고 ✎ |
| /abc | ABC 분석 |
| /inbound | 입고 처리 ✎ |
| /disburse-request | 불출 요청 ✎ |
| /disburse | 불출 처리 ✎ |
| /return | 현장 반납 ✎ |
| /tx-history | 입출고 이력 |
| /picking | 피킹리스트 ★ |
| /approval | 불출 승인 ✎ |
| /scanner | 바코드/QR 스캐너 — 휴대폰 카메라로 직접 읽는다 |
| /order-plan | 발주 제안 ✎ |
| /purchase | 구매 발주 ✎ |
| /suppliers | 협력사 |
| /calendar | 발주 캘린더 |
| /production | 생산 실적 ✎ |
| /stock-map | 재고 현황 지도 |
| /stock-count | 재고 실사 ✎ |
| /risk-radar | 납기 리스크 레이더 ★ |
| /forecast | 수요 예측 |
| /simulator | 발주 시뮬레이터 ★ |
| /report | 월간 리포트 |
| /wizard | 안전재고 일괄 갱신 ✎ |
| /users | 사용자 관리 |

★ = 완전 인터랙티브 (4단계)
✎ = DB 쓰기

## 돌아가는 업무 한 바퀴

**발주 → 입고 → (검수) → 불출 요청 → 승인 → 피킹 → 불출 → 생산 실적 → 반납 → 실사**
가 실제로 한 바퀴 돕니다. 각 단계가 `Transaction_tb` 에 거래를 남기고, 그 거래가 다음
단계의 재고 계산에 그대로 쓰입니다.

| 쓰는 일 | 남기는 것 |
|---|---|
| 발주 등록 | `Purchase_Header_tb` · `Purchase_Detail_tb` (그 시점 단가를 박아 둠) |
| 입고 등록 | `Lot_tb` · `Transaction_tb`(입고) |
| 입고 검수 불량 | `Inbound_Claim_tb` · `Transaction_tb`(불량) |
| 불출 요청 | `Disburse_Req_tb` · `Disburse_Req_Item_tb` |
| 불출 승인 | `Appr_Qty` · `Status` |
| 불출 등록 | `Transaction_tb`(불출) — FIFO 로 LOT 을 서버가 정함 |
| 생산 실적 | `Production_tb` |
| **현장 반납** | `Site_Return_tb` · `Transaction_tb`(반납) |
| **반납 검수 불량** | `Transaction_tb`(불량) + 협력사 귀책이면 `Inbound_Claim_tb` |
| **재고 실사** | `Stock_Count_tb` · `Stock_Count_Item_tb` · 차이만큼 조정 거래 |
| 자재 마스터 | `Product_tb` · `Safe_tb` (엑셀·CSV 일괄 등록) |
| **단가 변경** | `Price_Log_tb` (사유 필수, 30% 넘게 흔들리면 경고) |
| 안전재고 갱신 | `Safe_tb` · `Update_Log_tb` (되돌리기 가능) |
| 안전재고 수동 조정 | `Safe_Override_tb` (근거 5자 이상) |

처리 대기 중인 일은 **좌측 메뉴 배지와 상단 알림**에 어느 화면에서나 같은 숫자로 뜹니다.

> 승인되지 않은 요청은 불출 처리 화면에 내려가지 않습니다. 요청자 본인은 승인할 수 없고,
> 직급별 금액 한도(대리 300만 / 과장 1,000만 / 차장 3,000만 / 부장 무제한)를 넘으면
> 윗선이 처리합니다.

### 재고를 세 군데로 나눠 셉니다

```
창고 가용 = 입고 − 불출 − 불량 − 폐기 + 반납
현장 보유 = 불출 − 생산투입 − 반납
LOT 잔여 = 입고 − (불출 + 불량 + 폐기 − 반납)
```

반납은 **반대 방향 거래를 새로 남기는 방식**입니다. 불출을 지우지 않으므로 "그때 나갔다"는
사실이 이력에 남고, 요청·전표·생산 투입 이력도 어긋나지 않습니다.

## 프로젝트 구조
```
matman/
├── app.py              Flask 앱 · 라우트 91개 (화면 26 · API 56 · 쓰기 43)
├── db.py               SQL 전부 — 화면은 SQL 을 모릅니다
├── build_db.py         원본 accdb·CSV → data/erp.db 재생성
├── barcode.py          Code128 / QR 생성 (외부 라이브러리 없음)
├── requirements.txt
├── data/erp.db         25테이블 8,355행
├── static/
│   ├── css/main.css    전역 스타일 (사이드바/헤더/카드/테이블)
│   └── js/main.js      사이드바 토글 · 검색 · Toast
└── templates/
    ├── base.html       공통 레이아웃
    ├── *.html (29개)   각 화면의 겉틀
    └── embed/ (26개)   iframe 안에 들어가는 알맹이
```

## 문서

| 파일 | 내용 |
|---|---|
| `CLAUDE.md` | 설계 결정과 그 이유 — 왜 이렇게 만들었는지 |
| `MEETING_LOG.md` | 요구사항이 바뀌어 온 기록 |
| `SAFE_STOCK_DESIGN.md` | 안전재고 공식과 등급 기준 |
| `RETURN_QC_DESIGN.md` | 반납 검수 설계 |
| `DEPLOY.md` | 배포 절차 |

## 알려진 한계

- **거래 종류 7종 중 `교환` 만 화면에서 만들 수 없습니다.** 나머지 6종은
  입고·불출·반납·검수·실사 화면에서 나옵니다 (`폐기`·`이동` 은 재고 실사의
  조정 사유로 생깁니다).
- `Transaction_tb` 에 창고 구역 칸이 없어 **이동 거래 자체는 어디서 어디로인지
  남기지 못합니다.** 실사의 구역오류 조정은 `Lot_tb.Loc_ID` 를 바꿔 현재 위치만
  맞춥니다 — 이동 전 구역은 실사 항목(`Stock_Count_Item_tb`)에만 남습니다.
- 로그인이 없습니다. 담당자는 화면에서 고릅니다.
- 재고자산 평가는 **현재 단가 기준**입니다. LOT 별 취득원가(매입 시점 단가)로 평가하는
  방식은 넣지 않았습니다.
