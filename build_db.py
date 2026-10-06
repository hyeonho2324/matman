# -*- coding: utf-8 -*-
"""
ERP 데이터를 SQLite 파일(data/erp.db) 하나로 만들어 주는 스크립트.

[왜 두 곳에서 가져오나]
  - 마스터 데이터(자재/협력사/사원/분류/창고)는 ERP.accdb 에만 있음
  - 거래 데이터(입출고/LOT/발주/생산/안전재고)는 DB_csv/ 가 더 최신이고 완전함
    · accdb  Transaction_tb: 824행, 구분이 '입고/출고'
    · CSV    Transaction_tb: 1,075행, 구분이 '입고/불출'  ← 이쪽이 맞음
    · accdb  Safe_tb.Sf_Num: 201건 중 200건이 비어 있음
    · CSV    Safe_tb_v2   : 200건 전부 채워져 있음        ← 이쪽이 맞음
  그래서 마스터는 accdb, 거래는 CSV 에서 가져와 합친다.
  단 Product_tb 는 예외로 CSV 를 쓴다 — accdb 판에는 Spec 컬럼이 없고
  자재명이 "123"인 불량행이 섞여 있다.

[안전재고 재검토 주기]
  원본은 A=180/B=90/C=30 이지만 ABC 재고관리 원칙과 정반대라
  A=30/B=90/C=180 으로 되돌려 Next_Date 를 재계산한다. (REVIEW_CYCLE_DAYS 참조)

[실행 방법]
    py build_db.py

  몇 번을 실행해도 결과가 같다(매번 처음부터 다시 만듦).
  ERP.accdb 를 읽으려면 pyodbc 가 필요하고 윈도우에서만 동작한다.
  이미 만들어 둔 data/erp.db 가 있으면 다른 PC/서버에서는 이 스크립트 없이도 앱이 돌아간다.
"""
import csv
import os
import sqlite3
import sys
from datetime import datetime

# ── 경로 설정 ────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))        # .../matman
ROOT = os.path.dirname(HERE)                             # .../claude code accept
ACCDB = os.path.join(ROOT, "ProductManager", "ERP.accdb")
CSV_DIR = os.path.join(ROOT, "DB_csv")
OUT_DIR = os.path.join(HERE, "data")
OUT_DB = os.path.join(OUT_DIR, "erp.db")


# ── 공통 유틸 ────────────────────────────────────────────────
def clean_key(k):
    """엑셀에서 저장된 CSV의 첫 컬럼명에 붙는 보이지 않는 문자(BOM)를 떼어낸다."""
    return (k or "").replace("﻿", "").strip()


def norm_date(v):
    """어떤 형태로 들어오든 날짜를 'YYYY-MM-DD' 문자열로 통일한다."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s or None


def norm_int(v):
    """빈칸은 None(=DB의 NULL)으로, 숫자는 정수로."""
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def norm_str(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def mask_phone(v):
    """전화번호 가운데 4자리를 가린다.  01032181960 → 010-****-1960

    공개 저장소에 올라가는 데이터라 원본을 그대로 두지 않는다.
    (생성된 가상 데이터지만 개인정보 형태를 띠므로)
    """
    s = "".join(ch for ch in str(v or "") if ch.isdigit())
    if len(s) < 7:
        return None if not s else s
    return f"{s[:3]}-****-{s[-4:]}"


def mask_birth(v):
    """생년월일을 연도만 남긴다.  19980508 → 1998"""
    s = "".join(ch for ch in str(v or "") if ch.isdigit())
    return s[:4] if len(s) >= 4 else None


def read_csv(name):
    """DB_csv 폴더의 CSV 한 개를 읽어 딕셔너리 목록으로 돌려준다."""
    path = os.path.join(CSV_DIR, name)
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{clean_key(k): v for k, v in row.items()} for row in csv.DictReader(f)]


def read_accdb(table):
    """ERP.accdb 의 테이블 한 개를 읽어 딕셔너리 목록으로 돌려준다."""
    import pyodbc

    conn = pyodbc.connect(
        r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=" + ACCDB + ";"
    )
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM [{table}]")
        cols = [clean_key(d[0]) for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


# ── 테이블 정의 ──────────────────────────────────────────────
# 실제 데이터에 있는 컬럼만 정의한다.
# (Loc_DC, Purchase_Header_tb.Status 등은 실제 데이터에 없어 제외)
SCHEMA = """
PRAGMA foreign_keys = OFF;

DROP TABLE IF EXISTS Cat_tb;
CREATE TABLE Cat_tb (
    MainCat    TEXT NOT NULL,          -- 대분류 1자리 (V밸브 S센서 E전장 U탱크 T튜브)
    SubCat     TEXT NOT NULL,          -- 중분류 2자리
    DetailCat  TEXT NOT NULL,          -- 소분류 2자리
    Cat_Name   TEXT,                   -- 분류명 (예: 밸브-공기조정기-메인바디)
    PRIMARY KEY (MainCat, SubCat, DetailCat)
);

