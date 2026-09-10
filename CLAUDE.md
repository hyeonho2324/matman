# MatMan — 자재관리 시스템 프로젝트 컨텍스트

> **관련 문서**
> - [SAFE_STOCK_DESIGN.md](SAFE_STOCK_DESIGN.md) — 안전재고 전체 설계 명세 + 실측 검증
> - [MEETING_LOG.md](MEETING_LOG.md) — 개발 회의록 11회분 + 결정사항 반영 여부 대조
> - [DEPLOY.md](DEPLOY.md) — 배포 가이드 (PythonAnywhere / GitHub+Render)

## 프로젝트 개요
Python/Flask 기반 자재관리 웹 애플리케이션.
중대형 기업 수준의 자재관리 시스템으로 UI 목업 30개 이상을 개발한 프로젝트.

---

## 기술 스택
- **백엔드**: Python + Flask 3.x
- **프론트엔드**: Jinja2 템플릿 + 순수 HTML/CSS/JS (프레임워크 없음)
- **DB**: SQLite (`data/erp.db`) — `build_db.py`로 accdb+CSV 병합 생성
- **아이콘**: Tabler Icons CDN
- **차트**: Chart.js CDN (필요 시)

---

## 프로젝트 구조
```
matman/
├── app.py                      # Flask 앱 · 25개 라우트
├── requirements.txt            # flask>=3.0.0
├── CLAUDE.md                   # 이 파일
├── static/
│   ├── css/main.css            # 전역 CSS (사이드바/헤더/카드/테이블/배지 등)
│   └── js/main.js              # 사이드바 토글 · 전역 검색 · Toast · MatMan 유틸
├── data/                       # CSV 더미데이터 (비어있음, outputs에서 복사 필요)
└── templates/
    ├── base.html               # 공통 레이아웃 (사이드바 + 상단 헤더)
    ├── dashboard.html          # 메인 대시보드
    ├── *.html (22개)           # 각 화면 (레이아웃만, iframe으로 embed/ 호출)
    └── embed/                  # iframe 내부 실제 UI
        ├── risk_radar.html     ✅ 풀 인터랙티브
        ├── simulator.html      ✅ 풀 인터랙티브
        ├── picking.html        ✅ 풀 인터랙티브
        ├── approval.html       ✅ 풀 인터랙티브
        ├── calendar.html       ✅ 풀 인터랙티브
        ├── scanner.html        ✅ 풀 인터랙티브
        ├── users.html          ✅ 풀 인터랙티브
        └── (나머지 15개)       📋 카드형 → 풀 UI 교체 필요
```

---

## 아키텍처 패턴

### 라우팅 구조
```
/risk-radar (Flask 라우트)
    └── templates/risk_radar.html  ← 레이아웃 (사이드바+헤더, base.html 상속)
            └── <iframe src="/embed/risk_radar">
                    └── templates/embed/risk_radar.html  ← 실제 인터랙티브 UI
```

### 레이아웃 패턴
- `base.html`: 사이드바 + 상단 헤더 공통 레이아웃
- 각 화면 템플릿: `{% extends "base.html" %}` + `{% block content %}`
- embed 파일: 독립 HTML (base.html 미사용, 순수 standalone)

### CSS 변수 (main.css에 정의됨)
```css
--surface-0/1/2: 배경 계층
--border/border-strong: 테두리
--fill-accent: #2a78d6 (파랑)
--fill-success: #1D9E75 (초록)
--fill-warning: #EF9F27 (노랑)
--fill-danger: #E24B4A (빨강)
--text-primary/secondary/muted/accent: 텍스트
--bg-accent/success/warning/danger: 배경 tint
--radius: 6px
```

embed 파일은 CSS 변수 대신 직접 hex 사용 (standalone이므로):
```css
:root {
  --acc:#2a78d6; --ok:#1D9E75; --wn:#EF9F27; --dn:#E24B4A;
  --s0:#F8F7F4; --s1:#EFEEEA; --s2:#E5E4DF;
  --border:#D4D3CE; --txt:#1A1917; --txt3:#898781; --r:6px;
}
```

---

## 메뉴 구조 (app.py MENUS 변수)
```python
대시보드: / (dashboard)
자재 관리: /products, /lot, /bom, /safety-stock, /abc
입출고: /inbound, /disburse, /tx-history, /picking, /approval, /scanner
구매/협력사: /purchase, /suppliers, /calendar
생산: /production
재고 현황: /stock-map
분석/예측: /risk-radar, /forecast, /simulator, /report, /wizard
관리: /users
```