DROP TABLE IF EXISTS Company_tb;
CREATE TABLE Company_tb (
    BRN        TEXT PRIMARY KEY,       -- 협력사 코드
    CP_N       TEXT,                   -- 협력사명
    Is_Foreign TEXT                    -- 해외 여부 Y/N
);

DROP TABLE IF EXISTS Location_tb;
CREATE TABLE Location_tb (
    Loc_ID     TEXT PRIMARY KEY,       -- 창고 코드 (L01~L05)
    Loc_N      TEXT                    -- 창고명
);

DROP TABLE IF EXISTS User_tb;
CREATE TABLE User_tb (
    EP_ID      TEXT PRIMARY KEY,       -- 사번 8자리
    Name       TEXT,
    Birth      TEXT,                   -- 연도만 (마스킹)
    Phone      TEXT,                   -- 가운데 4자리 가림 (마스킹)
    Position   TEXT                    -- 직급
    -- 원본 accdb 의 PS(평문 비밀번호) 컬럼은 외부 배포를 고려해 의도적으로 제외함
);

DROP TABLE IF EXISTS Product_tb;
CREATE TABLE Product_tb (
    P_ID        TEXT PRIMARY KEY,      -- 자재코드 9자리 = 대(1)+중(2)+소(2)+일련(4)
    P_N         TEXT,                  -- 자재명
    Spec        TEXT,                  -- 규격/재질 (예: M6×12 SUS304, [대체] 표기 포함)
    BRN         TEXT,                  -- 공급 협력사
    P_Price     REAL,                  -- 단가
    MainCat     TEXT,
    SubCat      TEXT,
    DetailCat   TEXT,
    MinOrderQty INTEGER,               -- 최소발주수량(MOQ)
    PkgUnit     INTEGER                -- 포장단위
);

DROP TABLE IF EXISTS Purchase_Header_tb;
CREATE TABLE Purchase_Header_tb (
    H_ID   TEXT PRIMARY KEY,           -- 발주번호 PO...
    BRN    TEXT,                       -- 발주처
    P_Date TEXT                        -- 발주일
);

DROP TABLE IF EXISTS Purchase_Detail_tb;
CREATE TABLE Purchase_Detail_tb (
    H_ID         TEXT NOT NULL,
    Purchase_num INTEGER NOT NULL,     -- 발주서 내 순번
    P_ID         TEXT,
    P_Qty        INTEGER,              -- 발주수량
    Unit_Price   INTEGER,              -- 발주 시점 단가 (스냅샷)
    PRIMARY KEY (H_ID, Purchase_num)
);

DROP TABLE IF EXISTS Inbound_Claim_tb;
CREATE TABLE Inbound_Claim_tb (
    Claim_ID   TEXT PRIMARY KEY,     -- 클레임번호 RMA+YYYYMMDD+4자리
    Lot_ID     TEXT NOT NULL,        -- 불량이 난 LOT
    H_ID       TEXT,                 -- 원 발주 (있으면)
    P_ID       TEXT NOT NULL,
    Claim_Qty  INTEGER NOT NULL,     -- 불량 수량
    Claim_Type TEXT NOT NULL,        -- 입고검수 / 사용중발견
    Resolution TEXT NOT NULL,        -- 대체입고 / 환불 / 폐기 / 미정
    Status     TEXT NOT NULL,        -- 접수 / 완료
    Claim_Date TEXT NOT NULL,
    EP_ID      TEXT NOT NULL,
    Reason     TEXT,
    T_ID       TEXT,                 -- 불량 판정 거래
    New_Lot_ID TEXT,                 -- 대체품으로 생성된 LOT
    Done_Date  TEXT,
    Amount     INTEGER NOT NULL DEFAULT 0   -- 클레임 금액 (환불·청구 근거)
);

DROP TABLE IF EXISTS Disburse_Req_tb;
CREATE TABLE Disburse_Req_tb (
    Req_ID     TEXT PRIMARY KEY,     -- 요청번호 REQ+YYYYMMDD+4자리
    Req_Date   TEXT NOT NULL,        -- 요청일 (= 불출 희망일)
    FG_ID      TEXT,                 -- 생산할 완제품
    Plan_Qty   INTEGER,              -- 생산 목표 대수 (실적이 아니라 계획이다)
    Work_Order TEXT,                 -- 작업지시번호 WO+YYYYMMDD+4자리
    EP_ID      TEXT NOT NULL,        -- 요청자 (생산 작업자)
    Status     TEXT NOT NULL,        -- 요청 / 승인 / 반려 / 일부불출 / 불출완료 / 취소
    Note       TEXT,
    Appr_EP_ID TEXT,                 -- 승인자 (자재팀). 요청자와 같을 수 없다
    Appr_Date  TEXT,                 -- 승인·반려일
    Appr_Note  TEXT                  -- 승인 의견 / 반려 사유(반려는 필수)
);

DROP TABLE IF EXISTS Disburse_Req_Item_tb;
CREATE TABLE Disburse_Req_Item_tb (
    Req_ID    TEXT NOT NULL,
    Req_num   INTEGER NOT NULL,      -- 요청서 내 순번
    P_ID      TEXT NOT NULL,
    Need_Qty  INTEGER NOT NULL,      -- BOM 산출 소요량 (요청 근거)
    Site_Qty  INTEGER NOT NULL DEFAULT 0,  -- 요청 시점 현장 보유 (요청 근거)
    Stock_Qty INTEGER NOT NULL,      -- 요청 시점 자재창고 재고 (요청 근거)
    Req_Qty   INTEGER NOT NULL,      -- 실제 요청 수량
    Pkg_Unit  INTEGER,               -- 요청 시점 포장단위
    Is_Manual TEXT NOT NULL,         -- Y = 포장단위 배수가 아닌 직접 입력
    Appr_Qty  INTEGER,               -- 승인 수량. 미승인은 NULL, 0 이면 그 라인 반려
    Done_Qty  INTEGER NOT NULL DEFAULT 0,   -- 실제 불출된 누계
    PRIMARY KEY (Req_ID, Req_num)
);

DROP TABLE IF EXISTS Purchase_Change_tb;
CREATE TABLE Purchase_Change_tb (
    Chg_ID       TEXT PRIMARY KEY,     -- 변경번호 CHG+YYYYMMDD+4자리
    H_ID         TEXT NOT NULL,        -- 발주번호
    Purchase_num INTEGER NOT NULL,     -- 발주서 내 순번 (어느 라인이 바뀌었나)
    Ord_P_ID     TEXT NOT NULL,        -- 발주한 품번
    In_P_ID      TEXT NOT NULL,        -- 실제 입고된 품번 (대체면 Ord 와 다르다)
    Ord_Qty      INTEGER NOT NULL,     -- 발주 수량
    In_Qty       INTEGER NOT NULL,     -- 실입고 수량
    Ord_Amt      INTEGER NOT NULL,     -- 발주 금액
    In_Amt       INTEGER NOT NULL,     -- 실입고 금액
    Diff_Amt     INTEGER NOT NULL,     -- In_Amt - Ord_Amt (양수면 더 받은 것)
    Chg_Type     TEXT NOT NULL,        -- 수량변경 / 대체입고 / 대체+수량변경
    Settle       TEXT NOT NULL,        -- 추가청구 / 차감 / 정산없음
    Reason       TEXT,                 -- 거래처와 협의한 내용
    Chg_Date     TEXT NOT NULL,        -- 입고일
    EP_ID        TEXT NOT NULL,        -- 처리자
    Lot_ID       TEXT                  -- 이 변경으로 생성된 LOT
);

DROP TABLE IF EXISTS Lot_tb;
CREATE TABLE Lot_tb (
    Lot_ID   TEXT PRIMARY KEY,         -- LOT번호
    P_ID     TEXT,
    Lot_Date TEXT,                     -- 입고일
    Loc_ID   TEXT,                     -- 보관 창고
    P_Qty    INTEGER,                  -- 입고수량
    EP_ID    TEXT,                     -- 처리자
    H_ID     TEXT                      -- 연결된 발주번호
);

DROP TABLE IF EXISTS Transaction_tb;
CREATE TABLE Transaction_tb (
    T_ID   TEXT PRIMARY KEY,           -- 거래번호
    Lot_ID TEXT,
    T_Type TEXT,                       -- 입고 / 불출
    T_Date TEXT,
    T_Num  INTEGER,                    -- 수량
    EP_ID  TEXT
);

DROP TABLE IF EXISTS Safe_tb;
CREATE TABLE Safe_tb (
    P_ID          TEXT PRIMARY KEY,
    Lead_Time     INTEGER,             -- 리드타임(일)
    Sf_Lv         TEXT,                -- 등급 A/B/C (5개 점수 합계로 결정)
    Sf_Num        INTEGER,             -- 안전재고 수량 = 공식 계산 결과
    Price_Score   INTEGER,             -- 단가        1~3점
    Sub_Score     INTEGER,             -- 대체가능성  1~3점
    Impact_Score  INTEGER,             -- 결품영향도  1~3점
    Supply_Score  INTEGER,             -- 공급안정성  1~3점
    Usage_Score   INTEGER              -- 사용빈도    1~3점 (원본에 70건 결측)
);