---

## DB 테이블 구조 (SQLite · `data/erp.db`)

`build_db.py`가 원본 2곳을 합쳐 생성. **아래는 실제 존재하는 컬럼만 기재** (실측 확인됨).

| 테이블 | 행수 | 컬럼 | 출처 |
|---|---:|---|---|
| Product_tb | 200 | P_ID(PK,9자리), P_N, **Spec**, BRN, P_Price, MainCat, SubCat, DetailCat, MinOrderQty, PkgUnit | CSV |
| FG_tb | 15 | FG_ID(PK), FG_N, MainCat, SubCat, Unit | CSV |
| BOM_tb | 200 | BOM_ID(PK), FG_ID, P_ID, Spec, BOM_Qty, Unit, BOM_Type | CSV |
| Cat_tb | 75 | MainCat+SubCat+DetailCat(PK), Cat_Name | accdb |
| Company_tb | 20 | BRN(PK), CP_N, Is_Foreign | accdb |
| Location_tb | 5 | Loc_ID(PK), Loc_N | accdb |
| User_tb | 30 | EP_ID(PK,8자리), Name, Birth·Phone(**마스킹**), Position | accdb |
| Lot_tb | 636 | Lot_ID(PK), P_ID, Lot_Date, Loc_ID, P_Qty, EP_ID, H_ID | CSV |
| Transaction_tb | 1,075 | T_ID(PK), Lot_ID, T_Type, T_Date, T_Num, EP_ID | CSV |
| Purchase_Header_tb | 254 | H_ID(PK), BRN, P_Date | CSV |
| Purchase_Detail_tb | 636 | H_ID+Purchase_num(PK), P_ID, P_Qty | CSV |
| Safe_tb | 200 | P_ID(PK), Lead_Time, Sf_Lv, Sf_Num, Price/Sub/Impact/Supply/Usage_Score | CSV |
| Update_Log_tb | 200 | P_ID+Updated_Date(PK), Next_Date | CSV |
| Production_tb | 4,535 | Prod_ID(PK), FG_ID, P_ID, Lot_ID, Prod_Date, Prod_Qty, EP_ID, **Work_Order**, Note | CSV |

**총 14개 테이블 / 8,081행. 참조 무결성 점검 15항목 전부 고아 0행.**

### ⚠️ 존재하지 않는 것

> 2026-09-10 `FG_tb`·`BOM_tb`·`Product_tb`(Spec 포함)를 확보해 아래 목록에서 해제됨.

| 항목 | 실태 |
|---|---|
| `Audit_Log_tb` | **없음** |
| `Location_tb.Loc_DC` | **없음** |
| `Purchase_Header_tb.Status` | **없음** (발주 상태값이 데이터에 없음) |
| `User_tb.PS` | accdb에 평문 비밀번호로 존재하나 **외부 배포를 고려해 erp.db에서 의도적으로 제외**<br>전화번호·생년월일도 `build_db.py` 에서 마스킹 적재 |

### BOM 구조 (실제 테이블 확보됨)

```
FG_tb  15종   완제품 (FG001 공기조정기 … FG015 두산튜브)
BOM_tb 200행  표준 165 + 대체 35
               BOM_Qty  = 완제품 1대당 소요량
               BOM_Type = '표준' 또는 '대체 (→대체대상 P_ID)'
```
대체품은 `Product_tb.Spec`에도 `[대체]`로 표기됨 (35종, BOM_tb 대체 35건과 일치).

**검증**: `Production_tb` 생산실적에서 역산한 BOM과 실제 `BOM_tb`를 대조한 결과
표준 165행 중 역산 대상 **116행이 소요량 100% 일치**(오차 0). 나머지 49행은 등장빈도가 낮아 역산 대상에서 제외된 것.
→ 생산실적과 BOM이 서로 정합한다는 근거로 쓸 수 있다.

### 데이터 출처가 2곳인 이유
| | accdb | DB_csv | 채택 |
|---|---|---|---|
| 협력사·사원·분류·창고 | ✅ 있음 | ❌ 없음 | **accdb** |
| Product_tb | 201행, Spec 없음, 불량행 1건 | 200행, **Spec 있음** | **CSV** |
| Transaction_tb | 824행, 구분 `입고/출고` | 1,075행, 구분 `입고/불출` | **CSV** (최신) |
| Safe_tb | Sf_Num 201건 중 200건 NULL | 200건 전부 채워짐 | **CSV**(`Safe_tb_v2`) |
| Production_tb · FG_tb · BOM_tb | ❌ 없음 | ✅ 있음 | **CSV** |