DROP TABLE IF EXISTS Update_Log_tb;
CREATE TABLE Update_Log_tb (
    P_ID         TEXT NOT NULL,
    Updated_Date TEXT NOT NULL,        -- 안전재고 재계산 실행일
    Next_Date    TEXT,                 -- 다음 재계산 예정일 (A+30 B+90 C+180일)
    -- 아래는 화면에서 실행한 갱신분만 채워진다. 원본 200행은 NULL 이다.
    -- 무엇이 어떻게 바뀌었는지를 남겨야 되돌릴 수 있다.
    Old_Lv       TEXT,                 -- 갱신 전 등급        New_Lv    갱신 후
    New_Lv       TEXT,
    Old_Num      INTEGER,              -- 갱신 전 안전재고    New_Num   갱신 후
    New_Num      INTEGER,
    Old_Usage    INTEGER,              -- 갱신 전 사용금액 점수
    New_Usage    INTEGER,
    EP_ID        TEXT,                 -- 실행자
    Note         TEXT,
    PRIMARY KEY (P_ID, Updated_Date)
);

DROP TABLE IF EXISTS Safe_Override_tb;
CREATE TABLE Safe_Override_tb (
    Ovr_ID     TEXT PRIMARY KEY,   -- 조정번호 OVR+YYYYMMDD+4자리
    P_ID       TEXT NOT NULL,
    Ovr_Lv     TEXT,               -- 조정 등급. NULL 이면 등급은 계산값을 따른다
    Min_Qty    INTEGER,            -- 최소 보유량 하한. NULL 이면 하한 없음
    -- 등록 시점의 계산값을 동결한다. "계산은 B 인데 운영은 A" 를 말할 수 있어야 한다.
    Calc_Lv    TEXT NOT NULL,
    Calc_Num   INTEGER,
    Reason_Cd  TEXT NOT NULL,      -- 사유 분류 (자유 텍스트만 받으면 '중요해서' 가 쌓인다)
    Reason     TEXT NOT NULL,      -- 설명. 없으면 등록되지 않는다
    Start_Date TEXT NOT NULL,
    End_Date   TEXT NOT NULL,      -- 만료일 = 조정일 + 조정 등급의 재검토 주기
    Status     TEXT NOT NULL,      -- 적용 / 만료 / 해제
    EP_ID      TEXT NOT NULL,      -- 조정한 사람 (승인 권한 직급이어야 한다)
    Off_Date   TEXT,               -- 해제·만료 처리일
    Off_Note   TEXT
);

-- ── 재고 실사 ───────────────────────────────────────────────
-- 재고는 거래로만 움직인다. 실물과 장부가 어긋나도(파손·분실·오출고·전표 누락)
-- 고칠 방법이 없었다. 실사가 그 유일한 수단이다.
DROP TABLE IF EXISTS Price_Log_tb;
CREATE TABLE Price_Log_tb (
    Price_ID    TEXT    PRIMARY KEY,    -- PRC + YYYYMMDD + 4자리
    P_ID        TEXT    NOT NULL,
    Old_Price   INTEGER,                -- 바뀌기 전 단가 (최초 등록이면 NULL)
    New_Price   INTEGER NOT NULL,
    Diff        INTEGER,
    Diff_Pct    REAL,
    Reason_Cd   TEXT    NOT NULL,       -- 협력사인상 / 인하 / 원자재시세 / 환율변동 / 계약갱신 / 오류정정
    Reason      TEXT,
    Start_Date  TEXT    NOT NULL,       -- 적용일
    EP_ID       TEXT    NOT NULL
);
CREATE INDEX idx_prc_pid  ON Price_Log_tb(P_ID);
CREATE INDEX idx_prc_date ON Price_Log_tb(Start_Date);

DROP TABLE IF EXISTS Site_Return_tb;
CREATE TABLE Site_Return_tb (
    Ret_ID      TEXT    NOT NULL,          -- RET + YYYYMMDD + 4자리
    Line        INTEGER NOT NULL,
    Ret_Date    TEXT    NOT NULL,
    Lot_ID      TEXT    NOT NULL,
    P_ID        TEXT    NOT NULL,
    Ret_Qty     INTEGER NOT NULL,
    Reason_Cd   TEXT    NOT NULL,          -- 잔여반납 / 작업취소 / 과다불출 / 장기미사용
    Reason      TEXT,
    Work_Order  TEXT,
    Amount      INTEGER,                   -- 반납 금액 (수량 × 단가)
    Good_Qty    INTEGER,                   -- 검수 양품 (가용재고로 올라간다)
    Bad_Qty     INTEGER,                   -- 검수 불량 (가용재고에서 다시 뺀다)
    Bad_Cd      TEXT,                      -- 파손/변질/오염/포장훼손/사용흔적/입고하자
    Bad_Note    TEXT,
    Bad_T_ID    TEXT,                      -- 불량 판정 거래
    Claim_ID    TEXT,                      -- 협력사 귀책(입고하자)일 때만
    T_ID        TEXT    NOT NULL,          -- 이 반납이 남긴 '반납' 거래
    EP_ID       TEXT    NOT NULL,
    PRIMARY KEY (Ret_ID, Line)
);
CREATE INDEX idx_ret_lot  ON Site_Return_tb(Lot_ID);
CREATE INDEX idx_ret_date ON Site_Return_tb(Ret_Date);
CREATE INDEX idx_ret_wo   ON Site_Return_tb(Work_Order);

DROP TABLE IF EXISTS Stock_Count_tb;
CREATE TABLE Stock_Count_tb (
    Count_ID   TEXT PRIMARY KEY,   -- 실사번호 CNT+YYYYMMDD+4자리
    Count_Date TEXT NOT NULL,
    Scope      TEXT NOT NULL,      -- 등급 / 구역 / 전수
    Scope_Val  TEXT,               -- 'A' 또는 'L01'. 전수면 NULL
    Status     TEXT NOT NULL,      -- 진행 / 완료 / 취소
    EP_ID      TEXT NOT NULL,      -- 실사 담당
    Appr_EP_ID TEXT,               -- 조정 승인자 (재고를 바꾸므로 직급을 본다)
    Appr_Date  TEXT,
    Appr_Note  TEXT,
    Note       TEXT
);

DROP TABLE IF EXISTS Stock_Count_Item_tb;
CREATE TABLE Stock_Count_Item_tb (
    Count_ID  TEXT NOT NULL,
    Line      INTEGER NOT NULL,
    Lot_ID    TEXT NOT NULL,
    P_ID      TEXT NOT NULL,
    -- ⚠️ 실사 '시작 시점' 의 장부 재고를 박아 둔다. 실사 도는 동안 불출이
    --    일어나면 기준이 흔들린다. 그때 장부가 얼마였는지 설명할 수 있어야 한다.
    Book_Qty  INTEGER NOT NULL,
    Real_Qty  INTEGER,             -- 실물 수량. NULL 이면 아직 안 센 것
    -- 'Y' 면 이번 차수에서는 세지 않고 넘어간 줄. 안 센 줄(Real_Qty NULL)과
    -- 구분해야 "다 돌았다" 를 판정할 수 있다. 조정 대상에서도 빠진다.
    Skipped   TEXT,
    Reason_Cd TEXT,                -- 폐기 / 구역오류 / 분실 / 미기록불출 / 미기록반납 / 기타
    Reason    TEXT,
    Loc_To    TEXT,                -- 구역오류일 때 실제로 있던 구역
    T_ID      TEXT,                -- 조정으로 생긴 거래
    Counted_At TEXT,
    PRIMARY KEY (Count_ID, Line)
);

DROP TABLE IF EXISTS FG_tb;
CREATE TABLE FG_tb (
    FG_ID    TEXT PRIMARY KEY,         -- 완제품 코드 FG001~FG015
    FG_N     TEXT,                     -- 완제품명
    MainCat  TEXT,                     -- 대분류 (V/S/E/U/T)
    SubCat   TEXT,                     -- 중분류
    Unit     TEXT                      -- 단위
);

DROP TABLE IF EXISTS BOM_tb;
CREATE TABLE BOM_tb (
    BOM_ID   TEXT PRIMARY KEY,         -- BOM0001~
    FG_ID    TEXT,                     -- 완제품
    P_ID     TEXT,                     -- 소요 자재
    Spec     TEXT,                     -- 규격
    BOM_Qty  INTEGER,                  -- 완제품 1대당 소요량
    Unit     TEXT,
    BOM_Type TEXT                      -- '표준' 또는 '대체 (→원자재P_ID)'
);

DROP TABLE IF EXISTS Production_tb;
CREATE TABLE Production_tb (
    Prod_ID    TEXT PRIMARY KEY,
    FG_ID      TEXT,                   -- 완제품 코드 FG001~FG015
    P_ID       TEXT,                   -- 투입 자재
    Lot_ID     TEXT,                   -- 투입 LOT
    Prod_Date  TEXT,
    Prod_Qty   INTEGER,                -- 투입 수량
    EP_ID      TEXT,
    Work_Order TEXT,                   -- 작업지시번호
    Note       TEXT
);