accdb 는 11차 회의(7/9) 재생성 **이전** 버전이다. `DB_csv/` 가 재생성 결과물이므로 그쪽이 정본.

### P_ID 체계
```
형식: MainCat(1) + SubCat(2) + DetailCat(2) + Seq(4) = 9자리
예시: E01010001 (E=전장, 01=하위분류, 01=세분류, 0001=순번)
MainCat: V(밸브), S(센서), E(전장), U(탱크), T(튜브)
분류명은 Cat_tb에서 조회 (예: V-01-01 → "밸브-공기조정기-메인바디")
```

### T_Type — 실제 데이터에는 2종뿐
```
입고 (636건) / 불출 (439건)
※ 이동·불량·반납·교환·폐기는 설계상 개념일 뿐 데이터에 없음
```

### 창고 구조 — 실제로는 5개뿐
```
L01 A동(밸브)  L02 B동(센서)  L03 C동(전장)  L04 D동(탱크)  L05 E동(튜브)
※ 현장창고(F01/F02), QC 격리구역은 데이터에 없음
```

### 현재고 계산법 (재고 관련 모든 화면의 기준)
```sql
현재고 = 해당 LOT의 입고량(Lot_tb.P_Qty) - 그 LOT의 불출 합계(Transaction_tb T_Type='불출')
```
실측 결과: 안전재고 미달 **67종 / 200종(33.5%)**,
재고자산 A등급 7.89억 · B등급 9.09억 · C등급 1.00억 (총 약 18억원)

### 안전재고 설계 (핵심 도메인 로직)

> **📄 전체 설계 명세는 [SAFE_STOCK_DESIGN.md](SAFE_STOCK_DESIGN.md) 참조.**
> 항목별 채점 기준(단가 10만원 등), Z값, σ 추정 계수, 파레토 로직, 실측 검증 결과가 모두 정리되어 있음.
> 아래는 요약이다.

**설계 사상**: 안전재고를 감으로 정하지 않는다.
**5개 항목 점수제로 등급 산정 → 공식으로 수량 계산해 `Sf_Num`에 반영 → 등급별 주기마다 재계산.**

#### ① 등급 컷오프가 2가지다 (가장 중요)
```
① 신규 등록  Usage_Score 없음 → 4개 항목(만점 12점)   A:10+  B:6~9   C:5-
② 갱신 이후  5개 항목 전체    (만점 15점)             A:13+  B:8~12  C:7-
```
`Usage_Score`는 사람이 매기지 않고 **파레토 분석으로 자동 산정**(누적 75%→A, 90%→B, 그 외 C).
신규 등록 시엔 사용 이력이 없어 NULL이고, 갱신 시 채워진다.

> ✅ **실측 검증 — 예외 0건**
> 신규 70종은 4항목 컷오프로 **불일치 0/70**, 갱신 130종은 5항목 컷오프로 **불일치 0/130**.
> **`Usage_Score` 결측 70건은 데이터 결함이 아니라 "갱신 주기 미도래 신규 등록 상태"다.**

#### ② 채점 항목 (각 A=3 / B=2 / C=1점)
| 컬럼 | 의미 | 3점 기준 |
|---|---|---|
| Price_Score | 단가 | 10만원 이상 |
| Sub_Score | 대체 가능성 | 대체 불가 |
| Impact_Score | 품절 시 영향 | 생산 중단 |
| Supply_Score | 공급 안정성 | 공급처 1곳 / 해외 |
| Usage_Score | 연간 사용금액 | 파레토 상위 (자동 산정) |

#### ③ 계산식
```
SS  = Z × √(L×σ_d² + d²×σ_L²)
ROP = d × L + SS

Z   : A=2.33(99%)  B=1.65(95%)  C=1.28(90%)
L   : Purchase_Header_tb.P_Date → Lot_tb.Lot_Date 차이 평균  ✅ 636쌍 실측가능, 평균 12.1일
d   : Transaction_tb 불출 합계 ÷ 운영일수
σ_d : 초기 추정 A=d×0.20 B=d×0.25 C=d×0.30 (데이터 10건 이상 시 실측 전환)
σ_L : 초기 추정 A=L×0.20 B=L×0.25 C=L×0.30
```

#### ④ 재검토 주기 — **A를 자주, C를 느슨하게** (2026-09-10 변경)
```
A등급 = 30일     B등급 = 90일    C등급 = 180일
```
표준 ABC 재고관리 원칙에 따라 **고가·핵심 자재일수록 자주 검토**한다.