CREATE INDEX idx_lot_pid        ON Lot_tb(P_ID);
CREATE INDEX idx_lot_loc        ON Lot_tb(Loc_ID);
CREATE INDEX idx_lot_date       ON Lot_tb(Lot_Date);
CREATE INDEX idx_tx_lot         ON Transaction_tb(Lot_ID);
CREATE INDEX idx_tx_date        ON Transaction_tb(T_Date);
CREATE INDEX idx_tx_type        ON Transaction_tb(T_Type);
CREATE INDEX idx_pd_hid         ON Purchase_Detail_tb(H_ID);
CREATE INDEX idx_chg_hid        ON Purchase_Change_tb(H_ID);
CREATE INDEX idx_req_status     ON Disburse_Req_tb(Status);
CREATE INDEX idx_claim_status   ON Inbound_Claim_tb(Status);
CREATE INDEX idx_claim_lot      ON Inbound_Claim_tb(Lot_ID);
CREATE INDEX idx_reqitem_req    ON Disburse_Req_Item_tb(Req_ID);
CREATE UNIQUE INDEX idx_chg_line ON Purchase_Change_tb(H_ID, Purchase_num);
CREATE INDEX idx_pd_pid         ON Purchase_Detail_tb(P_ID);
CREATE INDEX idx_prod_wo        ON Production_tb(Work_Order);
CREATE INDEX idx_prod_pid       ON Production_tb(P_ID);
CREATE INDEX idx_prod_date      ON Production_tb(Prod_Date);
CREATE INDEX idx_ovr_pid        ON Safe_Override_tb(P_ID);
CREATE INDEX idx_ovr_status     ON Safe_Override_tb(Status);
CREATE INDEX idx_cnt_status     ON Stock_Count_tb(Status);
CREATE INDEX idx_cnt_item_lot   ON Stock_Count_Item_tb(Lot_ID);
CREATE INDEX idx_cnt_item_cid   ON Stock_Count_Item_tb(Count_ID);
CREATE INDEX idx_product_cat    ON Product_tb(MainCat, SubCat, DetailCat);
CREATE INDEX idx_bom_fg         ON BOM_tb(FG_ID);
CREATE INDEX idx_bom_pid        ON BOM_tb(P_ID);
"""

# 각 테이블을 어디서 읽고, 각 컬럼을 어떻게 변환할지 정의
S, I, D = norm_str, norm_int, norm_date

JOBS = [
    # (테이블명, 출처종류, 출처이름, {컬럼: 변환함수})
    ("Cat_tb", "accdb", "Cat_tb",
     {"MainCat": S, "SubCat": S, "DetailCat": S, "Cat_Name": S}),
    ("Company_tb", "accdb", "Company_tb",
     {"BRN": S, "CP_N": S, "Is_Foreign": S}),
    ("Location_tb", "accdb", "Location_tb",
     {"Loc_ID": S, "Loc_N": S}),
    # Birth/Phone 은 마스킹해서 적재한다 (공개 저장소 대비)
    ("User_tb", "accdb", "User_tb",
     {"EP_ID": S, "Name": S, "Birth": mask_birth, "Phone": mask_phone, "Position": S}),

    # Product_tb 는 11차 회의(7/9) 재생성본인 CSV 사용 — accdb 판에는 Spec 컬럼이 없고
    # 자재명이 "123"인 불량행(U03040004)이 섞여 있어 CSV(200종)가 정본이다.
    ("Product_tb", "csv", "Product_tb.csv",
     {"P_ID": S, "P_N": S, "Spec": S, "BRN": S,
      "P_Price": lambda v: float(v) if v not in (None, "") else None,
      "MainCat": S, "SubCat": S, "DetailCat": S, "MinOrderQty": I, "PkgUnit": I}),
    ("FG_tb", "csv", "FG_tb.csv",
     {"FG_ID": S, "FG_N": S, "MainCat": S, "SubCat": S, "Unit": S}),
    ("BOM_tb", "csv", "BOM_tb.csv",
     {"BOM_ID": S, "FG_ID": S, "P_ID": S, "Spec": S, "BOM_Qty": I, "Unit": S, "BOM_Type": S}),

    ("Purchase_Header_tb", "csv", "Purchase_Header_tb.csv",
     {"H_ID": S, "BRN": S, "P_Date": D}),
    ("Purchase_Detail_tb", "csv", "Purchase_Detail_tb.csv",
     {"H_ID": S, "Purchase_num": I, "P_ID": S, "P_Qty": I}),
    ("Lot_tb", "csv", "Lot_tb.csv",
     {"Lot_ID": S, "P_ID": S, "Lot_Date": D, "Loc_ID": S, "P_Qty": I, "EP_ID": S, "H_ID": S}),
    ("Transaction_tb", "csv", "Transaction_tb.csv",
     {"T_ID": S, "Lot_ID": S, "T_Type": S, "T_Date": D, "T_Num": I, "EP_ID": S}),
    ("Safe_tb", "csv", "Safe_tb_v2.csv",
     {"P_ID": S, "Lead_Time": I, "Sf_Lv": S, "Sf_Num": I, "Price_Score": I,
      "Sub_Score": I, "Impact_Score": I, "Supply_Score": I, "Usage_Score": I}),
    ("Update_Log_tb", "csv", "Update_Log_tb.csv",
     {"P_ID": S, "Updated_Date": D, "Next_Date": D}),
    ("Production_tb", "csv", "Production_tb.csv",
     {"Prod_ID": S, "FG_ID": S, "P_ID": S, "Lot_ID": S, "Prod_Date": D,
      "Prod_Qty": I, "EP_ID": S, "Work_Order": S, "Note": S}),
]


# ── 안전재고 재검토 주기 ─────────────────────────────────────
# 표준 ABC 재고관리 원칙: 고가·핵심 자재(A)일수록 자주 검토하고, 저가·범용(C)은 느슨하게 본다.
#
# [이력] 원본 데이터는 A=180 / B=90 / C=30 으로 이 원칙과 정반대였다.
#        (6차 회의 7/01: A=월1회 B=분기1회 C=반기1회  ← 원칙에 맞음
#         10차 회의 7/08: A=180 B=90 C=30            ← 뒤집힘, 근거 기록 없음)
#        6차 결정이 정석이므로 아래 값으로 되돌려 Next_Date 를 재계산한다.
REVIEW_CYCLE_DAYS = {"A": 30, "B": 90, "C": 180}


def rebuild_review_dates(conn):
    """Update_Log_tb.Next_Date 를 등급별 재검토 주기에 맞춰 다시 계산한다."""
    before = conn.execute("""
        SELECT s.Sf_Lv lv,
               MIN(julianday(u.Next_Date) - julianday(u.Updated_Date)) d
        FROM Update_Log_tb u JOIN Safe_tb s ON u.P_ID = s.P_ID
        GROUP BY s.Sf_Lv ORDER BY s.Sf_Lv
    """).fetchall()

    conn.execute("""
        UPDATE Update_Log_tb
           SET Next_Date = date(
                 Updated_Date,
                 '+' || (SELECT CASE s.Sf_Lv WHEN 'A' THEN ? WHEN 'B' THEN ? ELSE ? END
                           FROM Safe_tb s WHERE s.P_ID = Update_Log_tb.P_ID) || ' days')
         WHERE EXISTS (SELECT 1 FROM Safe_tb s WHERE s.P_ID = Update_Log_tb.P_ID)
    """, (REVIEW_CYCLE_DAYS["A"], REVIEW_CYCLE_DAYS["B"], REVIEW_CYCLE_DAYS["C"]))
    conn.commit()

    after = conn.execute("""
        SELECT s.Sf_Lv lv, COUNT(*) n,
               MIN(julianday(u.Next_Date) - julianday(u.Updated_Date)) mn,
               MAX(julianday(u.Next_Date) - julianday(u.Updated_Date)) mx
        FROM Update_Log_tb u JOIN Safe_tb s ON u.P_ID = s.P_ID
        GROUP BY s.Sf_Lv ORDER BY s.Sf_Lv
    """).fetchall()

    print()
    print("── 안전재고 재검토 주기 재계산 (A를 자주, C를 느슨하게) ──")
    old = {r[0]: int(r[1]) for r in before}
    for lv, n, mn, mx in after:
        chk = "OK " if mn == mx == REVIEW_CYCLE_DAYS[lv] else "!! "
        print(f"  {chk}{lv}등급 {n:>3}종:  {old.get(lv, '?'):>3}일 → {int(mn):>3}일")


def load(conn, table, kind, src, colmap):
    raw = read_accdb(src) if kind == "accdb" else read_csv(src)
    cols = list(colmap)
    rows = [tuple(colmap[c](r.get(c)) for c in cols) for r in raw]
    ph = ",".join("?" * len(cols))
    conn.executemany(
        f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({ph})", rows
    )
    dropped = len(raw) - len(rows)
    origin = "accdb" if kind == "accdb" else "CSV"
    print(f"  {table:<20} {len(rows):>5}행  ({origin}: {src})"
          + (f"  ※{dropped}행 누락" if dropped else ""))
    return len(rows)


def integrity_report(conn):
    """합쳐 놓은 데이터가 서로 연결되는지(고아 데이터가 없는지) 확인한다."""
    checks = [
        ("Lot_tb.P_ID → Product_tb",
         "SELECT COUNT(*) FROM Lot_tb l LEFT JOIN Product_tb p ON l.P_ID=p.P_ID WHERE p.P_ID IS NULL"),
        ("Lot_tb.Loc_ID → Location_tb",
         "SELECT COUNT(*) FROM Lot_tb l LEFT JOIN Location_tb x ON l.Loc_ID=x.Loc_ID WHERE x.Loc_ID IS NULL"),
        ("Lot_tb.H_ID → Purchase_Header_tb",
         "SELECT COUNT(*) FROM Lot_tb l LEFT JOIN Purchase_Header_tb h ON l.H_ID=h.H_ID WHERE h.H_ID IS NULL"),
        ("Lot_tb.EP_ID → User_tb",
         "SELECT COUNT(*) FROM Lot_tb l LEFT JOIN User_tb u ON l.EP_ID=u.EP_ID WHERE u.EP_ID IS NULL"),
        ("Transaction_tb.Lot_ID → Lot_tb",
         "SELECT COUNT(*) FROM Transaction_tb t LEFT JOIN Lot_tb l ON t.Lot_ID=l.Lot_ID WHERE l.Lot_ID IS NULL"),
        ("Purchase_Detail_tb.H_ID → Purchase_Header_tb",
         "SELECT COUNT(*) FROM Purchase_Detail_tb d LEFT JOIN Purchase_Header_tb h ON d.H_ID=h.H_ID WHERE h.H_ID IS NULL"),
        ("Purchase_Detail_tb.P_ID → Product_tb",
         "SELECT COUNT(*) FROM Purchase_Detail_tb d LEFT JOIN Product_tb p ON d.P_ID=p.P_ID WHERE p.P_ID IS NULL"),
        ("Safe_tb.P_ID → Product_tb",
         "SELECT COUNT(*) FROM Safe_tb s LEFT JOIN Product_tb p ON s.P_ID=p.P_ID WHERE p.P_ID IS NULL"),
        ("Production_tb.Lot_ID → Lot_tb",
         "SELECT COUNT(*) FROM Production_tb r LEFT JOIN Lot_tb l ON r.Lot_ID=l.Lot_ID WHERE l.Lot_ID IS NULL"),
        ("Production_tb.P_ID → Product_tb",
         "SELECT COUNT(*) FROM Production_tb r LEFT JOIN Product_tb p ON r.P_ID=p.P_ID WHERE p.P_ID IS NULL"),
        ("BOM_tb.FG_ID → FG_tb",
         "SELECT COUNT(*) FROM BOM_tb b LEFT JOIN FG_tb f ON b.FG_ID=f.FG_ID WHERE f.FG_ID IS NULL"),
        ("BOM_tb.P_ID → Product_tb",
         "SELECT COUNT(*) FROM BOM_tb b LEFT JOIN Product_tb p ON b.P_ID=p.P_ID WHERE p.P_ID IS NULL"),
        ("Production_tb.FG_ID → FG_tb",
         "SELECT COUNT(*) FROM Production_tb r LEFT JOIN FG_tb f ON r.FG_ID=f.FG_ID WHERE f.FG_ID IS NULL"),
        ("Product_tb.BRN → Company_tb",
         "SELECT COUNT(*) FROM Product_tb p LEFT JOIN Company_tb c ON p.BRN=c.BRN WHERE c.BRN IS NULL"),
        ("Product_tb 분류 → Cat_tb",
         """SELECT COUNT(*) FROM Product_tb p LEFT JOIN Cat_tb c
            ON p.MainCat=c.MainCat AND p.SubCat=c.SubCat AND p.DetailCat=c.DetailCat
            WHERE c.MainCat IS NULL"""),
    ]
    print()
    print("── 데이터 연결 상태 점검 (연결 안 되는 행 = 고아 데이터) ──")
    problems = []
    for label, sql in checks:
        n = conn.execute(sql).fetchone()[0]
        mark = "OK " if n == 0 else "!! "
        print(f"  {mark}{label:<42} 고아 {n}행")
        if n:
            problems.append((label, n))
    return problems


def main():
    if not os.path.exists(ACCDB):
        sys.exit(f"ERP.accdb 를 찾을 수 없습니다: {ACCDB}")
    if not os.path.isdir(CSV_DIR):
        sys.exit(f"DB_csv 폴더를 찾을 수 없습니다: {CSV_DIR}")
    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(OUT_DB):
        os.remove(OUT_DB)

    conn = sqlite3.connect(OUT_DB)
    conn.executescript(SCHEMA)

    print("── 데이터 적재 ──")
    total = 0
    for table, kind, src, colmap in JOBS:
        total += load(conn, table, kind, src, colmap)
    conn.commit()

    rebuild_review_dates(conn)

    problems = integrity_report(conn)

    print()
    print("── 요약 ──")
    print(f"  총 {len(JOBS)}개 테이블, {total:,}행")
    print(f"  생성 위치: {OUT_DB}")
    print(f"  파일 크기: {os.path.getsize(OUT_DB) / 1024:.0f} KB")
    if problems:
        print()
        print("  주의: 아래 항목은 서로 연결되지 않는 데이터가 있습니다.")
        for label, n in problems:
            print(f"    - {label}: {n}행")
    conn.close()


if __name__ == "__main__":
    main()