> ⚠️ **원본 데이터는 정반대(A=180/B=90/C=30)였다.**
> 6차 회의(7/01)에서 A=월1회로 정한 것을 10차 회의(7/08)가 근거 기록 없이 뒤집은 것으로 확인됨.
> 원칙에 맞는 6차 안으로 되돌렸다. → `build_db.py`의 `REVIEW_CYCLE_DAYS` 상수에서 관리.

`Update_Log_tb`가 관리 (`Updated_Date` → `Next_Date`). 신규 등록 시에도 이력이 남는다 (70/70 보유).

#### ⑤ 구현 시 반드시 주의할 것
| 항목 | 내용 |
|---|---|
| ⚠️ **출고 구분값** | 설계문서는 `T_Type='출고'`지만 **실제 데이터는 `'불출'`**. `'출고'`로 짜면 결과가 0이 나옴 |
| ⚠️ **운영일수** | 설계는 184일 고정. 실제 거래 범위는 219일(2025-07-03~2026-02-07), 불출만 208일. 기준 확정 필요 |
| ⚠️ **`Sf_Num` 근거 부재** | 결과값만 있고 d·σ가 DB에 없음 → 화면에 근거를 보이려면 `Transaction_tb`에서 계산해야 함 |
| ⚠️ **`Audit_Log_tb`** | 설계상으로만 존재. 실제 데이터에 없음 → 필요 시 신규 생성 |
| ⚠️ **정본 주의** | accdb `Safe_tb`는 `Sf_Num` 200/201 NULL → **`Safe_tb_v2.csv`가 정본** |

---

## 화면 구현 현황

### ✅ 실제 DB 연동 완료
| 화면 | 파일 | 내용 |
|---|---|---|
| **자재 목록** | `embed/products.html` | 200종. 대분류탭(V/S/E/U/T)·필터(재고없음/대체품/A등급/미달/해외)·검색·정렬·CSV.<br>클릭 시 **품번체계 분해**·기본정보·재고·**보유LOT(FIFO)**·**완제품 사용처(BOM)**·안전재고 |
| **안전재고 관리** | `embed/safety_stock.html` | 200종. 필터·정렬·검색·CSV.<br>클릭 시 등급근거(5점수)·**공식대입**·재검토주기·**실적기준 재계산 괴리** |
| **ABC 분석 (파레토)** | `embed/abc.html` | 불출이력×단가로 사용금액 파레토. SVG 차트(막대+누적선+등급경계).<br>**결측 Usage_Score 70건 산정 · 점수변동 59건 · 최종 ABC등급 변동 21종** 추적 |
| **BOM · 자재소요계획** | `embed/bom.html` | 완제품 15종 × BOM 200행.<br>**생산 목표 수량 입력 → 자재별 소요량·부족분·발주필요량 자동 계산**<br>MOQ·포장단위 반영 올림, 대체품 재고 합산 토글, 병목자재·착수가능일 |

두 화면 모두 모바일 대응(820px 이하에서 상세패널이 아래로).

### 🎨 인터랙티브 (더미데이터)
`embed/risk_radar.html` · `embed/simulator.html` · `embed/picking.html`

### 📋 안내문만 있음 (교체 필요)
```
자재 관리 : lot
입출고    : inbound, disburse, tx_history, approval, scanner
구매/생산 : purchase, suppliers, calendar, production
분석/기타 : forecast, report, wizard, stock_map, users
```

---

### ABC 파레토 산정 규칙 (`embed/abc.html`)
```
사용금액 = 불출수량 × 단가          (총 38.9억원 / 실적 있는 192종)
금액 내림차순 정렬 → 누적비율 계산
  누적 75% 이내 → Usage A (3점)
  누적 90% 이내 → Usage B (2점)
  나머지·실적없음 → Usage C (1점)
이 점수를 4개 항목과 합산해 최종 ABC 등급 재판정 (컷오프 13/8/7)
```
실측: 상위 20%가 사용금액 **65.6%** 차지. A 51종(74.5%) / B 41종(15.4%) / C 108종(10.1%)
→ 결측 70건 산정(A 9 / B 8 / C 53), 기존값과 다른 것 59건, **최종 등급이 바뀌는 것 21종**
   변동 내역: 상향 11종(B→A 5, C→B 6) · 하향 10종(B→C 9, A→B 1)

### 자재소요계획 계산 규칙 (`embed/bom.html`)
```
소요량   = BOM_Qty × 생산목표수량
가용재고 = 현재고 (+ 대체품 재고, 토글 시)
부족     = max(소요량 - 가용재고, 0)
발주필요 = 부족을 MinOrderQty 이상으로 올린 뒤 PkgUnit 배수로 올림
           예) 부족 260, MOQ 60, 포장 12 → ceil(260/12)×12 = 264
               부족  60, MOQ 2000       → 2000 (MOQ 하한)
착수가능 = 부족 자재들의 리드타임 중 최댓값
```
대체품은 `BOM_Type = '대체 (→원자재P_ID)'` 형식에서 대상을 파싱해 원자재 행 아래에 붙인다.

---

## 화면에 DB 데이터 넣는 법

1. `db.py` 에 조회 함수 추가 (SQL은 전부 여기 모음)
2. `app.py` 의 `_ctx_<화면명>()` 함수에서 그 함수를 호출해 dict 반환
3. `app.py` 의 `EMBED_CONTEXT` 에 `"화면명": _ctx_화면명` 등록
4. `templates/embed/<화면명>.html` 에서 `{{ rows|tojson }}` 로 받아 JS 로 렌더

> `EMBED_CONTEXT` 에 없는 화면은 데이터 없이 렌더링된다(= 기존 더미 화면 그대로 동작).

---

## 실행 방법

### 로컬 개발
```bash
cd matman
pip install -r requirements.txt
py app.py
# → http://localhost:5000
```
`debug` 는 기본 꺼짐. 켜려면 `FLASK_DEBUG=1 py app.py`

### DB 재생성 (원본 데이터가 바뀐 경우만 · 윈도우 전용)
```bash
py build_db.py
```

### 배포
[DEPLOY.md](DEPLOY.md) 참조. 런타임 의존성은 `flask` + `gunicorn` 뿐이고
`data/erp.db` 파일만 함께 올리면 DB 서버가 따로 필요 없다.
`build_db.py` 의 `pyodbc` 는 개발 PC 전용이라 배포 환경에서는 설치·실행되지 않는다.

---

## 코딩 컨벤션

### embed 파일 작성 규칙
1. 독립 HTML 파일 (DOCTYPE부터 시작)
2. CSS 변수는 `:root`에 직접 정의 (base.html 미상속)
3. Tabler Icons CDN 포함 필수
4. body는 `height:100vh; display:flex; flex-direction:column; overflow:hidden`
5. 최상위 `.wrap`이 `flex:1; display:flex; flex-direction:column; overflow:hidden`
6. 더미데이터는 JS 상단 const로 정의
7. 인터랙션은 순수 JS (라이브러리 최소화)

### 레이아웃 파일 (templates/*.html) 작성 규칙
```html
{% extends "base.html" %}
{% block content %}
<div class="page-header">...</div>
<!-- embed iframe -->
<div style="flex:1;border-radius:12px;overflow:hidden;border:0.5px solid var(--border);min-height:580px">
  <iframe src="/embed/{screen_name}" style="width:100%;height:100%;min-height:580px;border:none;display:block"></iframe>
</div>
{% endblock %}
```

### CSS 클래스 명명 (embed 파일)
- 컨테이너: `.wrap`, `.body`, `.left`, `.right`
- 패널: `.pn` (panel), `.pnh` (panel header)
- 테이블: `table`, `th`, `td` (클래스 없이 태그로)
- 배지: `.bg .bok/.bwn/.bdn/.bac/.bmu`
- 버튼: `.btn`, `.btnp`(primary), `.btns`(success), `.btnd`(danger)
- KPI: `.krow`, `.kc`, `.kl`, `.kv`, `.ks`
- 탭: `.tabs`, `.tab`, `.tc`

---

## 주요 더미데이터 샘플

### 품목 (E01010001)
```
P_ID: E01010001
P_N: 후방카메라 LCD
Spec: CMOS 1/3" 720P
Grade: A
현재고: 8개
안전재고: 16개
리드타임: 64일
일평균사용: 0.5개/일
```

### LOT 샘플
```
LOT202507030001 → E01010001, 잔여 8개, L03 전장구역
LOT202512080001 → E01040001, 잔여 500개, L03 전장구역
```

### 협력사 샘플
```
글로벌디스플레이: E계열 전장품 공급, 리드타임 64일
세종볼트: 볼트류 공급, 리드타임 3~4일
인영오토밸브: V계열 밸브 공급, 리드타임 35일
비테스코센서: S계열 센서 공급, 리드타임 42일
전진탱크산업: U계열 탱크 공급, 리드타임 14~20일
```
