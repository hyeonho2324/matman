# -*- coding: utf-8 -*-
"""
erp.db 조회 모듈.

화면에서 쓰는 SQL을 여기 모아둔다. app.py 는 이 함수들만 부른다.
"""
import csv
import io
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "erp.db")

# 안전재고 등급별 서비스 수준 계수 Z (설계: A=99%, B=95%, C=90%)
Z_BY_GRADE = {"A": 2.33, "B": 1.65, "C": 1.28}

# 등급별 재검토 주기(일) — build_db.py 의 REVIEW_CYCLE_DAYS 와 동일하게 유지할 것
REVIEW_CYCLE_DAYS = {"A": 30, "B": 90, "C": 180}


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ── 거래 유형(T_Type) 정의 ───────────────────────────────────
# 현재 데이터에는 입고/불출 2종뿐이지만 설계상 7종이다.
# 나머지 유형의 실데이터가 들어와도 바로 동작하도록 부호를 여기서만 관리한다.
#
#   stock  : LOT 잔량에 미치는 영향.
#            입고는 Lot_tb.P_Qty 에 이미 반영돼 있어 0 으로 둔다
#            (거래 행을 또 더하면 이중 계산이 된다).
#   demand : 현장 실소비로 볼 것인가.
#            수요예측·파레토·일평균 사용량의 분자로 쓰인다.
TX_TYPES = [
    # 유형,   재고부호, 수요, 설명,                              분류
    ("입고",    0, False, "협력사 → 자재창고 (P_Qty 에 반영됨)",  "in"),
    ("불출",   -1, True,  "자재창고 → 현장",                     "out"),
    ("반납",   +1, False, "현장 → 자재창고 복귀",                "in"),
    ("불량",   -1, False, "불량 판정으로 재고에서 제거",          "bad"),
    ("폐기",   -1, False, "폐기 처분",                           "bad"),
    ("이동",    0, False, "창고 간 이동 (총량 불변, 위치만 변경)", "move"),
    ("교환",    0, False, "동수량 교체 (총량 불변)",              "move"),
]
TX_SIGN   = {t: sg for t, sg, _, _, _ in TX_TYPES}
TX_LABELS = [t for t, _, _, _, _ in TX_TYPES]
TX_DEMAND = [t for t, _, d, _, _ in TX_TYPES if d]
TX_MINUS  = [t for t, sg, _, _, _ in TX_TYPES if sg < 0]
TX_PLUS   = [t for t, sg, _, _, _ in TX_TYPES if sg > 0]
# 자재창고에서 현장으로 나가는 유일한 실소비 유형.
# 불출 등록이 Transaction_tb 에 남기는 T_Type 이 이 값이다.
# TX_TYPES 에서 끌어오므로 유형명이 바뀌어도 여기만 따라간다.
TX_DISBURSE = TX_DEMAND[0] if TX_DEMAND else "불출"
# 협력사에서 자재창고로 들어오는 최초 입고 유형.
# 입고 등록이 Transaction_tb 에 남기는 T_Type 이 이 값이다.
# 재고 부호가 0 인 in 유형은 이것 하나다 (반납은 +1).
TX_RECEIPT = next((t for t, sg, _, _, k in TX_TYPES if k == "in" and sg == 0), "입고")


def _inlist(vals):
    """SQL IN 절에 넣을 문자열. 비어 있으면 매칭되지 않는 값을 넣는다."""
    return ", ".join("'%s'" % v for v in vals) if vals else "''"


# LOT 잔량에서 빼야 할 순차감량.
#   재고를 깎는 유형(불출·불량·폐기)은 +, 되돌리는 유형(반납)은 −.
#   입고·이동·교환은 잔량에 영향이 없어 0.
LOT_DELTA = ("SUM(CASE WHEN T_Type IN ({m}) THEN T_Num "
             "WHEN T_Type IN ({p}) THEN -T_Num ELSE 0 END)").format(
                 m=_inlist(TX_MINUS), p=_inlist(TX_PLUS))

# 현장 실소비 조건 (수요 지표 전용)
DEMAND_IN = "T_Type IN (%s)" % _inlist(TX_DEMAND)
DEMAND_T  = "t." + DEMAND_IN

# 입출고 양방향 집계용 조각.
#   입고 측 = 창고로 들어오는 것 (입고 · 반납)
#   출고 측 = 창고에서 나가는 것 (불출 · 불량 · 폐기)
#   이동·교환은 창고 총량이 변하지 않으므로 양쪽 모두에서 뺀다.
TX_KIND   = {t: k for t, _, _, _, k in TX_TYPES}
TX_IN     = [t for t, k in TX_KIND.items() if k == "in"]
TX_OUT    = [t for t, k in TX_KIND.items() if k in ("out", "bad")]
TX_IN_SQL  = "T_Type IN (%s)" % _inlist(TX_IN)
TX_OUT_SQL = "T_Type IN (%s)" % _inlist(TX_OUT)
# JOIN 쿼리에서 별칭 t 를 붙인 형태
TX_IN_T   = "t." + TX_IN_SQL
TX_OUT_T  = "t." + TX_OUT_SQL

# 화면에 넘길 유형 메타 — 필터 목록을 자동 생성하는 데 쓴다
TX_META = [{"type": t, "sign": sg, "demand": d, "desc": desc, "kind": kind}
           for t, sg, d, desc, kind in TX_TYPES]


# ── 공통 조각 ────────────────────────────────────────────────
# 현재고 = LOT 입고량 - 그 LOT의 불출 합계
STOCK_SQL = f"""
    SELECT l.P_ID,
           SUM(l.P_Qty) - COALESCE(SUM(x.out_qty), 0) AS stock
      FROM Lot_tb l
      LEFT JOIN (SELECT Lot_ID, {LOT_DELTA} AS out_qty FROM Transaction_tb GROUP BY Lot_ID) x
             ON x.Lot_ID = l.Lot_ID
     GROUP BY l.P_ID
"""


def operating_days(conn):
    """불출 이력이 존재하는 기간(일). 일평균사용량 d 의 분모."""
    r = conn.execute(f"""
        SELECT CAST(julianday(MAX(T_Date)) - julianday(MIN(T_Date)) AS INT) d
          FROM Transaction_tb WHERE {DEMAND_IN}
    """).fetchone()
    return max(int(r["d"] or 1), 1)


# ── 안전재고 화면 ────────────────────────────────────────────
def safety_stock_list(conn):
    """안전재고 200종 전체 + 현재고 + 부족량 + 계산 근거."""
    days = operating_days(conn)
    return _rows(conn, f"""
        WITH stock AS ({STOCK_SQL}),
             used AS (
                SELECT l.P_ID, SUM(t.T_Num) AS out_qty, COUNT(*) AS out_cnt
                  FROM Transaction_tb t JOIN Lot_tb l ON t.Lot_ID = l.Lot_ID
                 WHERE {DEMAND_T}
                 GROUP BY l.P_ID
             ),
             lead AS (
                SELECT l.P_ID,
                       AVG(julianday(l.Lot_Date) - julianday(h.P_Date)) AS lt_real
                  FROM Lot_tb l JOIN Purchase_Header_tb h ON l.H_ID = h.H_ID
                 GROUP BY l.P_ID
             ),
             -- ⚠️ 갱신을 한 번 실행하면 한 품번에 이력이 여러 행 생긴다.
             --    Update_Log_tb 를 그대로 조인하면 200종이 400종으로 불어난다.
             --    (원본 데이터가 품번당 1행뿐이라 드러나지 않던 함정)
             last_log AS (
                SELECT P_ID, Updated_Date, Next_Date
                  FROM Update_Log_tb l
                 WHERE Updated_Date = (SELECT MAX(Updated_Date) FROM Update_Log_tb x
                                        WHERE x.P_ID = l.P_ID)
             )
        SELECT s.P_ID,
               p.P_N, p.Spec, p.P_Price, p.MinOrderQty, p.PkgUnit,
               c.CP_N          AS supplier,
               c.Is_Foreign    AS is_foreign,
               cat.Cat_Name    AS cat_name,
               s.Sf_Lv         AS grade,
               s.Sf_Num        AS safe_qty,
               s.Lead_Time     AS lead_time,
               ROUND(ld.lt_real, 1)               AS lead_time_real,
               COALESCE(st.stock, 0)              AS stock,
               COALESCE(st.stock, 0) - s.Sf_Num   AS diff,
               s.Price_Score, s.Sub_Score, s.Impact_Score, s.Supply_Score, s.Usage_Score,
               (s.Price_Score + s.Sub_Score + s.Impact_Score + s.Supply_Score
                 + COALESCE(s.Usage_Score, 0))    AS score_sum,
               CASE WHEN s.Usage_Score IS NULL THEN 1 ELSE 0 END AS is_new,
               COALESCE(u.out_qty, 0)             AS used_qty,
               COALESCE(u.out_cnt, 0)             AS used_cnt,
               -- 연간 사용금액 = 불출수량 x 단가. ABC 파레토와 같은 기준이라
               -- 이 값 내림차순이 곧 파레토 순위다 (abc_analysis 를 다시 조인하지 않는다)
               ROUND(COALESCE(u.out_qty, 0) * p.P_Price) AS use_amount,
               ROUND(COALESCE(u.out_qty, 0) * 1.0 / {days}, 2) AS daily_use,
               ul.Updated_Date                    AS updated_date,
               ul.Next_Date                       AS next_date
          FROM Safe_tb s
          JOIN Product_tb p   ON s.P_ID = p.P_ID
          LEFT JOIN Company_tb c  ON p.BRN = c.BRN
          LEFT JOIN Cat_tb cat    ON p.MainCat = cat.MainCat
                                 AND p.SubCat = cat.SubCat
                                 AND p.DetailCat = cat.DetailCat
          LEFT JOIN stock st  ON s.P_ID = st.P_ID
          LEFT JOIN used u    ON s.P_ID = u.P_ID
          LEFT JOIN lead ld   ON s.P_ID = ld.P_ID
          LEFT JOIN last_log ul ON s.P_ID = ul.P_ID
         ORDER BY (COALESCE(st.stock, 0) - s.Sf_Num) ASC
    """)


def safety_stock_summary(rows):
    """목록에서 요약 지표를 뽑는다 (SQL 재조회 없이)."""
    short = [r for r in rows if r["diff"] < 0]
    by_grade = {}
    for g in ("A", "B", "C"):
        g_rows = [r for r in rows if r["grade"] == g]
        by_grade[g] = {
            "count": len(g_rows),
            "short": len([r for r in g_rows if r["diff"] < 0]),
            "cycle": REVIEW_CYCLE_DAYS[g],
        }
    return {
        "total": len(rows),
        "short": len(short),
        "short_pct": round(len(short) / len(rows) * 100, 1) if rows else 0,
        "new_count": len([r for r in rows if r["is_new"]]),
        "by_grade": by_grade,
        "stock_value": sum((r["stock"] or 0) * (r["P_Price"] or 0) for r in rows),
    }


# ── 자재 목록 화면 ───────────────────────────────────────────
def product_list(conn):
    """자재 200종 + 현재고 + 등급 + 분류명 + 협력사 + 입출고 요약."""
    return _rows(conn, f"""
        WITH stock AS ({STOCK_SQL}),
             lots AS (
                SELECT P_ID, COUNT(*) AS lot_cnt, MAX(Lot_Date) AS last_in
                  FROM Lot_tb GROUP BY P_ID
             ),
             tx AS (
                SELECT l.P_ID,
                       SUM(CASE WHEN {TX_IN_T} THEN t.T_Num ELSE 0 END) AS in_qty,
                       SUM(CASE WHEN {TX_OUT_T} THEN t.T_Num ELSE 0 END) AS out_qty,
                       MAX(CASE WHEN {TX_OUT_T} THEN t.T_Date END)       AS last_out
                  FROM Transaction_tb t JOIN Lot_tb l ON t.Lot_ID = l.Lot_ID
                 GROUP BY l.P_ID
             ),
             bom AS (
                SELECT P_ID, COUNT(DISTINCT FG_ID) AS fg_cnt,
                       SUM(CASE WHEN BOM_Type='표준' THEN 1 ELSE 0 END) AS std_cnt
                  FROM BOM_tb GROUP BY P_ID
             )
        SELECT p.P_ID, p.P_N, p.Spec, p.P_Price, p.MinOrderQty, p.PkgUnit,
               p.MainCat, p.SubCat, p.DetailCat,
               cat.Cat_Name              AS cat_name,
               p.BRN, c.CP_N             AS supplier,
               c.Is_Foreign              AS is_foreign,
               COALESCE(st.stock, 0)     AS stock,
               COALESCE(lt.lot_cnt, 0)   AS lot_cnt,
               lt.last_in,
               COALESCE(tx.in_qty, 0)    AS in_qty,
               COALESCE(tx.out_qty, 0)   AS out_qty,
               tx.last_out,
               s.Sf_Lv                   AS grade,
               s.Sf_Num                  AS safe_qty,
               s.Lead_Time               AS lead_time,
               CASE WHEN s.Usage_Score IS NULL THEN 1 ELSE 0 END AS is_new,
               COALESCE(bm.fg_cnt, 0)    AS fg_cnt,
               ROUND(COALESCE(st.stock,0) * p.P_Price)          AS stock_value,
               CASE WHEN p.Spec LIKE '%[대체]%' THEN 1 ELSE 0 END AS is_alt
          FROM Product_tb p
          LEFT JOIN Company_tb c ON p.BRN = c.BRN
          LEFT JOIN Cat_tb cat   ON p.MainCat = cat.MainCat
                                AND p.SubCat = cat.SubCat
                                AND p.DetailCat = cat.DetailCat
          LEFT JOIN stock st ON p.P_ID = st.P_ID
          LEFT JOIN lots  lt ON p.P_ID = lt.P_ID
          LEFT JOIN tx       ON p.P_ID = tx.P_ID
          LEFT JOIN Safe_tb s ON p.P_ID = s.P_ID
          LEFT JOIN bom bm   ON p.P_ID = bm.P_ID
         ORDER BY p.P_ID
    """)


def lot_list(conn):
    """자재별 LOT 전체 이력 (자재 목록 화면의 LOT 이력 패널용).

    입고 → 불출 → 생산투입까지 한 LOT 의 일생을 담는다.
    FIFO 순번과 위반 여부도 함께 계산해, 자재 하나만 봐도
    선입선출이 지켜졌는지 판단할 수 있게 한다.
    """
    base = conn.execute("SELECT MAX(T_Date) FROM Transaction_tb").fetchone()[0]

    rows = _rows(conn, f"""
        SELECT l.Lot_ID, l.P_ID, l.Lot_Date, l.Loc_ID, lo.Loc_N AS loc_name,
               l.P_Qty, l.H_ID,
               h.P_Date AS order_date,
               CAST(julianday(l.Lot_Date) - julianday(h.P_Date) AS INT) AS lead_days,
               u.Name AS receiver,
               x.out_date, COALESCE(x.out_qty, 0) AS out_qty,
               l.P_Qty - COALESCE(x.out_qty, 0)  AS remain,
               CAST(julianday(?) - julianday(l.Lot_Date) AS INT)          AS age_days,
               CAST(julianday(x.out_date) - julianday(l.Lot_Date) AS INT) AS hold_days
          FROM Lot_tb l
          LEFT JOIN Location_tb lo ON l.Loc_ID = lo.Loc_ID
          LEFT JOIN User_tb u      ON l.EP_ID = u.EP_ID
          LEFT JOIN Purchase_Header_tb h ON l.H_ID = h.H_ID
          LEFT JOIN (SELECT Lot_ID, {LOT_DELTA} AS out_qty, MIN(CASE WHEN T_Type IN ('불출') THEN T_Date END) AS out_date FROM Transaction_tb GROUP BY Lot_ID) x
                 ON x.Lot_ID = l.Lot_ID
         ORDER BY l.P_ID, l.Lot_Date
    """, (base,))

    # 생산 투입 요약
    used = {r["Lot_ID"]: r for r in _rows(conn, """
        SELECT Lot_ID, COUNT(DISTINCT Work_Order) AS wo_n,
               SUM(Prod_Qty) AS qty, MIN(FG_ID) AS fg
          FROM Production_tb GROUP BY Lot_ID
    """)}

    by_pid = {}
    for r in rows:
        by_pid.setdefault(r["P_ID"], []).append(r)

    for pid, ls in by_pid.items():
        for i, r in enumerate(ls):            # 이미 입고일 순
            r["fifo_seq"] = i + 1
            r["fifo_total"] = len(ls)
            # 이 LOT 이 남아 있는데 더 늦게 들어온 LOT 이 먼저 나갔으면 위반
            skipped = (len([n for n in ls[i + 1:] if n["out_date"]])
                       if (r["remain"] or 0) > 0 else 0)
            r["fifo_skipped"] = skipped
            r["fifo_ok"] = 0 if skipped else 1
            r["used_pct"] = round((r["out_qty"] or 0) / r["P_Qty"] * 100) if r["P_Qty"] else 0
            u = used.get(r["Lot_ID"])
            r["prod_wo"] = u["wo_n"] if u else 0
            r["prod_qty"] = u["qty"] if u else 0
            r["prod_fg"] = u["fg"] if u else None
    return rows


def bom_usage(conn):
    """자재가 어느 완제품에 쓰이는지 (자재 상세용)."""
    return _rows(conn, """
        SELECT b.P_ID, b.FG_ID, f.FG_N, b.BOM_Qty, b.BOM_Type
          FROM BOM_tb b JOIN FG_tb f ON b.FG_ID = f.FG_ID
         ORDER BY b.P_ID, b.FG_ID
    """)


def category_tree(conn):
    """대분류/중분류 목록 + 각 분류의 품목 수 (필터용)."""
    return _rows(conn, """
        SELECT p.MainCat, p.SubCat,
               MIN(cat.Cat_Name) AS sample_name,
               COUNT(*) AS cnt
          FROM Product_tb p
          LEFT JOIN Cat_tb cat ON p.MainCat=cat.MainCat AND p.SubCat=cat.SubCat
                              AND p.DetailCat=cat.DetailCat
         GROUP BY p.MainCat, p.SubCat
         ORDER BY p.MainCat, p.SubCat
    """)


# 대분류 — 순서 그대로 화면 탭에 쓰인다 (dict 는 JSON 직렬화 시 키 정렬되므로 리스트로 넘길 것)
MAINCAT = [("V", "밸브"), ("S", "센서"), ("E", "전장"), ("U", "탱크"), ("T", "튜브")]
MAINCAT_NAME = dict(MAINCAT)


def product_summary(rows):
    by_cat = {}
    for m, label in MAINCAT_NAME.items():
        sub = [r for r in rows if r["MainCat"] == m]
        by_cat[m] = {"label": label, "count": len(sub),
                     "value": sum(r["stock_value"] or 0 for r in sub)}
    return {
        "total": len(rows),
        "suppliers": len({r["BRN"] for r in rows if r["BRN"]}),
        "stock_value": sum(r["stock_value"] or 0 for r in rows),
        "no_stock": len([r for r in rows if (r["stock"] or 0) <= 0]),
        "alt_count": len([r for r in rows if r["is_alt"]]),
        "by_cat": by_cat,
    }


# ── BOM 화면 ─────────────────────────────────────────────────
import re

_ALT_RE = re.compile(r"→\s*([A-Za-z0-9]+)")


def _alt_target(bom_type):
    """'대체 (→E02030001)' 에서 대체 대상 P_ID 를 뽑는다. 표준이면 None."""
    if not bom_type or not bom_type.startswith("대체"):
        return None
    m = _ALT_RE.search(bom_type)
    return m.group(1) if m else None


def bom_rows(conn):
    """BOM 200행 + 자재 정보 + 현재고."""
    rows = _rows(conn, f"""
        WITH stock AS ({STOCK_SQL})
        SELECT b.BOM_ID, b.FG_ID, b.P_ID, b.Spec, b.BOM_Qty, b.Unit, b.BOM_Type,
               p.P_N, p.P_Price, p.MainCat, p.SubCat, p.DetailCat,
               p.MinOrderQty, p.PkgUnit,
               c.CP_N               AS supplier,
               c.Is_Foreign         AS is_foreign,
               cat.Cat_Name         AS cat_name,
               COALESCE(st.stock,0) AS stock,
               s.Sf_Lv              AS grade,
               s.Sf_Num             AS safe_qty,
               s.Lead_Time          AS lead_time
          FROM BOM_tb b
          JOIN Product_tb p   ON b.P_ID = p.P_ID
          LEFT JOIN Company_tb c ON p.BRN = c.BRN
          LEFT JOIN Cat_tb cat   ON p.MainCat=cat.MainCat AND p.SubCat=cat.SubCat
                                AND p.DetailCat=cat.DetailCat
          LEFT JOIN stock st  ON b.P_ID = st.P_ID
          LEFT JOIN Safe_tb s ON b.P_ID = s.P_ID
         ORDER BY b.FG_ID, b.BOM_ID
    """)
    for r in rows:
        r["is_alt"] = 1 if (r["BOM_Type"] or "").startswith("대체") else 0
        r["alt_for"] = _alt_target(r["BOM_Type"])
        # 이 자재 단독으로 만들 수 있는 완제품 수
        r["can_make"] = (r["stock"] // r["BOM_Qty"]) if r["BOM_Qty"] else 0
    return rows


def fg_list(conn, brows=None):
    """완제품 15종 + BOM 요약 + 생산 가능 수량 + 병목 자재."""
    fgs = _rows(conn, "SELECT FG_ID, FG_N, MainCat, SubCat, Unit FROM FG_tb ORDER BY FG_ID")
    brows = brows if brows is not None else bom_rows(conn)

    # 생산 실적(참고용)
    prod = {r["FG_ID"]: r for r in _rows(conn, """
        SELECT FG_ID, COUNT(DISTINCT Work_Order) AS wo_cnt,
               MIN(Prod_Date) AS first_date, MAX(Prod_Date) AS last_date
          FROM Production_tb GROUP BY FG_ID
    """)}

    for fg in fgs:
        mine = [b for b in brows if b["FG_ID"] == fg["FG_ID"]]
        std = [b for b in mine if not b["is_alt"]]
        alts = [b for b in mine if b["is_alt"]]

        # 대체품 재고를 원자재 쪽에 합산
        alt_stock = {}
        for a in alts:
            if a["alt_for"]:
                alt_stock[a["alt_for"]] = alt_stock.get(a["alt_for"], 0) + a["stock"]

        cap_std, cap_alt, neck = None, None, None
        for b in std:
            q = b["BOM_Qty"] or 1
            c1 = b["stock"] // q
            c2 = (b["stock"] + alt_stock.get(b["P_ID"], 0)) // q
            if cap_std is None or c1 < cap_std:
                cap_std, neck = c1, b
            cap_alt = c2 if cap_alt is None else min(cap_alt, c2)

        fg["part_cnt"] = len(std)
        fg["alt_cnt"] = len(alts)
        fg["unit_cost"] = sum((b["BOM_Qty"] or 0) * (b["P_Price"] or 0) for b in std)
        fg["can_make"] = cap_std or 0
        fg["can_make_alt"] = cap_alt or 0
        fg["neck_pid"] = neck["P_ID"] if neck else None
        fg["neck_name"] = neck["P_N"] if neck else None
        fg["neck_stock"] = neck["stock"] if neck else 0
        fg["neck_qty"] = neck["BOM_Qty"] if neck else 0
        fg["zero_parts"] = len([b for b in std if b["stock"] <= 0])
        fg["wo_cnt"] = prod.get(fg["FG_ID"], {}).get("wo_cnt", 0)
        fg["last_date"] = prod.get(fg["FG_ID"], {}).get("last_date")
    return fgs


def bom_summary(fgs, brows):
    return {
        "fg_count": len(fgs),
        "bom_rows": len(brows),
        "std_rows": len([b for b in brows if not b["is_alt"]]),
        "alt_rows": len([b for b in brows if b["is_alt"]]),
        "makeable": len([f for f in fgs if f["can_make"] > 0]),
        "blocked": len([f for f in fgs if f["can_make"] <= 0]),
        "gain_by_alt": len([f for f in fgs if f["can_make_alt"] > f["can_make"]]),
        "total_wo": sum(f["wo_cnt"] for f in fgs),
    }


# ── ABC 분석 화면 ────────────────────────────────────────────
# 설계: 연간 사용금액 파레토 누적비율로 Usage_Score 자동 산정
#       누적 75% 이내 → A(3점) / 90% 이내 → B(2점) / 나머지 → C(1점)
PARETO_CUT = {"A": 75, "B": 90}


def abc_analysis(conn, period=""):
    """불출 이력 × 단가로 파레토 분석해 Usage 등급을 산정한다.

    period 를 주면 그 기간의 불출만으로 집계한다.
    전체 누적으로만 보면 예전에 많이 쓰고 지금은 안 쓰는 자재가 계속 A 로 남는다.
    """
    base = conn.execute(f"SELECT MAX(T_Date) FROM Transaction_tb WHERE {DEMAND_IN}").fetchone()[0]
    pf, pt = period_range(base, period)
    where = f"{DEMAND_T} AND {_between('t.T_Date', pf, pt)}"
    rows = _rows(conn, f"""
        SELECT p.P_ID, p.P_N, p.Spec, p.P_Price,
               p.MainCat, p.SubCat, p.DetailCat,
               cat.Cat_Name          AS cat_name,
               c.CP_N                AS supplier,
               c.Is_Foreign          AS is_foreign,
               COALESCE(u.out_qty,0) AS used_qty,
               COALESCE(u.out_cnt,0) AS used_cnt,
               COALESCE(u.out_qty,0) * p.P_Price AS amount,
               s.Sf_Lv               AS grade,
               s.Usage_Score         AS usage_score,
               s.Sf_Num              AS safe_qty,
               s.Price_Score, s.Sub_Score, s.Impact_Score, s.Supply_Score
          FROM Product_tb p
          LEFT JOIN (SELECT l.P_ID,
                            SUM(t.T_Num) AS out_qty,
                            COUNT(*)     AS out_cnt
                       FROM Transaction_tb t JOIN Lot_tb l ON t.Lot_ID = l.Lot_ID
                      WHERE {where}
                      GROUP BY l.P_ID) u ON p.P_ID = u.P_ID
          LEFT JOIN Company_tb c ON p.BRN = c.BRN
          LEFT JOIN Cat_tb cat   ON p.MainCat=cat.MainCat AND p.SubCat=cat.SubCat
                                AND p.DetailCat=cat.DetailCat
          LEFT JOIN Safe_tb s    ON p.P_ID = s.P_ID
         ORDER BY amount DESC, p.P_ID
    """)

    total = sum(r["amount"] or 0 for r in rows) or 1
    cum = 0
    for i, r in enumerate(rows, 1):
        amt = r["amount"] or 0
        cum += amt
        r["rank"] = i
        r["share"] = round(amt / total * 100, 3)
        r["cum_share"] = round(cum / total * 100, 2)
        # 파레토 등급 산정 — 사용실적이 없으면 최하위 C
        if amt <= 0:
            g = "C"
        elif r["cum_share"] <= PARETO_CUT["A"]:
            g = "A"
        elif r["cum_share"] <= PARETO_CUT["B"]:
            g = "B"
        else:
            g = "C"
        r["calc_usage_grade"] = g
        r["calc_usage_score"] = {"A": 3, "B": 2, "C": 1}[g]
        # 기존 값과 비교
        r["is_missing"] = 1 if r["usage_score"] is None else 0
        r["changed"] = 0 if r["usage_score"] is None else (
            1 if r["usage_score"] != r["calc_usage_score"] else 0)
        # 5개 항목 합계 재산정 → 등급 재판정 (컷오프 13/8/7)
        four = sum(r[k] or 0 for k in
                   ("Price_Score", "Sub_Score", "Impact_Score", "Supply_Score"))
        new_sum = four + r["calc_usage_score"]
        r["new_score_sum"] = new_sum if r["grade"] else None
        r["new_grade"] = (
            None if not r["grade"] else
            ("A" if new_sum >= 13 else ("B" if new_sum >= 8 else "C")))
        r["grade_changed"] = 1 if (r["grade"] and r["new_grade"] != r["grade"]) else 0
    return rows


def abc_summary(rows):
    total = sum(r["amount"] or 0 for r in rows) or 1
    by = {}
    for g in ("A", "B", "C"):
        sub = [r for r in rows if r["calc_usage_grade"] == g]
        by[g] = {
            "count": len(sub),
            "amount": sum(r["amount"] or 0 for r in sub),
            "share": round(sum(r["amount"] or 0 for r in sub) / total * 100, 1),
        }
    used = [r for r in rows if (r["amount"] or 0) > 0]
    top20 = used[:max(len(used) // 5, 1)]
    return {
        "total": len(rows),
        "total_amount": total,
        "used_count": len(used),
        "no_use": len(rows) - len(used),
        "by_grade": by,
        "top20_share": round(sum(r["amount"] for r in top20) / total * 100, 1),
        "missing": len([r for r in rows if r["is_missing"]]),
        "changed": len([r for r in rows if r["changed"]]),
        "grade_changed": len([r for r in rows if r["grade_changed"]]),
        "cut": PARETO_CUT,
    }


# ── 입출고 이력 화면 ─────────────────────────────────────────
def transaction_list(conn):
    """입출고 1,075건. 자재 정보는 tx_products() 조회표로 분리해 중복을 없앤다.

    (자재명·규격·분류를 1,075행마다 반복하면 응답이 1.5MB까지 커진다.
     P_ID 만 담고 화면에서 조회표와 합치면 1/3 이하로 줄어든다.)
    """
    return _rows(conn, """
        SELECT t.T_ID, t.T_Type, t.T_Date, t.T_Num,
               t.Lot_ID, l.P_ID, l.Loc_ID,
               u.Name AS worker, u.Position AS worker_pos
          FROM Transaction_tb t
          JOIN Lot_tb l     ON t.Lot_ID = l.Lot_ID
          LEFT JOIN User_tb u ON t.EP_ID = u.EP_ID
         ORDER BY t.T_Date DESC, t.T_ID DESC
    """)


def tx_products(conn):
    """P_ID → 자재 정보 조회표 (200건). 거래 목록과 합쳐 쓴다."""
    out = {}
    for r in _rows(conn, """
        SELECT p.P_ID, p.P_N, p.Spec, p.P_Price,
               cat.Cat_Name AS cat_name,
               c.CP_N       AS supplier,
               s.Sf_Lv      AS grade
          FROM Product_tb p
          LEFT JOIN Company_tb c ON p.BRN = c.BRN
          LEFT JOIN Cat_tb cat   ON p.MainCat=cat.MainCat AND p.SubCat=cat.SubCat
                                AND p.DetailCat=cat.DetailCat
          LEFT JOIN Safe_tb s    ON p.P_ID = s.P_ID
    """):
        out[r.pop("P_ID")] = r
    return out


def tx_locations(conn):
    """Loc_ID → 창고명 조회표."""
    return {r["Loc_ID"]: r["Loc_N"] for r in _rows(conn, "SELECT Loc_ID, Loc_N FROM Location_tb")}


def lot_trace(conn):
    """LOT별 전체 이력 — 발주→입고→불출들→잔량. 추적(traceability)용."""
    lots = _rows(conn, f"""
        SELECT l.Lot_ID, l.P_ID, p.P_N, p.Spec, l.Lot_Date, l.P_Qty,
               l.Loc_ID, lo.Loc_N AS loc_name, l.H_ID,
               h.P_Date           AS order_date,
               c.CP_N             AS supplier,
               u.Name             AS receiver,
               CAST(julianday(l.Lot_Date) - julianday(h.P_Date) AS INT) AS lead_days,
               l.P_Qty - COALESCE(x.out_qty, 0) AS remain,
               COALESCE(x.out_qty, 0)  AS out_qty,
               COALESCE(x.out_cnt, 0)  AS out_cnt
          FROM Lot_tb l
          JOIN Product_tb p ON l.P_ID = p.P_ID
          LEFT JOIN Location_tb lo ON l.Loc_ID = lo.Loc_ID
          LEFT JOIN Purchase_Header_tb h ON l.H_ID = h.H_ID
          LEFT JOIN Company_tb c ON h.BRN = c.BRN
          LEFT JOIN User_tb u ON l.EP_ID = u.EP_ID
          LEFT JOIN (SELECT Lot_ID, {LOT_DELTA} AS out_qty, SUM(CASE WHEN T_Type IN ('불출') THEN 1 ELSE 0 END) out_cnt FROM Transaction_tb GROUP BY Lot_ID) x
                 ON x.Lot_ID = l.Lot_ID
    """)
    # 생산 투입 이력 (그 LOT이 어느 완제품에 쓰였나)
    used = {}
    for r in _rows(conn, """
        SELECT Lot_ID, FG_ID, Work_Order, Prod_Date, SUM(Prod_Qty) qty
          FROM Production_tb GROUP BY Lot_ID, FG_ID, Work_Order, Prod_Date
         ORDER BY Prod_Date
    """):
        used.setdefault(r["Lot_ID"], []).append(r)
    # 화면에는 LOT당 상위 몇 건만 보여주므로 전량(4,535건)을 실어 보내지 않는다.
    # 전부 담으면 응답이 500KB 이상 불어난다.
    SHOW = 6
    for l in lots:
        u = used.get(l["Lot_ID"], [])
        l["used_in"] = u[:SHOW]
        l["used_total"] = len(u)
        l["used_fgs"] = sorted({x["FG_ID"] for x in u})
        l["used_qty"] = sum(x["qty"] or 0 for x in u)
    return lots


def tx_monthly(conn):
    """월별 입출고 추이 (차트용)."""
    return _rows(conn, f"""
        SELECT substr(t.T_Date,1,7) AS ym,
               SUM(CASE WHEN {TX_IN_T} THEN t.T_Num ELSE 0 END) AS in_qty,
               SUM(CASE WHEN {TX_OUT_T} THEN t.T_Num ELSE 0 END) AS out_qty,
               SUM(CASE WHEN {TX_IN_T} THEN 1 ELSE 0 END)       AS in_cnt,
               SUM(CASE WHEN {TX_OUT_T} THEN 1 ELSE 0 END)       AS out_cnt,
               SUM(CASE WHEN {TX_IN_T} THEN t.T_Num*p.P_Price ELSE 0 END) AS in_amt,
               SUM(CASE WHEN {TX_OUT_T} THEN t.T_Num*p.P_Price ELSE 0 END) AS out_amt
          FROM Transaction_tb t
          JOIN Lot_tb l ON t.Lot_ID = l.Lot_ID
          JOIN Product_tb p ON l.P_ID = p.P_ID
         GROUP BY ym ORDER BY ym
    """)


def tx_summary(rows, lots, prods=None):
    prods = prods or {}
    price = lambda r: (prods.get(r["P_ID"], {}).get("P_Price") or 0)
    ins = [r for r in rows if r["T_Type"] == "입고"]
    outs = [r for r in rows if r["T_Type"] == "불출"]
    dates = [r["T_Date"] for r in rows if r["T_Date"]]
    return {
        "total": len(rows),
        "in_cnt": len(ins),
        "out_cnt": len(outs),
        "in_qty": sum(r["T_Num"] or 0 for r in ins),
        "out_qty": sum(r["T_Num"] or 0 for r in outs),
        "in_amt": sum((r["T_Num"] or 0) * price(r) for r in ins),
        "out_amt": sum((r["T_Num"] or 0) * price(r) for r in outs),
        "date_from": min(dates) if dates else "-",
        "date_to": max(dates) if dates else "-",
        "lot_total": len(lots),
        "lot_live": len([l for l in lots if (l["remain"] or 0) > 0]),
        "lot_done": len([l for l in lots if (l["remain"] or 0) <= 0]),
        "workers": len({r["worker"] for r in rows if r["worker"]}),
    }


# ── 협력사 화면 ──────────────────────────────────────────────
import statistics as _st


def supplier_list(conn, period=""):
    """협력사 20개사 + 리드타임 통계 + 발주 실적 + 공급 리스크.

    period 를 주면 리드타임·발주 실적을 그 기간의 발주만으로 집계한다.
    ⚠️ 공급 품목 수·A등급·안전재고 미달·재고자산은 '현재 상태'라 기간을 타지 않는다.
       작년에 발주한 자재도 지금 재고가 모자라면 모자란 것이다.
    """
    comps = _rows(conn, "SELECT BRN, CP_N, Is_Foreign FROM Company_tb ORDER BY CP_N")

    base = conn.execute("SELECT MAX(P_Date) FROM Purchase_Header_tb").fetchone()[0]
    pf, pt = period_range(base, period)
    pw = _between("h.P_Date", pf, pt)

    # 발주-입고 쌍에서 리드타임 실측
    lt_raw = _rows(conn, f"""
        SELECT h.BRN, l.Lot_ID, h.H_ID, h.P_Date, l.Lot_Date,
               CAST(julianday(l.Lot_Date) - julianday(h.P_Date) AS INT) AS lt
          FROM Lot_tb l JOIN Purchase_Header_tb h ON l.H_ID = h.H_ID
         WHERE {pw}
         ORDER BY h.P_Date
    """)
    by_brn = {}
    for r in lt_raw:
        by_brn.setdefault(r["BRN"], []).append(r)

    # 발주 실적 (금액은 발주상세 × 단가)
    orders = {r["BRN"]: r for r in _rows(conn, f"""
        SELECT h.BRN,
               COUNT(DISTINCT h.H_ID)   AS order_cnt,
               COUNT(*)                 AS line_cnt,
               SUM(d.P_Qty * p.P_Price) AS amount,
               SUM(d.P_Qty)             AS qty,
               MIN(h.P_Date)            AS first_order,
               MAX(h.P_Date)            AS last_order
          FROM Purchase_Header_tb h
          JOIN Purchase_Detail_tb d ON h.H_ID = d.H_ID
          JOIN Product_tb p         ON d.P_ID = p.P_ID
         WHERE {pw}
         GROUP BY h.BRN
    """)}

    # ── 입고 준수 ────────────────────────────────────────────
    # "발주한 대로 들어왔는가" 를 세 갈래로 본다.
    #   품번 준수 — 대체 입고가 아니다 (A 를 주문했는데 B 가 오지 않았다)
    #   수량 준수 — 발주 수량 = 입고 수량
    #   납기 준수 — 실제 리드타임 <= 계획 리드타임
    #   종합      — 셋 다 만족
    #
    # ⚠️ 약속 납기일 컬럼이 데이터에 없다. 그래서 자재별 계획 리드타임
    #    (Safe_tb.Lead_Time — 안전재고·발주 계산이 쓰는 그 값)을 약속 납기로 대용한다.
    #    협력사가 실제로 약속한 날짜가 아니라 우리 쪽 계획값이라는 점을 화면에도 적는다.
    #
    # LOT 을 찾을 때 변경 이력을 먼저 본다. 대체 입고는 발주 품번과 LOT 품번이
    # 달라 H_ID+P_ID 로는 못 찾고, 그대로 두면 미입고로 잘못 집계된다.
    comply = {r["BRN"]: r for r in _rows(conn, """
        SELECT h.BRN,
               COUNT(*) AS po_lines,
               SUM(CASE WHEN l.Lot_ID IS NOT NULL THEN 1 ELSE 0 END) AS recv,
               SUM(CASE WHEN l.Lot_ID IS NULL  THEN 1 ELSE 0 END) AS pending,
               SUM(CASE WHEN l.Lot_ID IS NOT NULL
                         AND ch.Chg_ID IS NOT NULL AND ch.In_P_ID <> ch.Ord_P_ID
                        THEN 1 ELSE 0 END) AS swap,
               SUM(CASE WHEN l.Lot_ID IS NOT NULL AND l.P_Qty = d.P_Qty THEN 1 ELSE 0 END) AS qty_ok,
               SUM(CASE WHEN l.Lot_ID IS NOT NULL AND l.P_Qty < d.P_Qty THEN 1 ELSE 0 END) AS qty_short,
               SUM(CASE WHEN l.Lot_ID IS NOT NULL AND l.P_Qty > d.P_Qty THEN 1 ELSE 0 END) AS qty_over,
               SUM(CASE WHEN l.Lot_ID IS NOT NULL AND s.Lead_Time IS NOT NULL
                        THEN 1 ELSE 0 END) AS due_base,
               SUM(CASE WHEN l.Lot_ID IS NOT NULL AND s.Lead_Time IS NOT NULL
                         AND julianday(l.Lot_Date) - julianday(h.P_Date) <= s.Lead_Time
                        THEN 1 ELSE 0 END) AS due_ok,
               SUM(CASE WHEN l.Lot_ID IS NOT NULL
                         AND l.P_Qty = d.P_Qty
                         AND (ch.Chg_ID IS NULL OR ch.In_P_ID = ch.Ord_P_ID)
                         AND s.Lead_Time IS NOT NULL
                         AND julianday(l.Lot_Date) - julianday(h.P_Date) <= s.Lead_Time
                        THEN 1 ELSE 0 END) AS all_ok,
               AVG(CASE WHEN l.Lot_ID IS NOT NULL AND s.Lead_Time IS NOT NULL
                        THEN julianday(l.Lot_Date) - julianday(h.P_Date) - s.Lead_Time END) AS delay,
               MAX(CASE WHEN l.Lot_ID IS NOT NULL AND s.Lead_Time IS NOT NULL
                        THEN julianday(l.Lot_Date) - julianday(h.P_Date) - s.Lead_Time END) AS delay_max
          FROM Purchase_Detail_tb d
          JOIN Purchase_Header_tb h ON d.H_ID = h.H_ID
          LEFT JOIN Safe_tb s       ON d.P_ID = s.P_ID
          LEFT JOIN Purchase_Change_tb ch
                 ON ch.H_ID = d.H_ID AND ch.Purchase_num = d.Purchase_num
          LEFT JOIN Lot_tb l
                 ON l.Lot_ID = COALESCE(ch.Lot_ID, (
                        SELECT l2.Lot_ID FROM Lot_tb l2
                         WHERE l2.H_ID = d.H_ID AND l2.P_ID = d.P_ID
                           AND l2.Lot_ID NOT IN (%s)
                         LIMIT 1))
         WHERE %s
         GROUP BY h.BRN
    """ % (_CLAIMED_LOTS, pw))}

    # 공급 품목 + 리스크 (A등급 / 안전재고 미달)
    risk = {r["BRN"]: r for r in _rows(conn, f"""
        WITH stock AS ({STOCK_SQL})
        SELECT p.BRN,
               COUNT(*)                                              AS item_cnt,
               SUM(CASE WHEN s.Sf_Lv='A' THEN 1 ELSE 0 END)          AS grade_a,
               SUM(CASE WHEN COALESCE(st.stock,0) < s.Sf_Num THEN 1 ELSE 0 END) AS short_cnt,
               SUM(COALESCE(st.stock,0) * p.P_Price)                 AS stock_value
          FROM Product_tb p
          LEFT JOIN Safe_tb s ON p.P_ID = s.P_ID
          LEFT JOIN stock st  ON p.P_ID = st.P_ID
         GROUP BY p.BRN
    """)}

    for c in comps:
        b = c["BRN"]
        lts = [r["lt"] for r in by_brn.get(b, []) if r["lt"] is not None]
        c["lt_list"] = lts
        c["lt_hist"] = [{"date": r["P_Date"], "lt": r["lt"], "hid": r["H_ID"]}
                        for r in by_brn.get(b, [])][-40:]
        if lts:
            c["lt_n"] = len(lts)
            c["lt_avg"] = round(_st.mean(lts), 1)
            c["lt_med"] = int(_st.median(lts))
            c["lt_min"] = min(lts)
            c["lt_max"] = max(lts)
            c["lt_sd"] = round(_st.stdev(lts), 1) if len(lts) > 1 else 0.0
            # 변동계수 — 리드타임 길이가 달라도 공정하게 안정성을 비교할 수 있다
            c["lt_cv"] = round(c["lt_sd"] / c["lt_avg"] * 100, 1) if c["lt_avg"] else 0.0
        else:
            c.update(lt_n=0, lt_avg=None, lt_med=None, lt_min=None,
                     lt_max=None, lt_sd=None, lt_cv=None)

        o = orders.get(b, {})
        c["order_cnt"] = o.get("order_cnt", 0)
        c["line_cnt"] = o.get("line_cnt", 0)
        c["amount"] = o.get("amount", 0) or 0
        c["qty"] = o.get("qty", 0) or 0
        c["first_order"] = o.get("first_order")
        c["last_order"] = o.get("last_order")

        # 입고 준수율 — 분모는 '입고 판정이 끝난 라인'. 미입고를 분모에 넣으면
        # 발주 직후 기간일수록 준수율이 0% 에 가깝게 떨어진다.
        m = comply.get(b, {})
        recv = m.get("recv", 0) or 0
        due_base = m.get("due_base", 0) or 0
        c["po_lines"] = m.get("po_lines", 0) or 0
        c["recv_lines"] = recv
        c["pending_lines"] = m.get("pending", 0) or 0
        c["swap_lines"] = m.get("swap", 0) or 0
        c["qty_ok"] = m.get("qty_ok", 0) or 0
        c["qty_short"] = m.get("qty_short", 0) or 0
        c["qty_over"] = m.get("qty_over", 0) or 0
        c["due_ok"] = m.get("due_ok", 0) or 0
        c["due_base"] = due_base
        c["all_ok"] = m.get("all_ok", 0) or 0
        c["qty_pct"] = _pct(c["qty_ok"], recv) if recv else None
        c["due_pct"] = _pct(c["due_ok"], due_base) if due_base else None
        c["comply_pct"] = _pct(c["all_ok"], recv) if recv else None
        c["delay_avg"] = round(m["delay"], 1) if m.get("delay") is not None else None
        c["delay_max"] = int(m["delay_max"]) if m.get("delay_max") is not None else None
        c["comply_lv"] = (None if c["comply_pct"] is None else
                          ("우수" if c["comply_pct"] >= 95 else
                           ("양호" if c["comply_pct"] >= 80 else
                            ("주의" if c["comply_pct"] >= 60 else "미흡"))))

        k = risk.get(b, {})
        c["item_cnt"] = k.get("item_cnt", 0)
        c["grade_a"] = k.get("grade_a", 0) or 0
        c["short_cnt"] = k.get("short_cnt", 0) or 0
        c["stock_value"] = k.get("stock_value", 0) or 0

        # 공급 리스크 점수 (높을수록 주의)
        #   리드타임이 길수록 / 변동이 클수록 / A등급을 많이 댈수록 / 미달이 많을수록 / 해외일수록
        score = 0
        if c["lt_avg"]:
            score += min(c["lt_avg"] / 10, 6)          # 최대 6점
            score += min((c["lt_cv"] or 0) / 5, 4)     # 최대 4점
        score += min(c["grade_a"] * 1.5, 4)            # 최대 4점
        score += min(c["short_cnt"] * 0.5, 4)          # 최대 4점
        if c["Is_Foreign"] == "Y":
            score += 2
        c["risk"] = round(score, 1)
        c["risk_lv"] = "높음" if score >= 12 else ("보통" if score >= 7 else "낮음")
    return comps


def supplier_items(conn):
    """협력사별 공급 품목 (상세 패널용)."""
    return _rows(conn, f"""
        WITH stock AS ({STOCK_SQL})
        SELECT p.BRN, p.P_ID, p.P_N, p.Spec, p.P_Price,
               s.Sf_Lv AS grade, s.Sf_Num AS safe_qty, s.Lead_Time AS lead_time,
               COALESCE(st.stock, 0) AS stock,
               COALESCE(st.stock, 0) - COALESCE(s.Sf_Num, 0) AS diff
          FROM Product_tb p
          LEFT JOIN Safe_tb s ON p.P_ID = s.P_ID
          LEFT JOIN stock st  ON p.P_ID = st.P_ID
         ORDER BY p.BRN, (COALESCE(st.stock,0) - COALESCE(s.Sf_Num,0))
    """)


def supplier_summary(comps):
    with_lt = [c for c in comps if c["lt_n"]]
    # 전체 입고 준수율 — 협력사별 비율의 평균이 아니라 라인 수로 합산한다.
    # 평균을 내면 한 라인짜리 협력사가 100라인짜리와 같은 무게를 갖는다.
    recv = sum(c.get("recv_lines", 0) or 0 for c in comps)
    dueb = sum(c.get("due_base", 0) or 0 for c in comps)
    return {
        "total": len(comps),
        "foreign": len([c for c in comps if c["Is_Foreign"] == "Y"]),
        "domestic": len([c for c in comps if c["Is_Foreign"] != "Y"]),
        "amount": sum(c["amount"] for c in comps),
        "order_cnt": sum(c["order_cnt"] for c in comps),
        "lt_avg": round(sum(c["lt_avg"] * c["lt_n"] for c in with_lt)
                        / sum(c["lt_n"] for c in with_lt), 1) if with_lt else 0,
        "risk_high": len([c for c in comps if c["risk_lv"] == "높음"]),
        "risk_mid": len([c for c in comps if c["risk_lv"] == "보통"]),
        "risk_low": len([c for c in comps if c["risk_lv"] == "낮음"]),
        "recv_lines": recv,
        "pending_lines": sum(c.get("pending_lines", 0) or 0 for c in comps),
        "qty_pct": _pct(sum(c.get("qty_ok", 0) or 0 for c in comps), recv) if recv else 0,
        "due_pct": _pct(sum(c.get("due_ok", 0) or 0 for c in comps), dueb) if dueb else 0,
        "comply_pct": _pct(sum(c.get("all_ok", 0) or 0 for c in comps), recv) if recv else 0,
        "comply_poor": len([c for c in comps
                            if c.get("comply_lv") in ("주의", "미흡")]),
    }


# ── 생산 실적 화면 ───────────────────────────────────────────
def production_orders(conn):
    """작업지시 488건 + 투입 자재 + BOM 대비 검증.

    완제품 생산 수량이 원본 데이터에 없어 추정해야 한다.
    처음엔 '최소 투입량 = 1대당'으로 잡았으나, 최소 투입 자재가 1대당 1개가
    아닌 경우 전체 비율이 어긋나 오탐이 대량 발생했다(423행).
    → 표준 BOM이 있는 자재들의 (실투입 ÷ BOM소요량) 중앙값을 생산 대수로 삼는다.
      일부 자재가 대체품으로 빠져도 중앙값이라 흔들리지 않는다.

    또 한 자재를 여러 LOT에서 나눠 꺼내면 행이 쪼개지므로(FIFO 분할 출고),
    BOM 비교 전에 작업지시 내에서 자재별로 합산한다.
    """
    raw = _rows(conn, """
        SELECT r.Work_Order, r.FG_ID, r.P_ID, r.Prod_Qty, r.Prod_Date,
               r.Lot_ID, r.EP_ID,
               p.P_N, p.Spec, p.P_Price,
               u.Name AS worker
          FROM Production_tb r
          JOIN Product_tb p ON r.P_ID = p.P_ID
          LEFT JOIN User_tb u ON r.EP_ID = u.EP_ID
         ORDER BY r.Prod_Date, r.Work_Order, r.P_ID
    """)
    fg_name = {r["FG_ID"]: r["FG_N"] for r in _rows(conn, "SELECT FG_ID, FG_N FROM FG_tb")}

    # BOM 조회표
    bom, alt_of = {}, {}
    for b in _rows(conn, "SELECT FG_ID, P_ID, BOM_Qty, BOM_Type FROM BOM_tb"):
        bom.setdefault(b["FG_ID"], {})[b["P_ID"]] = b
        t = _alt_target(b["BOM_Type"])
        if t:
            alt_of[b["P_ID"]] = t

    grouped = {}
    for r in raw:
        grouped.setdefault(r["Work_Order"], []).append(r)

    orders = []
    for wo, lines in grouped.items():
        fg = lines[0]["FG_ID"]
        std = bom.get(fg, {})
        # 생산 대수 추정 — 표준 BOM 자재들의 (실투입 ÷ 소요량) 중앙값
        # 자재별 합산 (같은 자재가 여러 LOT으로 쪼개진 경우 통합)
        merged = {}
        for l in lines:
            m = merged.get(l["P_ID"])
            if m:
                m["Prod_Qty"] += l["Prod_Qty"]
                m["lots"].append(l["Lot_ID"])
            else:
                merged[l["P_ID"]] = {
                    "P_ID": l["P_ID"], "P_N": l["P_N"], "Spec": l["Spec"],
                    "P_Price": l["P_Price"], "Prod_Qty": l["Prod_Qty"],
                    "lots": [l["Lot_ID"]],
                }
        mlines = list(merged.values())

        cands = []
        for l in mlines:
            b = std.get(l["P_ID"])
            if b and not _alt_target(b["BOM_Type"]) and b["BOM_Qty"]:
                cands.append(l["Prod_Qty"] / b["BOM_Qty"])
        if cands:
            base = round(_st.median(cands))
        else:
            base = min(l["Prod_Qty"] for l in mlines)
        base = base or 1
        items, match, diff, alt_used, extra = [], 0, 0, 0, 0
        for l in mlines:
            b = std.get(l["P_ID"])
            per = l["Prod_Qty"] / base
            if not b:
                verdict, bq = "미등록", None
                extra += 1
            elif _alt_target(b["BOM_Type"]):
                verdict, bq = "대체", b["BOM_Qty"]
                alt_used += 1
            elif abs(per - b["BOM_Qty"]) < 0.01:
                verdict, bq = "일치", b["BOM_Qty"]
                match += 1
            else:
                verdict, bq = "차이", b["BOM_Qty"]
                diff += 1
            # 자재명·규격은 prod_products() 조회표로 분리한다 (4,476개 항목에 중복되면 +450KB)
            items.append({
                "P_ID": l["P_ID"],
                "qty": l["Prod_Qty"], "per": round(per, 2),
                "bom_qty": bq, "verdict": verdict,
                "alt_for": alt_of.get(l["P_ID"]),
                "amount": round((l["Prod_Qty"] or 0) * (l["P_Price"] or 0)),
                "lot_n": len(l["lots"]),
            })
        orders.append({
            "wo": wo, "FG_ID": fg, "FG_N": fg_name.get(fg, fg),
            "date": lines[0]["Prod_Date"],
            "worker": lines[0]["worker"],
            "units": base,                     # 추정 생산 대수
            "line_cnt": len(mlines),
            "raw_cnt": len(lines),
            "amount": sum(i["amount"] for i in items),
            "match": match, "diff": diff, "alt_used": alt_used, "extra": extra,
            "items": items,
        })
    orders.sort(key=lambda o: (o["date"] or "", o["wo"]), reverse=True)
    return orders


def prod_products(conn):
    """P_ID → 자재명·규격 조회표 (생산 화면 items 와 합쳐 쓴다)."""
    return {r["P_ID"]: {"P_N": r["P_N"], "Spec": r["Spec"]}
            for r in _rows(conn, "SELECT P_ID, P_N, Spec FROM Product_tb")}


def production_monthly(conn):
    return _rows(conn, """
        SELECT substr(r.Prod_Date,1,7) AS ym,
               COUNT(DISTINCT r.Work_Order) AS wo_cnt,
               COUNT(*)                     AS line_cnt,
               SUM(r.Prod_Qty * p.P_Price)  AS amount
          FROM Production_tb r JOIN Product_tb p ON r.P_ID = p.P_ID
         GROUP BY ym ORDER BY ym
    """)


def production_summary(orders, conn=None):
    fgs = {}
    for o in orders:
        f = fgs.setdefault(o["FG_ID"], {"FG_N": o["FG_N"], "wo": 0, "units": 0, "amount": 0})
        f["wo"] += 1
        f["units"] += o["units"]
        f["amount"] += o["amount"]
    tot_line = sum(o["line_cnt"] for o in orders)
    tot_match = sum(o["match"] for o in orders)
    tot_alt = sum(o["alt_used"] for o in orders)
    tot_diff = sum(o["diff"] for o in orders)
    dates = [o["date"] for o in orders if o["date"]]
    return {
        "wo_cnt": len(orders),
        "fg_cnt": len(fgs),
        "line_cnt": tot_line,
        "units": sum(o["units"] for o in orders),
        "amount": sum(o["amount"] for o in orders),
        "match": tot_match,
        "match_pct": round(tot_match / tot_line * 100, 1) if tot_line else 0,
        "alt_used": tot_alt,
        "alt_wo": len([o for o in orders if o["alt_used"]]),
        "alt_wo_pct": round(len([o for o in orders if o["alt_used"]]) / len(orders) * 100, 1) if orders else 0,
        "diff": tot_diff,
        "date_from": min(dates) if dates else "-",
        "date_to": max(dates) if dates else "-",
        "by_fg": fgs,
    }


# ── 구매 발주 화면 ───────────────────────────────────────────
def purchase_orders(conn):
    """발주 254건 + 품목별 발주/입고 대조.

    발주 수량과 실제 입고 수량을 비교해 부족 입고를 잡아낸다.
    (부족분은 예외 없이 포장단위 배수 — 상자 단위 출하라 반 상자는 없다)
    """
    # LOT 을 찾을 때 변경 이력을 먼저 본다.
    # 대체 입고는 발주 품번과 LOT 품번이 다르므로 H_ID+P_ID 로는 못 찾는다.
    # 그대로 두면 대체로 받은 라인이 영원히 '미입고' 로 보인다.
    lines = _rows(conn, """
        SELECT h.H_ID, h.BRN, h.P_Date,
               c.CP_N AS supplier, c.Is_Foreign AS is_foreign,
               d.Purchase_num, d.P_ID, d.P_Qty AS ord_qty,
               p.P_N, p.P_Price, p.PkgUnit, p.MinOrderQty,
               s.Sf_Lv AS grade,
               l.Lot_ID, l.P_Qty AS in_qty, l.Lot_Date, l.P_ID AS in_P_ID,
               lo.Loc_N AS loc_name,
               ch.Chg_ID, ch.In_P_ID AS chg_pid, ch.Chg_Type, ch.Settle,
               ch.Diff_Amt, ch.Reason,
               CAST(julianday(l.Lot_Date) - julianday(h.P_Date) AS INT) AS lead_days
          FROM Purchase_Header_tb h
          JOIN Purchase_Detail_tb d ON h.H_ID = d.H_ID
          JOIN Product_tb p         ON d.P_ID = p.P_ID
          LEFT JOIN Company_tb c    ON h.BRN = c.BRN
          LEFT JOIN Safe_tb s       ON p.P_ID = s.P_ID
          LEFT JOIN Purchase_Change_tb ch
                 ON ch.H_ID = d.H_ID AND ch.Purchase_num = d.Purchase_num
          LEFT JOIN Lot_tb l
                 ON l.Lot_ID = COALESCE(ch.Lot_ID, (
                        SELECT l2.Lot_ID FROM Lot_tb l2
                         WHERE l2.H_ID = d.H_ID AND l2.P_ID = d.P_ID
                           AND l2.Lot_ID NOT IN (%s)
                         LIMIT 1))
          LEFT JOIN Location_tb lo  ON l.Loc_ID = lo.Loc_ID
         ORDER BY h.P_Date DESC, h.H_ID DESC, d.Purchase_num
    """ % _CLAIMED_LOTS)

    grouped = {}
    for r in lines:
        grouped.setdefault(r["H_ID"], []).append(r)

    orders = []
    for hid, ls in grouped.items():
        h = ls[0]
        items, short, ok, pending = [], 0, 0, 0
        for l in ls:
            gap = (l["in_qty"] - l["ord_qty"]) if l["in_qty"] is not None else None
            swapped = bool(l["chg_pid"]) and l["chg_pid"] != l["P_ID"]
            if l["in_qty"] is None:
                st = "미입고"; pending += 1
            elif swapped:
                st = "대체"; ok += 1        # 발주는 닫혔다 — 다른 품번으로 받았을 뿐
            elif gap == 0:
                st = "일치"; ok += 1
            elif gap < 0:
                st = "부족"; short += 1
            else:
                st = "초과"
            items.append({
                "num": l["Purchase_num"], "P_ID": l["P_ID"],
                "grade": l["grade"],
                "ord_qty": l["ord_qty"], "in_qty": l["in_qty"], "gap": gap,
                "status": st,
                "pkg": l["PkgUnit"], "moq": l["MinOrderQty"],
                "pkg_multiple": (l["PkgUnit"] and gap is not None and gap != 0
                                 and abs(gap) % l["PkgUnit"] == 0) or False,
                "amount": round((l["ord_qty"] or 0) * (l["P_Price"] or 0)),
                "in_amount": round((l["in_qty"] or 0) * (l["P_Price"] or 0)),
                "Lot_ID": l["Lot_ID"], "Lot_Date": l["Lot_Date"],
                "loc_name": l["loc_name"], "lead_days": l["lead_days"],
                "swap_to": l["chg_pid"] if swapped else None,
                "chg_type": l["Chg_Type"], "settle": l["Settle"],
                "diff_amt": l["Diff_Amt"], "reason": l["Reason"],
            })
        lds = [i["lead_days"] for i in items if i["lead_days"] is not None]
        orders.append({
            "H_ID": hid, "BRN": h["BRN"], "supplier": h["supplier"],
            "is_foreign": h["is_foreign"], "date": h["P_Date"],
            "line_cnt": len(items),
            "amount": sum(i["amount"] for i in items),
            "in_amount": sum(i["in_amount"] for i in items),
            "ok": ok, "short": short, "pending": pending,
            "lead_days": round(sum(lds) / len(lds)) if lds else None,
            "recv_date": max((i["Lot_Date"] for i in items if i["Lot_Date"]), default=None),
            "items": items,
        })
    orders.sort(key=lambda o: (o["date"] or "", o["H_ID"]), reverse=True)
    return orders


def purchase_monthly(conn):
    return _rows(conn, """
        SELECT substr(h.P_Date,1,7) AS ym,
               COUNT(DISTINCT h.H_ID)   AS order_cnt,
               COUNT(*)                 AS line_cnt,
               SUM(d.P_Qty * p.P_Price) AS amount
          FROM Purchase_Header_tb h
          JOIN Purchase_Detail_tb d ON h.H_ID = d.H_ID
          JOIN Product_tb p         ON d.P_ID = p.P_ID
         GROUP BY ym ORDER BY ym
    """)


# ── 기간 구분 ────────────────────────────────────────────────
# 화면의 static/js/period.js 와 같은 구분을 쓴다.
#   ''  = 전체 / 'm3' = 최근 3개월 / '2025-09' = 특정 월
#
# ⚠️ 기준일은 '오늘'이 아니라 데이터의 마지막 날짜다.
#    더미데이터의 시간축과 실제 오늘이 달라 오늘 기준으로 자르면 전부 0건이 된다.
PERIOD_MONTHS = [12, 6, 3, 1]


def period_range(base, period):
    """(시작일, 종료일) 을 돌려준다. 전체면 (None, None).

    base 는 그 화면의 기준일(데이터 마지막 날짜).
    """
    import datetime
    import re as _re
    if not period or not base:
        return None, None
    if _re.fullmatch(r"m(\d{1,2})", period or ""):
        n = int(period[1:])
        if n not in PERIOD_MONTHS:
            return None, None
        y, m, d = (int(x) for x in base.split("-"))
        m2 = m - n
        y2 = y + (m2 - 1) // 12
        m2 = (m2 - 1) % 12 + 1
        # 말일 보정 — 3/31 의 1개월 전은 2/28
        nxt = datetime.date(y2 + (m2 == 12), (m2 % 12) + 1, 1)
        last = (nxt - datetime.timedelta(days=1)).day
        return datetime.date(y2, m2, min(d, last)).isoformat(), base
    if _re.fullmatch(r"\d{4}-\d{2}", period or ""):
        return period + "-01", period + "-31"
    return None, None


def month_list(conn, table, col, where="1=1"):
    """그 테이블에 실제로 데이터가 있는 월 목록. 기간 선택의 '특정 월' 용."""
    return [r["ym"] for r in _rows(conn, f"""
        SELECT DISTINCT substr({col},1,7) AS ym
          FROM {table} WHERE {where} AND {col} IS NOT NULL
         ORDER BY ym
    """)]


def period_label(base, period):
    """화면 상단에 쓸 문장."""
    if not period:
        return "전체 기간"
    f, t = period_range(base, period)
    if not f:
        return "전체 기간"
    names = {12: "최근 1년", 6: "최근 6개월", 3: "최근 3개월 (분기)", 1: "최근 1개월"}
    if period.startswith("m"):
        return "%s (%s ~ %s)" % (names.get(int(period[1:]), period), f, t)
    return period + " 한 달"


def _between(col, f, t):
    """WHERE 조각. 기간이 없으면 항상 참이라 SQL 을 그대로 쓸 수 있다."""
    if not f:
        return "1=1"
    return "%s BETWEEN '%s' AND '%s'" % (col, f, t)


def _pct(part, whole, nd=1):
    """백분율 반올림. 파이썬 round() 는 은행가 반올림이라 96.25 → 96.2 가 되어
    화면(JS Math.round, 올림)과 값이 갈린다. 표시용은 0.5 올림으로 통일한다."""
    if not whole:
        return 0
    import math
    f = 10 ** nd
    return math.floor(part / whole * 100 * f + 0.5) / f


def purchase_summary(orders):
    """발주 요약.

    ⚠️ 입고 일치율의 분모는 '이미 입고된 품목'뿐이다.
       아직 들어오지 않은 발주(미입고)를 불일치로 세면,
       최근 발주가 많을수록 일치율이 가짜로 떨어진다.
       발주 직후에는 전부 미입고이므로 0% 가 되어버린다.
    """
    lines = [i for o in orders for i in o["items"]]
    ok = len([i for i in lines if i["status"] == "일치"])
    short = [i for i in lines if i["status"] == "부족"]
    pending = len([i for i in lines if i["status"] == "미입고"])
    recv = len(lines) - pending          # 입고 판정이 끝난 품목
    dates = [o["date"] for o in orders if o["date"]]
    lds = [o["lead_days"] for o in orders if o["lead_days"] is not None]
    return {
        "order_cnt": len(orders),
        "line_cnt": len(lines),
        "recv_cnt": recv,
        "amount": sum(o["amount"] for o in orders),
        "in_amount": sum(o["in_amount"] for o in orders),
        "ok": ok,
        "ok_pct": _pct(ok, recv),
        "short": len(short),
        "short_qty": sum(abs(i["gap"] or 0) for i in short),
        "short_pkg_ok": len([i for i in short if i["pkg_multiple"]]),
        "pending": pending,
        "short_orders": len([o for o in orders if o["short"]]),
        "lead_avg": round(sum(lds) / len(lds), 1) if lds else 0,
        "date_from": min(dates) if dates else "-",
        "date_to": max(dates) if dates else "-",
        "suppliers": len({o["BRN"] for o in orders}),
    }


# ── 재고 현황 지도 화면 ──────────────────────────────────────
def stock_zones(conn):
    """창고 5구역별 재고 현황 + 소분류 구성."""
    zones = _rows(conn, f"""
        WITH lot AS (
            SELECT l.Lot_ID, l.Loc_ID, l.P_ID, l.Lot_Date,
                   l.P_Qty - COALESCE(x.out_qty, 0) AS remain, l.P_Qty
              FROM Lot_tb l
              LEFT JOIN (SELECT Lot_ID, {LOT_DELTA} AS out_qty FROM Transaction_tb GROUP BY Lot_ID) x ON x.Lot_ID = l.Lot_ID
        )
        SELECT lo.Loc_ID, lo.Loc_N,
               COUNT(*)                                        AS lot_total,
               SUM(CASE WHEN t.remain > 0 THEN 1 ELSE 0 END)    AS lot_live,
               COUNT(DISTINCT t.P_ID)                           AS item_cnt,
               SUM(t.remain)                                    AS qty,
               SUM(t.remain * p.P_Price)                        AS value,
               MIN(p.MainCat)                                   AS main_cat
          FROM lot t
          JOIN Location_tb lo ON t.Loc_ID = lo.Loc_ID
          JOIN Product_tb p   ON t.P_ID = p.P_ID
         GROUP BY lo.Loc_ID, lo.Loc_N
         ORDER BY lo.Loc_ID
    """)

    # 구역별 소분류 구성
    sub = {}
    # 주의: Lot_tb 를 그냥 조인하면 한 자재의 재고가 LOT 개수만큼 중복 합산된다.
    #       구역-자재 조합을 먼저 DISTINCT 로 뽑고 나서 재고를 붙인다.
    for r in _rows(conn, f"""
        WITH stock AS ({STOCK_SQL}),
             zp AS (SELECT DISTINCT Loc_ID, P_ID FROM Lot_tb)
        SELECT zp.Loc_ID, p.DetailCat,
               MIN(cat.Cat_Name) AS cat_name,
               COUNT(DISTINCT p.P_ID) AS item_cnt,
               SUM(COALESCE(st.stock,0))              AS qty,
               SUM(COALESCE(st.stock,0) * p.P_Price)  AS value
          FROM zp
          JOIN Product_tb p ON zp.P_ID = p.P_ID
          LEFT JOIN stock st ON p.P_ID = st.P_ID
          LEFT JOIN Cat_tb cat ON p.MainCat=cat.MainCat AND p.SubCat=cat.SubCat
                              AND p.DetailCat=cat.DetailCat
         GROUP BY zp.Loc_ID, p.DetailCat
         ORDER BY zp.Loc_ID, p.DetailCat
    """):
        sub.setdefault(r["Loc_ID"], []).append(r)

    # 구역별 안전재고 미달
    short = {}
    for r in _rows(conn, f"""
        WITH stock AS ({STOCK_SQL})
        SELECT l.Loc_ID, COUNT(DISTINCT p.P_ID) AS n
          FROM Lot_tb l
          JOIN Product_tb p ON l.P_ID = p.P_ID
          JOIN Safe_tb s    ON p.P_ID = s.P_ID
          LEFT JOIN stock st ON p.P_ID = st.P_ID
         WHERE COALESCE(st.stock,0) < s.Sf_Num
         GROUP BY l.Loc_ID
    """):
        short[r["Loc_ID"]] = r["n"]

    for z in zones:
        z["sub"] = sub.get(z["Loc_ID"], [])
        z["short_cnt"] = short.get(z["Loc_ID"], 0)
    return zones


def stock_items(conn):
    """구역별 보유 품목 (지도 상세용)."""
    return _rows(conn, f"""
        WITH stock AS ({STOCK_SQL}),
             lots AS (
                SELECT P_ID, Loc_ID, COUNT(*) AS lot_n, MAX(Lot_Date) AS last_in
                  FROM Lot_tb GROUP BY P_ID, Loc_ID
             )
        SELECT lt.Loc_ID, p.P_ID, p.P_N, p.Spec, p.P_Price, p.DetailCat,
               c.CP_N AS supplier,
               s.Sf_Lv AS grade, s.Sf_Num AS safe_qty,
               COALESCE(st.stock,0) AS stock,
               COALESCE(st.stock,0) - COALESCE(s.Sf_Num,0) AS diff,
               ROUND(COALESCE(st.stock,0) * p.P_Price) AS value,
               lt.lot_n, lt.last_in
          FROM lots lt
          JOIN Product_tb p ON lt.P_ID = p.P_ID
          LEFT JOIN Company_tb c ON p.BRN = c.BRN
          LEFT JOIN Safe_tb s ON p.P_ID = s.P_ID
          LEFT JOIN stock st  ON p.P_ID = st.P_ID
         ORDER BY lt.Loc_ID, (COALESCE(st.stock,0) - COALESCE(s.Sf_Num,0))
    """)


def stock_summary(zones):
    return {
        "zone_cnt": len(zones),
        "qty": sum(z["qty"] or 0 for z in zones),
        "value": sum(z["value"] or 0 for z in zones),
        "item_cnt": sum(z["item_cnt"] or 0 for z in zones),
        "lot_live": sum(z["lot_live"] or 0 for z in zones),
        "lot_total": sum(z["lot_total"] or 0 for z in zones),
        "short_cnt": sum(z["short_cnt"] or 0 for z in zones),
        "max_value": max((z["value"] or 0) for z in zones) if zones else 1,
        "max_qty": max((z["qty"] or 0) for z in zones) if zones else 1,
    }


# ── 메인 대시보드 ────────────────────────────────────────────
# ── 오늘 할 일 ───────────────────────────────────────────────
#
# 대시보드가 재고·발주·리스크 같은 '현황' 만 보여주고 있었다.
# 정작 담당자가 아침에 알아야 할 것은 "지금 내 손이 필요한 곳" 이다.
# 쓰기 화면 7개가 각자 대기열을 갖고 있는데 한 군데서 볼 수가 없었다.
#
# 숫자는 각 화면의 조회 함수를 그대로 쓴다 — 대시보드만 따로 세면 어긋난다.

def worklist(conn):
    """처리 대기 중인 일감을 화면별로 모은다."""
    base = conn.execute("SELECT MAX(T_Date) FROM Transaction_tb").fetchone()[0]

    pending = pending_po(conn)                 # 받을 발주
    claims = claim_summary(conn)               # 불량·반품
    wait_appr = wait_approval_cnt(conn)        # 승인 대기
    opens = open_requests(conn)                # 승인 끝나고 아직 안 나간 요청

    due = conn.execute(
        "SELECT COUNT(*) FROM Safe_tb s"
        " LEFT JOIN (SELECT P_ID, MAX(Updated_Date) AS d FROM Update_Log_tb GROUP BY P_ID) l"
        "        ON s.P_ID = l.P_ID"
        " LEFT JOIN Update_Log_tb u ON u.P_ID = l.P_ID AND u.Updated_Date = l.d"
        " WHERE u.Next_Date IS NOT NULL AND u.Next_Date <= ?", (base,)).fetchone()[0]

    ovr_soon = conn.execute(
        "SELECT COUNT(*) FROM Safe_Override_tb"
        " WHERE Status = '적용'"
        "   AND CAST(julianday(End_Date) - julianday(?) AS INT) BETWEEN 0 AND 14",
        (base,)).fetchone()[0]

    return [
        {"key": "inbound",  "label": "입고 대기",    "n": len(pending),    "unit": "건",
         "sub": "발주는 나갔는데 아직 안 받은 건", "url": "/inbound",
         "tone": "ac" if pending else "mu"},
        {"key": "claim",    "label": "불량 · 반품",  "n": claims.get("open", 0), "unit": "건",
         "sub": "대체입고 / 환불 / 폐기를 정해야 함", "url": "/inbound",
         "tone": "dn" if claims.get("open") else "mu"},
        {"key": "approval", "label": "불출 승인",    "n": wait_appr,       "unit": "건",
         "sub": "승인해야 창고가 열린다", "url": "/approval",
         "tone": "wn" if wait_appr else "mu"},
        {"key": "picking",  "label": "피킹 지시서",  "n": len(opens),      "unit": "장",
         "sub": "승인된 요청 — 집으러 갈 목록", "url": "/picking",
         "tone": "ac" if opens else "mu"},
        {"key": "disburse", "label": "불출 대기",    "n": sum(1 for r in opens if r["left_qty"] > 0), "unit": "건",
         "sub": "잔여 %s개" % format(sum(r["left_qty"] for r in opens), ","), "url": "/disburse",
         "tone": "ac" if opens else "mu"},
        {"key": "safety",   "label": "안전재고 재검토", "n": due,           "unit": "종",
         "sub": ("조정 만료 임박 %d종" % ovr_soon) if ovr_soon else "등급별 주기 도래",
         "url": "/wizard", "tone": "wn" if due else "mu"},
    ]


# ── 알림 ─────────────────────────────────────────────────────
#
# 대기 건수를 대시보드에 들어가야만 볼 수 있었다. 불출 처리 화면에 앉아
# 있으면 승인이 밀려 있는지 알 길이 없다. worklist() 를 레이아웃까지
# 끌어올려 어느 화면에서나 같은 숫자가 보이게 한다.
#
# 숫자를 여기서 다시 세지 않는다 — worklist() 하나만 쓴다. 대시보드의
# '오늘 할 일' 카드와 사이드바 배지가 어긋나면 둘 다 못 믿는다.

# 일감이 실제로 '처리되는' 메뉴. 한 메뉴가 여러 일감을 받기도 한다
# (입고 처리 화면이 입고 대기와 불량·반품 두 탭을 함께 쥔다).
# 불출 요청 화면에는 달지 않는다 — 요청을 올린 사람이 할 일은 끝났고,
# 승인 대기는 승인 화면의 일이다. 보는 곳마다 점이 찍히면 의미가 없다.
ALERT_MENU = {
    "inbound":  "inbound",
    "claim":    "inbound",
    "approval": "approval",
    "picking":  "picking",
    "disburse": "disburse",
    "safety":   "wizard",
}

# 정기 점검은 '밀린 일' 이 아니다. 199종이 주기 도래라고 종에 199가 뜨면
# 다른 숫자가 묻힌다. 목록에는 두되 배지 합계에서는 뺀다
# (대시보드 todo_total 이 이미 같은 기준을 쓴다).
ALERT_ROUTINE = ("safety",)

_TONE_RANK = {"mu": 0, "ac": 1, "wn": 2, "dn": 3}


def _badge_txt(n):
    """배지는 좁다. 세 자리가 넘으면 자리를 못 잡는다."""
    return "99+" if n > 99 else str(n)


def alerts(conn):
    """상단 종·사이드바 배지에 쓸 대기 현황.

    todo     처리 대기 (종 배지에 합산되는 것)
    routine  정기 점검 (목록에만 — 합계에서 뺀다)
    menu     {메뉴 id: {n, txt, tone, title}} — 사이드바 배지
    total    종 배지 숫자
    """
    live = [t for t in worklist(conn) if t["n"] > 0]
    # 정기 점검은 조용히. 199종이 승인 1건과 같은 색이면 급한 게 뭔지 안 보인다
    for t in live:
        if t["key"] in ALERT_ROUTINE:
            t["tone"] = "mu"

    menu = {}
    for t in live:
        mid = ALERT_MENU.get(t["key"])
        if not mid:
            continue
        m = menu.setdefault(mid, {"n": 0, "tone": "mu", "parts": []})
        m["n"] += t["n"]
        if _TONE_RANK[t["tone"]] > _TONE_RANK[m["tone"]]:
            m["tone"] = t["tone"]
        m["parts"].append("%s %s%s" % (t["label"], format(t["n"], ","), t["unit"]))
    for m in menu.values():
        # 배지에 마우스를 올리면 무엇이 몇 건인지 — 숫자만 보고 못 넘어가게
        m["title"] = " · ".join(m.pop("parts"))
        m["txt"] = _badge_txt(m["n"])

    # 키 이름이 'items' 면 Jinja 가 dict.items 메서드로 먼저 잡는다
    todo = [t for t in live if t["key"] not in ALERT_ROUTINE]
    routine = [t for t in live if t["key"] in ALERT_ROUTINE]
    total = sum(t["n"] for t in todo)
    return {"todo": todo, "routine": routine, "menu": menu,
            "total": total, "txt": _badge_txt(total)}


def dashboard(conn):
    """대시보드 한 화면에 필요한 집계를 모아 돌려준다.

    개별 화면들의 조회 함수를 재사용해 숫자가 서로 어긋나지 않게 한다.
    """
    safe = safety_stock_list(conn)
    safe_sum = safety_stock_summary(safe)
    prods = product_list(conn)
    prod_sum = product_summary(prods)
    zones = stock_zones(conn)
    comps = supplier_list(conn)
    brows = bom_rows(conn)
    fgs = fg_list(conn, brows)
    tx = transaction_list(conn)
    txp = tx_products(conn)
    po = purchase_orders(conn)
    po_sum = purchase_summary(po)
    abc = abc_analysis(conn)
    abc_sum = abc_summary(abc)

    # 최근 입출고 8건 (자재명 붙여서)
    recent_tx = []
    for r in tx[:8]:
        p = txp.get(r["P_ID"], {})
        recent_tx.append({
            "T_ID": r["T_ID"], "T_Type": r["T_Type"], "T_Date": r["T_Date"],
            # 유형별 표시 분류 — 새 유형이 들어와도 색이 자동으로 맞는다
            "kind": dict((t, k) for t, _, _, _, k in TX_TYPES).get(r["T_Type"], "move"),
            "T_Num": r["T_Num"], "P_ID": r["P_ID"],
            "P_N": p.get("P_N"), "grade": p.get("grade"),
            "worker": r["worker"],
        })

    todo = worklist(conn)

    # 안전재고 긴급 — 부족량이 큰 순
    urgent = [{
        "P_ID": r["P_ID"], "P_N": r["P_N"], "grade": r["grade"],
        "safe_qty": r["safe_qty"], "stock": r["stock"], "diff": r["diff"],
        "lead_time": r["lead_time"], "supplier": r["supplier"],
    } for r in safe if r["diff"] < 0][:6]

    return {
        "kpi": {
            "item_cnt": prod_sum["total"],
            "stock_value": prod_sum["stock_value"],
            "short_cnt": safe_sum["short"],
            "short_pct": safe_sum["short_pct"],
            "lot_live": sum(z["lot_live"] or 0 for z in zones),
            "lot_total": sum(z["lot_total"] or 0 for z in zones),
            "makeable": len([f for f in fgs if f["can_make"] > 0]),
            "fg_cnt": len(fgs),
            "risk_high": len([c for c in comps if c["risk_lv"] == "높음"]),
            "supplier_cnt": len(comps),
        },
        "urgent": urgent,
        "zones": [{
            "Loc_ID": z["Loc_ID"], "Loc_N": z["Loc_N"], "qty": z["qty"],
            "value": z["value"], "item_cnt": z["item_cnt"],
            "short_cnt": z["short_cnt"],
        } for z in zones],
        "zone_max": max((z["value"] or 0) for z in zones) if zones else 1,
        "recent_tx": recent_tx,
        "todo": todo,
        # 안전재고 재검토는 199종짜리 일괄 작업이라 대기열 합계에서 뺀다.
        # 넣으면 나머지 6건이 숫자에 묻힌다.
        "todo_total": sum(t["n"] for t in todo if t["key"] != "safety"),
        "recent_po": [{
            "H_ID": o["H_ID"], "date": o["date"], "supplier": o["supplier"],
            "line_cnt": o["line_cnt"], "amount": o["amount"],
            "short": o["short"], "lead_days": o["lead_days"],
        } for o in po[:6]],
        "risk_suppliers": sorted(
            [{"CP_N": c["CP_N"], "risk": c["risk"], "risk_lv": c["risk_lv"],
              "lt_avg": c["lt_avg"], "grade_a": c["grade_a"],
              "short_cnt": c["short_cnt"], "is_foreign": c["Is_Foreign"]}
             for c in comps], key=lambda c: -c["risk"])[:5],
        "abc": abc_sum["by_grade"],
        "abc_total": abc_sum["total_amount"],
        "grade_dist": {g: safe_sum["by_grade"][g] for g in ("A", "B", "C")},
        "po_sum": {
            "order_cnt": po_sum["order_cnt"], "amount": po_sum["amount"],
            "ok_pct": po_sum["ok_pct"], "lead_avg": po_sum["lead_avg"],
        },
        "fg_blocked": sorted(
            [{"FG_ID": f["FG_ID"], "FG_N": f["FG_N"], "part_cnt": f["part_cnt"],
              "zero_parts": f["zero_parts"], "neck_name": f["neck_name"],
              "neck_pid": f["neck_pid"]} for f in fgs],
            key=lambda f: -(f["zero_parts"] / (f["part_cnt"] or 1)))[:5],
        "date_to": max((r["T_Date"] for r in tx if r["T_Date"]), default="-"),
    }


# ── LOT 상세 화면 ────────────────────────────────────────────
# 재고 체류일수 구간 (장기 체화 판정용)
AGE_BANDS = [(90, "90일 미만"), (180, "90~180일"), (270, "180~270일"), (10**9, "270일 이상")]


def _age_band(days):
    for limit, label in AGE_BANDS:
        if days < limit:
            return label
    return AGE_BANDS[-1][1]


def lot_detail(conn):
    """LOT 636건 + 체류일수 + FIFO 순번 + 소진 현황.

    기준일은 데이터의 마지막 거래일로 잡는다(실시간 today 를 쓰면
    더미데이터라 체류일수가 비현실적으로 커진다).
    """
    base = conn.execute(
        "SELECT MAX(T_Date) FROM Transaction_tb").fetchone()[0] or "2026-02-07"

    rows = _rows(conn, f"""
        SELECT l.Lot_ID, l.P_ID, l.Lot_Date, l.P_Qty, l.Loc_ID, l.H_ID,
               lo.Loc_N            AS loc_name,
               p.P_N, p.Spec, p.P_Price,
               c.CP_N              AS supplier,
               s.Sf_Lv             AS grade,
               u.Name              AS receiver,
               h.P_Date            AS order_date,
               CAST(julianday(l.Lot_Date) - julianday(h.P_Date) AS INT) AS lead_days,
               x.out_date, COALESCE(x.out_qty, 0) AS out_qty,
               l.P_Qty - COALESCE(x.out_qty, 0)   AS remain,
               CAST(julianday(?) - julianday(l.Lot_Date) AS INT)        AS age_days,
               CAST(julianday(x.out_date) - julianday(l.Lot_Date) AS INT) AS hold_days
          FROM Lot_tb l
          JOIN Product_tb p ON l.P_ID = p.P_ID
          LEFT JOIN Location_tb lo ON l.Loc_ID = lo.Loc_ID
          LEFT JOIN Company_tb c   ON p.BRN = c.BRN
          LEFT JOIN Safe_tb s      ON p.P_ID = s.P_ID
          LEFT JOIN User_tb u      ON l.EP_ID = u.EP_ID
          LEFT JOIN Purchase_Header_tb h ON l.H_ID = h.H_ID
          LEFT JOIN (SELECT Lot_ID, {LOT_DELTA} AS out_qty, MIN(CASE WHEN T_Type IN ('불출') THEN T_Date END) AS out_date FROM Transaction_tb GROUP BY Lot_ID) x
                 ON x.Lot_ID = l.Lot_ID
         ORDER BY l.P_ID, l.Lot_Date
    """, (base,))

    # 생산 투입 요약
    used = {}
    for r in _rows(conn, """
        SELECT Lot_ID, COUNT(DISTINCT Work_Order) AS wo_n,
               SUM(Prod_Qty) AS qty, MIN(FG_ID) AS fg
          FROM Production_tb GROUP BY Lot_ID
    """):
        used[r["Lot_ID"]] = r

    # 자재별 FIFO 순번과 준수 여부
    by_pid = {}
    for r in rows:
        by_pid.setdefault(r["P_ID"], []).append(r)

    for pid, ls in by_pid.items():
        ls.sort(key=lambda x: (x["Lot_Date"] or "", x["Lot_ID"]))
        for i, r in enumerate(ls, 1):
            r["fifo_seq"] = i
            r["fifo_total"] = len(ls)
        # FIFO 위반 판정
        #   "소진된 LOT 끼리의 출고 순서"만 보면 위반이 잡히지 않는다(항상 순서대로 나감).
        #   실제 위반은 **오래된 LOT이 아직 남아 있는데 더 늦게 들어온 LOT이 먼저 나간** 경우다.
        #   이 LOT 이 건너뛰어진 횟수(skipped)를 함께 기록한다.
        for i, r in enumerate(ls):
            skipped = 0
            if (r["remain"] or 0) > 0:
                skipped = len([n for n in ls[i + 1:] if n["out_date"]])
            r["fifo_skipped"] = skipped
            r["fifo_ok"] = 0 if skipped else 1
        for r in ls:
            r["age_band"] = _age_band(r["age_days"] or 0)
            u = used.get(r["Lot_ID"])
            r["prod_wo"] = u["wo_n"] if u else 0
            r["prod_qty"] = u["qty"] if u else 0
            r["prod_fg"] = u["fg"] if u else None
            r["value"] = round((r["remain"] or 0) * (r["P_Price"] or 0))
            r["used_pct"] = round((r["out_qty"] or 0) / r["P_Qty"] * 100, 1) if r["P_Qty"] else 0
    return rows, base


def lot_summary(rows, base):
    live = [r for r in rows if (r["remain"] or 0) > 0]
    done = [r for r in rows if (r["remain"] or 0) <= 0]
    holds = [r["hold_days"] for r in done if r["hold_days"] is not None]
    bands = {}
    for _, label in AGE_BANDS:
        sub = [r for r in live if r["age_band"] == label]
        bands[label] = {"n": len(sub),
                        "qty": sum(r["remain"] or 0 for r in sub),
                        "value": sum(r["value"] or 0 for r in sub)}
    aged = [r for r in live if (r["age_days"] or 0) >= 180]
    return {
        "base_date": base,
        "total": len(rows),
        "live": len(live),
        "done": len(done),
        "qty": sum(r["remain"] or 0 for r in live),
        "value": sum(r["value"] or 0 for r in live),
        "age_avg": round(sum(r["age_days"] or 0 for r in live) / len(live), 1) if live else 0,
        "hold_avg": round(sum(holds) / len(holds), 1) if holds else 0,
        "bands": bands,
        "aged_n": len(aged),
        "aged_value": sum(r["value"] or 0 for r in aged),
        "aged_pct": round(len(aged) / len(live) * 100, 1) if live else 0,
        "fifo_ok": len([r for r in rows if r["fifo_ok"]]),
        "fifo_bad": len([r for r in rows if not r["fifo_ok"]]),
        "fifo_bad_qty": sum(r["remain"] or 0 for r in rows if not r["fifo_ok"]),
        "fifo_bad_value": sum(r["value"] or 0 for r in rows if not r["fifo_ok"]),
    }


# ── 수요 예측 화면 ───────────────────────────────────────────
# [설계 판단]
#   자재당 불출 관측이 1~6회(중앙값 2회)뿐이라 회귀·계절성 같은 통계 예측은
#   표본이 부족해 신뢰할 수 없다. 그래서 방어 가능한 단순 계산만 쓴다.
#       일평균 사용량 d  = 총 불출량 ÷ 관측기간
#       소진 예상일      = 현재고 ÷ d
#       발주 마감일      = 소진 예상일 − 리드타임
#   관측 횟수를 함께 표시해 신뢰도를 사용자가 판단하게 한다.
def forecast_list(conn):
    base = conn.execute("SELECT MAX(T_Date) FROM Transaction_tb").fetchone()[0]
    span = operating_days(conn)

    rows = _rows(conn, f"""
        WITH stock AS ({STOCK_SQL}),
             used AS (
                SELECT l.P_ID,
                       SUM(t.T_Num) AS out_qty,
                       COUNT(*)     AS out_cnt,
                       MIN(t.T_Date) AS first_out,
                       MAX(t.T_Date) AS last_out
                  FROM Transaction_tb t JOIN Lot_tb l ON t.Lot_ID = l.Lot_ID
                 WHERE {DEMAND_T}
                 GROUP BY l.P_ID
             )
        SELECT p.P_ID, p.P_N, p.Spec, p.P_Price, p.MinOrderQty, p.PkgUnit,
               c.CP_N  AS supplier, c.Is_Foreign AS is_foreign,
               s.Sf_Lv AS grade, s.Sf_Num AS safe_qty, s.Lead_Time AS lead_time,
               COALESCE(st.stock, 0)  AS stock,
               COALESCE(u.out_qty, 0) AS out_qty,
               COALESCE(u.out_cnt, 0) AS out_cnt,
               u.first_out, u.last_out
          FROM Product_tb p
          LEFT JOIN Company_tb c ON p.BRN = c.BRN
          LEFT JOIN Safe_tb s    ON p.P_ID = s.P_ID
          LEFT JOIN stock st     ON p.P_ID = st.P_ID
          LEFT JOIN used u       ON p.P_ID = u.P_ID
    """)

    # 자재별 월간 불출 (스파크라인용)
    monthly = {}
    for r in _rows(conn, f"""
        SELECT l.P_ID, substr(t.T_Date,1,7) AS ym, SUM(t.T_Num) AS qty
          FROM Transaction_tb t JOIN Lot_tb l ON t.Lot_ID = l.Lot_ID
         WHERE {DEMAND_T}
         GROUP BY l.P_ID, ym ORDER BY ym
    """):
        monthly.setdefault(r["P_ID"], []).append({"ym": r["ym"], "qty": r["qty"]})

    for r in rows:
        d = (r["out_qty"] or 0) / span
        r["daily"] = round(d, 2)
        r["monthly_avg"] = round(d * 30)
        r["months"] = monthly.get(r["P_ID"], [])
        lt = r["lead_time"] or 0

        if d <= 0:
            r["days_left"] = None      # 사용 이력이 없어 예측 불가
            r["deadline"] = None
            r["urgency"] = "예측불가"
        else:
            left = (r["stock"] or 0) / d
            r["days_left"] = round(left)
            r["deadline"] = round(left - lt)   # 발주까지 남은 일수
            if r["stock"] <= 0:
                r["urgency"] = "재고소진"
            elif r["deadline"] <= 0:
                r["urgency"] = "발주지연"
            elif r["deadline"] <= 14:
                r["urgency"] = "발주임박"
            elif r["deadline"] <= 45:
                r["urgency"] = "주의"
            else:
                r["urgency"] = "여유"

        # 권장 발주량 — 리드타임 소요분 + 안전재고 − 현재고, MOQ·포장단위로 올림
        need = max(round(d * lt + (r["safe_qty"] or 0) - (r["stock"] or 0)), 0)
        moq, pkg = r["MinOrderQty"] or 0, r["PkgUnit"] or 1
        if need > 0:
            need = max(need, moq)
            if pkg > 1:
                need = -(-need // pkg) * pkg     # 포장단위 올림
        r["order_qty"] = need
        r["order_amt"] = round(need * (r["P_Price"] or 0))
        # 관측 횟수로 신뢰도 표시
        r["confidence"] = ("높음" if r["out_cnt"] >= 4
                           else ("보통" if r["out_cnt"] >= 2
                                 else ("낮음" if r["out_cnt"] == 1 else "없음")))
    rows.sort(key=lambda r: (r["deadline"] if r["deadline"] is not None else 9999))
    return rows, base, span


def forecast_monthly(conn):
    return _rows(conn, f"""
        SELECT substr(t.T_Date,1,7) AS ym,
               COUNT(*) AS cnt, SUM(t.T_Num) AS qty,
               SUM(t.T_Num * p.P_Price) AS amount
          FROM Transaction_tb t
          JOIN Lot_tb l ON t.Lot_ID = l.Lot_ID
          JOIN Product_tb p ON l.P_ID = p.P_ID
         WHERE {DEMAND_T}
         GROUP BY ym ORDER BY ym
    """)


def forecast_summary(rows, base, span):
    U = lambda k: [r for r in rows if r["urgency"] == k]
    need = [r for r in rows if r["order_qty"] > 0]
    return {
        "base_date": base,
        "span": span,
        "total": len(rows),
        "predictable": len([r for r in rows if r["days_left"] is not None]),
        "no_history": len(U("예측불가")),
        "sold_out": len(U("재고소진")),
        "delayed": len(U("발주지연")),
        "imminent": len(U("발주임박")),
        "caution": len(U("주의")),
        "safe": len(U("여유")),
        "order_items": len(need),
        "order_amt": sum(r["order_amt"] for r in need),
        "daily_total": round(sum(r["daily"] for r in rows), 1),
        "conf": {k: len([r for r in rows if r["confidence"] == k])
                 for k in ("높음", "보통", "낮음", "없음")},
    }


# ── 발주 캘린더 화면 ─────────────────────────────────────────
def calendar_data(conn):
    """날짜별 발주·입고 집계 + 각 날짜의 상세 목록."""
    # 날짜별 발주
    po_day = _rows(conn, """
        SELECT h.P_Date AS d,
               COUNT(DISTINCT h.H_ID)   AS cnt,
               SUM(d.P_Qty * p.P_Price) AS amount,
               SUM(d.P_Qty)             AS qty
          FROM Purchase_Header_tb h
          JOIN Purchase_Detail_tb d ON h.H_ID = d.H_ID
          JOIN Product_tb p         ON d.P_ID = p.P_ID
         GROUP BY h.P_Date
    """)
    # 날짜별 입고
    in_day = _rows(conn, """
        SELECT l.Lot_Date AS d,
               COUNT(*)                 AS cnt,
               SUM(l.P_Qty * p.P_Price) AS amount,
               SUM(l.P_Qty)             AS qty
          FROM Lot_tb l JOIN Product_tb p ON l.P_ID = p.P_ID
         GROUP BY l.Lot_Date
    """)
    days = {}
    for r in po_day:
        days.setdefault(r["d"], {})["po"] = {"cnt": r["cnt"], "amount": r["amount"], "qty": r["qty"]}
    for r in in_day:
        days.setdefault(r["d"], {})["in"] = {"cnt": r["cnt"], "amount": r["amount"], "qty": r["qty"]}

    # 발주 상세 (날짜 클릭용)
    po_list = {}
    for r in _rows(conn, """
        SELECT h.P_Date AS d, h.H_ID, c.CP_N AS supplier, c.Is_Foreign AS is_foreign,
               COUNT(*) AS line_cnt, SUM(pd.P_Qty * p.P_Price) AS amount,
               MIN(l.Lot_Date) AS recv_date
          FROM Purchase_Header_tb h
          JOIN Purchase_Detail_tb pd ON h.H_ID = pd.H_ID
          JOIN Product_tb p          ON pd.P_ID = p.P_ID
          LEFT JOIN Company_tb c     ON h.BRN = c.BRN
          LEFT JOIN Lot_tb l         ON l.H_ID = h.H_ID
         GROUP BY h.P_Date, h.H_ID, c.CP_N, c.Is_Foreign
         ORDER BY h.P_Date, h.H_ID
    """):
        po_list.setdefault(r["d"], []).append(r)

    # 입고 상세
    in_list = {}
    for r in _rows(conn, """
        SELECT l.Lot_Date AS d, l.Lot_ID, l.P_ID, p.P_N, l.P_Qty,
               lo.Loc_N AS loc_name, c.CP_N AS supplier, l.H_ID,
               CAST(julianday(l.Lot_Date) - julianday(h.P_Date) AS INT) AS lead_days,
               ROUND(l.P_Qty * p.P_Price) AS amount
          FROM Lot_tb l
          JOIN Product_tb p ON l.P_ID = p.P_ID
          LEFT JOIN Location_tb lo ON l.Loc_ID = lo.Loc_ID
          LEFT JOIN Company_tb c   ON p.BRN = c.BRN
          LEFT JOIN Purchase_Header_tb h ON l.H_ID = h.H_ID
         ORDER BY l.Lot_Date, l.Lot_ID
    """):
        in_list.setdefault(r["d"], []).append(r)

    all_dates = sorted(days)
    months = sorted({d[:7] for d in all_dates})
    return {
        "days": days,
        "po_list": po_list,
        "in_list": in_list,
        "months": months,
        "date_from": all_dates[0] if all_dates else None,
        "date_to": all_dates[-1] if all_dates else None,
        "max_po": max((v.get("po", {}).get("cnt", 0) for v in days.values()), default=1),
        "max_in": max((v.get("in", {}).get("cnt", 0) for v in days.values()), default=1),
    }


def calendar_summary(cal):
    po = [v["po"] for v in cal["days"].values() if "po" in v]
    ins = [v["in"] for v in cal["days"].values() if "in" in v]
    return {
        "po_cnt": sum(x["cnt"] for x in po),
        "po_days": len(po),
        "po_amount": sum(x["amount"] or 0 for x in po),
        "in_cnt": sum(x["cnt"] for x in ins),
        "in_days": len(ins),
        "in_amount": sum(x["amount"] or 0 for x in ins),
        "months": len(cal["months"]),
        "date_from": cal["date_from"],
        "date_to": cal["date_to"],
        "po_per_day": round(sum(x["cnt"] for x in po) / len(po), 1) if po else 0,
        "in_per_day": round(sum(x["cnt"] for x in ins) / len(ins), 1) if ins else 0,
    }


# ── 납기 리스크 레이더 ───────────────────────────────────────
# [수요 예측과의 차이]
#   수요 예측 = "언제 발주해야 하는가" (시점 중심)
#   리스크 레이더 = "결품되면 얼마나 아픈가" (영향도 중심)
#   → 긴급도 × 영향도 2차원으로 평가해 우선순위를 매긴다.
def risk_radar(conn):
    rows, base, span = forecast_list(conn)

    # 영향도 재료: 이 자재가 몇 개 완제품에 쓰이나 + 대체품이 있나
    impact = {}
    for r in _rows(conn, """
        SELECT b.P_ID,
               COUNT(DISTINCT b.FG_ID) AS fg_cnt,
               SUM(CASE WHEN b.BOM_Type = '표준' THEN 1 ELSE 0 END) AS std_cnt
          FROM BOM_tb b GROUP BY b.P_ID
    """):
        impact[r["P_ID"]] = r
    # 대체 가능 여부 — 이 자재를 대체할 수 있는 자재가 BOM 에 등록돼 있나
    has_alt = set()
    for r in _rows(conn, "SELECT BOM_Type FROM BOM_tb WHERE BOM_Type LIKE '대체%'"):
        t = _alt_target(r["BOM_Type"])
        if t:
            has_alt.add(t)

    out = []
    for r in rows:
        d = r["daily"] or 0
        # ── 긴급도 0~5 : 발주 마감까지 남은 일수
        if d <= 0:
            urg = 0
        elif r["stock"] <= 0:
            urg = 5
        elif r["deadline"] is None:
            urg = 0
        elif r["deadline"] <= 0:
            urg = 4.5
        elif r["deadline"] <= 14:
            urg = 3.5
        elif r["deadline"] <= 45:
            urg = 2
        elif r["deadline"] <= 90:
            urg = 1
        else:
            urg = 0.5

        # ── 영향도 0~5 : 결품 시 파급
        im = impact.get(r["P_ID"], {})
        imp = 0.0
        imp += {"A": 2.0, "B": 1.0, "C": 0.5}.get(r["grade"], 0)   # 자재 등급
        imp += min((im.get("fg_cnt") or 0) * 0.8, 1.5)             # 투입 완제품 수
        if r["P_ID"] not in has_alt:
            imp += 1.0                                             # 대체품 없음
        if r["is_foreign"] == "Y":
            imp += 0.5                                             # 해외 조달
        if (r["lead_time"] or 0) >= 30:
            imp += 0.5                                             # 장納期
        imp = min(round(imp, 1), 5)

        score = round(urg * 0.6 + imp * 0.4, 2)
        lv = ("위험" if score >= 3.6 else
              "경고" if score >= 2.6 else
              "주의" if score >= 1.6 else "안전")

        r2 = dict(r)
        r2.pop("months", None)          # 레이더에선 쓰지 않아 응답에서 제외
        r2.update({
            "urg": urg, "imp": imp, "score": score, "lv": lv,
            "fg_cnt": im.get("fg_cnt") or 0,
            "has_alt": 1 if r["P_ID"] in has_alt else 0,
            # 결품 시 영향 금액 = 이 자재가 들어가는 완제품의 자재비 기준 근사
            "risk_amt": round((r["safe_qty"] or 0) * (r["P_Price"] or 0)),
        })
        out.append(r2)
    out.sort(key=lambda x: -x["score"])
    return out, base, span


def risk_summary(rows):
    C = lambda k: len([r for r in rows if r["lv"] == k])
    danger = [r for r in rows if r["lv"] in ("위험", "경고")]
    return {
        "total": len(rows),
        "danger": C("위험"), "warn": C("경고"),
        "caution": C("주의"), "safe": C("안전"),
        "danger_amt": sum(r["order_amt"] for r in danger),
        "danger_items": len([r for r in danger if r["order_qty"] > 0]),
        "no_alt": len([r for r in danger if not r["has_alt"]]),
        "foreign": len([r for r in danger if r["is_foreign"] == "Y"]),
        "grade_a": len([r for r in danger if r["grade"] == "A"]),
        "max_lt": max((r["lead_time"] or 0) for r in danger) if danger else 0,
    }


# ── 사용자 관리 화면 ─────────────────────────────────────────
# 주의: Birth/Phone 은 build_db.py 에서 마스킹 적재된 값이다.
POSITION_ORDER = ["부장", "차장", "과장", "대리", "주임", "사원"]


def user_list(conn, period=""):
    """사원 30명 + 처리 실적.

    period 를 주면 처리 실적(입출고·LOT 등록·작업지시)을 그 기간으로 집계한다.
    직급과 담당 구역은 현재 상태라 기간을 타지 않는다.
    """
    pf, pt = period_range(user_base(conn), period)
    tw = _between("T_Date", pf, pt)
    lw = _between("Lot_Date", pf, pt)
    dw = _between("Prod_Date", pf, pt)
    rows = _rows(conn, f"""
        SELECT u.EP_ID, u.Name, u.Birth, u.Phone, u.Position,
               COALESCE(l.n, 0)  AS lot_cnt,
               COALESCE(l.qty, 0) AS lot_qty,
               COALESCE(t.n, 0)  AS tx_cnt,
               COALESCE(t.in_n, 0)  AS in_cnt,
               COALESCE(t.out_n, 0) AS out_cnt,
               COALESCE(p.wo, 0) AS wo_cnt,
               COALESCE(p.n, 0)  AS prod_cnt,
               t.first_tx, t.last_tx
          FROM User_tb u
          LEFT JOIN (SELECT EP_ID, COUNT(*) n, SUM(P_Qty) qty
                       FROM Lot_tb WHERE {lw} GROUP BY EP_ID) l
                 ON u.EP_ID = l.EP_ID
          LEFT JOIN (SELECT EP_ID, COUNT(*) n,
                            SUM(CASE WHEN {TX_IN_SQL} THEN 1 ELSE 0 END) in_n,
                            SUM(CASE WHEN {TX_OUT_SQL} THEN 1 ELSE 0 END) out_n,
                            MIN(T_Date) first_tx, MAX(T_Date) last_tx
                       FROM Transaction_tb WHERE {tw} GROUP BY EP_ID) t
                 ON u.EP_ID = t.EP_ID
          LEFT JOIN (SELECT EP_ID, COUNT(DISTINCT Work_Order) wo, COUNT(*) n
                       FROM Production_tb WHERE {dw} GROUP BY EP_ID) p
                 ON u.EP_ID = p.EP_ID
    """)

    # 담당 창고 분포 (LOT 등록 기준)
    zones = {}
    for r in _rows(conn, """
        SELECT l.EP_ID, lo.Loc_N AS loc, COUNT(*) AS n
          FROM Lot_tb l LEFT JOIN Location_tb lo ON l.Loc_ID = lo.Loc_ID
         GROUP BY l.EP_ID, lo.Loc_N ORDER BY n DESC
    """):
        zones.setdefault(r["EP_ID"], []).append(r)

    # 최근 처리 이력
    recent = {}
    for r in _rows(conn, f"""
        SELECT t.EP_ID, t.T_ID, t.T_Type, t.T_Date, t.T_Num, p.P_N
          FROM Transaction_tb t
          JOIN Lot_tb l     ON t.Lot_ID = l.Lot_ID
          JOIN Product_tb p ON l.P_ID = p.P_ID
         ORDER BY t.T_Date DESC
    """):
        lst = recent.setdefault(r["EP_ID"], [])
        if len(lst) < 5:
            lst.append(r)

    for r in rows:
        r["zones"] = zones.get(r["EP_ID"], [])[:5]
        r["recent"] = recent.get(r["EP_ID"], [])
        r["total"] = (r["lot_cnt"] or 0) + (r["tx_cnt"] or 0) + (r["wo_cnt"] or 0)
        r["pos_rank"] = (POSITION_ORDER.index(r["Position"])
                         if r["Position"] in POSITION_ORDER else 99)
    rows.sort(key=lambda r: -r["total"])
    return rows


def user_base(conn):
    """사용자 화면의 기준일 — 거래·LOT·생산 중 가장 마지막 날.

    테이블마다 마지막 날짜가 다르므로(거래 2026-02-07 / 생산 2025-12-31)
    기준일을 하나로 못 박지 않으면 같은 '최근 6개월'이 지표마다 다른 구간을 가리킨다.
    """
    return max(x for x in (
        conn.execute("SELECT MAX(T_Date) FROM Transaction_tb").fetchone()[0],
        conn.execute("SELECT MAX(Lot_Date) FROM Lot_tb").fetchone()[0],
        conn.execute("SELECT MAX(Prod_Date) FROM Production_tb").fetchone()[0],
    ) if x)


def _wo_total(conn, period="", base=None):
    """실제 작업지시 건수. 기간을 주면 그 기간만 센다.

    base 는 반드시 화면과 같은 기준일을 받는다. 테이블마다 마지막 날짜가 달라
    (거래 2026-02-07 / 생산 2025-12-31) 각자 기준일을 쓰면 같은 '최근 6개월'이
    서로 다른 구간을 가리키게 된다.
    """
    if base is None:
        base = conn.execute("SELECT MAX(Prod_Date) FROM Production_tb").fetchone()[0]
    pf, pt = period_range(base, period)
    return conn.execute(
        f"SELECT COUNT(DISTINCT Work_Order) FROM Production_tb"
        f" WHERE {_between('Prod_Date', pf, pt)}").fetchone()[0]


def user_summary(rows, conn=None, period="", base=None):
    pos = {}
    for p in POSITION_ORDER:
        n = len([r for r in rows if r["Position"] == p])
        if n:
            pos[p] = n
    return {
        "total": len(rows),
        "positions": pos,
        "tx_total": sum(r["tx_cnt"] for r in rows),
        "lot_total": sum(r["lot_cnt"] for r in rows),
        # 주의: 사용자별 wo_cnt 를 그냥 더하면 한 작업지시에 여러 명이 참여한 만큼
        #       중복 합산된다(3,874 vs 실제 488). 실제 건수를 따로 센다.
        "wo_total": _wo_total(conn, period, base) if conn else sum(r["wo_cnt"] for r in rows),
        "active": len([r for r in rows if r["total"] > 0]),
        "max_total": max((r["total"] for r in rows), default=1),
        "avg_total": round(sum(r["total"] for r in rows) / len(rows), 1) if rows else 0,
    }


# ── 월간 리포트 화면 ─────────────────────────────────────────
def monthly_report(conn):
    """월별 입출고·발주·생산·재고 종합 집계. 월 선택형 리포트."""
    months = [r["ym"] for r in _rows(conn, """
        SELECT DISTINCT substr(T_Date,1,7) AS ym FROM Transaction_tb ORDER BY ym
    """)]

    def by_month(sql):
        return {r["ym"]: r for r in _rows(conn, sql)}

    tx = by_month(f"""
        SELECT substr(t.T_Date,1,7) AS ym,
               SUM(CASE WHEN {TX_IN_T} THEN 1 ELSE 0 END) AS in_cnt,
               SUM(CASE WHEN {TX_OUT_T} THEN 1 ELSE 0 END) AS out_cnt,
               SUM(CASE WHEN {TX_IN_T} THEN t.T_Num ELSE 0 END) AS in_qty,
               SUM(CASE WHEN {TX_OUT_T} THEN t.T_Num ELSE 0 END) AS out_qty,
               SUM(CASE WHEN {TX_IN_T} THEN t.T_Num*p.P_Price ELSE 0 END) AS in_amt,
               SUM(CASE WHEN {TX_OUT_T} THEN t.T_Num*p.P_Price ELSE 0 END) AS out_amt,
               COUNT(DISTINCT l.P_ID) AS items,
               COUNT(DISTINCT t.EP_ID) AS workers
          FROM Transaction_tb t
          JOIN Lot_tb l ON t.Lot_ID = l.Lot_ID
          JOIN Product_tb p ON l.P_ID = p.P_ID
         GROUP BY ym
    """)
    po = by_month("""
        SELECT substr(h.P_Date,1,7) AS ym,
               COUNT(DISTINCT h.H_ID) AS cnt,
               COUNT(*) AS lines,
               SUM(d.P_Qty * p.P_Price) AS amt,
               COUNT(DISTINCT h.BRN) AS suppliers
          FROM Purchase_Header_tb h
          JOIN Purchase_Detail_tb d ON h.H_ID = d.H_ID
          JOIN Product_tb p ON d.P_ID = p.P_ID
         GROUP BY ym
    """)
    prod = by_month("""
        SELECT substr(r.Prod_Date,1,7) AS ym,
               COUNT(DISTINCT r.Work_Order) AS wo,
               COUNT(DISTINCT r.FG_ID) AS fg,
               SUM(r.Prod_Qty * p.P_Price) AS amt
          FROM Production_tb r JOIN Product_tb p ON r.P_ID = p.P_ID
         GROUP BY ym
    """)
    lot = by_month("""
        SELECT substr(Lot_Date,1,7) AS ym, COUNT(*) AS cnt FROM Lot_tb GROUP BY ym
    """)

    out = []
    for ym in months:
        t, o, r, l = tx.get(ym, {}), po.get(ym, {}), prod.get(ym, {}), lot.get(ym, {})
        out.append({
            "ym": ym,
            "in_cnt": t.get("in_cnt", 0), "out_cnt": t.get("out_cnt", 0),
            "in_qty": t.get("in_qty", 0), "out_qty": t.get("out_qty", 0),
            "in_amt": t.get("in_amt", 0) or 0, "out_amt": t.get("out_amt", 0) or 0,
            "items": t.get("items", 0), "workers": t.get("workers", 0),
            "po_cnt": o.get("cnt", 0), "po_lines": o.get("lines", 0),
            "po_amt": o.get("amt", 0) or 0, "po_suppliers": o.get("suppliers", 0),
            "wo_cnt": r.get("wo", 0), "fg_cnt": r.get("fg", 0),
            "prod_amt": r.get("amt", 0) or 0,
            "lot_cnt": l.get("cnt", 0),
        })

    # 월별 상위 품목 (불출 금액 기준)
    top = {}
    for r in _rows(conn, f"""
        SELECT substr(t.T_Date,1,7) AS ym, l.P_ID, p.P_N, s.Sf_Lv AS grade,
               SUM(t.T_Num) AS qty, SUM(t.T_Num * p.P_Price) AS amt
          FROM Transaction_tb t
          JOIN Lot_tb l ON t.Lot_ID = l.Lot_ID
          JOIN Product_tb p ON l.P_ID = p.P_ID
          LEFT JOIN Safe_tb s ON p.P_ID = s.P_ID
         WHERE {DEMAND_T}
         GROUP BY ym, l.P_ID, p.P_N, s.Sf_Lv
         ORDER BY ym, amt DESC
    """):
        lst = top.setdefault(r["ym"], [])
        if len(lst) < 6:
            lst.append(r)

    # 월별 협력사 상위 (발주 금액)
    sup = {}
    for r in _rows(conn, """
        SELECT substr(h.P_Date,1,7) AS ym, c.CP_N AS supplier, c.Is_Foreign AS is_foreign,
               COUNT(DISTINCT h.H_ID) AS cnt, SUM(d.P_Qty * p.P_Price) AS amt
          FROM Purchase_Header_tb h
          JOIN Purchase_Detail_tb d ON h.H_ID = d.H_ID
          JOIN Product_tb p ON d.P_ID = p.P_ID
          LEFT JOIN Company_tb c ON h.BRN = c.BRN
         GROUP BY ym, c.CP_N, c.Is_Foreign
         ORDER BY ym, amt DESC
    """):
        lst = sup.setdefault(r["ym"], [])
        if len(lst) < 5:
            lst.append(r)

    for m in out:
        m["top_items"] = top.get(m["ym"], [])
        m["top_suppliers"] = sup.get(m["ym"], [])
    return out


def report_summary(months):
    return {
        "months": len(months),
        "date_from": months[0]["ym"] if months else "-",
        "date_to": months[-1]["ym"] if months else "-",
        "in_amt": sum(m["in_amt"] for m in months),
        "out_amt": sum(m["out_amt"] for m in months),
        "po_amt": sum(m["po_amt"] for m in months),
        "po_cnt": sum(m["po_cnt"] for m in months),
        "wo_cnt": sum(m["wo_cnt"] for m in months),
        "max_in": max((m["in_qty"] for m in months), default=1),
        "max_out": max((m["out_qty"] for m in months), default=1),
        "max_amt": max((max(m["in_amt"], m["out_amt"], m["po_amt"]) for m in months), default=1),
    }


# ── 안전재고 일괄 갱신 마법사 ────────────────────────────────
# SAFE_STOCK_DESIGN.md 의 갱신 절차를 화면에서 단계별로 재현한다.
#   1) Update_Log_tb.Next_Date 로 재검토 대상 선정
#   2) 불출 이력 파레토 분석으로 Usage_Score 산출
#   3) 5개 항목 합산으로 등급 재판정 (컷오프 13/8/7)
#   4) 공식으로 Sf_Num 재계산
# 실제로 DB 를 쓰지는 않는다 — 조회 전용이므로 "적용하면 이렇게 된다"를 보여준다.
def wizard_data(conn):
    base = conn.execute("SELECT MAX(T_Date) FROM Transaction_tb").fetchone()[0]
    span = operating_days(conn)
    abc = {r["P_ID"]: r for r in abc_analysis(conn)}
    safe = safety_stock_list(conn)
    # ⚠️ 수동 조정이 걸린 품목은 일괄 갱신이 등급을 되돌리면 안 된다.
    #    조정을 무시하면 자동 갱신이 사람 판단을 조용히 지운다.
    ovr = active_overrides(conn, base)

    due, rows = 0, []
    for r in safe:
        a = abc.get(r["P_ID"], {})
        is_due = bool(r["next_date"] and r["next_date"] <= base)
        if is_due:
            due += 1

        old_g = r["grade"]
        new_g = a.get("new_grade") or old_g
        old_u = r["Usage_Score"]
        new_u = a.get("calc_usage_score")

        L = r["lead_time_real"] or r["lead_time"] or 0
        d = r["daily_use"] or 0

        # 조정이 걸려 있으면 등급은 조정값으로 고정하고 수량만 다시 계산한다.
        # 사용량·리드타임이 변한 건 따라가되, 사람이 정한 등급은 건드리지 않는다.
        o = ovr.get(r["P_ID"])
        if o is not None and o["Ovr_Lv"]:
            new_g = o["Ovr_Lv"]
        new_ss = safe_formula(new_g, L, d)
        if new_ss is None:
            new_ss = r["safe_qty"]
        if o is not None and o["Min_Qty"]:
            new_ss = max(new_ss, o["Min_Qty"])       # 최소 보유량 하한

        rows.append({
            "P_ID": r["P_ID"], "P_N": r["P_N"], "Spec": r["Spec"],
            "supplier": r["supplier"],
            "due": 1 if is_due else 0,
            "updated_date": r["updated_date"], "next_date": r["next_date"],
            "cycle": REVIEW_CYCLE_DAYS.get(old_g, 90),
            "old_grade": old_g, "new_grade": new_g,
            "grade_changed": 1 if new_g != old_g else 0,
            "old_usage": old_u, "new_usage": new_u,
            "usage_filled": 1 if old_u is None else 0,
            "usage_changed": 1 if (old_u is not None and new_u != old_u) else 0,
            "score4": sum(r[k2] or 0 for k2 in
                          ("Price_Score", "Sub_Score", "Impact_Score", "Supply_Score")),
            "new_sum": a.get("new_score_sum"),
            "amount": a.get("amount", 0) or 0,
            "cum_share": a.get("cum_share"),
            "rank": a.get("rank"),
            "old_ss": r["safe_qty"], "new_ss": new_ss,
            "ss_diff": (new_ss or 0) - (r["safe_qty"] or 0),
            "stock": r["stock"], "daily": d, "lead_time": L,
            "new_cycle": REVIEW_CYCLE_DAYS.get(new_g, 90),
            # 조정이 걸린 품목 — 화면이 '등급 고정' 으로 표시한다
            "ovr_id": o["Ovr_ID"] if o else None,
            "ovr_lv": o["Ovr_Lv"] if o else None,
            "ovr_min": o["Min_Qty"] if o else None,
            "ovr_reason": o["Reason_Cd"] if o else None,
            "ovr_end": o["End_Date"] if o else None,
            "locked": 1 if (o and o["Ovr_Lv"]) else 0,
        })

    rows.sort(key=lambda x: (-x["grade_changed"], -abs(x["ss_diff"])))
    return rows, base, span, due


def wizard_summary(rows, base, due):
    gc = [r for r in rows if r["grade_changed"]]
    up = [r for r in gc if ["C", "B", "A"].index(r["new_grade"]) > ["C", "B", "A"].index(r["old_grade"])]
    ss = [r for r in rows if r["ss_diff"]]
    return {
        "base_date": base,
        "total": len(rows),
        "due": due,
        "usage_filled": len([r for r in rows if r["usage_filled"]]),
        "usage_changed": len([r for r in rows if r["usage_changed"]]),
        "grade_changed": len(gc),
        "grade_up": len(up),
        "grade_down": len(gc) - len(up),
        "ss_changed": len(ss),
        "ss_up": len([r for r in ss if r["ss_diff"] > 0]),
        "ss_down": len([r for r in ss if r["ss_diff"] < 0]),
        "ss_total_diff": sum(r["ss_diff"] for r in rows),
        "grade_dist_old": {g: len([r for r in rows if r["old_grade"] == g]) for g in "ABC"},
        "grade_dist_new": {g: len([r for r in rows if r["new_grade"] == g]) for g in "ABC"},
        "locked": len([r for r in rows if r.get("locked")]),
        "ovr_min": len([r for r in rows if r.get("ovr_min")]),
    }


# ── 안전재고 일괄 갱신 (여섯 번째 쓰기 기능) ────────────────
#
# wizard_data() 가 "적용하면 이렇게 된다" 를 계산해 왔다. 여기서 실제로 적용한다.
#
#   Safe_tb        Sf_Lv (재판정 등급) · Sf_Num (재계산 안전재고) · Usage_Score (파레토)
#   Update_Log_tb  실행일 · 다음 예정일 · 변경 전/후 값 · 실행자
#
# ⚠️ 200행을 한 번에 덮어쓰는 유일한 기능이다. 그래서 두 가지를 지킨다.
#   ① 주기가 도래한 품목만 건드린다. 재검토 주기 설계(A30/B90/C180)가 살아 있어야 한다
#   ② 변경 전 값을 Update_Log_tb 에 남긴다 — 남기지 않으면 되돌릴 방법이 없다
#
# 사람이 매기는 4개 점수(단가·대체·영향·공급)는 건드리지 않는다.
# 자동 산정 대상은 Usage_Score 하나뿐이다 (SAFE_STOCK_DESIGN 참조).


def safety_targets(conn, date=None):
    """갱신 대상과 제외 사유. 화면과 API 가 같은 판정을 쓴다.

    만료된 수동 조정을 먼저 정리한다 — 만료분이 남아 있으면 갱신이 그 등급을
    계속 고정해 조정이 영구 관행이 된다.
    """
    expire_overrides(conn)
    rows, base, span, _due = wizard_data(conn)
    date = date or _add_days(base, 1)
    done = {r["P_ID"] for r in _rows(
        conn, "SELECT P_ID FROM Update_Log_tb WHERE Updated_Date = ?", (date,))}

    out = []
    for r in rows:
        r = dict(r)
        if r["P_ID"] in done:
            r["skip"] = "오늘 이미 갱신됨"        # 같은 날 두 번 돌리면 이력이 덮인다
        elif not r["due"]:
            r["skip"] = "재검토 주기 미도래"
        elif r["new_ss"] is None:
            r["skip"] = "재계산 불가 (불출 이력 또는 리드타임 없음)"
        else:
            r["skip"] = None
        r["changed"] = bool(r["grade_changed"] or r["ss_diff"]
                            or r["usage_filled"] or r["usage_changed"])
        r["next_date_new"] = _add_days(date, REVIEW_CYCLE_DAYS.get(r["new_grade"], 90))
        out.append(r)
    return out, base, date


def preview_safety_update(conn, date, pids=None, ep_id=None, note=None):
    """갱신 미리보기 — 대상 판정과 변경 건수. apply 가 저장 직전에 다시 부른다.

    pids 를 주면 그중 대상인 것만 처리한다(화면에서 일부를 제외할 수 있다).
    주지 않으면 대상 전체다.
    """
    targets, base, date = safety_targets(conn, date)
    by_id = {r["P_ID"]: r for r in targets}

    if pids is not None:
        pids = [str(x or "").strip() for x in pids]
        if not pids:
            return None, ["갱신할 품목을 하나 이상 고르세요."]
        unknown = [x for x in pids if x not in by_id]
        if unknown:
            return None, ["등록되지 않은 품번입니다: %s" % ", ".join(unknown[:5])]
        picked = [by_id[x] for x in dict.fromkeys(pids)]
        blocked = [r for r in picked if r["skip"]]
        if blocked:
            return None, ["갱신 대상이 아닌 품목이 있습니다: %s"
                          % ", ".join("%s(%s)" % (r["P_ID"], r["skip"]) for r in blocked[:5])]
        items = picked
    else:
        items = [r for r in targets if not r["skip"]]

    if not items:
        return None, ["갱신할 대상이 없습니다. 재검토 주기가 도래한 품목이 없거나 이미 갱신했습니다."]

    ep_id = str(ep_id or "").strip()
    if not ep_id:
        return None, ["실행 담당자를 선택하세요."]
    row = conn.execute(
        "SELECT EP_ID, Name, Position FROM User_tb WHERE EP_ID = ?", (ep_id,)).fetchone()
    if row is None:
        return None, ["등록되지 않은 담당자입니다: %s" % ep_id]
    w = {"EP_ID": row["EP_ID"], "Name": row["Name"], "Position": row["Position"]}

    gc = [r for r in items if r["grade_changed"]]
    up = [r for r in gc if "CBA".index(r["new_grade"]) > "CBA".index(r["old_grade"])]
    ss = [r for r in items if r["ss_diff"]]
    return {
        "date": date, "base": base,
        "worker": w, "note": (note or "").strip() or None,
        "items": items, "cnt": len(items),
        "skipped": len(targets) - len(items),
        "changed": len([r for r in items if r["changed"]]),
        "grade_changed": len(gc), "grade_up": len(up), "grade_down": len(gc) - len(up),
        "usage_filled": len([r for r in items if r["usage_filled"]]),
        "usage_changed": len([r for r in items if r["usage_changed"]]),
        "ss_changed": len(ss),
        "ss_up": len([r for r in ss if r["ss_diff"] > 0]),
        "ss_down": len([r for r in ss if r["ss_diff"] < 0]),
        "ss_total_diff": sum(r["ss_diff"] for r in items),
        "grade_dist_old": {g: len([r for r in items if r["old_grade"] == g]) for g in "ABC"},
        "grade_dist_new": {g: len([r for r in items if r["new_grade"] == g]) for g in "ABC"},
        "locked": len([r for r in items if r.get("locked")]),
        "ovr_min": len([r for r in items if r.get("ovr_min")]),
    }, []


def apply_safety_update(conn, date, pids=None, ep_id=None, note=None):
    """안전재고 일괄 갱신 실행. Safe_tb 갱신 + Update_Log_tb 이력."""
    plan, errors = preview_safety_update(conn, date, pids, ep_id, note)
    if errors:
        return None, errors
    date = plan["date"]
    with conn:                                   # 전부 성공 아니면 전부 실패
        for r in plan["items"]:
            conn.execute(
                "UPDATE Safe_tb SET Sf_Lv = ?, Sf_Num = ?, Usage_Score = ? WHERE P_ID = ?",
                (r["new_grade"], r["new_ss"], r["new_usage"], r["P_ID"]))
            conn.execute(
                "INSERT INTO Update_Log_tb"
                " (P_ID, Updated_Date, Next_Date, Old_Lv, New_Lv, Old_Num, New_Num,"
                "  Old_Usage, New_Usage, EP_ID, Note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (r["P_ID"], date, r["next_date_new"], r["old_grade"], r["new_grade"],
                 r["old_ss"], r["new_ss"], r["old_usage"], r["new_usage"],
                 plan["worker"]["EP_ID"], plan["note"]))
    return plan, []


def safety_runs(conn):
    """화면에서 실행한 갱신 이력. 되돌리기 대상 목록이기도 하다.

    원본 200행은 EP_ID 가 비어 있어 여기 잡히지 않는다 — 되돌릴 근거(변경 전 값)가 없다.
    """
    return _rows(conn, """
        SELECT l.Updated_Date AS run_date, l.EP_ID, u.Name AS worker, u.Position AS pos,
               MAX(l.Note) AS note,
               COUNT(*) AS cnt,
               SUM(CASE WHEN l.Old_Lv <> l.New_Lv THEN 1 ELSE 0 END) AS grade_changed,
               SUM(CASE WHEN COALESCE(l.Old_Num,0) <> COALESCE(l.New_Num,0)
                        THEN 1 ELSE 0 END) AS ss_changed,
               SUM(COALESCE(l.New_Num,0) - COALESCE(l.Old_Num,0)) AS ss_total_diff,
               SUM(CASE WHEN l.Old_Usage IS NULL AND l.New_Usage IS NOT NULL
                        THEN 1 ELSE 0 END) AS usage_filled
          FROM Update_Log_tb l
          LEFT JOIN User_tb u ON l.EP_ID = u.EP_ID
         WHERE l.EP_ID IS NOT NULL
         GROUP BY l.Updated_Date, l.EP_ID
         ORDER BY l.Updated_Date DESC
    """)


def revert_safety_update(conn, run_date, ep_id=None):
    """갱신 되돌리기 — 그 실행분의 '변경 전' 값을 Safe_tb 에 되돌리고 이력을 지운다.

    200행을 한 번에 덮는 기능이라 되돌릴 길을 함께 둔다.
    ⚠️ 그 뒤에 더 최근 갱신이 있으면 되돌리지 않는다. 중간 이력을 빼면
       Safe_tb 가 어느 실행 결과인지 설명할 수 없게 된다.
    """
    run_date = str(run_date or "").strip()
    rows = _rows(conn, "SELECT * FROM Update_Log_tb"
                       " WHERE Updated_Date = ? AND EP_ID IS NOT NULL", (run_date,))
    if not rows:
        return None, ["되돌릴 갱신 이력이 없습니다: %s" % (run_date or "(빈값)")]

    later = conn.execute(
        "SELECT MIN(Updated_Date) FROM Update_Log_tb"
        " WHERE EP_ID IS NOT NULL AND Updated_Date > ?", (run_date,)).fetchone()[0]
    if later:
        return None, ["이후에 %s 갱신이 있어 되돌릴 수 없습니다. 최근 것부터 되돌리세요." % later]

    if ep_id is not None and str(ep_id).strip():
        # 199행을 되돌리는 일이라 아무나 못 하게 한다. 불출 승인과 같은 직급 기준을 쓴다.
        w, errors = _approver(conn, ep_id, what="안전재고 되돌리기")
        if errors:
            return None, errors
    else:
        return None, ["되돌리기 담당자를 선택하세요."]

    with conn:
        for r in rows:
            conn.execute(
                "UPDATE Safe_tb SET Sf_Lv = ?, Sf_Num = ?, Usage_Score = ? WHERE P_ID = ?",
                (r["Old_Lv"], r["Old_Num"], r["Old_Usage"], r["P_ID"]))
        conn.execute("DELETE FROM Update_Log_tb WHERE Updated_Date = ? AND EP_ID IS NOT NULL",
                     (run_date,))
    return {"run_date": run_date, "cnt": len(rows), "worker": w}, []


# ── 안전재고 수동 조정 ───────────────────────────────────────
#
# 점수제가 B 를 주지만 실무에서는 A 로 다뤄야 하는 자재가 있다.
# 단종 예정, 고객사가 품번을 지정한 것, 품질 이슈 이력이 있는 것 같은 사정은
# 5개 항목 어디에도 안 들어간다.
#
# ⚠️ "주관이 들어가서 문제" 가 아니다. 주관은 이미 들어와 있다.
#    5개 점수 중 자동은 Usage_Score 하나뿐이고 나머지 넷은 사람이 매긴다.
#    규칙이 분명한 Price_Score(단가 10만원) 조차 200종 중 13종이 규칙을 벗어나 있다
#    (53,800~99,200원인데 3점). 누가 왜 그랬는지는 DB 어디에도 없다.
#
#    막는다고 사라지지 않는다. 담당자가 발주할 때 더 넣는 식으로 시스템 밖에 남고,
#    그 사람이 퇴사하면 근거가 증발한다. 그래서 막는 대신 기록한다.
#
# 네 가지를 강제한다
#   ① 계산값을 동결한다   Calc_Lv/Calc_Num. "계산은 B, 운영은 A" 를 항상 말할 수 있어야 한다
#   ② 사유를 분류로 받는다 자유 텍스트만 받으면 '중요해서' 가 쌓인다
#   ③ 만료된다           만료 없는 예외는 아무도 이유를 모르는 관행이 된다.
#                        만료일 = 조정일 + 조정 등급의 재검토 주기 (A30/B90/C180)
#   ④ 권한이 필요하다     불출 승인과 같은 직급 기준을 쓴다
#
# Safe_tb 는 '운영값' 을 담는다 — 창고가 실제로 쓰는 숫자여야 하기 때문이다.
# 기존 24개 화면은 Safe_tb 만 읽으므로 손댈 필요가 없고,
# 왜 계산과 다른지는 Safe_Override_tb 가 설명한다.

# 사유 분류. 점수제가 못 담는 사정만 모았다.
OVERRIDE_REASONS = (
    ("단종예정",   "단종·EOL 예정이라 남은 물량을 확보해야 한다"),
    ("고객사지정", "고객사가 품번을 지정해 대체가 불가능하다"),
    ("품질이슈",   "불량·클레임 이력이 있어 여유분이 필요하다"),
    ("신규양산",   "신규 양산 초기라 사용량 실적을 믿기 어렵다"),
    ("공급불안",   "협력사 사정으로 납기가 불안정하다"),
    ("라인전용",   "특정 라인 전용이라 결품 시 대체 투입이 안 된다"),
    ("기타",       "위에 없는 사유 — 설명에 자세히 적는다"),
)
_REASON_CODES = tuple(c for c, _ in OVERRIDE_REASONS)
OVERRIDE_STATUS = ("적용", "만료", "해제")


def next_ovr_id(conn, date):
    """Ovr_ID 채번 — OVR + YYYYMMDD + 4자리."""
    pre = "OVR" + date.replace("-", "")
    last = conn.execute(
        "SELECT MAX(Ovr_ID) FROM Safe_Override_tb WHERE Ovr_ID LIKE ?", (pre + "%",)).fetchone()[0]
    return "%s%04d" % (pre, int(last[-4:]) + 1 if last else 1)


def safe_formula(grade, lead, daily):
    """등급 · 리드타임 · 일평균 사용량으로 안전재고를 계산한다.

    SAFE_STOCK_DESIGN 의 공식. wizard 와 수동 조정이 같은 식을 쓰도록 떼어냈다.
        SS = Z x sqrt(L x sigma_d^2 + d^2 x sigma_L^2)
    """
    L, d = (lead or 0), (daily or 0)
    if not L or not d:
        return None
    Z = Z_BY_GRADE.get(grade, 1.65)
    k = {"A": 0.20, "B": 0.25, "C": 0.30}.get(grade, 0.25)
    return round(Z * ((L * (d * k) ** 2 + d * d * (L * k) ** 2) ** 0.5))


def active_overrides(conn, date=None):
    """유효한 조정을 품번별로 돌려준다. 만료일이 지난 것은 빼고 본다."""
    date = date or conn.execute("SELECT MAX(T_Date) FROM Transaction_tb").fetchone()[0]
    return {r["P_ID"]: r for r in _rows(conn, """
        SELECT o.*, u.Name AS worker, u.Position AS pos
          FROM Safe_Override_tb o LEFT JOIN User_tb u ON o.EP_ID = u.EP_ID
         WHERE o.Status = '적용' AND o.End_Date >= ?
         ORDER BY o.P_ID, o.Ovr_ID
    """, (date,))}


def expire_overrides(conn, date=None):
    """만료일이 지난 조정을 정리하고 그 품목을 계산값으로 되돌린다.

    일괄 갱신 직전과 조회 화면에서 부른다. 만료를 방치하면 조정이 영구 관행이 된다.
    """
    date = date or conn.execute("SELECT MAX(T_Date) FROM Transaction_tb").fetchone()[0]
    stale = _rows(conn, "SELECT * FROM Safe_Override_tb"
                        " WHERE Status = '적용' AND End_Date < ?", (date,))
    if not stale:
        return []
    safe = {r["P_ID"]: r for r in safety_stock_list(conn)}
    with conn:
        for o in stale:
            r = safe.get(o["P_ID"])
            if r:
                # 만료 시점의 계산값으로 되돌린다 (동결값이 아니라 지금 계산값이다 —
                # 그 사이 사용량이 변했으면 현재 실적을 따르는 게 맞다)
                lv = r["grade"] if o["Ovr_Lv"] is None else o["Calc_Lv"]
                num = safe_formula(lv, r["lead_time_real"] or r["lead_time"], r["daily_use"])
                conn.execute("UPDATE Safe_tb SET Sf_Lv = ?, Sf_Num = ? WHERE P_ID = ?",
                             (lv, num if num is not None else r["safe_qty"], o["P_ID"]))
            conn.execute("UPDATE Safe_Override_tb SET Status = '만료', Off_Date = ?,"
                         " Off_Note = '만료일 경과로 자동 해제' WHERE Ovr_ID = ?",
                         (date, o["Ovr_ID"]))
    return [dict(o) for o in stale]


def preview_override(conn, date, pid, ep_id, lv=None, min_qty=None,
                     reason_cd=None, reason=None):
    """조정 미리보기 — 계산값과 운영값의 차이를 보여준다. create 가 다시 부른다."""
    pid = str(pid or "").strip()
    row = next((r for r in safety_stock_list(conn) if r["P_ID"] == pid), None)
    if row is None:
        return None, ["등록되지 않은 품번입니다: %s" % (pid or "(빈값)")]

    cur = active_overrides(conn, date).get(pid)
    if cur:
        return None, ["이미 조정이 걸려 있습니다: %s (%s 까지). 해제 후 다시 등록하세요."
                      % (cur["Ovr_ID"], cur["End_Date"])]

    lv = (str(lv).strip().upper() if lv else None) or None
    if lv is not None and lv not in ("A", "B", "C"):
        return None, ["등급은 A · B · C 중 하나여야 합니다: %s" % lv]

    if min_qty in ("", None):
        min_qty = None
    else:
        try:
            min_qty = int(min_qty)
        except (TypeError, ValueError):
            return None, ["최소 보유량이 숫자가 아닙니다."]
        if min_qty < 0:
            return None, ["최소 보유량은 음수일 수 없습니다."]
        if min_qty == 0:
            min_qty = None

    if lv is None and min_qty is None:
        return None, ["조정할 내용이 없습니다. 등급이나 최소 보유량 중 하나는 지정하세요."]

    calc_lv = row["grade"]
    calc_num = row["safe_qty"]
    if lv is not None and lv == calc_lv and min_qty is None:
        return None, ["계산 등급과 같습니다(%s). 조정할 이유가 없습니다." % calc_lv]

    reason_cd = str(reason_cd or "").strip()
    if reason_cd not in _REASON_CODES:
        return None, ["사유 분류를 고르세요: %s" % " / ".join(_REASON_CODES)]
    reason = (reason or "").strip()
    if len(reason) < 5:
        return None, ["사유를 5자 이상 적으세요. 나중에 이 조정을 설명할 근거가 됩니다."]

    ep, errors = _approver(conn, ep_id, what="안전재고 조정")
    if errors:
        return None, errors

    eff_lv = lv or calc_lv
    lead = row["lead_time_real"] or row["lead_time"]
    by_formula = safe_formula(eff_lv, lead, row["daily_use"])
    if by_formula is None:
        by_formula = calc_num or 0
    eff_num = max(by_formula, min_qty or 0)

    # 만료일 = 조정일 + 조정 등급의 재검토 주기. A 로 올리면 30일마다 다시 본다.
    end = _add_days(date, REVIEW_CYCLE_DAYS.get(eff_lv, 90))
    return {
        "P_ID": pid, "P_N": row["P_N"], "Spec": row["Spec"], "supplier": row["supplier"],
        "calc_lv": calc_lv, "calc_num": calc_num,
        "ovr_lv": lv, "min_qty": min_qty,
        "eff_lv": eff_lv, "by_formula": by_formula, "eff_num": eff_num,
        "num_diff": eff_num - (calc_num or 0),
        "lv_changed": eff_lv != calc_lv,
        "min_binds": bool(min_qty and min_qty > by_formula),   # 하한이 공식을 이기는가
        "stock": row["stock"], "daily": row["daily_use"], "lead": lead,
        "price": row["P_Price"],
        "amount_diff": int(round((eff_num - (calc_num or 0)) * (row["P_Price"] or 0))),
        "reason_cd": reason_cd, "reason": reason,
        "worker": ep, "start": date, "end": end,
        "cycle": REVIEW_CYCLE_DAYS.get(eff_lv, 90),
        "short_after": (row["stock"] or 0) < eff_num,
    }, []


def create_override(conn, date, pid, ep_id, lv=None, min_qty=None,
                    reason_cd=None, reason=None):
    """안전재고 수동 조정 등록. Safe_tb(운영값) + Safe_Override_tb(근거)."""
    plan, errors = preview_override(conn, date, pid, ep_id, lv, min_qty, reason_cd, reason)
    if errors:
        return None, errors
    with conn:
        oid = next_ovr_id(conn, date)
        conn.execute(
            "INSERT INTO Safe_Override_tb"
            " (Ovr_ID, P_ID, Ovr_Lv, Min_Qty, Calc_Lv, Calc_Num, Reason_Cd, Reason,"
            "  Start_Date, End_Date, Status, EP_ID)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (oid, pid, plan["ovr_lv"], plan["min_qty"], plan["calc_lv"], plan["calc_num"],
             plan["reason_cd"], plan["reason"], date, plan["end"], "적용",
             plan["worker"]["EP_ID"]))
        conn.execute("UPDATE Safe_tb SET Sf_Lv = ?, Sf_Num = ? WHERE P_ID = ?",
                     (plan["eff_lv"], plan["eff_num"], pid))
        plan["Ovr_ID"] = oid
    return plan, []


def release_override(conn, date, oid, ep_id, note=None):
    """조정 해제 — 계산값으로 되돌린다."""
    oid = str(oid or "").strip()
    o = conn.execute("SELECT * FROM Safe_Override_tb WHERE Ovr_ID = ?", (oid,)).fetchone()
    if o is None:
        return None, ["등록되지 않은 조정번호입니다: %s" % (oid or "(빈값)")]
    if o["Status"] != "적용":
        return None, ["이미 %s된 조정입니다: %s" % (o["Status"], oid)]

    ep, errors = _approver(conn, ep_id, what="안전재고 조정")
    if errors:
        return None, errors

    row = next((r for r in safety_stock_list(conn) if r["P_ID"] == o["P_ID"]), None)
    lv = o["Calc_Lv"]
    num = safe_formula(lv, (row["lead_time_real"] or row["lead_time"]) if row else None,
                       row["daily_use"] if row else None)
    if num is None:
        num = o["Calc_Num"]
    with conn:
        conn.execute("UPDATE Safe_tb SET Sf_Lv = ?, Sf_Num = ? WHERE P_ID = ?",
                     (lv, num, o["P_ID"]))
        conn.execute("UPDATE Safe_Override_tb SET Status = '해제', Off_Date = ?, Off_Note = ?"
                     " WHERE Ovr_ID = ?", (date, (note or "").strip() or None, oid))
    return {"Ovr_ID": oid, "P_ID": o["P_ID"], "Status": "해제",
            "back_lv": lv, "back_num": num, "worker": ep}, []


def override_list(conn, date=None):
    """조정 현황 — 적용 중인 것과 지난 것. 만료 임박을 함께 센다."""
    date = date or conn.execute("SELECT MAX(T_Date) FROM Transaction_tb").fetchone()[0]
    rows = _rows(conn, """
        SELECT o.*, p.P_N, p.Spec, p.P_Price, c.CP_N AS supplier,
               s.Sf_Lv AS now_lv, s.Sf_Num AS now_num,
               u.Name AS worker, u.Position AS pos,
               CAST(julianday(o.End_Date) - julianday(?) AS INT) AS days_left
          FROM Safe_Override_tb o
          JOIN Product_tb p    ON o.P_ID = p.P_ID
          LEFT JOIN Company_tb c ON p.BRN = c.BRN
          LEFT JOIN Safe_tb s  ON o.P_ID = s.P_ID
          LEFT JOIN User_tb u  ON o.EP_ID = u.EP_ID
         ORDER BY CASE o.Status WHEN '적용' THEN 0 ELSE 1 END, o.End_Date, o.Ovr_ID
    """, (date,))
    for r in rows:
        r["eff_lv"] = r["Ovr_Lv"] or r["Calc_Lv"]
        r["lv_changed"] = r["eff_lv"] != r["Calc_Lv"]
        r["num_diff"] = (r["now_num"] or 0) - (r["Calc_Num"] or 0)
        r["amount_diff"] = int(round(r["num_diff"] * (r["P_Price"] or 0)))
        r["soon"] = bool(r["Status"] == "적용" and r["days_left"] is not None
                         and 0 <= r["days_left"] <= 14)
    live = [r for r in rows if r["Status"] == "적용"]
    return {
        "rows": rows, "base": date,
        "reasons": [{"code": c, "desc": d} for c, d in OVERRIDE_REASONS],
        "live_cnt": len(live),
        "soon_cnt": len([r for r in live if r["soon"]]),
        "lv_cnt": len([r for r in live if r["lv_changed"]]),
        "min_cnt": len([r for r in live if r["Min_Qty"]]),
        "qty_diff": sum(r["num_diff"] for r in live),
        "amount_diff": sum(r["amount_diff"] for r in live),
        "past_cnt": len(rows) - len(live),
    }


# ── 생산 실적 등록 (여덟 번째 쓰기 기능) ────────────────────
#
# 흐름이 불출에서 끊겨 있었다. 창고에서 현장으로는 나가는데,
# 현장이 그걸 써서 완제품을 만들었다는 기록을 넣을 데가 없었다.
#
#   불출 요청 → 승인 → 피킹 → 불출 → [생산 실적] → 완제품
#
# ⚠️ 투입은 '현장에 나가 있는 LOT' 에서만 나온다.
#    창고 재고에서 바로 투입하면 불출 기록 없이 재고가 줄어 흐름이 깨진다.
#    현장 보유 = 불출 − 생산 투입 − 반납 (LOT 단위로도 음수 0건인 것을 확인했다)
#
# ⚠️ 생산일은 LOT 입고일·불출일보다 앞설 수 없다.
#    원본 4,535행 중 1,224행(27%)이 입고일보다 앞선 생산일이다. 그건 손대지 않지만
#    (문서화된 수치가 전부 흔들린다) 새로 들어오는 실적은 여기서 막는다.

PROD_ID_PRE = "PR"


def next_prod_id(conn, date):
    """Prod_ID 채번 — PR + YYYYMMDD + 4자리."""
    pre = PROD_ID_PRE + date.replace("-", "")
    last = conn.execute(
        "SELECT MAX(Prod_ID) FROM Production_tb WHERE Prod_ID LIKE ?", (pre + "%",)).fetchone()[0]
    return "%s%04d" % (pre, int(last[-4:]) + 1 if last else 1)


# LOT 단위 현장 보유. 창고 재고(LOT_DELTA)와 달리 '현장에 나가 있는 양' 이다.
SITE_LOT_SQL = """
    SELECT l.Lot_ID, l.P_ID, l.Lot_Date,
           COALESCE(o.q, 0) - COALESCE(u.q, 0) - COALESCE(r.q, 0) AS site,
           o.last_out
      FROM Lot_tb l
      LEFT JOIN (SELECT t.Lot_ID, SUM(t.T_Num) q, MAX(t.T_Date) last_out
                   FROM Transaction_tb t WHERE {demand} GROUP BY t.Lot_ID) o ON o.Lot_ID = l.Lot_ID
      LEFT JOIN (SELECT Lot_ID, SUM(Prod_Qty) q
                   FROM Production_tb GROUP BY Lot_ID) u ON u.Lot_ID = l.Lot_ID
      LEFT JOIN (SELECT t.Lot_ID, SUM(t.T_Num) q FROM Transaction_tb t
                  WHERE t.T_Type IN ({ret}) GROUP BY t.Lot_ID) r ON r.Lot_ID = l.Lot_ID
"""


def site_lots(conn, pid=None):
    """현장에 나가 있는 LOT 을 FIFO(입고일) 순으로. 투입은 여기서만 뽑는다."""
    sql = SITE_LOT_SQL.format(demand=DEMAND_T, ret=_inlist(TX_PLUS))
    sql = ("SELECT * FROM (%s) WHERE site > 0 %s ORDER BY Lot_Date, Lot_ID"
           % (sql, "AND P_ID = ?" if pid else ""))
    return _rows(conn, sql, (pid,) if pid else ())


def production_source(conn):
    """생산 실적 등록 화면의 기준 데이터."""
    base = conn.execute("SELECT MAX(T_Date) FROM Transaction_tb").fetchone()[0]

    site = {}
    for r in site_lots(conn):
        site.setdefault(r["P_ID"], []).append(dict(r))

    # 완제품별로 현장 보유만으로 몇 대까지 만들 수 있는지
    bom = {}
    for r in _rows(conn, "SELECT FG_ID, P_ID, BOM_Qty FROM BOM_tb WHERE BOM_Type = '표준'"):
        bom.setdefault(r["FG_ID"], []).append(r)

    fgs = []
    for f in _rows(conn, "SELECT FG_ID, FG_N, Unit FROM FG_tb ORDER BY FG_ID"):
        lines = bom.get(f["FG_ID"], [])
        cap, short = None, 0
        for b in lines:
            have = sum(l["site"] for l in site.get(b["P_ID"], []))
            n = have // b["BOM_Qty"] if b["BOM_Qty"] else 0
            if n <= 0:
                short += 1
            cap = n if cap is None else min(cap, n)
        fgs.append({"FG_ID": f["FG_ID"], "FG_N": f["FG_N"], "Unit": f["Unit"],
                    "line_cnt": len(lines), "capacity": cap or 0, "short_cnt": short})

    # 불출이 끝난 작업지시 — 실적을 붙일 자리
    wos = _rows(conn, """
        SELECT r.Work_Order, r.Req_ID, r.FG_ID, f.FG_N, r.Plan_Qty, r.Status,
               SUM(i.Done_Qty) AS done_qty,
               (SELECT COUNT(*) FROM Production_tb p WHERE p.Work_Order = r.Work_Order) AS prod_rows
          FROM Disburse_Req_tb r
          LEFT JOIN FG_tb f ON r.FG_ID = f.FG_ID
          LEFT JOIN Disburse_Req_Item_tb i ON i.Req_ID = r.Req_ID
         WHERE r.Status IN ('일부불출', '불출완료')
         GROUP BY r.Req_ID
         ORDER BY r.Req_Date DESC
    """)
    return {"base": base, "entry": _add_days(base, 1), "fgs": fgs, "work_orders": wos,
            "workers": _rows(conn, "SELECT EP_ID, Name, Position FROM User_tb ORDER BY Name")}


def production_plan(conn, fg_id, qty):
    """완제품 N대를 만들 때 현장에서 무엇을 얼마나 빼야 하는지 (FIFO)."""
    fg_id = str(fg_id or "").strip()
    fg = conn.execute("SELECT FG_ID, FG_N, Unit FROM FG_tb WHERE FG_ID = ?", (fg_id,)).fetchone()
    if fg is None:
        return None, ["등록되지 않은 완제품입니다: %s" % (fg_id or "(빈값)")]
    try:
        qty = int(qty or 0)
    except (TypeError, ValueError):
        return None, ["생산 수량이 숫자가 아닙니다."]
    if qty <= 0:
        return None, ["생산 수량은 1 이상이어야 합니다."]

    lines = _rows(conn, """
        SELECT b.P_ID, b.BOM_Qty, p.P_N, p.Spec, s.Sf_Lv AS grade
          FROM BOM_tb b
          JOIN Product_tb p ON b.P_ID = p.P_ID
          LEFT JOIN Safe_tb s ON b.P_ID = s.P_ID
         WHERE b.FG_ID = ? AND b.BOM_Type = '표준'
         ORDER BY b.P_ID
    """, (fg_id,))
    if not lines:
        return None, ["BOM 이 등록되지 않은 완제품입니다: %s" % fg_id]

    items, short = [], 0
    for b in lines:
        need = b["BOM_Qty"] * qty
        lots, left = [], need
        for l in site_lots(conn, b["P_ID"]):
            if left <= 0:
                break
            take = min(left, l["site"])
            lots.append({"Lot_ID": l["Lot_ID"], "Lot_Date": l["Lot_Date"],
                         "site": l["site"], "last_out": l["last_out"], "take": take})
            left -= take
        if left > 0:
            short += 1
        items.append({
            "P_ID": b["P_ID"], "P_N": b["P_N"], "Spec": b["Spec"], "grade": b["grade"],
            "bom_qty": b["BOM_Qty"], "need": need,
            "site": sum(l["site"] for l in site_lots(conn, b["P_ID"])),
            "alloc": lots, "lot_cnt": len(lots),
            "shortage": left,
        })
    return {"FG_ID": fg["FG_ID"], "FG_N": fg["FG_N"], "Unit": fg["Unit"],
            "qty": qty, "items": items, "line_cnt": len(items),
            "short_cnt": short,
            "total_qty": sum(sum(a["take"] for a in i["alloc"]) for i in items)}, []


def preview_production(conn, date, fg_id, qty, ep_id, wo=None, note=None):
    """등록 전 검증. create_production 이 저장 직전에 다시 부른다."""
    plan, errors = production_plan(conn, fg_id, qty)
    if errors:
        return None, errors

    w = conn.execute("SELECT EP_ID, Name, Position FROM User_tb WHERE EP_ID = ?",
                     (str(ep_id or "").strip(),)).fetchone()
    if not str(ep_id or "").strip():
        return None, ["생산 담당자를 선택하세요."]
    if w is None:
        return None, ["등록되지 않은 담당자입니다: %s" % ep_id]

    if plan["short_cnt"]:
        bad = [i["P_ID"] for i in plan["items"] if i["shortage"]][:5]
        return None, ["현장에 자재가 모자랍니다: %s%s. 불출을 먼저 하세요."
                      % (", ".join(bad), " 외" if plan["short_cnt"] > 5 else "")]

    # ⚠️ 생산일이 입고일·불출일보다 앞설 수 없다
    for i in plan["items"]:
        for a in i["alloc"]:
            if a["Lot_Date"] and date < a["Lot_Date"]:
                return None, ["생산일(%s)이 %s 입고일(%s)보다 앞섭니다."
                              % (date, a["Lot_ID"], a["Lot_Date"])]
            if a["last_out"] and date < a["last_out"]:
                return None, ["생산일(%s)이 %s 불출일(%s)보다 앞섭니다. 현장에 오기 전입니다."
                              % (date, a["Lot_ID"], a["last_out"])]

    wo = str(wo or "").strip() or None
    if wo:
        row = conn.execute(
            "SELECT FG_ID FROM Disburse_Req_tb WHERE Work_Order = ?", (wo,)).fetchone()
        if row and row["FG_ID"] and row["FG_ID"] != plan["FG_ID"]:
            return None, ["작업지시 %s 는 %s 용입니다. 완제품이 다릅니다." % (wo, row["FG_ID"])]

    plan["Work_Order"] = wo
    plan["worker"] = {"EP_ID": w["EP_ID"], "Name": w["Name"], "Position": w["Position"]}
    plan["note"] = (note or "").strip() or None
    plan["date"] = date
    plan["row_cnt"] = sum(i["lot_cnt"] for i in plan["items"])
    return plan, []


def create_production(conn, date, fg_id, qty, ep_id, wo=None, note=None):
    """생산 실적 등록. 자재 x LOT 마다 Production_tb 에 한 행."""
    plan, errors = preview_production(conn, date, fg_id, qty, ep_id, wo, note)
    if errors:
        return None, errors
    with conn:                                   # 전부 성공 아니면 전부 실패
        if not plan["Work_Order"]:
            plan["Work_Order"] = next_wo_id(conn, date)
        for i in plan["items"]:
            for a in i["alloc"]:
                pid = next_prod_id(conn, date)
                conn.execute(
                    "INSERT INTO Production_tb"
                    " (Prod_ID, FG_ID, P_ID, Lot_ID, Prod_Date, Prod_Qty, EP_ID, Work_Order, Note)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (pid, plan["FG_ID"], i["P_ID"], a["Lot_ID"], date, a["take"],
                     plan["worker"]["EP_ID"], plan["Work_Order"], plan["note"]))
                a["Prod_ID"] = pid
    return plan, []


# ── 발주 시뮬레이터 ──────────────────────────────────────────
# 실제 품목 데이터를 주고, 발주량·발주시점·리드타임을 바꿔가며
# 향후 재고 추이가 어떻게 달라지는지 화면에서 계산한다.
def simulator_items(conn):
    rows, base, span = forecast_list(conn)
    out = []
    for r in rows:
        if not r["lead_time"]:
            continue
        out.append({
            "P_ID": r["P_ID"], "P_N": r["P_N"], "Spec": r["Spec"],
            "P_Price": r["P_Price"], "MinOrderQty": r["MinOrderQty"], "PkgUnit": r["PkgUnit"],
            "supplier": r["supplier"], "is_foreign": r["is_foreign"],
            "grade": r["grade"], "safe_qty": r["safe_qty"] or 0,
            "lead_time": r["lead_time"] or 0,
            "stock": r["stock"] or 0,
            "daily": r["daily"] or 0,
            "out_cnt": r["out_cnt"],
            "confidence": r["confidence"],
            "days_left": r["days_left"],
            "order_qty": r["order_qty"],
            "urgency": r["urgency"],
        })
    # 기본 선택은 리스크가 큰 품목부터
    order = {"재고소진": 0, "발주지연": 1, "발주임박": 2, "주의": 3, "여유": 4, "예측불가": 5}
    out.sort(key=lambda x: (order.get(x["urgency"], 9), -(x["daily"] or 0)))
    return out, base, span


# ── 피킹리스트 ───────────────────────────────────────────────
# 생산에 필요한 자재를 FIFO(선입선출)로 LOT 을 지정하고 창고 구역 순으로
# 정렬해 이동 동선을 줄인 피킹 지시서를 만든다.
# 필요 수량은 화면에서 '생산 세트 수'로 조절하므로, 원재료(BOM 소요량 + 잔여 LOT)만
# 넘기고 실제 배분은 화면에서 계산한다.
# ── 피킹 리스트 ──────────────────────────────────────────────
#
# 승인된 불출 요청 한 건이 피킹 지시서 한 장이 된다.
# 자재팀이 이 종이를 들고 창고를 돌며 체크하고, 다 담으면 불출 처리로 넘어간다.
#
# ⚠️ LOT 은 요청끼리 나눠 갖는다.
#    요청마다 따로 FIFO 를 돌리면 같은 LOT 이 두 장의 지시서에 동시에 찍힌다.
#    창고에 500개뿐인데 두 사람이 각각 500개를 집으러 가는 셈이다.
#    그래서 요청을 오래된 순으로 세우고 LOT 재고를 하나의 통에서 덜어 쓴다.
#    뒤에 선 요청은 앞이 가져간 만큼 빠진 상태로 계산돼 '부족' 이 정직하게 뜬다.
#
# 동선
#   1단계  창고 구역 (Location_tb)
#   2단계  구역 안의 소분류 구간 (P_ID 의 DetailCat, Cat_tb 에서 이름을 가져온다)
#
# ⚠️ Location_tb 에 통로·랙·번지가 없다. 그래서 구역보다 잘게는 분류로 대신한다.
#    P_ID 가 대분류(1)+중분류(2)+소분류(2)+순번(4) 이라 소분류가 같으면 같은 물건 계열이고,
#    실제 선반에도 붙어 있을 순서다. 없는 번지를 지어내지 않고 있는 분류를 쓴다.
#
#    이 데이터에서는 완제품 하나의 BOM 이 한 대분류에만 들어 있어 요청 하나가
#    늘 한 구역이다. 구역 여러 곳에 걸치는 요청이 와도 코드는 그대로 동작한다.


def picking_lists(conn):
    """승인된 요청마다 피킹 지시서 한 장씩."""
    base = conn.execute("SELECT MAX(T_Date) FROM Transaction_tb").fetchone()[0]
    reqs = open_requests(conn)
    reqs.sort(key=lambda r: (r["Req_Date"], r["Req_ID"]))   # 오래된 요청이 먼저 집는다

    # 소분류 이름 — 'E' + '01' + '01' -> '전장-후방카메라-LCD'
    cat_name = {}
    for r in _rows(conn, "SELECT MainCat, SubCat, DetailCat, Cat_Name FROM Cat_tb"):
        cat_name[(r["MainCat"], r["SubCat"], r["DetailCat"])] = r["Cat_Name"]

    # 자재별 잔여 LOT 을 한 번만 읽어 요청들이 나눠 쓴다
    pool = {}
    stock0 = {}        # 배분 전 창고 현재고 — 앞 요청이 덜어 가도 이 값은 안 변한다
    loc_name = {}
    out = []
    for r in reqs:
        zones, seq = {}, 0
        tot_qty = tot_short = split = 0
        price = {i["P_ID"]: (i["P_Price"] or 0) for i in r["items"]}
        amount = 0
        for it in sorted(r["items"], key=lambda x: x["P_ID"]):
            need = it["left_qty"]
            if need <= 0:
                continue
            pid = it["P_ID"]
            if pid not in pool:
                pool[pid] = [dict(l) for l in live_lots(conn, pid)]
                stock0[pid] = sum(l["remain"] for l in pool[pid])
                for l in pool[pid]:
                    loc_name[l["Loc_ID"]] = l["loc_name"]
            # 앞선 요청이 이미 덜어 간 몫. 창고에는 있지만 이 지시서가 쓸 수 없는 양이다.
            avail = sum(l["remain"] for l in pool[pid])
            taken = stock0[pid] - avail
            mine = []
            left, picks = need, 0
            for l in pool[pid]:
                if left <= 0:
                    break
                take = min(left, l["remain"])
                if take <= 0:
                    continue
                l["remain"] -= take
                left -= take
                picks += 1
                z = zones.setdefault(l["Loc_ID"], [])
                ln = {
                    "cat": pid[3:5],
                    "cat_name": cat_name.get((pid[0], pid[1:3], pid[3:5]), pid[:5]),
                    "P_ID": pid, "P_N": it["P_N"], "Spec": it["Spec"],
                    "grade": it["grade"], "Req_num": it["Req_num"],
                    "Lot_ID": l["Lot_ID"], "Lot_Date": l["Lot_Date"],
                    "age_days": _days_between(l["Lot_Date"], base),
                    "take": take, "need": need,
                    "lot_remain": l["remain"] + take,     # 집기 전 잔량
                    "stock": stock0[pid], "avail": avail, "reserved": taken,
                }
                z.append(ln)
                mine.append(ln)
                tot_qty += take
                amount += int(round(take * price.get(pid, 0)))
            for n, ln in enumerate(mine, 1):
                ln["pick_idx"] = n
                ln["pick_of"] = len(mine)
                ln["pick_qty"] = sum(x["take"] for x in mine)   # 이 자재를 다 합쳐 몇 개
            if picks > 1:
                split += 1
            if left > 0:
                # 재고가 모자라 이 요청에서 못 채우는 몫
                tot_short += left
                zones.setdefault("__short__", []).append({
                    "P_ID": pid, "P_N": it["P_N"], "Spec": it["Spec"],
                    "grade": it["grade"], "Req_num": it["Req_num"],
                    "Lot_ID": None, "Lot_Date": None, "age_days": None,
                    "take": 0, "need": need, "got": need - left, "shortage": left,
                    "stock": stock0[pid], "avail": avail, "reserved": taken,
                })

        zlist = []
        for loc in sorted(k for k in zones if k != "__short__"):
            lines = sorted(zones[loc], key=lambda x: (x["P_ID"], x["Lot_Date"]))
            groups = []
            for ln in lines:
                seq += 1
                ln["seq"] = seq
                if not groups or groups[-1]["cat"] != ln["cat"]:
                    groups.append({"cat": ln["cat"], "cat_name": ln["cat_name"],
                                   "lines": [], "qty": 0})
                groups[-1]["lines"].append(ln)
                groups[-1]["qty"] += ln["take"]
            zlist.append({"Loc_ID": loc, "loc_name": loc_name.get(loc, loc),
                          "lines": lines, "groups": groups,
                          "qty": sum(x["take"] for x in lines)})
        short_lines = sorted(zones.get("__short__", []), key=lambda x: x["P_ID"])

        out.append({
            "Req_ID": r["Req_ID"], "Req_Date": r["Req_Date"], "Status": r["Status"],
            "FG_ID": r["FG_ID"], "FG_N": r["FG_N"], "Plan_Qty": r["Plan_Qty"],
            "Work_Order": r["Work_Order"], "Note": r["Note"],
            "worker": r["worker"], "pos": r["pos"],
            "approver": r["approver"], "appr_pos": r["appr_pos"],
            "Appr_Date": r["Appr_Date"],
            "zones": zlist, "short_lines": short_lines,
            "line_cnt": seq, "item_cnt": len({x["P_ID"] for z in zlist for x in z["lines"]}),
            "zone_cnt": len(zlist),
            "step_cnt": sum(len(z["groups"]) for z in zlist),
            "total_qty": tot_qty,
            "short_qty": tot_short, "short_cnt": len(short_lines),
            "split_cnt": split,
            "req_qty": r["left_qty"],
            "amount": amount,
        })

    return {"base": base, "lists": out,
            "req_cnt": len(out),
            "wait_appr": wait_approval_cnt(conn),
            "line_cnt": sum(o["line_cnt"] for o in out),
            "qty": sum(o["total_qty"] for o in out),
            "short_cnt": sum(o["short_cnt"] for o in out),
            "workers": _rows(conn, "SELECT EP_ID, Name, Position FROM User_tb ORDER BY Name")}


# ── 발주 시뮬레이터 ──────────────────────────────────────────
# 실제 품목 데이터를 주고, 발주량·발주시점·리드타임을 바꿔가며
# 향후 재고 추이가 어떻게 달라지는지 화면에서 계산한다.
def simulator_items(conn):
    rows, base, span = forecast_list(conn)
    out = []
    for r in rows:
        if not r["lead_time"]:
            continue
        out.append({
            "P_ID": r["P_ID"], "P_N": r["P_N"], "Spec": r["Spec"],
            "P_Price": r["P_Price"], "MinOrderQty": r["MinOrderQty"], "PkgUnit": r["PkgUnit"],
            "supplier": r["supplier"], "is_foreign": r["is_foreign"],
            "grade": r["grade"], "safe_qty": r["safe_qty"] or 0,
            "lead_time": r["lead_time"] or 0,
            "stock": r["stock"] or 0,
            "daily": r["daily"] or 0,
            "out_cnt": r["out_cnt"],
            "confidence": r["confidence"],
            "days_left": r["days_left"],
            "order_qty": r["order_qty"],
            "urgency": r["urgency"],
        })
    # 기본 선택은 리스크가 큰 품목부터
    order = {"재고소진": 0, "발주지연": 1, "발주임박": 2, "주의": 3, "여유": 4, "예측불가": 5}
    out.sort(key=lambda x: (order.get(x["urgency"], 9), -(x["daily"] or 0)))
    return out, base, span


# ── 피킹리스트 ───────────────────────────────────────────────
# 불출해야 할 자재를 FIFO(선입선출) 순으로 LOT 을 지정하고,
# 창고 구역 순서대로 정렬해 이동 동선을 최소화한 피킹 지시서를 만든다.
def picking_list(conn, need=None):
    """need: {P_ID: 필요수량}. 없으면 안전재고 미달분을 채우는 양으로 자동 산출."""
    rows, base, span = forecast_list(conn)

    # 기본 대상 — 재고가 있으면서 안전재고에 못 미치는 품목은 불출 대상이 아니므로
    # '생산에 필요해서 현장으로 내보낼 자재'를 BOM 소요 기준으로 잡는다.
    fg_need = _rows(conn, """
        SELECT b.P_ID, SUM(b.BOM_Qty) AS per_set
          FROM BOM_tb b WHERE b.BOM_Type = '표준'
         GROUP BY b.P_ID
    """)
    per_set = {r["P_ID"]: r["per_set"] for r in fg_need}

    # 잔여 LOT (FIFO 순)
    lots = {}
    for r in _rows(conn, f"""
        SELECT l.Lot_ID, l.P_ID, l.Lot_Date, l.Loc_ID, lo.Loc_N AS loc_name,
               l.P_Qty - COALESCE(x.out_qty, 0) AS remain,
               CAST(julianday((SELECT MAX(T_Date) FROM Transaction_tb))
                    - julianday(l.Lot_Date) AS INT) AS age_days
          FROM Lot_tb l
          LEFT JOIN Location_tb lo ON l.Loc_ID = lo.Loc_ID
          LEFT JOIN (SELECT Lot_ID, {LOT_DELTA} AS out_qty FROM Transaction_tb GROUP BY Lot_ID) x ON x.Lot_ID = l.Lot_ID
         WHERE l.P_Qty - COALESCE(x.out_qty, 0) > 0
         ORDER BY l.P_ID, l.Lot_Date
    """):
        lots.setdefault(r["P_ID"], []).append(r)

    info = {r["P_ID"]: r for r in rows}
    picks = []
    for pid, ls in lots.items():
        r = info.get(pid)
        if not r:
            continue
        # 필요 수량 — 지정값이 없으면 완제품 10세트분 소요량
        want = (need or {}).get(pid)
        if want is None:
            want = (per_set.get(pid) or 0) * 10
        if want <= 0:
            continue

        left, alloc = want, []
        for l in ls:                      # 이미 입고일 순(FIFO)으로 정렬돼 있다
            if left <= 0:
                break
            take = min(left, l["remain"])
            alloc.append({
                "Lot_ID": l["Lot_ID"], "Lot_Date": l["Lot_Date"],
                "loc": l["loc_name"], "Loc_ID": l["Loc_ID"],
                "remain": l["remain"], "take": take,
                "age_days": l["age_days"],
            })
            left -= take

        picks.append({
            "P_ID": pid, "P_N": r["P_N"], "Spec": r["Spec"],
            "grade": r["grade"], "supplier": r["supplier"],
            "want": want, "picked": want - left, "short": left,
            "stock": r["stock"], "lots": alloc,
            "Loc_ID": alloc[0]["Loc_ID"] if alloc else "ZZ",
            "loc": alloc[0]["loc"] if alloc else "-",
            "lot_n": len(alloc),
        })

    # 창고 구역 → 품번 순으로 정렬 (동선 최소화)
    picks.sort(key=lambda p: (p["Loc_ID"], p["P_ID"]))
    for i, p in enumerate(picks, 1):
        p["seq"] = i
    return picks, base


def picking_summary(picks):
    zones = {}
    for p in picks:
        z = zones.setdefault(p["Loc_ID"], {"loc": p["loc"], "items": 0, "qty": 0, "lots": 0})
        z["items"] += 1
        z["qty"] += p["picked"]
        z["lots"] += p["lot_n"]
    return {
        "items": len(picks),
        "qty": sum(p["picked"] for p in picks),
        "lots": sum(p["lot_n"] for p in picks),
        "zones": sorted(zones.items()),
        "short_items": len([p for p in picks if p["short"] > 0]),
        "short_qty": sum(p["short"] for p in picks),
        "multi_lot": len([p for p in picks if p["lot_n"] > 1]),
    }


# ── 입출고 작업 화면 (입고/불출/승인/스캐너) ─────────────────
# 이 4개는 본래 '입력' 화면이라 조회할 이력이 없다.
# 대신 입력에 필요한 실제 참조 데이터를 붙여, 작업 맥락을 보여준다.
def workbench(conn):
    base = conn.execute("SELECT MAX(T_Date) FROM Transaction_tb").fetchone()[0]

    # 입고: 발주 → 입고 실적 (LOT 채번 규칙 확인용)
    recent_po = _rows(conn, """
        SELECT h.H_ID, h.P_Date, c.CP_N AS supplier, c.Is_Foreign AS is_foreign,
               COUNT(*) AS lines, SUM(d.P_Qty) AS qty,
               SUM(d.P_Qty * p.P_Price) AS amount,
               MIN(l.Lot_Date) AS recv_date,
               CAST(julianday(MIN(l.Lot_Date)) - julianday(h.P_Date) AS INT) AS lead_days
          FROM Purchase_Header_tb h
          JOIN Purchase_Detail_tb d ON h.H_ID = d.H_ID
          JOIN Product_tb p         ON d.P_ID = p.P_ID
          LEFT JOIN Company_tb c    ON h.BRN = c.BRN
          LEFT JOIN Lot_tb l        ON l.H_ID = h.H_ID
         GROUP BY h.H_ID, h.P_Date, c.CP_N, c.Is_Foreign
         ORDER BY h.P_Date DESC LIMIT 20
    """)
    po_detail = {}
    for r in _rows(conn, """
        SELECT d.H_ID, d.P_ID, p.P_N, p.PkgUnit, d.P_Qty AS ord_qty,
               l.Lot_ID, l.P_Qty AS in_qty, l.Lot_Date, lo.Loc_N AS loc_name
          FROM Purchase_Detail_tb d
          JOIN Product_tb p ON d.P_ID = p.P_ID
          LEFT JOIN Lot_tb l ON l.H_ID = d.H_ID AND l.P_ID = d.P_ID
          LEFT JOIN Location_tb lo ON l.Loc_ID = lo.Loc_ID
         ORDER BY d.H_ID, d.Purchase_num
    """):
        po_detail.setdefault(r["H_ID"], []).append(r)

    # 불출: 잔여 LOT 이 있는 자재 (FIFO 대상)
    disburse = _rows(conn, f"""
        WITH live AS (
            SELECT l.P_ID, l.Lot_ID, l.Lot_Date, l.Loc_ID, lo.Loc_N AS loc_name,
                   l.P_Qty - COALESCE(x.out_qty,0) AS remain
              FROM Lot_tb l
              LEFT JOIN Location_tb lo ON l.Loc_ID = lo.Loc_ID
              LEFT JOIN (SELECT Lot_ID, {LOT_DELTA} AS out_qty FROM Transaction_tb GROUP BY Lot_ID) x ON x.Lot_ID = l.Lot_ID
             WHERE l.P_Qty - COALESCE(x.out_qty,0) > 0
        )
        SELECT v.P_ID, p.P_N, p.Spec, s.Sf_Lv AS grade, s.Sf_Num AS safe_qty,
               COUNT(*) AS lot_n, SUM(v.remain) AS stock,
               MIN(v.Lot_ID) AS first_lot, MIN(v.Lot_Date) AS first_date,
               MIN(v.loc_name) AS loc_name
          FROM live v JOIN Product_tb p ON v.P_ID = p.P_ID
          LEFT JOIN Safe_tb s ON v.P_ID = s.P_ID
         GROUP BY v.P_ID, p.P_N, p.Spec, s.Sf_Lv, s.Sf_Num
         ORDER BY v.P_ID
    """)

    # 승인: 실제 불출 이력을 결재 이력처럼 본다 (금액 큰 순)
    approvals = _rows(conn, f"""
        SELECT t.T_ID, t.T_Date, t.T_Num, l.P_ID, p.P_N, p.Spec,
               s.Sf_Lv AS grade, u.Name AS worker, u.Position AS pos,
               lo.Loc_N AS loc_name, l.Lot_ID,
               ROUND(t.T_Num * p.P_Price) AS amount
          FROM Transaction_tb t
          JOIN Lot_tb l     ON t.Lot_ID = l.Lot_ID
          JOIN Product_tb p ON l.P_ID = p.P_ID
          LEFT JOIN Safe_tb s ON p.P_ID = s.P_ID
          LEFT JOIN User_tb u ON t.EP_ID = u.EP_ID
          LEFT JOIN Location_tb lo ON l.Loc_ID = lo.Loc_ID
         WHERE {DEMAND_T}
         ORDER BY (t.T_Num * p.P_Price) DESC LIMIT 40
    """)

    # 스캐너: 조회 대상 (LOT / 품번)
    scan_lots = _rows(conn, f"""
        SELECT l.Lot_ID, l.P_ID, p.P_N, p.Spec, l.Lot_Date,
               lo.Loc_N AS loc_name, l.P_Qty,
               l.P_Qty - COALESCE(x.out_qty,0) AS remain,
               c.CP_N AS supplier, l.H_ID,
               s.Sf_Lv AS grade
          FROM Lot_tb l
          JOIN Product_tb p ON l.P_ID = p.P_ID
          LEFT JOIN Location_tb lo ON l.Loc_ID = lo.Loc_ID
          LEFT JOIN Company_tb c   ON p.BRN = c.BRN
          LEFT JOIN Safe_tb s      ON p.P_ID = s.P_ID
          LEFT JOIN (SELECT Lot_ID, {LOT_DELTA} AS out_qty FROM Transaction_tb GROUP BY Lot_ID) x ON x.Lot_ID = l.Lot_ID
         ORDER BY l.Lot_Date DESC
    """)

    # 입고 등록 대상 — 아직 입고되지 않은 발주.
    pending = pending_po(conn)
    # 대체 입고용 품목 목록 (발주와 다른 품번이 왔을 때 고른다)
    subs = sub_products(conn) if pending else []
    # 생산에서 올라온 불출 요청 — 불출 처리 화면이 골라 소비한다
    open_reqs = open_requests(conn)
    wait_appr = wait_approval_cnt(conn)     # 승인 관문에 걸려 여기 안 오는 요청
    # 입고 클레임 (불량·반품·대체) — 입고 화면의 '불량·반품' 탭이 쓴다
    claims = claim_list(conn)
    claim_sum = claim_summary(conn)

    # 불출·입고 등록 담당자 — 로그인이 없으므로 화면에서 직접 고른다.
    workers = _rows(conn, """
        SELECT EP_ID, Name, Position FROM User_tb ORDER BY Name
    """)

    return {
        "base": base,
        "recent_po": recent_po, "po_detail": po_detail,
        "disburse": disburse,
        "approvals": approvals,
        "scan_lots": scan_lots,
        "workers": workers,
        "pending": pending,
        "subs": subs,
        "open_reqs": open_reqs,
        "wait_appr": wait_appr,
        "claims": claims,
        "claim_sum": claim_sum,
    }

# ── 발주 등록 (이 앱에서 유일하게 DB 를 쓰는 기능) ───────────
#
# 다른 화면은 전부 조회 전용이다. 발주만 쓰기를 허용한 이유는
# "부족 → 발주 → 입고" 가 자재관리의 핵심 흐름이라 조회만으로는
# 업무를 보여줄 수 없기 때문이다.

def order_candidates(conn):
    """발주 화면용 후보 목록.

    권장 발주량은 수요예측(forecast_list)의 계산을 그대로 쓴다.
    화면마다 다른 값이 나오면 안 되므로 로직을 복제하지 않는다.
    여기에 발주서 생성에 필요한 BRN 과 '이미 나가 있는 발주'를 덧붙인다.
    """
    rows, base, span = forecast_list(conn)

    brn = {r["P_ID"]: r["BRN"]
           for r in _rows(conn, "SELECT P_ID, BRN FROM Product_tb")}

    # 아직 입고되지 않은 발주 — 중복 발주를 막기 위한 경고용.
    # 현장에서 가장 흔한 사고가 '이미 발주한 걸 모르고 또 발주'다.
    open_po = {}
    for r in _rows(conn, """
        SELECT d.P_ID, h.H_ID, h.P_Date, d.P_Qty
          FROM Purchase_Detail_tb d
          JOIN Purchase_Header_tb h ON d.H_ID = h.H_ID
          LEFT JOIN Lot_tb l        ON l.H_ID = d.H_ID AND l.P_ID = d.P_ID
         WHERE l.Lot_ID IS NULL
         ORDER BY h.P_Date DESC
    """):
        open_po.setdefault(r["P_ID"], []).append(r)

    # 화면에 필요한 필드만 남긴다.
    # forecast_list 는 스파크라인용 월별 배열까지 들고 있어 그대로 보내면 무겁다.
    KEEP = ("P_ID", "P_N", "Spec", "grade", "P_Price", "MinOrderQty", "PkgUnit",
            "supplier", "is_foreign", "lead_time", "stock", "safe_qty",
            "daily", "days_left", "deadline", "urgency", "order_qty", "order_amt",
            "confidence")
    out = []
    for r in rows:
        c = {k: r.get(k) for k in KEEP}
        c["BRN"] = brn.get(r["P_ID"])
        op = open_po.get(r["P_ID"], [])
        c["open_qty"] = sum(x["P_Qty"] or 0 for x in op)
        c["open_po"] = [{"H_ID": x["H_ID"], "date": x["P_Date"], "qty": x["P_Qty"]}
                        for x in op[:3]]
        out.append(c)
    return out, base


def round_order_qty(need, moq, pkg):
    """발주 수량을 실제 발주 가능한 단위로 올린다.

    부족분 그대로는 발주할 수 없다. 최소발주수량(MOQ)을 밑돌 수 없고,
    상자 단위로 출하되므로 포장단위(PkgUnit)의 배수여야 한다.
    """
    need = max(int(need or 0), 0)
    if need <= 0:
        return 0
    need = max(need, int(moq or 0))
    pkg = int(pkg or 1)
    if pkg > 1:
        need = -(-need // pkg) * pkg
    return need


def next_po_id(conn, date):
    """H_ID 채번 — PO + YYYYMMDD + 4자리 순번 (그날 기준)."""
    pre = "PO" + date.replace("-", "")
    last = conn.execute(
        "SELECT MAX(H_ID) FROM Purchase_Header_tb WHERE H_ID LIKE ?",
        (pre + "%",)).fetchone()[0]
    seq = int(last[-4:]) + 1 if last else 1
    return "%s%04d" % (pre, seq)


def preview_purchase(conn, date, items):
    """발주 등록 전 미리보기 — 협력사별로 어떻게 나뉘는지 보여준다.

    발주서 1장은 협력사 1곳 앞으로 나간다. 자재마다 공급처가 정해져 있으므로
    여러 자재를 한 번에 담으면 공급처 기준으로 발주서가 자동 분리된다.
    """
    info = {r["P_ID"]: r for r in _rows(conn, """
        SELECT p.P_ID, p.P_N, p.Spec, p.BRN, p.P_Price, p.MinOrderQty, p.PkgUnit,
               c.CP_N AS supplier, c.Is_Foreign AS is_foreign,
               s.Lead_Time AS lead_time, s.Sf_Lv AS grade
          FROM Product_tb p
          LEFT JOIN Company_tb c ON p.BRN = c.BRN
          LEFT JOIN Safe_tb s    ON p.P_ID = s.P_ID
    """)}

    merged, errors = {}, []
    for it in items:
        pid = str(it.get("P_ID", "")).strip()
        if pid not in info:
            errors.append("등록되지 않은 품번입니다: %s" % (pid or "(빈값)"))
            continue
        try:
            qty = int(it.get("qty") or 0)
        except (TypeError, ValueError):
            errors.append("%s 수량이 숫자가 아닙니다." % pid)
            continue
        if qty <= 0:
            errors.append("%s 수량은 1 이상이어야 합니다." % pid)
            continue
        if not info[pid]["BRN"]:
            errors.append("%s 는 공급 협력사가 지정되어 있지 않습니다." % pid)
            continue
        merged[pid] = merged.get(pid, 0) + qty      # 같은 품번은 합산

    groups = {}
    for pid, qty in merged.items():
        p = info[pid]
        g = groups.setdefault(p["BRN"], {
            "BRN": p["BRN"], "supplier": p["supplier"],
            "is_foreign": p["is_foreign"], "items": [], "amount": 0, "lead_max": 0,
        })
        amt = round(qty * (p["P_Price"] or 0))
        lt = int(p["lead_time"] or 0)
        g["items"].append({
            "P_ID": pid, "P_N": p["P_N"], "Spec": p["Spec"], "grade": p["grade"],
            "qty": qty, "price": p["P_Price"], "amount": amt,
            "moq": p["MinOrderQty"], "pkg": p["PkgUnit"], "lead_time": lt,
        })
        g["amount"] += amt
        g["lead_max"] = max(g["lead_max"], lt)

    out = []
    for g in groups.values():
        g["items"].sort(key=lambda x: x["P_ID"])
        g["line_cnt"] = len(g["items"])
        g["eta"] = _add_days(date, g["lead_max"])   # 예상 입고일
        out.append(g)
    out.sort(key=lambda g: g["supplier"] or "")
    return out, errors


def _add_days(date, days):
    import datetime
    y, m, d = (int(x) for x in date.split("-"))
    return (datetime.date(y, m, d) + datetime.timedelta(days=int(days or 0))).isoformat()


def _days_between(a, b):
    """a → b 일수. 발주일 → 입고일 리드타임에 쓴다."""
    import datetime
    ay, am, ad = (int(x) for x in a.split("-"))
    by, bm, bd = (int(x) for x in b.split("-"))
    return (datetime.date(by, bm, bd) - datetime.date(ay, am, ad)).days


def create_purchase(conn, date, items):
    """발주 등록. 협력사별로 발주서를 나눠 INSERT 한다.

    전부 성공하거나 전부 실패한다(트랜잭션). 발주서를 반쯤 만들어두면
    현장에서 어느 게 유효한지 알 수 없게 된다.
    """
    groups, errors = preview_purchase(conn, date, items)
    if errors:
        return None, errors
    if not groups:
        return None, ["발주할 자재가 없습니다."]

    created = []
    with conn:                                   # 커밋/롤백 자동
        for g in groups:
            hid = next_po_id(conn, date)
            conn.execute(
                "INSERT INTO Purchase_Header_tb (H_ID, BRN, P_Date) VALUES (?, ?, ?)",
                (hid, g["BRN"], date))
            for n, it in enumerate(g["items"], 1):
                conn.execute(
                    "INSERT INTO Purchase_Detail_tb (H_ID, Purchase_num, P_ID, P_Qty)"
                    " VALUES (?, ?, ?, ?)",
                    (hid, n, it["P_ID"], it["qty"]))
            g["H_ID"] = hid
            created.append(g)
    return created, []


# ── 불출 등록 (두 번째 쓰기 기능) ────────────────────────────
#
# 발주가 "부족 → 발주 → 입고" 의 입구라면 불출은 출구다.
# 자재창고에서 현장으로 나가는 유일한 실소비(TX_TYPES 의 demand 유형)라,
# 이것까지 저장돼야 재고가 실제로 줄어드는 흐름이 닫힌다.
#
# ⚠️ FIFO 배분을 화면에서 받지 않는다.
#    화면도 같은 계산을 해서 보여주지만, 저장할 때는 서버가 잔여 LOT 을
#    다시 읽어 처음부터 배분한다. 화면이 보낸 배분을 그대로 믿으면
#    FIFO 를 건너뛰거나 잔여보다 많이 꺼내는 요청을 막을 방법이 없다.

def next_tx_id(conn, date):
    """T_ID 채번 — T + YYYYMMDD + 4자리 순번 (그날 기준).

    H_ID(next_po_id) 와 같은 규칙이다. 한 번의 불출이 여러 LOT 으로
    쪼개지면 LOT 마다 거래 행이 생기므로 순번이 하나씩 올라간다.
    """
    pre = "T" + date.replace("-", "")
    last = conn.execute(
        "SELECT MAX(T_ID) FROM Transaction_tb WHERE T_ID LIKE ?",
        (pre + "%",)).fetchone()[0]
    seq = int(last[-4:]) + 1 if last else 1
    return "%s%04d" % (pre, seq)


def live_lots(conn, pid):
    """자재 하나의 잔여 LOT 을 FIFO 순(입고일 오름차순)으로 돌려준다.

    잔여 계산은 다른 화면과 같은 LOT_DELTA 를 쓴다.
    여기서만 따로 계산하면 화면마다 재고가 달라진다.
    """
    return _rows(conn, """
        SELECT l.Lot_ID, l.Lot_Date, l.Loc_ID, lo.Loc_N AS loc_name,
               l.P_Qty - COALESCE(x.out_qty, 0) AS remain
          FROM Lot_tb l
          LEFT JOIN Location_tb lo ON l.Loc_ID = lo.Loc_ID
          LEFT JOIN (SELECT Lot_ID, %s AS out_qty
                       FROM Transaction_tb GROUP BY Lot_ID) x ON x.Lot_ID = l.Lot_ID
         WHERE l.P_ID = ?
           AND l.P_Qty - COALESCE(x.out_qty, 0) > 0
         ORDER BY l.Lot_Date, l.Lot_ID
    """ % LOT_DELTA, (pid,))


def _req_line(conn, req):
    """불출이 소비할 요청 라인을 찾아 검증한다.

    요청 없이 불출하는 것도 허용한다(긴급 불출·재고 조정). 요청을 지목했을 때만
    그 라인이 실재하고 잔여가 있는지 확인한다.
    """
    if not req:
        return None, []
    rid = str(req.get("Req_ID") or "").strip()
    try:
        num = int(req.get("Req_num"))
    except (TypeError, ValueError):
        return None, ["요청 라인 번호가 잘못되었습니다."]
    row = conn.execute("""
        SELECT i.*, r.Status, r.FG_ID, r.Work_Order
          FROM Disburse_Req_Item_tb i
          JOIN Disburse_Req_tb r ON r.Req_ID = i.Req_ID
         WHERE i.Req_ID = ? AND i.Req_num = ?
    """, (rid, num)).fetchone()
    if row is None:
        return None, ["등록되지 않은 요청 라인입니다: %s #%d" % (rid or "(빈값)", num)]
    d = dict(row)
    if d["Status"] in ("요청", "반려", "취소"):
        return None, ["%s 상태의 요청은 불출할 수 없습니다: %s (승인 후 처리하세요)"
                      % (d["Status"], rid)]
    if d["Appr_Qty"] is None:
        return None, ["승인되지 않은 요청 라인입니다: %s #%d" % (rid, num)]
    # 불출 가능량의 기준은 요청 수량이 아니라 승인 수량이다
    d["left_qty"] = max((d["Appr_Qty"] or 0) - (d["Done_Qty"] or 0), 0)
    if d["Appr_Qty"] == 0:
        return None, ["승인 과정에서 제외된 요청 라인입니다: %s #%d" % (rid, num)]
    if d["left_qty"] <= 0:
        return None, ["이미 전량 불출된 요청 라인입니다: %s #%d" % (rid, num)]
    return d, []


def preview_disburse(conn, pid, qty, ep_id, req=None):
    """불출 미리보기 — FIFO 배분과 불출 후 재고를 계산한다.

    검증에 걸리면 (None, [사유]) 를 돌려주고 아무것도 계산하지 않는다.
    create_disburse 가 이 함수를 그대로 다시 불러 저장 직전에 재검증한다.
    """
    pid = str(pid or "").strip()
    row = conn.execute("""
        SELECT p.P_ID, p.P_N, p.Spec, p.P_Price,
               s.Sf_Lv AS grade, s.Sf_Num AS safe_qty
          FROM Product_tb p
          LEFT JOIN Safe_tb s ON p.P_ID = s.P_ID
         WHERE p.P_ID = ?
    """, (pid,)).fetchone()
    if row is None:
        return None, ["등록되지 않은 품번입니다: %s" % (pid or "(빈값)")]
    info = dict(row)

    try:
        qty = int(qty or 0)
    except (TypeError, ValueError):
        return None, ["불출 수량이 숫자가 아닙니다."]
    if qty <= 0:
        return None, ["불출 수량은 1 이상이어야 합니다."]

    ep_id = str(ep_id or "").strip()
    if not ep_id:
        return None, ["불출 담당자를 선택하세요."]
    w = conn.execute(
        "SELECT EP_ID, Name, Position FROM User_tb WHERE EP_ID = ?",
        (ep_id,)).fetchone()
    if w is None:
        return None, ["등록되지 않은 사원번호입니다: %s" % ep_id]

    rq, errors = _req_line(conn, req)
    if errors:
        return None, errors
    if rq:
        if rq["P_ID"] != pid:
            return None, ["요청 라인의 품번(%s)과 불출 품번(%s)이 다릅니다."
                          % (rq["P_ID"], pid)]
        if qty > rq["left_qty"]:
            return None, ["요청 잔여 수량을 넘습니다. 잔여 {:,}개 / 불출 {:,}개".format(
                rq["left_qty"], qty)]

    lots = live_lots(conn, pid)
    stock = sum(l["remain"] for l in lots)
    if qty > stock:
        return None, ["재고가 부족합니다. 보유 {:,}개 / 요청 {:,}개".format(stock, qty)]

    # FIFO 배분 — 오래된 LOT 부터 채우고, 모자라면 다음 LOT 으로 넘어간다
    left, alloc = qty, []
    for l in lots:
        if left <= 0:
            break
        take = min(left, l["remain"])
        alloc.append({
            "seq": len(alloc) + 1, "Lot_ID": l["Lot_ID"],
            "Lot_Date": l["Lot_Date"], "loc_name": l["loc_name"],
            "remain": l["remain"], "take": take, "after": l["remain"] - take,
        })
        left -= take

    after = stock - qty
    safe = info["safe_qty"]
    plan = {
        "P_ID": pid, "P_N": info["P_N"], "Spec": info["Spec"],
        "grade": info["grade"], "price": info["P_Price"],
        "qty": qty, "stock": stock, "after": after,
        "safe_qty": safe,
        "below_safe": bool(safe and after < safe),
        "amount": round(qty * (info["P_Price"] or 0)),
        "worker": {"EP_ID": w["EP_ID"], "Name": w["Name"], "Position": w["Position"]},
        "alloc": alloc, "lot_cnt": len(alloc),
        "T_Type": TX_DISBURSE,
        "req": ({"Req_ID": rq["Req_ID"], "Req_num": rq["Req_num"],
                 "FG_ID": rq["FG_ID"], "Work_Order": rq["Work_Order"],
                 "req_qty": rq["Req_Qty"], "done_qty": rq["Done_Qty"],
                 "left_qty": rq["left_qty"]} if rq else None),
    }
    return plan, []


def _write_disburse(conn, date, plan):
    """검증이 끝난 불출 계획 하나를 실제로 기록한다 (트랜잭션은 호출자가 연다).

    단건 불출과 일괄 불출이 같은 코드를 쓰도록 떼어냈다. 여기서 `with conn:` 을
    열면 일괄 처리 중간에 커밋이 끼어들어 원자성이 깨진다.
    """
    for a in plan["alloc"]:
        tid = next_tx_id(conn, date)
        conn.execute(
            "INSERT INTO Transaction_tb"
            " (T_ID, Lot_ID, T_Type, T_Date, T_Num, EP_ID)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (tid, a["Lot_ID"], TX_DISBURSE, date,
             a["take"], plan["worker"]["EP_ID"]))
        a["T_ID"] = tid
    if plan["req"]:
        # 요청 잔여를 깎고 요청서 상태를 다시 매긴다
        conn.execute(
            "UPDATE Disburse_Req_Item_tb SET Done_Qty = Done_Qty + ?"
            " WHERE Req_ID = ? AND Req_num = ?",
            (plan["qty"], plan["req"]["Req_ID"], plan["req"]["Req_num"]))
        plan["req"]["status"] = _refresh_req_status(conn, plan["req"]["Req_ID"])
        plan["req"]["done_qty"] = plan["req"]["done_qty"] + plan["qty"]
        plan["req"]["left_qty"] = plan["req"]["left_qty"] - plan["qty"]
    plan["date"] = date
    return plan


def create_disburse(conn, date, pid, qty, ep_id, req=None):
    """불출 등록. FIFO 로 나눈 LOT 마다 Transaction_tb 에 불출 행을 남긴다.

    한 번의 불출이 LOT 여러 개로 쪼개져도 전부 성공하거나 전부 실패한다.
    절반만 저장되면 재고가 실제와 어긋난 채로 굳어 되돌리기 어렵다.
    """
    plan, errors = preview_disburse(conn, pid, qty, ep_id, req)
    if errors:
        return None, errors
    with conn:                                   # 커밋/롤백 자동
        _write_disburse(conn, date, plan)
    return plan, []


# ── 일괄 불출 ───────────────────────────────────────────────
#
# 불출 요청 한 건은 보통 자재 10~15종이다. 한 품목씩 열다섯 번 등록하게 하면
# 현장에서 쓸 수 없다. 요청서를 통째로 골라 한 번에 내보낸다.
#
# 자재마다 사정이 다르므로 전부 다 나가지는 않는다.
#   전량가능 — 요청 잔여만큼 재고가 있다
#   부분가능 — 재고가 모자라 있는 만큼만 (나머지는 요청에 잔여로 남는다)
#   불가     — 재고 0. 발주가 먼저다
# 무엇을 얼마나 낼지는 화면에서 고르고, 서버는 받은 것만 검증해 기록한다.

def preview_disburse_batch(conn, lines, ep_id):
    """여러 자재를 한 번에 불출하기 전 검증·FIFO 배분.

    lines: [{"P_ID": ..., "qty": ..., "req": {"Req_ID": ..., "Req_num": ...}}, ...]
    한 줄이라도 걸리면 전체를 반려한다. 일부만 나가면 화면에 보인 것과
    실제 결과가 달라져 현장에서 무엇이 나갔는지 알 수 없다.
    """
    if not lines:
        return None, ["불출할 자재를 선택하세요."]

    seen, plans = set(), []
    for ln in lines:
        pid = str((ln or {}).get("P_ID") or "").strip()
        if pid in seen:
            # 같은 자재를 두 줄로 보내면 재고를 두 번 쓴 것으로 계산된다
            return None, ["같은 자재가 두 번 들어왔습니다: %s" % pid]
        seen.add(pid)
        p, e = preview_disburse(conn, pid, (ln or {}).get("qty"),
                                ep_id, (ln or {}).get("req"))
        if e:
            return None, ["%s — %s" % (pid or "(빈값)", e[0])]
        plans.append(p)

    plans.sort(key=lambda p: p["P_ID"])
    reqs = {p["req"]["Req_ID"] for p in plans if p["req"]}
    return {
        "items": plans,
        "line_cnt": len(plans),
        "qty": sum(p["qty"] for p in plans),
        "amount": sum(p["amount"] for p in plans),
        "lot_cnt": sum(p["lot_cnt"] for p in plans),
        "below_safe_cnt": sum(1 for p in plans if p["below_safe"]),
        "worker": plans[0]["worker"],
        "req_ids": sorted(reqs),
        "req_cnt": len(reqs),
    }, []


def create_disburse_batch(conn, date, lines, ep_id):
    """일괄 불출 등록. 전부 성공하거나 전부 실패한다."""
    plan, errors = preview_disburse_batch(conn, lines, ep_id)
    if errors:
        return None, errors
    with conn:                                   # 커밋/롤백 자동
        for p in plan["items"]:
            _write_disburse(conn, date, p)
    plan["date"] = date
    # 요청서별 최종 상태를 담아 화면이 그대로 보여줄 수 있게 한다
    plan["reqs"] = [dict(r) for r in _rows(conn, """
        SELECT r.Req_ID, r.Status, f.FG_N, r.Work_Order,
               SUM(i.Req_Qty) AS req_qty, SUM(i.Done_Qty) AS done_qty
          FROM Disburse_Req_tb r
          LEFT JOIN FG_tb f ON r.FG_ID = f.FG_ID
          LEFT JOIN Disburse_Req_Item_tb i ON i.Req_ID = r.Req_ID
         WHERE r.Req_ID IN (%s)
         GROUP BY r.Req_ID
    """ % ",".join("?" * len(plan["req_ids"])), tuple(plan["req_ids"]))] \
        if plan["req_ids"] else []
    for r in plan["reqs"]:
        r["pct"] = _pct(r["done_qty"] or 0, r["req_qty"] or 0)
    return plan, []
# ── 입고 등록 (세 번째 쓰기 기능) ────────────────────────────
#
# 발주(입구) → 입고 → 불출(출구) 중 가운데 토막이다.
# 입고가 저장돼야 발주한 물건이 LOT 으로 창고에 들어오고,
# 그 LOT 에서 불출이 나가는 한 바퀴가 닫힌다.
#
# 입고는 테이블 두 개를 함께 쓴다.
#   Lot_tb          새 LOT 1행 (재고의 실체)
#   Transaction_tb  '입고' 거래 1행 (이력)
# 기존 데이터도 LOT 636건 : 입고거래 636건으로 1:1 이므로 같은 모양을 유지한다.
# 입고 거래의 재고 부호는 0 이다 — 수량이 Lot_tb.P_Qty 에 이미 들어 있어
# 거래행을 또 더하면 이중 계산이 된다 (TX_TYPES 참조).

# 대분류 → 보관 창고.
# 기존 LOT 636건이 전수 이 매핑을 따르고 예외가 0건이라 상수로 고정한다.
#   V 117건→L01 · S 97건→L02 · E 109건→L03 · U 166건→L04 · T 147건→L05
LOC_BY_MAINCAT = {"V": "L01", "S": "L02", "E": "L03", "U": "L04", "T": "L05"}


def next_lot_id(conn, date):
    """Lot_ID 채번 — LOT + YYYYMMDD + 4자리 순번 (그날 기준).

    H_ID · T_ID 와 같은 규칙이다. 한 발주에서 품목 여러 건이 들어오면
    품목마다 LOT 이 생기므로 순번이 하나씩 올라간다.
    """
    pre = "LOT" + date.replace("-", "")
    last = conn.execute(
        "SELECT MAX(Lot_ID) FROM Lot_tb WHERE Lot_ID LIKE ?",
        (pre + "%",)).fetchone()[0]
    seq = int(last[-4:]) + 1 if last else 1
    return "%s%04d" % (pre, seq)


# 실무에서 발주한 그대로 들어오는 일은 오히려 드물다.
# 수량이 모자라거나, 아예 다른 품번이 오거나(거래처 결품 → 호환품 대체),
# 그래서 금액이 어긋나면 잔금을 정산해야 한다.
# 이 세 가지를 입고 한 번에 처리한다.

SETTLE_KINDS = ("추가청구", "차감", "정산없음")

# 대체 입고로 생긴 LOT 은 "그 발주 라인이 쓴 것"으로 선점된다.
#
# 왜 필요한가 — A 를 발주했는데 B 가 와서 B 로 입고하면 Lot_tb 에는 (H_ID, B) 가 남는다.
# 그런데 같은 발주서에 원래 B 라인이 따로 있으면, H_ID+P_ID 조인만으로는
# 그 B 라인까지 입고된 것으로 잘못 닫힌다. 그래서 대체로 생긴 LOT 은 조인 대상에서 뺀다.
_CLAIMED_LOTS = ("SELECT Lot_ID FROM Purchase_Change_tb "
                 "WHERE Lot_ID IS NOT NULL AND In_P_ID <> Ord_P_ID")

# 발주 라인이 아직 입고되지 않았는지 판정하는 조건.
#   ch : 이 라인에 변경 이력이 있으면 처리가 끝난 것 (대체 입고가 여기 걸린다)
#   l  : 품번이 같은 정상 입고는 Lot_tb 로 잡힌다 (과거 데이터 포함)
PENDING_JOIN = """
          LEFT JOIN Purchase_Change_tb ch
                 ON ch.H_ID = d.H_ID AND ch.Purchase_num = d.Purchase_num
          LEFT JOIN Lot_tb l
                 ON l.H_ID = d.H_ID AND l.P_ID = d.P_ID
                AND l.Lot_ID NOT IN (%s)
""" % _CLAIMED_LOTS
PENDING_WHERE = "ch.Chg_ID IS NULL AND l.Lot_ID IS NULL"


def next_chg_id(conn, date):
    """Chg_ID 채번 — CHG + YYYYMMDD + 4자리 순번."""
    pre = "CHG" + date.replace("-", "")
    last = conn.execute(
        "SELECT MAX(Chg_ID) FROM Purchase_Change_tb WHERE Chg_ID LIKE ?",
        (pre + "%",)).fetchone()[0]
    seq = int(last[-4:]) + 1 if last else 1
    return "%s%04d" % (pre, seq)


def pending_po(conn):
    """아직 입고되지 않은 발주. 입고 등록의 대상 목록이다."""
    rows = _rows(conn, """
        SELECT h.H_ID, h.P_Date, c.CP_N AS supplier, c.Is_Foreign AS is_foreign,
               d.Purchase_num, d.P_ID, p.P_N, p.Spec, p.PkgUnit, p.MinOrderQty,
               p.P_Price, p.MainCat, s.Sf_Lv AS grade, s.Lead_Time AS lead_time,
               d.P_Qty AS ord_qty
          FROM Purchase_Detail_tb d
          JOIN Purchase_Header_tb h ON d.H_ID = h.H_ID
          JOIN Product_tb p         ON d.P_ID = p.P_ID
          LEFT JOIN Company_tb c    ON h.BRN = c.BRN
          LEFT JOIN Safe_tb s       ON d.P_ID = s.P_ID
          %s
         WHERE %s
         ORDER BY h.P_Date, d.H_ID, d.Purchase_num
    """ % (PENDING_JOIN, PENDING_WHERE))
    loc_n = {r["Loc_ID"]: r["Loc_N"]
             for r in _rows(conn, "SELECT Loc_ID, Loc_N FROM Location_tb")}

    groups = {}
    for r in rows:
        g = groups.setdefault(r["H_ID"], {
            "H_ID": r["H_ID"], "P_Date": r["P_Date"],
            "supplier": r["supplier"], "is_foreign": r["is_foreign"],
            "lead_max": 0, "amount": 0, "qty": 0, "items": [],
        })
        loc = LOC_BY_MAINCAT.get(r["MainCat"])
        lt = int(r["lead_time"] or 0)
        g["items"].append({
            "Purchase_num": r["Purchase_num"], "P_ID": r["P_ID"],
            "P_N": r["P_N"], "Spec": r["Spec"], "grade": r["grade"],
            "ord_qty": r["ord_qty"], "price": r["P_Price"],
            "pkg": r["PkgUnit"], "moq": r["MinOrderQty"],
            "Loc_ID": loc, "loc_name": loc_n.get(loc), "lead_time": lt,
        })
        g["qty"] += r["ord_qty"] or 0
        g["amount"] += round((r["ord_qty"] or 0) * (r["P_Price"] or 0))
        g["lead_max"] = max(g["lead_max"], lt)

    out = sorted(groups.values(), key=lambda g: (g["P_Date"], g["H_ID"]))
    for g in out:
        g["line_cnt"] = len(g["items"])
        g["eta"] = _add_days(g["P_Date"], g["lead_max"])
        g["items"].sort(key=lambda x: x["Purchase_num"])
    return out


def sub_products(conn):
    """대체 입고용 품목 목록. 화면의 검색 드롭다운이 쓴다."""
    loc_n = {r["Loc_ID"]: r["Loc_N"]
             for r in _rows(conn, "SELECT Loc_ID, Loc_N FROM Location_tb")}
    out = []
    for r in _rows(conn, """
        SELECT p.P_ID, p.P_N, p.Spec, p.P_Price, p.MainCat, p.BRN,
               c.CP_N AS supplier
          FROM Product_tb p LEFT JOIN Company_tb c ON p.BRN = c.BRN
         ORDER BY p.P_ID
    """):
        loc = LOC_BY_MAINCAT.get(r["MainCat"])
        r["Loc_ID"] = loc
        r["loc_name"] = loc_n.get(loc)
        out.append(r)
    return out


def preview_inbound(conn, date, hid, lines, ep_id, settle=None, note=None):
    """입고 미리보기 — 라인별 판정·배정 창고·정산 금액을 계산한다.

    lines 는 발주 라인 기준이다.
        [{"Purchase_num": 1, "P_ID": "V01010001", "qty": 380, "reason": ""}, ...]
    품번(P_ID)을 발주와 다르게 보내면 대체 입고다. 수량만 다르면 수량 변경이다.
    보내지 않은 라인은 이번에 받지 않은 것으로 보고 미입고로 남긴다.

    라인을 품번이 아니라 Purchase_num 으로 지목하는 이유 —
    대체 입고를 하면 품번이 바뀌므로 품번으로는 어느 라인인지 알 수 없다.
    """
    hid = str(hid or "").strip()
    pend = {g["H_ID"]: g for g in pending_po(conn)}
    if hid not in pend:
        done = conn.execute(
            "SELECT 1 FROM Purchase_Header_tb WHERE H_ID = ?", (hid,)).fetchone()
        return None, ["이미 전량 입고된 발주입니다: %s" % hid if done
                      else "등록되지 않은 발주번호입니다: %s" % (hid or "(빈값)")]
    po = pend[hid]
    by_num = {it["Purchase_num"]: it for it in po["items"]}

    ep_id = str(ep_id or "").strip()
    if not ep_id:
        return None, ["입고 담당자를 선택하세요."]
    w = conn.execute(
        "SELECT EP_ID, Name, Position FROM User_tb WHERE EP_ID = ?",
        (ep_id,)).fetchone()
    if w is None:
        return None, ["등록되지 않은 사원번호입니다: %s" % ep_id]

    if date < po["P_Date"]:
        return None, ["입고일(%s)이 발주일(%s)보다 앞설 수 없습니다." % (date, po["P_Date"])]

    prod = {r["P_ID"]: r for r in sub_products(conn)}

    rows, seen = [], set()
    for ln in (lines or []):
        try:
            num = int(ln.get("Purchase_num"))
        except (TypeError, ValueError):
            return None, ["발주 라인 번호가 잘못되었습니다."]
        if num not in by_num:
            return None, ["이 발주에 없거나 이미 입고된 라인입니다: %d번" % num]
        if num in seen:
            return None, ["같은 발주 라인이 두 번 들어왔습니다: %d번" % num]
        seen.add(num)

        ord_it = by_num[num]
        try:
            qty = int(ln.get("qty") or 0)
        except (TypeError, ValueError):
            return None, ["%s 입고 수량이 숫자가 아닙니다." % ord_it["P_ID"]]
        if qty < 0:
            return None, ["%s 입고 수량은 0 이상이어야 합니다." % ord_it["P_ID"]]
        if qty == 0:
            continue                       # 이번에 안 받음 → 미입고로 남김

        in_pid = str(ln.get("P_ID") or ord_it["P_ID"]).strip()
        if in_pid not in prod:
            return None, ["등록되지 않은 품번입니다: %s" % in_pid]
        in_p = prod[in_pid]
        if not in_p["Loc_ID"]:
            return None, ["%s 의 보관 창고를 정할 수 없습니다 (대분류 미상)." % in_pid]

        # 검수 불량 — 받긴 받았지만 쓸 수 없는 수량
        try:
            bad = int(ln.get("defect") or 0)
        except (TypeError, ValueError):
            return None, ["%s 불량 수량이 숫자가 아닙니다." % in_pid]
        if bad < 0:
            return None, ["%s 불량 수량은 0 이상이어야 합니다." % in_pid]
        if bad > qty:
            return None, ["%s 불량 수량(%s)이 입고 수량(%s)을 넘습니다."
                          % (in_pid, format(bad, ","), format(qty, ","))]

        reason = (ln.get("reason") or "").strip()
        swapped = in_pid != ord_it["P_ID"]
        if swapped and not reason:
            # 품번이 바뀌는 건 거래처와 협의한 결과다. 근거가 없으면 나중에 아무도 설명 못 한다.
            return None, ["%s → %s 대체 입고는 협의 내용을 적어야 합니다."
                          % (ord_it["P_ID"], in_pid)]

        ord_amt = int(round((ord_it["ord_qty"] or 0) * (ord_it["price"] or 0)))
        in_amt = int(round(qty * (in_p["P_Price"] or 0)))
        gap = qty - (ord_it["ord_qty"] or 0)
        chg = ("대체+수량변경" if swapped and gap else
               "대체입고" if swapped else
               "수량변경" if gap else "없음")
        rows.append({
            "Purchase_num": num,
            "defect_qty": bad, "good_qty": qty - bad,
            "defect_note": (ln.get("defect_note") or "").strip(),
            "ord_P_ID": ord_it["P_ID"], "ord_P_N": ord_it["P_N"],
            "ord_qty": ord_it["ord_qty"], "ord_price": ord_it["price"],
            "ord_amt": ord_amt,
            "P_ID": in_pid, "P_N": in_p["P_N"], "Spec": in_p["Spec"],
            "in_qty": qty, "price": in_p["P_Price"], "amount": in_amt,
            "Loc_ID": in_p["Loc_ID"], "loc_name": in_p["loc_name"],
            "supplier": in_p["supplier"],
            "swapped": swapped, "gap": gap, "diff_amt": in_amt - ord_amt,
            "chg_type": chg, "reason": reason,
            "judge": "일치" if gap == 0 else ("부족" if gap < 0 else "초과"),
            "pkg_ok": bool(ord_it["pkg"]) and gap < 0 and (-gap) % ord_it["pkg"] == 0,
        })

    if not rows:
        return None, ["입고할 품목이 없습니다. 수량을 1 이상으로 지정하세요."]

    ord_total = sum(r["ord_amt"] for r in rows)
    in_total = sum(r["amount"] for r in rows)
    diff = in_total - ord_total

    settle = (settle or "").strip() or _default_settle(diff)
    if settle not in SETTLE_KINDS:
        return None, ["잔금 처리 방식이 잘못되었습니다: %s" % settle]

    plan = {
        "H_ID": hid, "P_Date": po["P_Date"], "date": date,
        "supplier": po["supplier"], "is_foreign": po["is_foreign"],
        "lead_days": _days_between(po["P_Date"], date),
        "worker": {"EP_ID": w["EP_ID"], "Name": w["Name"], "Position": w["Position"]},
        "items": sorted(rows, key=lambda r: r["Purchase_num"]),
        "line_cnt": len(rows),
        "qty": sum(r["in_qty"] for r in rows),
        "ord_amount": ord_total, "amount": in_total, "diff_amt": diff,
        "settle": settle, "settle_note": (note or "").strip(),
        "ok_cnt": sum(1 for r in rows if r["judge"] == "일치" and not r["swapped"]),
        "short_cnt": sum(1 for r in rows if r["judge"] == "부족"),
        "over_cnt": sum(1 for r in rows if r["judge"] == "초과"),
        "swap_cnt": sum(1 for r in rows if r["swapped"]),
        "defect_cnt": sum(1 for r in rows if r["defect_qty"] > 0),
        "defect_qty": sum(r["defect_qty"] for r in rows),
        "good_qty": sum(r["good_qty"] for r in rows),
        "chg_cnt": sum(1 for r in rows if r["chg_type"] != "없음"),
        "rest_cnt": len(po["items"]) - len(rows),
    }
    return plan, []


def _default_settle(diff):
    """차액 부호로 기본 정산 방식을 고른다. 화면에서 바꿀 수 있다."""
    if diff > 0:
        return "추가청구"          # 발주보다 많이/비싸게 받음 → 더 낸다
    if diff < 0:
        return "차감"              # 덜 받음 → 결제에서 뺀다
    return "정산없음"


def create_inbound(conn, date, hid, lines, ep_id, settle=None, note=None):
    """입고 등록. 라인마다 Lot_tb + Transaction_tb, 변경이 있으면 Purchase_Change_tb.

    셋을 한 트랜잭션에 묶는다. LOT 만 만들고 거래를 못 남기면 이력에서 입고가
    사라지고, 변경 이력을 못 남기면 대체 입고한 발주 라인이 영원히 미입고로 남는다.
    """
    plan, errors = preview_inbound(conn, date, hid, lines, ep_id, settle, note)
    if errors:
        return None, errors

    ep = plan["worker"]["EP_ID"]
    with conn:
        for it in plan["items"]:
            lot = next_lot_id(conn, date)
            conn.execute(
                "INSERT INTO Lot_tb (Lot_ID, P_ID, Lot_Date, Loc_ID, P_Qty, EP_ID, H_ID)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (lot, it["P_ID"], date, it["Loc_ID"], it["in_qty"], ep, hid))
            tid = next_tx_id(conn, date)
            conn.execute(
                "INSERT INTO Transaction_tb"
                " (T_ID, Lot_ID, T_Type, T_Date, T_Num, EP_ID)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (tid, lot, TX_RECEIPT, date, it["in_qty"], ep))
            it["Lot_ID"] = lot
            it["T_ID"] = tid

            # 검수 불량은 입고와 같은 트랜잭션에서 차감하고 클레임을 접수한다.
            # 따로 처리하면 "입고는 됐는데 불량은 안 잡힌" 중간 상태가 생긴다.
            if it["defect_qty"] > 0:
                it["claim"] = _write_claim(
                    conn, date, lot, it["defect_qty"], ep, "입고검수", "미정",
                    it["defect_note"] or None, it["price"])

            if it["chg_type"] != "없음":
                cid = next_chg_id(conn, date)
                conn.execute(
                    "INSERT INTO Purchase_Change_tb"
                    " (Chg_ID, H_ID, Purchase_num, Ord_P_ID, In_P_ID,"
                    "  Ord_Qty, In_Qty, Ord_Amt, In_Amt, Diff_Amt,"
                    "  Chg_Type, Settle, Reason, Chg_Date, EP_ID, Lot_ID)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, hid, it["Purchase_num"], it["ord_P_ID"], it["P_ID"],
                     it["ord_qty"], it["in_qty"], it["ord_amt"], it["amount"],
                     it["diff_amt"], it["chg_type"], plan["settle"],
                     it["reason"] or plan["settle_note"] or None,
                     date, ep, lot))
                it["Chg_ID"] = cid
    return plan, []
# ── 불출 요청 (생산 → 자재) ──────────────────────────────────
#
# 실제 흐름은 이렇다.
#   고객 주문 → 생산 일정 수립 → 작업지시 → [자재 불출 요청] → 자재팀이 FIFO 로
#   준비해 현장 전달 → 생산 → 생산 실적
#
# ⚠️ 요청 근거는 '생산 실적'이 아니라 '생산 계획(목표 대수)'이다.
#    실적은 생산이 끝나야 쌓인다. 실적 기준으로 자재를 요청하면 이미 써버린
#    자재를 나중에 요청하는 시간 역전이 생긴다. 자재는 생산 전에 현장에 가 있어야 한다.
#
# 시스템이 BOM 으로 초안을 만들고 작업자가 확정한다(반자동).
#   완전 수동 — 작업자가 BOM 을 외워 입력해야 해서 누락·오입력이 잦다.
#   완전 자동 — 불량 여유분·현장 잔여 자재 같은 변수를 못 담고,
#              잘못된 요청이 검토 없이 그대로 창고를 비운다.
#   반자동   — 산출 근거(BOM 소요량·현재고)가 요청에 남아 자재팀이 검증할 수 있다.

REQ_STATUS = ("요청", "승인", "반려", "일부불출", "불출완료", "취소")

# 요청은 곧바로 창고를 비우지 못한다. 자재팀 승인이 관문이다.
#
#   요청 ─┬─ 승인 ─→ 일부불출 ─→ 불출완료
#         ├─ 반려          (사유 필수. 되돌릴 수 없다)
#         └─ 취소          (요청자 스스로 거둠)
#
# 승인 없이 불출하면 생산이 올린 숫자가 그대로 재고를 깎는다.
# 요청 근거(소요량·현장·재고)를 자재팀이 한 번 보고 수량을 조정할 자리가 필요하다.

# 직급별 승인 한도. 금액이 크면 윗선이 봐야 한다.
# User_tb 에 부서가 없어 직급으로만 가른다 — 있는 데이터로 세울 수 있는 선이다.
APPROVAL_LIMIT = {"사원": 0, "주임": 0, "대리": 3000000,
                  "과장": 10000000, "차장": 30000000, "부장": None}  # None = 무제한
_LIMIT_DEFAULT = 0      # 모르는 직급은 권한 없음으로 본다 (열어두면 통제가 아니다)


def approval_limit(position):
    """직급의 승인 한도. None 이면 무제한, 0 이면 승인 권한 없음."""
    return APPROVAL_LIMIT.get(position, _LIMIT_DEFAULT)


def next_req_id(conn, date):
    """Req_ID 채번 — REQ + YYYYMMDD + 4자리."""
    pre = "REQ" + date.replace("-", "")
    last = conn.execute(
        "SELECT MAX(Req_ID) FROM Disburse_Req_tb WHERE Req_ID LIKE ?",
        (pre + "%",)).fetchone()[0]
    seq = int(last[-4:]) + 1 if last else 1
    return "%s%04d" % (pre, seq)


def next_wo_id(conn, date):
    """작업지시번호 채번 — WO + YYYYMMDD + 4자리.

    생산 실적(Production_tb)과 요청(Disburse_Req_tb) 양쪽을 봐야 한다.
    실적이 아직 없는 작업지시라도 번호는 이미 나가 있기 때문이다.
    """
    pre = "WO" + date.replace("-", "")
    a = conn.execute("SELECT MAX(Work_Order) FROM Production_tb WHERE Work_Order LIKE ?",
                     (pre + "%",)).fetchone()[0]
    b = conn.execute("SELECT MAX(Work_Order) FROM Disburse_Req_tb WHERE Work_Order LIKE ?",
                     (pre + "%",)).fetchone()[0]
    last = max(x for x in (a, b, "") if x is not None)
    seq = int(last[-4:]) + 1 if last else 1
    return "%s%04d" % (pre, seq)


def pkg_options(need, pkg, span=3):
    """요청 가능한 포장단위 배수 후보와 기본값.

    입고가 상자 단위로 들어오므로 불출도 상자 단위로 맞추는 것이 기본이다.
    낱개로 뜯으면 남은 수량의 관리 주체가 애매해진다.
    기본값은 소요량을 올린 배수이고, 앞뒤 몇 개를 더 골라 쓸 수 있게 한다.
    """
    need = max(int(need or 0), 0)
    if not pkg or pkg <= 0:
        return ([need] if need else []), need
    base = -(-need // pkg) * pkg if need else pkg     # 올림
    b = base // pkg
    opts = sorted({k * pkg for k in range(max(1, b - 1), b + span)})
    return opts, base


# 현장(생산라인) 보유 재고.
#
# Location_tb 에는 자재창고 5개뿐이고 현장창고는 없다. 그래서 창고 재고처럼
# 조회할 수 없고 거래에서 역산한다.
#
#   현장 재고 = 불출(창고→현장) − 생산 투입(현장에서 소비) − 반납(현장→창고)
#
# 실측 검증: 200종 전수에서 음수가 0건이다. 불출보다 투입이 많은 모순이 없다는 뜻이라
# 이 식이 데이터와 정합한다고 볼 수 있다.
SITE_STOCK_SQL = """
    SELECT p.P_ID,
           COALESCE(o.q, 0) - COALESCE(u.q, 0) - COALESCE(r.q, 0) AS site
      FROM Product_tb p
      LEFT JOIN (SELECT l.P_ID, SUM(t.T_Num) q
                   FROM Transaction_tb t JOIN Lot_tb l ON t.Lot_ID = l.Lot_ID
                  WHERE {demand} GROUP BY l.P_ID) o ON p.P_ID = o.P_ID
      LEFT JOIN (SELECT P_ID, SUM(Prod_Qty) q FROM Production_tb GROUP BY P_ID) u
             ON p.P_ID = u.P_ID
      LEFT JOIN (SELECT l.P_ID, SUM(t.T_Num) q
                   FROM Transaction_tb t JOIN Lot_tb l ON t.Lot_ID = l.Lot_ID
                  WHERE t.T_Type IN ({ret}) GROUP BY l.P_ID) r ON p.P_ID = r.P_ID
"""


def site_stock(conn):
    """품번별 현장 보유 수량. 음수는 0 으로 막는다(데이터 모순 방어)."""
    sql = SITE_STOCK_SQL.format(demand=DEMAND_T, ret=_inlist(TX_PLUS))
    return {r["P_ID"]: max(r["site"] or 0, 0) for r in _rows(conn, sql)}


def request_source(conn):
    """불출 요청 화면의 기준 데이터 — 완제품·담당자·기준일."""
    base = conn.execute("SELECT MAX(T_Date) FROM Transaction_tb").fetchone()[0]
    brows = bom_rows(conn)
    fgs = fg_list(conn, brows)
    # 화면이 자체 계산할 수 있도록 표준 BOM 만 내려보낸다 (대체품은 자재팀 판단)
    site = site_stock(conn)
    bom = [{"FG_ID": b["FG_ID"], "P_ID": b["P_ID"], "P_N": b["P_N"], "Spec": b["Spec"],
            "BOM_Qty": b["BOM_Qty"], "Unit": b["Unit"], "stock": b["stock"],
            "site": site.get(b["P_ID"], 0),
            "PkgUnit": b["PkgUnit"], "grade": b["grade"], "price": b["P_Price"],
            "lead_time": b["lead_time"]}
           for b in brows if not b["is_alt"]]
    workers = _rows(conn, "SELECT EP_ID, Name, Position FROM User_tb ORDER BY Name")
    return {"base": base, "fgs": fgs, "bom": bom, "workers": workers,
            "next_wo": next_wo_id(conn, _add_days(base, 1))}


def request_plan(conn, fg_id, plan_qty):
    """생산 목표 대수로 자재 소요량을 산출한다 (요청 초안).

    소요량 = 표준 BOM 소요량 × 목표 대수.
    현장에 남아 있는 자재는 빼지 않는다 — 자재창고 시스템은 현장 재고를 모른다.
    """
    fg = conn.execute("SELECT FG_ID, FG_N FROM FG_tb WHERE FG_ID = ?", (fg_id,)).fetchone()
    if fg is None:
        return None, ["등록되지 않은 완제품입니다: %s" % (fg_id or "(빈값)")]
    try:
        plan_qty = int(plan_qty or 0)
    except (TypeError, ValueError):
        return None, ["생산 목표 수량이 숫자가 아닙니다."]
    if plan_qty <= 0:
        return None, ["생산 목표 수량은 1 이상이어야 합니다."]

    site = site_stock(conn)
    rows = []
    for b in bom_rows(conn):
        if b["FG_ID"] != fg_id or b["is_alt"]:
            continue
        need = (b["BOM_Qty"] or 0) * plan_qty
        on_site = site.get(b["P_ID"], 0)
        # 현장에 이미 있는 만큼은 다시 내보낼 필요가 없다
        net = max(need - on_site, 0)
        if net > 0:
            opts, base = pkg_options(net, b["PkgUnit"])
        else:
            # 요청할 게 없어도 후보는 남겨 둔다 — 여유분을 더 받고 싶을 수 있다
            opts, _ = pkg_options(b["PkgUnit"] or 1, b["PkgUnit"])
            base = 0
        rows.append({
            "P_ID": b["P_ID"], "P_N": b["P_N"], "Spec": b["Spec"],
            "grade": b["grade"], "unit": b["Unit"],
            "bom_qty": b["BOM_Qty"], "need_qty": need,
            "site_qty": on_site, "net_need": net,
            "stock": b["stock"], "pkg": b["PkgUnit"],
            "options": opts, "req_qty": base,
            "short": max(net - b["stock"], 0),
            "price": b["P_Price"], "lead_time": b["lead_time"],
        })
    rows.sort(key=lambda r: r["P_ID"])
    return {"FG_ID": fg["FG_ID"], "FG_N": fg["FG_N"], "plan_qty": plan_qty,
            "items": rows, "line_cnt": len(rows),
            "short_cnt": sum(1 for r in rows if r["short"] > 0),
            "covered_cnt": sum(1 for r in rows if r["net_need"] == 0),
            "site_qty": sum(r["site_qty"] for r in rows)}, []


def preview_request(conn, date, fg_id, plan_qty, items, ep_id, wo=None, note=None):
    """요청 등록 전 검증 + 합계 산출.

    화면이 보낸 수량만 받고 소요량·현재고는 서버가 다시 계산한다.
    근거 숫자를 화면에서 받으면 요청서에 사실과 다른 근거가 박힐 수 있다.
    """
    plan, errors = request_plan(conn, fg_id, plan_qty)
    if errors:
        return None, errors

    ep_id = str(ep_id or "").strip()
    if not ep_id:
        return None, ["요청자를 선택하세요."]
    w = conn.execute("SELECT EP_ID, Name, Position FROM User_tb WHERE EP_ID = ?",
                     (ep_id,)).fetchone()
    if w is None:
        return None, ["등록되지 않은 사원번호입니다: %s" % ep_id]

    by_pid = {r["P_ID"]: r for r in plan["items"]}
    want, seen = {}, set()
    for it in (items or []):
        pid = str(it.get("P_ID") or "").strip()
        if pid not in by_pid:
            return None, ["이 완제품의 BOM 에 없는 품번입니다: %s" % (pid or "(빈값)")]
        if pid in seen:
            return None, ["같은 품번이 두 번 들어왔습니다: %s" % pid]
        seen.add(pid)
        try:
            q = int(it.get("qty") or 0)
        except (TypeError, ValueError):
            return None, ["%s 요청 수량이 숫자가 아닙니다." % pid]
        if q < 0:
            return None, ["%s 요청 수량은 0 이상이어야 합니다." % pid]
        if q:
            want[pid] = (q, bool(it.get("manual")))

    rows = []
    for r in plan["items"]:
        if r["P_ID"] not in want:
            continue
        q, manual = want[r["P_ID"]]
        pkg = r["pkg"] or 0
        off_pkg = bool(pkg) and q % pkg != 0
        if off_pkg and not manual:
            # 포장단위를 벗어나려면 '직접 입력'을 켜야 한다. 실수로 낱개가 나가는 걸 막는다.
            return None, ["%s 요청 수량 %s개는 포장단위 %d의 배수가 아닙니다. "
                          "직접 입력으로 전환하세요." % (r["P_ID"], format(q, ","), pkg)]
        d = dict(r)
        d["req_qty"] = q
        d["manual"] = bool(manual and off_pkg)
        d["boxes"] = (q // pkg) if pkg and not off_pkg else None
        d["short"] = max(q - r["stock"], 0)
        d["amount"] = int(round(q * (r["price"] or 0)))
        rows.append(d)

    if not rows:
        return None, ["요청할 자재가 없습니다. 수량을 1 이상으로 지정하세요."]

    plan["items"] = rows
    plan["line_cnt"] = len(rows)
    plan["req_qty"] = sum(r["req_qty"] for r in rows)
    plan["amount"] = sum(r["amount"] for r in rows)
    plan["short_cnt"] = sum(1 for r in rows if r["short"] > 0)
    plan["manual_cnt"] = sum(1 for r in rows if r["manual"])
    plan["site_qty"] = sum(r["site_qty"] for r in rows)
    plan["need_qty"] = sum(r["need_qty"] for r in rows)
    plan["date"] = date
    plan["Work_Order"] = (wo or "").strip() or next_wo_id(conn, date)
    plan["worker"] = {"EP_ID": w["EP_ID"], "Name": w["Name"], "Position": w["Position"]}
    plan["note"] = (note or "").strip()
    return plan, []


def create_request(conn, date, fg_id, plan_qty, items, ep_id, wo=None, note=None):
    """불출 요청 등록. 요청서 1건 + 품목 N행."""
    plan, errors = preview_request(conn, date, fg_id, plan_qty, items, ep_id, wo, note)
    if errors:
        return None, errors

    with conn:
        rid = next_req_id(conn, date)
        conn.execute(
            "INSERT INTO Disburse_Req_tb"
            " (Req_ID, Req_Date, FG_ID, Plan_Qty, Work_Order, EP_ID, Status, Note)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, date, plan["FG_ID"], plan["plan_qty"], plan["Work_Order"],
             plan["worker"]["EP_ID"], "요청", plan["note"] or None))
        for n, it in enumerate(plan["items"], 1):
            conn.execute(
                "INSERT INTO Disburse_Req_Item_tb"
                " (Req_ID, Req_num, P_ID, Need_Qty, Site_Qty, Stock_Qty, Req_Qty,"
                "  Pkg_Unit, Is_Manual, Done_Qty)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (rid, n, it["P_ID"], it["need_qty"], it["site_qty"], it["stock"],
                 it["req_qty"], it["pkg"], "Y" if it["manual"] else "N"))
            it["Req_num"] = n
        plan["Req_ID"] = rid
    return plan, []


def request_list(conn, limit=60):
    """요청 현황 — 최근 요청서와 진행률."""
    reqs = _rows(conn, """
        SELECT r.Req_ID, r.Req_Date, r.FG_ID, f.FG_N, r.Plan_Qty, r.Work_Order,
               r.EP_ID, u.Name AS worker, u.Position AS pos, r.Status, r.Note,
               r.Appr_EP_ID, r.Appr_Date, r.Appr_Note,
               a.Name AS approver, a.Position AS appr_pos,
               COUNT(i.Req_num)        AS line_cnt,
               SUM(i.Req_Qty)          AS req_qty,
               SUM(COALESCE(i.Appr_Qty, i.Req_Qty)) AS eff_qty,
               SUM(i.Appr_Qty)         AS appr_qty,
               SUM(i.Done_Qty)         AS done_qty,
               SUM(CASE WHEN i.Is_Manual = 'Y' THEN 1 ELSE 0 END) AS manual_cnt,
               SUM(CASE WHEN i.Appr_Qty IS NOT NULL
                         AND i.Appr_Qty <> i.Req_Qty THEN 1 ELSE 0 END) AS cut_cnt
          FROM Disburse_Req_tb r
          LEFT JOIN FG_tb f   ON r.FG_ID = f.FG_ID
          LEFT JOIN User_tb u ON r.EP_ID = u.EP_ID
          LEFT JOIN User_tb a ON r.Appr_EP_ID = a.EP_ID
          LEFT JOIN Disburse_Req_Item_tb i ON i.Req_ID = r.Req_ID
         GROUP BY r.Req_ID
         ORDER BY r.Req_Date DESC, r.Req_ID DESC
         LIMIT ?
    """, (limit,))
    items = {}
    for r in _rows(conn, f"""
        WITH stock AS ({STOCK_SQL})
        SELECT i.*, p.P_N, p.Spec, p.P_Price,
               COALESCE(st.stock, 0) AS stock, s.Sf_Lv AS grade
          FROM Disburse_Req_Item_tb i
          JOIN Product_tb p    ON i.P_ID = p.P_ID
          LEFT JOIN stock st   ON i.P_ID = st.P_ID
          LEFT JOIN Safe_tb s  ON i.P_ID = s.P_ID
         ORDER BY i.Req_ID, i.Req_num
    """):
        # 불출 가능량의 기준은 승인 수량이다. 아직 미승인이면 요청 수량으로 본다.
        r["eff_qty"] = r["Req_Qty"] if r["Appr_Qty"] is None else r["Appr_Qty"]
        r["cut"] = r["Appr_Qty"] is not None and r["Appr_Qty"] != r["Req_Qty"]
        r["left_qty"] = max((r["eff_qty"] or 0) - (r["Done_Qty"] or 0), 0)
        r["short"] = max(r["left_qty"] - r["stock"], 0)
        r["amount"] = int(round((r["eff_qty"] or 0) * (r["P_Price"] or 0)))
        r["req_amount"] = int(round((r["Req_Qty"] or 0) * (r["P_Price"] or 0)))
        items.setdefault(r["Req_ID"], []).append(r)
    for r in reqs:
        r["items"] = items.get(r["Req_ID"], [])
        r["left_qty"] = sum(x["left_qty"] for x in r["items"])
        r["amount"] = sum(x["amount"] for x in r["items"])
        r["req_amount"] = sum(x["req_amount"] for x in r["items"])
        r["short_cnt"] = sum(1 for x in r["items"] if x["short"] > 0)
        r["cut_cnt"] = sum(1 for x in r["items"] if x["cut"])
        r["pct"] = _pct(r["done_qty"] or 0, r["eff_qty"] or 0)
    return reqs


def wait_approval_cnt(conn):
    """승인 대기(= 요청 상태) 건수.

    불출 처리·피킹 화면이 '왜 요청 현황보다 적게 보이는가' 를 설명하는 데 쓴다.
    """
    return conn.execute(
        "SELECT COUNT(*) FROM Disburse_Req_tb WHERE Status = '요청'").fetchone()[0]


def open_requests(conn):
    """불출 처리 화면이 집어갈 요청.

    '요청' 상태는 여기 오지 않는다 — 승인을 거쳐야 창고가 열린다.
    """
    return [r for r in request_list(conn)
            if r["Status"] in ("승인", "일부불출") and r["left_qty"] > 0]


def _refresh_req_status(conn, rid):
    """요청 품목의 불출 누계로 요청서 상태를 다시 매긴다.

    완료 판정 기준은 요청 수량이 아니라 **승인 수량**이다. 자재팀이 깎은 만큼만
    나가면 그 요청은 끝난 것이고, 요청 수량을 기준으로 삼으면 영원히 미완으로 남는다.
    """
    row = conn.execute(
        "SELECT SUM(COALESCE(Appr_Qty, Req_Qty)), SUM(Done_Qty),"
        "       SUM(CASE WHEN Appr_Qty IS NULL THEN 0 ELSE 1 END)"
        "  FROM Disburse_Req_Item_tb WHERE Req_ID = ?", (rid,)).fetchone()
    eff, done, appr = (row[0] or 0), (row[1] or 0), (row[2] or 0)
    if done > 0 and done >= eff:
        st = "불출완료"
    elif done > 0:
        st = "일부불출"
    else:
        st = "승인" if appr else "요청"
    conn.execute("UPDATE Disburse_Req_tb SET Status = ? WHERE Req_ID = ?", (st, rid))
    return st


def cancel_request(conn, rid, ep_id=None):
    """요청 취소. 이미 일부라도 불출됐으면 취소하지 않는다."""
    r = conn.execute("SELECT Status FROM Disburse_Req_tb WHERE Req_ID = ?", (rid,)).fetchone()
    if r is None:
        return None, ["등록되지 않은 요청번호입니다: %s" % (rid or "(빈값)")]
    if r["Status"] in ("취소", "반려"):
        return None, ["이미 %s된 요청입니다." % r["Status"]]
    done = conn.execute(
        "SELECT COALESCE(SUM(Done_Qty),0) FROM Disburse_Req_Item_tb WHERE Req_ID = ?",
        (rid,)).fetchone()[0]
    if done:
        return None, ["이미 %s개가 불출된 요청이라 취소할 수 없습니다." % format(done, ",")]
    with conn:
        conn.execute("UPDATE Disburse_Req_tb SET Status = '취소' WHERE Req_ID = ?", (rid,))
    return {"Req_ID": rid, "Status": "취소"}, []
# ── 불출 승인 ───────────────────────────────────────────────
#
# 생산이 올린 요청을 자재팀이 한 번 보고 통과시키는 자리다.
# 여기가 없으면 생산이 입력한 숫자가 검토 없이 그대로 창고를 깎는다.
#
# 승인자는 라인별로 수량을 깎을 수 있다(부분 승인). 요청보다 늘리지는 못한다 —
# 늘려야 하면 근거(BOM 소요량·현장 보유)가 바뀐 것이므로 새 요청으로 올려야 한다.
#
# 통제 두 가지
#   자기결재 금지  요청자와 승인자가 같을 수 없다
#   금액 한도      직급별 한도를 넘으면 윗선이 처리한다 (APPROVAL_LIMIT)


def _worker(conn, ep_id):
    """담당자 검증 — 등록 여부만 본다.

    _approver 와 달리 직급을 묻지 않는다. 등록·기록하는 일에까지 한도를
    걸면 일이 안 돈다. 되돌릴 수 없는 쪽(승인·삭제·되돌리기)만 _approver 를 쓴다.
    """
    ep_id = str(ep_id or "").strip()
    if not ep_id:
        return None, ["담당자를 선택하세요."]
    r = conn.execute(
        "SELECT EP_ID, Name, Position FROM User_tb WHERE EP_ID = ?", (ep_id,)).fetchone()
    if r is None:
        return None, ["등록되지 않은 담당자입니다: %s" % ep_id]
    return {"EP_ID": r["EP_ID"], "Name": r["Name"], "Position": r["Position"]}, []


def _approver(conn, ep_id, amount=None, what="불출 승인"):
    """승인자 검증 — 등록 여부 · 승인 권한 · 금액 한도.

    안전재고 되돌리기도 같은 직급 기준을 쓴다. what 으로 문구만 바꾼다.
    """
    ep_id = str(ep_id or "").strip()
    if not ep_id:
        return None, ["승인자를 선택하세요."]
    w = conn.execute(
        "SELECT EP_ID, Name, Position FROM User_tb WHERE EP_ID = ?", (ep_id,)).fetchone()
    if w is None:
        return None, ["등록되지 않은 담당자입니다: %s" % ep_id]
    lim = approval_limit(w["Position"])
    if lim == 0:
        return None, ["%s %s 직급은 %s 권한이 없습니다." % (w["Name"], w["Position"], what)]
    if lim is not None and amount is not None and amount > lim:
        return None, ["%s %s의 승인 한도(%s원)를 넘습니다. 승인 금액 %s원 — 상위 직급이 처리해야 합니다."
                      % (w["Name"], w["Position"], format(lim, ","), format(int(amount), ","))]
    return {"EP_ID": w["EP_ID"], "Name": w["Name"], "Position": w["Position"],
            "limit": lim}, []


def _req_for_approval(conn, rid):
    """승인 대상 요청서를 꺼낸다. 이미 처리된 건은 여기서 막는다."""
    rid = str(rid or "").strip()
    r = conn.execute("""
        SELECT r.*, f.FG_N, u.Name AS worker, u.Position AS pos
          FROM Disburse_Req_tb r
          LEFT JOIN FG_tb f   ON r.FG_ID = f.FG_ID
          LEFT JOIN User_tb u ON r.EP_ID = u.EP_ID
         WHERE r.Req_ID = ?
    """, (rid,)).fetchone()
    if r is None:
        return None, ["등록되지 않은 요청번호입니다: %s" % (rid or "(빈값)")]
    if r["Status"] != "요청":
        return None, ["%s 상태의 요청은 승인 대상이 아닙니다: %s" % (r["Status"], rid)]
    return dict(r), []


def preview_approval(conn, rid, ep_id, lines=None, note=None):
    """승인 미리보기 — 라인별 승인 수량을 확정하고 금액·한도를 검증한다.

    lines 는 {Req_num: 승인수량}. 주지 않은 라인은 요청 수량 그대로 승인한다.
    approve_request 가 저장 직전에 이 함수를 다시 불러 재검증한다.
    """
    req, errors = _req_for_approval(conn, rid)
    if errors:
        return None, errors

    want = {}
    for k, v in (lines or {}).items():
        try:
            want[int(k)] = int(v)
        except (TypeError, ValueError):
            return None, ["승인 수량이 숫자가 아닙니다: %s" % k]

    rows = _rows(conn, f"""
        WITH stock AS ({STOCK_SQL})
        SELECT i.*, p.P_N, p.Spec, p.P_Price, s.Sf_Lv AS grade, s.Sf_Num AS safe_qty,
               COALESCE(st.stock, 0) AS stock
          FROM Disburse_Req_Item_tb i
          JOIN Product_tb p   ON i.P_ID = p.P_ID
          LEFT JOIN stock st  ON i.P_ID = st.P_ID
          LEFT JOIN Safe_tb s ON i.P_ID = s.P_ID
         WHERE i.Req_ID = ?
         ORDER BY i.Req_num
    """, (rid,))
    if not rows:
        return None, ["품목이 없는 요청입니다: %s" % rid]

    known = {r["Req_num"] for r in rows}
    for n in want:
        if n not in known:
            return None, ["요청에 없는 라인 번호입니다: #%d" % n]

    items, amount, cut_cnt, short_cnt = [], 0, 0, 0
    for r in rows:
        q = want.get(r["Req_num"], r["Req_Qty"])
        if q < 0:
            return None, ["승인 수량은 음수일 수 없습니다: #%d %s" % (r["Req_num"], r["P_ID"])]
        if q > r["Req_Qty"]:
            return None, ["요청 수량보다 많이 승인할 수 없습니다: #%d %s (요청 %s / 승인 %s)"
                          % (r["Req_num"], r["P_ID"],
                             format(r["Req_Qty"], ","), format(q, ","))]
        amt = int(round(q * (r["P_Price"] or 0)))
        short = max(q - r["stock"], 0)
        cut = q != r["Req_Qty"]
        cut_cnt += 1 if cut else 0
        short_cnt += 1 if short > 0 else 0
        amount += amt
        items.append({
            "Req_num": r["Req_num"], "P_ID": r["P_ID"], "P_N": r["P_N"],
            "Spec": r["Spec"], "grade": r["grade"], "price": r["P_Price"],
            "need_qty": r["Need_Qty"], "site_qty": r["Site_Qty"],
            "stock_at_req": r["Stock_Qty"], "stock": r["stock"],
            "pkg": r["Pkg_Unit"], "manual": r["Is_Manual"] == "Y",
            "req_qty": r["Req_Qty"], "appr_qty": q, "cut": cut,
            "diff": q - r["Req_Qty"], "short": short, "amount": amt,
            "safe_qty": r["safe_qty"],
            "below_safe": bool(r["safe_qty"] and r["stock"] - q < r["safe_qty"]),
        })

    if amount <= 0 or not any(it["appr_qty"] > 0 for it in items):
        return None, ["승인 수량이 전부 0 입니다. 전량 거절이면 반려로 처리하세요."]

    appr, errors = _approver(conn, ep_id, amount)
    if errors:
        return None, errors
    if appr["EP_ID"] == req["EP_ID"]:
        return None, ["요청자 본인은 승인할 수 없습니다: %s %s (자기결재 금지)"
                      % (req["worker"] or "", req["pos"] or "")]

    return {
        "Req_ID": req["Req_ID"], "Req_Date": req["Req_Date"],
        "FG_ID": req["FG_ID"], "FG_N": req["FG_N"], "Plan_Qty": req["Plan_Qty"],
        "Work_Order": req["Work_Order"], "Note": req["Note"],
        "requester": {"EP_ID": req["EP_ID"], "Name": req["worker"], "Position": req["pos"]},
        "approver": appr, "appr_note": (note or "").strip() or None,
        "items": items, "line_cnt": len(items),
        "req_qty": sum(it["req_qty"] for it in items),
        "appr_qty": sum(it["appr_qty"] for it in items),
        "req_amount": sum(int(round(it["req_qty"] * (it["price"] or 0))) for it in items),
        "amount": amount, "cut_cnt": cut_cnt, "short_cnt": short_cnt,
        "Status": "승인",
    }, []


def approve_request(conn, date, rid, ep_id, lines=None, note=None):
    """불출 승인 — 라인별 승인 수량을 확정하고 요청서를 승인 상태로 넘긴다."""
    plan, errors = preview_approval(conn, rid, ep_id, lines, note)
    if errors:
        return None, errors
    with conn:                                   # 요청서와 라인이 함께 넘어간다
        for it in plan["items"]:
            conn.execute(
                "UPDATE Disburse_Req_Item_tb SET Appr_Qty = ?"
                " WHERE Req_ID = ? AND Req_num = ?",
                (it["appr_qty"], rid, it["Req_num"]))
        conn.execute(
            "UPDATE Disburse_Req_tb"
            "   SET Status = ?, Appr_EP_ID = ?, Appr_Date = ?, Appr_Note = ?"
            " WHERE Req_ID = ?",
            ("승인", plan["approver"]["EP_ID"], date, plan["appr_note"], rid))
    plan["Appr_Date"] = date
    return plan, []


def reject_request(conn, date, rid, ep_id, reason=None):
    """불출 반려. 사유 없이는 반려하지 않는다 — 생산이 무엇을 고쳐 올릴지 알아야 한다."""
    req, errors = _req_for_approval(conn, rid)
    if errors:
        return None, errors
    reason = (reason or "").strip()
    if not reason:
        return None, ["반려 사유를 입력하세요. 생산이 무엇을 고쳐 다시 올릴지 알 수 없습니다."]

    appr, errors = _approver(conn, ep_id)        # 반려는 금액 한도를 보지 않는다
    if errors:
        return None, errors
    if appr["EP_ID"] == req["EP_ID"]:
        return None, ["요청자 본인은 반려할 수 없습니다. 본인 요청은 취소로 거두세요."]

    with conn:
        conn.execute(
            "UPDATE Disburse_Req_tb"
            "   SET Status = ?, Appr_EP_ID = ?, Appr_Date = ?, Appr_Note = ?"
            " WHERE Req_ID = ?",
            ("반려", appr["EP_ID"], date, reason, rid))
    return {"Req_ID": rid, "Status": "반려", "approver": appr,
            "Appr_Date": date, "reason": reason,
            "requester": {"EP_ID": req["EP_ID"], "Name": req["worker"],
                          "Position": req["pos"]}}, []


def approval_queue(conn):
    """승인 화면 데이터 — 대기 목록 · 처리 이력 · 승인 가능한 담당자."""
    reqs = request_list(conn)
    waiting = [r for r in reqs if r["Status"] == "요청"]
    decided = [r for r in reqs if r["Appr_EP_ID"]]

    # 승인 권한이 있는 담당자만 내려보낸다. 한도를 함께 줘서 화면이 미리 거른다.
    approvers = []
    for u in _rows(conn, "SELECT EP_ID, Name, Position FROM User_tb ORDER BY Name"):
        lim = approval_limit(u["Position"])
        if lim == 0:
            continue
        approvers.append({"EP_ID": u["EP_ID"], "Name": u["Name"],
                          "Position": u["Position"], "limit": lim})
    approvers.sort(key=lambda a: (a["limit"] is not None, -(a["limit"] or 0)))

    base = conn.execute("SELECT MAX(T_Date) FROM Transaction_tb").fetchone()[0]
    # 승인일은 기준일 다음 날이다. 요청일보다 앞서면 결재선 날짜가 거꾸로 붙는다.
    entry = max(_add_days(base, 1), max((r["Req_Date"] for r in waiting), default=""))
    return {
        "base": base, "entry": entry,
        "waiting": waiting, "decided": decided[:40], "approvers": approvers,
        "limits": [{"pos": k, "limit": v} for k, v in APPROVAL_LIMIT.items()],
        "wait_cnt": len(waiting),
        "wait_amt": sum(r["amount"] for r in waiting),
        "wait_line": sum(r["line_cnt"] or 0 for r in waiting),
        "short_cnt": sum(1 for r in waiting if r["short_cnt"]),
        "appr_cnt": sum(1 for r in reqs if r["Status"] in ("승인", "일부불출", "불출완료")),
        "rej_cnt": sum(1 for r in reqs if r["Status"] == "반려"),
    }


# ── 입고 클레임 (불량 · 반품 · 대체) ─────────────────────────
#
# 물건이 실제로 창고에 왔으면 일단 입고한다. 불량이라고 LOT 을 안 만들면
# 물리적으로 존재하는 물건이 시스템에 없어지고 반품 이력도 남길 데가 없다.
#
#   ① 입고 검수  실입고 100 → LOT 생성(물리적 사실)
#                그중 불량 20 → '불량' 거래로 차감 → 가용재고 80
#                동시에 클레임 접수
#   ② 처리 결정  대체입고 / 환불 / 폐기
#   ③ 대체품 입고 새 LOT 생성 + 원 발주에 연결 + 클레임 종결
#
# ⚠️ 반품 거래 유형을 따로 만들지 않는다.
#    반품은 이미 '불량' 으로 재고에서 빠진 물건을 물리적으로 내보내는 것이라
#    거래를 또 남기면 출고가 이중 집계된다. 반품·대체·환불은 클레임의 '상태'다.
#    덕분에 9차 회의에서 확정한 T_Type 7종을 건드리지 않아도 된다.

CLAIM_TYPES = ("입고검수", "사용중발견")
CLAIM_RESOLUTIONS = ("대체입고", "환불", "폐기", "미정")
CLAIM_STATUS = ("접수", "완료")

# 불량 판정 유형 — 유형명을 코드에 박지 않고 TX_TYPES 에서 끌어온다
TX_DEFECT = next((t for t, sg, _, _, k in TX_TYPES if k == "bad" and sg < 0), "불량")


def next_claim_id(conn, date):
    """Claim_ID 채번 — RMA + YYYYMMDD + 4자리."""
    pre = "RMA" + date.replace("-", "")
    last = conn.execute(
        "SELECT MAX(Claim_ID) FROM Inbound_Claim_tb WHERE Claim_ID LIKE ?",
        (pre + "%",)).fetchone()[0]
    seq = int(last[-4:]) + 1 if last else 1
    return "%s%04d" % (pre, seq)


def _lot_remain(conn, lot_id):
    """LOT 하나의 잔량. 클레임 수량이 잔량을 넘지 못하게 막는 데 쓴다."""
    row = conn.execute("""
        SELECT l.P_Qty - COALESCE(x.out_qty, 0) AS remain, l.P_ID, l.H_ID, l.Lot_Date
          FROM Lot_tb l
          LEFT JOIN (SELECT Lot_ID, %s AS out_qty FROM Transaction_tb GROUP BY Lot_ID) x
                 ON x.Lot_ID = l.Lot_ID
         WHERE l.Lot_ID = ?
    """ % LOT_DELTA, (lot_id,)).fetchone()
    return dict(row) if row else None


def _write_claim(conn, date, lot_id, qty, ep_id, ctype, resolution, reason, price):
    """불량 판정 거래 + 클레임 1건을 남긴다 (트랜잭션은 호출자가 연다)."""
    tid = next_tx_id(conn, date)
    conn.execute(
        "INSERT INTO Transaction_tb (T_ID, Lot_ID, T_Type, T_Date, T_Num, EP_ID)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (tid, lot_id, TX_DEFECT, date, qty, ep_id))
    lot = _lot_remain(conn, lot_id)
    cid = next_claim_id(conn, date)
    conn.execute(
        "INSERT INTO Inbound_Claim_tb"
        " (Claim_ID, Lot_ID, H_ID, P_ID, Claim_Qty, Claim_Type, Resolution,"
        "  Status, Claim_Date, EP_ID, Reason, T_ID, Amount)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, '접수', ?, ?, ?, ?, ?)",
        (cid, lot_id, lot["H_ID"], lot["P_ID"], qty, ctype, resolution,
         date, ep_id, reason or None, tid, int(round(qty * (price or 0)))))
    return {"Claim_ID": cid, "T_ID": tid, "Lot_ID": lot_id,
            "P_ID": lot["P_ID"], "qty": qty, "resolution": resolution}


def create_claim(conn, date, lot_id, qty, ep_id, resolution="미정", reason=None):
    """이미 입고된 LOT 에서 불량을 발견했을 때 접수한다 (사용 중 발견).

    입고 검수에서 나온 불량은 create_inbound 가 입고와 한 트랜잭션으로 함께 남긴다.
    """
    lot_id = str(lot_id or "").strip()
    lot = _lot_remain(conn, lot_id)
    if lot is None:
        return None, ["등록되지 않은 LOT 입니다: %s" % (lot_id or "(빈값)")]
    try:
        qty = int(qty or 0)
    except (TypeError, ValueError):
        return None, ["불량 수량이 숫자가 아닙니다."]
    if qty <= 0:
        return None, ["불량 수량은 1 이상이어야 합니다."]
    if qty > lot["remain"]:
        return None, ["LOT 잔량을 넘습니다. 잔량 {:,}개 / 불량 {:,}개".format(lot["remain"], qty)]

    ep_id = str(ep_id or "").strip()
    w = conn.execute("SELECT EP_ID, Name, Position FROM User_tb WHERE EP_ID = ?",
                     (ep_id,)).fetchone()
    if w is None:
        return None, ["불량 처리 담당자를 선택하세요." if not ep_id
                      else "등록되지 않은 사원번호입니다: %s" % ep_id]
    resolution = (resolution or "미정").strip()
    if resolution not in CLAIM_RESOLUTIONS:
        return None, ["처리 방법이 잘못되었습니다: %s" % resolution]

    price = conn.execute("SELECT P_Price FROM Product_tb WHERE P_ID = ?",
                         (lot["P_ID"],)).fetchone()[0]
    with conn:
        out = _write_claim(conn, date, lot_id, qty, ep_id,
                           "사용중발견", resolution, reason, price)
    out["date"] = date
    out["worker"] = {"EP_ID": w["EP_ID"], "Name": w["Name"], "Position": w["Position"]}
    return out, []


def claim_list(conn, limit=80):
    """클레임 목록 + 자재·발주·처리 상태."""
    rows = _rows(conn, """
        SELECT k.*, p.P_N, p.Spec, p.P_Price, p.MainCat,
               s.Sf_Lv AS grade,
               u.Name AS worker, u.Position AS pos,
               h.P_Date AS order_date, c.CP_N AS supplier, c.BRN,
               l.Lot_Date, lo.Loc_N AS loc_name,
               nl.Lot_Date AS new_lot_date, nl.P_Qty AS new_lot_qty
          FROM Inbound_Claim_tb k
          JOIN Product_tb p        ON k.P_ID = p.P_ID
          LEFT JOIN Safe_tb s      ON k.P_ID = s.P_ID
          LEFT JOIN User_tb u      ON k.EP_ID = u.EP_ID
          LEFT JOIN Lot_tb l       ON k.Lot_ID = l.Lot_ID
          LEFT JOIN Location_tb lo ON l.Loc_ID = lo.Loc_ID
          LEFT JOIN Lot_tb nl      ON k.New_Lot_ID = nl.Lot_ID
          LEFT JOIN Purchase_Header_tb h ON k.H_ID = h.H_ID
          LEFT JOIN Company_tb c   ON h.BRN = c.BRN
         ORDER BY k.Claim_Date DESC, k.Claim_ID DESC
         LIMIT ?
    """, (limit,))
    for r in rows:
        r["open"] = r["Status"] == "접수"
    return rows


def open_claims(conn):
    """아직 처리되지 않은 클레임."""
    return [k for k in claim_list(conn) if k["open"]]


def resolve_claim(conn, date, claim_id, resolution, ep_id, reason=None):
    """클레임을 처리한다.

    대체입고 — 협력사가 같은 자재를 다시 보내온 경우. 새 LOT 을 만들고 원 발주에 건다.
               불량으로 빠진 수량이 대체품으로 회복되므로 재고 순증감은 0 이다.
    환불     — 물건은 돌려보내고 돈으로 받는다. 재고는 회복되지 않는다.
    폐기     — 반품이 불가해 버린다. 이미 불량으로 재고에서 빠졌으므로
               추가 차감은 하지 않는다(이중 차감 방지). 상태만 남는다.
    """
    claim_id = str(claim_id or "").strip()
    row = conn.execute("SELECT * FROM Inbound_Claim_tb WHERE Claim_ID = ?",
                       (claim_id,)).fetchone()
    if row is None:
        return None, ["등록되지 않은 클레임번호입니다: %s" % (claim_id or "(빈값)")]
    k = dict(row)
    if k["Status"] == "완료":
        return None, ["이미 처리된 클레임입니다: %s (%s)" % (claim_id, k["Resolution"])]

    resolution = (resolution or "").strip()
    if resolution not in ("대체입고", "환불", "폐기"):
        return None, ["처리 방법이 잘못되었습니다: %s" % (resolution or "(빈값)")]

    ep_id = str(ep_id or "").strip()
    w = conn.execute("SELECT EP_ID, Name, Position FROM User_tb WHERE EP_ID = ?",
                     (ep_id,)).fetchone()
    if w is None:
        return None, ["처리 담당자를 선택하세요." if not ep_id
                      else "등록되지 않은 사원번호입니다: %s" % ep_id]

    out = {"Claim_ID": claim_id, "P_ID": k["P_ID"], "qty": k["Claim_Qty"],
           "resolution": resolution, "date": date, "amount": k["Amount"],
           "worker": {"EP_ID": w["EP_ID"], "Name": w["Name"], "Position": w["Position"]}}

    if resolution == "대체입고":
        loc = LOC_BY_MAINCAT.get(
            conn.execute("SELECT MainCat FROM Product_tb WHERE P_ID = ?",
                         (k["P_ID"],)).fetchone()[0])
        if not loc:
            return None, ["%s 의 보관 창고를 정할 수 없습니다 (대분류 미상)." % k["P_ID"]]
        with conn:
            lot = next_lot_id(conn, date)
            conn.execute(
                "INSERT INTO Lot_tb (Lot_ID, P_ID, Lot_Date, Loc_ID, P_Qty, EP_ID, H_ID)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (lot, k["P_ID"], date, loc, k["Claim_Qty"], ep_id, k["H_ID"]))
            tid = next_tx_id(conn, date)
            conn.execute(
                "INSERT INTO Transaction_tb (T_ID, Lot_ID, T_Type, T_Date, T_Num, EP_ID)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (tid, lot, TX_RECEIPT, date, k["Claim_Qty"], ep_id))
            conn.execute(
                "UPDATE Inbound_Claim_tb SET Status='완료', Resolution=?, New_Lot_ID=?,"
                " Done_Date=?, Reason=COALESCE(?, Reason) WHERE Claim_ID=?",
                (resolution, lot, date, reason, claim_id))
        out["New_Lot_ID"] = lot
        out["T_ID"] = tid
        out["Loc_ID"] = loc
    else:
        with conn:
            conn.execute(
                "UPDATE Inbound_Claim_tb SET Status='완료', Resolution=?, Done_Date=?,"
                " Reason=COALESCE(?, Reason) WHERE Claim_ID=?",
                (resolution, date, reason, claim_id))
    return out, []


def claim_summary(conn):
    """클레임 요약 + 협력사별 불량률(품질 지표)."""
    ks = claim_list(conn, limit=10000)
    by_sup = {}
    for k in ks:
        b = k["supplier"] or "-"
        g = by_sup.setdefault(b, {"supplier": b, "cnt": 0, "qty": 0, "amount": 0})
        g["cnt"] += 1
        g["qty"] += k["Claim_Qty"] or 0
        g["amount"] += k["Amount"] or 0
    return {
        "total": len(ks),
        "open": len([k for k in ks if k["open"]]),
        "qty": sum(k["Claim_Qty"] or 0 for k in ks),
        "amount": sum(k["Amount"] or 0 for k in ks),
        "by_resolution": {r: len([k for k in ks if k["Resolution"] == r])
                          for r in CLAIM_RESOLUTIONS},
        "by_supplier": sorted(by_sup.values(), key=lambda x: -x["qty"])[:10],
    }
# ── LOT 이벤트 타임라인 ─────────────────────────────────────
#
# 자재 목록의 LOT 이력 모달은 LOT 단위 집계(입고·불출 합계)만 보여줬다.
# 불량·반납·폐기·이동·교환, 생산 투입, 클레임, 대체품 입고가 전부 묻혔다.
#
# 전 자재의 이벤트를 화면에 미리 실으면 응답이 수 MB 가 된다
# (거래 1,075 + 생산 4,535 + 클레임). 그래서 모달을 열 때 그 자재만 조회한다.
#
# 이벤트 종류를 SQL 에 박지 않는다 — T_Type 은 TX_META 의 kind 로 분류하므로
# 새 유형이 들어와도 타임라인에 그대로 잡힌다.

# 같은 날짜 안에서의 표시 순서. 발주가 먼저고 처분이 뒤다.
_EVENT_ORDER = {"order": 0, "in": 1, "move": 2, "out": 3, "prod": 4, "bad": 5, "claim": 6}


def lot_events(conn, pid):
    """자재 하나의 전 LOT 이벤트를 시간순으로 돌려준다.

    한 LOT 의 일생: 발주 → 입고 → (불출·불량·반납·폐기·이동·교환) → 생산 투입
                    → 클레임 접수·처리 → 대체품 입고
    """
    pid = str(pid or "").strip()
    prod = conn.execute(
        "SELECT P_ID, P_N, Spec, P_Price FROM Product_tb WHERE P_ID = ?", (pid,)).fetchone()
    if prod is None:
        return None, ["등록되지 않은 품번입니다: %s" % (pid or "(빈값)")]

    lots = _rows(conn, f"""
        SELECT l.Lot_ID, l.Lot_Date, l.Loc_ID, lo.Loc_N AS loc_name, l.P_Qty,
               l.H_ID, l.EP_ID, u.Name AS receiver,
               h.P_Date AS order_date, c.CP_N AS supplier,
               CAST(julianday(l.Lot_Date) - julianday(h.P_Date) AS INT) AS lead_days,
               l.P_Qty - COALESCE(x.out_qty, 0) AS remain
          FROM Lot_tb l
          LEFT JOIN Location_tb lo ON l.Loc_ID = lo.Loc_ID
          LEFT JOIN User_tb u      ON l.EP_ID = u.EP_ID
          LEFT JOIN Purchase_Header_tb h ON l.H_ID = h.H_ID
          LEFT JOIN Company_tb c   ON h.BRN = c.BRN
          LEFT JOIN (SELECT Lot_ID, {LOT_DELTA} AS out_qty
                       FROM Transaction_tb GROUP BY Lot_ID) x ON x.Lot_ID = l.Lot_ID
         WHERE l.P_ID = ?
         ORDER BY l.Lot_Date, l.Lot_ID
    """, (pid,))
    if not lots:
        return {"P_ID": pid, "P_N": prod["P_N"], "Spec": prod["Spec"],
                "lots": [], "event_cnt": 0, "kinds": {}}, []

    ids = [l["Lot_ID"] for l in lots]
    ph = ",".join("?" * len(ids))

    # 거래 — 유형은 TX_KIND 로 분류한다 (유형명을 SQL 에 박지 않는다)
    tx = _rows(conn, """
        SELECT t.T_ID, t.Lot_ID, t.T_Type, t.T_Date, t.T_Num, t.EP_ID, u.Name AS worker
          FROM Transaction_tb t LEFT JOIN User_tb u ON t.EP_ID = u.EP_ID
         WHERE t.Lot_ID IN (%s)
         ORDER BY t.T_Date, t.T_ID
    """ % ph, tuple(ids))

    # 생산 투입
    pr = _rows(conn, """
        SELECT r.Lot_ID, r.Work_Order, r.FG_ID, f.FG_N, r.Prod_Date,
               SUM(r.Prod_Qty) AS qty, MAX(r.Note) AS note
          FROM Production_tb r LEFT JOIN FG_tb f ON r.FG_ID = f.FG_ID
         WHERE r.Lot_ID IN (%s)
         GROUP BY r.Lot_ID, r.Work_Order, r.FG_ID, f.FG_N, r.Prod_Date
         ORDER BY r.Prod_Date
    """ % ph, tuple(ids))

    # 클레임 — 이 LOT 에서 난 것과, 이 LOT 이 대체품으로 만들어진 경우 둘 다
    cl = _rows(conn, """
        SELECT k.*, u.Name AS worker
          FROM Inbound_Claim_tb k LEFT JOIN User_tb u ON k.EP_ID = u.EP_ID
         WHERE k.Lot_ID IN (%s) OR k.New_Lot_ID IN (%s)
    """ % (ph, ph), tuple(ids) * 2)

    # 발주 변경 (대체 입고로 생긴 LOT 인지)
    chg = {r["Lot_ID"]: r for r in _rows(conn, """
        SELECT Lot_ID, Ord_P_ID, In_P_ID, Chg_Type, Settle, Diff_Amt, Reason
          FROM Purchase_Change_tb WHERE Lot_ID IN (%s)
    """ % ph, tuple(ids))}

    by_lot = {i: [] for i in ids}
    kinds = {}

    def add(lot, kind, date, title, desc, qty=None, ref=None, tone=None):
        by_lot[lot].append({"kind": kind, "date": date, "title": title,
                            "desc": desc, "qty": qty, "ref": ref,
                            "tone": tone or kind})
        kinds[title] = kinds.get(title, 0) + 1

    for l in lots:
        lid = l["Lot_ID"]
        if l["order_date"]:
            ch = chg.get(lid)
            d = "%s · %s" % (l["H_ID"], l["supplier"] or "-")
            if ch and ch["In_P_ID"] != ch["Ord_P_ID"]:
                d += " · 발주품 %s → 대체 입고" % ch["Ord_P_ID"]
            elif ch:
                d += " · %s" % ch["Chg_Type"]
            add(lid, "order", l["order_date"], "발주", d, ref=l["H_ID"])

    for t in tx:
        kind = TX_KIND.get(t["T_Type"], "move")
        desc = t["worker"] or "-"
        add(t["Lot_ID"], kind, t["T_Date"], t["T_Type"], desc, t["T_Num"], t["T_ID"])

    lot_date = {l["Lot_ID"]: l["Lot_Date"] for l in lots}
    for r in pr:
        # ⚠️ 더미데이터에 생산일이 입고일보다 빠른 행이 있다(4,535 중 1,224행).
        #    물리적으로 불가능한 순서라 타임라인에 그대로 표시하고 표시만 남긴다.
        #    고치면 생산 실적·BOM 일치율 등 문서화된 수치가 흔들린다.
        warn = bool(r["Prod_Date"] and lot_date.get(r["Lot_ID"])
                    and r["Prod_Date"] < lot_date[r["Lot_ID"]])
        by_lot[r["Lot_ID"]].append({
            "kind": "prod", "date": r["Prod_Date"], "title": "생산 투입",
            "desc": "%s · %s%s" % (r["Work_Order"], r["FG_ID"] or "-",
                                   " " + (r["FG_N"] or "") if r["FG_N"] else ""),
            "qty": r["qty"], "ref": r["Work_Order"], "tone": "prod",
            "warn": "입고일(%s)보다 앞선 생산일" % lot_date.get(r["Lot_ID"]) if warn else None})
        kinds["생산 투입"] = kinds.get("생산 투입", 0) + 1

    for k in cl:
        if k["Lot_ID"] in by_lot:
            add(k["Lot_ID"], "claim", k["Claim_Date"], "클레임 접수",
                "%s · %s%s" % (k["Claim_ID"], k["Claim_Type"],
                               " · " + k["Reason"] if k["Reason"] else ""),
                k["Claim_Qty"], k["Claim_ID"])
            if k["Status"] == "완료":
                add(k["Lot_ID"], "claim", k["Done_Date"] or k["Claim_Date"],
                    "클레임 " + k["Resolution"],
                    "%s%s" % (k["Claim_ID"],
                              " · 대체 LOT " + k["New_Lot_ID"] if k["New_Lot_ID"] else
                              " · %s원 정산" % format(k["Amount"] or 0, ",")),
                    k["Claim_Qty"], k["Claim_ID"])
        if k["New_Lot_ID"] in by_lot:
            add(k["New_Lot_ID"], "claim", k["Done_Date"] or k["Claim_Date"],
                "대체품으로 입고", "%s 의 불량 %s개를 대신해 들어온 LOT (원 LOT %s)"
                % (k["Claim_ID"], format(k["Claim_Qty"], ","), k["Lot_ID"]),
                k["Claim_Qty"], k["Claim_ID"])

    total = 0
    for l in lots:
        ev = by_lot[l["Lot_ID"]]
        ev.sort(key=lambda e: (e["date"] or "", _EVENT_ORDER.get(e["kind"], 9)))
        l["events"] = ev
        l["event_cnt"] = len(ev)
        l["is_swap_in"] = any(e["title"] == "대체품으로 입고" for e in ev)
        l["has_defect"] = any(e["kind"] == "bad" for e in ev)
        l["warn_cnt"] = sum(1 for e in ev if e.get("warn"))
        total += len(ev)

    return {"P_ID": pid, "P_N": prod["P_N"], "Spec": prod["Spec"],
            "price": prod["P_Price"], "lots": lots,
            "event_cnt": total, "kinds": kinds,
            "warn_cnt": sum(l["warn_cnt"] for l in lots)}, []


# ── 자재 마스터 등록 ─────────────────────────────────────────
#
# 200종이 처음부터 DB 에 들어 있었다. 화면은 그걸 읽기만 했다 —
# 새 자재가 들어오면 손댈 데가 없었다.
#
# ⚠️ 자재 하나를 만들면 테이블 셋이 함께 움직인다.
#     Product_tb     품번·품명·분류·단가·포장           (물건 자체)
#     Safe_tb        등급·안전재고·4개 점수             (관리 기준)
#     Update_Log_tb  등록 이력 + 다음 재검토 예정일      (근거)
#
# Safe_tb 를 빼먹으면 조용히 사라진다. safety_stock_list() 가
# `FROM Safe_tb JOIN Product_tb` 라서, 안전재고·수요예측·시뮬레이터·
# 마법사·대시보드 다섯 화면에서 그 자재가 아예 안 보인다.
# 그래서 등록은 셋을 한 트랜잭션에 묶는다.

# 신규 등록 컷오프 — 4항목 만점 12점 (SAFE_STOCK_DESIGN 4절).
# 갱신 이후의 13/8/7 과 다르다. 사용 이력이 없어 Usage_Score 가 NULL 이라
# 5항목 컷오프를 쓰면 전부 C 로 떨어진다.
NEW_CUT = {"A": 10, "B": 6}
UPD_CUT = {"A": 13, "B": 8}

SCORE_KEYS = ("Price_Score", "Sub_Score", "Impact_Score", "Supply_Score")

# 화면이 체크리스트를 그릴 수 있게 판정 기준까지 내려보낸다.
# 점수를 숫자로만 받으면 "왜 3점인가" 가 사람 머릿속에만 남는다.
SCORE_META = [
    ("Price_Score",  "단가",        "단가",
     {3: "10만원 이상", 2: "1만원 이상", 1: "1만원 미만"}),
    ("Sub_Score",    "대체 가능성",  "대체품이 있는가",
     {3: "대체 불가", 2: "대체 어려움", 1: "대체 가능"}),
    ("Impact_Score", "품절 영향",    "없으면 무슨 일이 생기는가",
     {3: "생산 중단", 2: "일부 지연", 1: "영향 적음"}),
    ("Supply_Score", "공급 안정성",  "어디서 오는가",
     {3: "공급처 1곳 / 해외", 2: "공급처 2곳", 1: "공급처 다수"}),
]


def new_grade(scores, usage=None):
    """점수 합으로 등급을 매긴다.

    usage 가 없으면(신규) 4항목 10/6/5, 있으면(갱신 완료) 5항목 13/8/7.
    같은 자재라도 어느 컷오프를 쓰는지에 따라 등급이 달라지므로
    한 함수에 모아 둔다.
    """
    four = sum(int(scores.get(k) or 0) for k in SCORE_KEYS)
    if usage is None:
        cut, total = NEW_CUT, four
    else:
        cut, total = UPD_CUT, four + int(usage)
    return ("A" if total >= cut["A"] else "B" if total >= cut["B"] else "C"), total


def cat_list(conn):
    """분류 75종. 신규 품번의 앞 5자리가 여기서 나온다."""
    rows = _rows(conn, """
        SELECT c.MainCat, c.SubCat, c.DetailCat, c.Cat_Name,
               (SELECT COUNT(*) FROM Product_tb p
                 WHERE p.MainCat = c.MainCat AND p.SubCat = c.SubCat
                   AND p.DetailCat = c.DetailCat) AS n
          FROM Cat_tb c
         ORDER BY c.MainCat, c.SubCat, c.DetailCat
    """)
    for r in rows:
        r["code"] = r["MainCat"] + r["SubCat"] + r["DetailCat"]
        r["loc"] = LOC_BY_MAINCAT.get(r["MainCat"])
    return rows


def next_pid(conn, main, sub, detail, taken=()):
    """품번 채번 — 대분류(1) + 중분류(2) + 소분류(2) + 순번(4).

    taken 은 같은 요청 안에서 이미 뽑아 둔 번호다. CSV 로 같은 분류를
    여러 줄 올리면 DB 만 보고는 전부 같은 번호가 나온다.
    """
    pre = "%s%s%s" % (main, sub, detail)
    last = conn.execute(
        "SELECT MAX(CAST(SUBSTR(P_ID, 6) AS INTEGER)) FROM Product_tb"
        " WHERE P_ID LIKE ? AND LENGTH(P_ID) = 9", (pre + "%",)).fetchone()[0] or 0
    n = int(last) + 1
    while ("%s%04d" % (pre, n)) in taken:
        n += 1
    return "%s%04d" % (pre, n)


def product_source(conn):
    """자재 등록 화면이 필요로 하는 선택지."""
    base = conn.execute("SELECT MAX(T_Date) FROM Transaction_tb").fetchone()[0]
    return {
        "cats": cat_list(conn),
        "mains": _rows(conn, """
            SELECT MainCat, COUNT(*) AS n FROM Product_tb
             GROUP BY MainCat ORDER BY MainCat
        """),
        "companies": _rows(conn, """
            SELECT c.BRN, c.CP_N, c.Is_Foreign,
                   (SELECT COUNT(*) FROM Product_tb p WHERE p.BRN = c.BRN) AS n
              FROM Company_tb c ORDER BY c.CP_N
        """),
        "workers": _rows(conn, "SELECT EP_ID, Name, Position FROM User_tb ORDER BY Name"),
        "scores": [{"key": k, "label": lb, "q": q,
                    "opts": [{"v": v, "t": t} for v, t in sorted(o.items(), reverse=True)]}
                   for k, lb, q, o in SCORE_META],
        "cut": {"new": NEW_CUT, "upd": UPD_CUT},
        "cycle": REVIEW_CYCLE_DAYS,
        "loc": LOC_BY_MAINCAT,
        "base": base, "entry": _add_days(base, 1),
        "csv_cols": PRODUCT_CSV_COLS,
    }


def _josa(word, pair="은는"):
    """받침에 맞는 조사를 고른다 — '단가이(가)' 같은 문구를 안 쓰기 위해.

    오류 메시지는 칸 이름을 끼워 만든다. 조사를 괄호로 둘 다 적으면
    읽는 사람이 자기 경우를 골라야 한다.
    """
    w = str(word or "").rstrip()
    if not w:
        return pair[1]
    c = ord(w[-1])
    if 0xAC00 <= c <= 0xD7A3:                 # 한글 음절 — 종성 유무로 가른다
        return pair[0] if (c - 0xAC00) % 28 else pair[1]
    return pair[1] if w[-1] in "0123456789aeiouAEIOU" else pair[0]


def _pos_int(v, name, errors, lo=0, hi=None, required=True):
    """숫자 칸 하나를 검사한다. 빈칸·문자·음수를 전부 여기서 잡는다."""
    t = str(v if v is not None else "").strip().replace(",", "")
    eun, i_ga, eul = (_josa(name, p) for p in ("은는", "이가", "을를"))
    if t == "":
        if required:
            errors.append("%s%s 입력하세요." % (name, eul))
        return None
    try:
        n = float(t)
    except ValueError:
        errors.append("%s%s 숫자가 아닙니다: %s" % (name, i_ga, v))
        return None
    if n != int(n):
        errors.append("%s%s 정수여야 합니다: %s" % (name, eun, v))
        return None
    n = int(n)
    if n < lo:
        errors.append("%s%s %s 이상이어야 합니다: %s" % (name, eun, format(lo, ","), v))
        return None
    if hi is not None and n > hi:
        errors.append("%s%s %s 이하여야 합니다: %s" % (name, eun, format(hi, ","), v))
        return None
    return n


def _check_product(conn, body, pid=None, taken=()):
    """등록·수정이 함께 쓰는 입력 검증.

    pid 가 있으면 수정이다 — 분류는 품번에 박혀 있어 바꿀 수 없다.
    """
    e = []
    f = {}

    f["P_N"] = str(body.get("P_N") or "").strip()
    if len(f["P_N"]) < 2:
        e.append("품명을 2자 이상 입력하세요.")
    elif len(f["P_N"]) > 60:
        e.append("품명이 너무 깁니다(60자 이내).")
    f["Spec"] = str(body.get("Spec") or "").strip()[:120]

    # ⚠️ 품명만으로는 못 가린다 — 200종 중 188종이 남과 이름을 나눠 쓴다
    #    ('현대 기타' 6종 등). 실제로 유일한 건 품명 + 규격이다 (중복 0).
    #    같은 쌍을 또 넣으면 발주·불출 화면에서 어느 쪽인지 구분할 수 없다.
    if f["P_N"]:
        dup = conn.execute(
            "SELECT P_ID FROM Product_tb WHERE P_N = ? AND COALESCE(Spec,'') = ?"
            "   AND P_ID <> ?", (f["P_N"], f["Spec"], pid or "")).fetchone()
        if dup is not None:
            e.append("품명·규격이 같은 자재가 이미 있습니다: %s %s"
                     % (dup["P_ID"], f["P_N"] + (" " + f["Spec"] if f["Spec"] else "")))

    brn = str(body.get("BRN") or "").strip()
    if not brn:
        e.append("협력사를 선택하세요.")
    else:
        c = conn.execute("SELECT BRN, CP_N FROM Company_tb WHERE BRN = ?", (brn,)).fetchone()
        if c is None:
            e.append("등록되지 않은 협력사입니다: %s" % brn)
        else:
            f["BRN"] = c["BRN"]
            f["supplier"] = c["CP_N"]

    price = _pos_int(body.get("P_Price"), "단가", e, lo=1)
    if price is not None:
        f["P_Price"] = price
    # 최소 발주량·규격은 비워도 된다. 없는 자재가 실제로 있다
    f["MinOrderQty"] = _pos_int(body.get("MinOrderQty"), "최소 발주량", e,
                                lo=0, required=False) or 0
    pkg = _pos_int(body.get("PkgUnit"), "포장단위", e, lo=1)
    if pkg is not None:
        f["PkgUnit"] = pkg
    f["Lead_Time"] = _pos_int(body.get("Lead_Time"), "리드타임", e, lo=1, hi=365)

    for k, label, _q, opts in SCORE_META:
        v = _pos_int(body.get(k), label + " 점수", e, lo=1, hi=3)
        if v is not None:
            f[k] = v

    if pid is None:
        main = str(body.get("MainCat") or "").strip().upper()
        sub = str(body.get("SubCat") or "").strip()
        det = str(body.get("DetailCat") or "").strip()
        if not (main and sub and det):
            e.append("분류(대/중/소)를 모두 지정하세요.")
        elif conn.execute(
                "SELECT 1 FROM Cat_tb WHERE MainCat=? AND SubCat=? AND DetailCat=?",
                # 엑셀이 '01' 을 1 로 바꿔 저장한다. 두 자리로 되돌린다
                (main, sub.zfill(2), det.zfill(2))).fetchone() is None:
            e.append("등록되지 않은 분류입니다: %s-%s-%s" % (main, sub, det))
        else:
            sub, det = sub.zfill(2), det.zfill(2)
            f["MainCat"], f["SubCat"], f["DetailCat"] = main, sub, det
            f["P_ID"] = next_pid(conn, main, sub, det, taken)

    # 월 예상 소요량 — 신규는 불출 이력이 없어 공식의 d 를 여기서만 얻을 수 있다
    mon = _pos_int(body.get("month_qty"), "월 예상 소요량", e,
                   lo=1, required=(pid is None))
    if mon is not None:
        f["month_qty"] = mon
        f["daily"] = round(mon / 30.0, 4)

    return f, e


def preview_product(conn, body, pid=None):
    """등록 전 판정 — 품번·등급·안전재고·창고 (저장 안 함)."""
    taken = tuple(body.get("_taken") or ())
    f, e = _check_product(conn, body, pid, taken)
    if e:
        return None, e

    usage = None
    if pid:
        old = conn.execute(
            "SELECT p.*, s.Sf_Lv, s.Sf_Num, s.Lead_Time, s.Usage_Score,"
            "       s.Price_Score, s.Sub_Score, s.Impact_Score, s.Supply_Score"
            "  FROM Product_tb p LEFT JOIN Safe_tb s ON p.P_ID = s.P_ID"
            " WHERE p.P_ID = ?", (pid,)).fetchone()
        if old is None:
            return None, ["등록되지 않은 품번입니다: %s" % pid]
        usage = old["Usage_Score"]
        f["P_ID"] = pid
        f["MainCat"], f["SubCat"] = old["MainCat"], old["SubCat"]
        f["DetailCat"] = old["DetailCat"]
    else:
        old = None

    grade, total = new_grade(f, usage)
    f["grade"], f["score_sum"] = grade, total
    f["is_new"] = 1 if usage is None else 0

    # 일평균 사용량 — 수정 시에는 실적이 있으면 실적을 쓴다.
    # 사람이 적어 낸 예상보다 실제로 나간 양이 언제나 낫다.
    daily = f.get("daily")
    if pid:
        days = operating_days(conn)
        r = conn.execute(f"""
            SELECT SUM(t.T_Num) q FROM Transaction_tb t
              JOIN Lot_tb l ON t.Lot_ID = l.Lot_ID
             WHERE l.P_ID = ? AND {DEMAND_T}""", (pid,)).fetchone()
        if r and r["q"]:
            daily = round(r["q"] / days, 4)
            f["daily_src"] = "실적"
        else:
            f["daily_src"] = "예상" if daily else None
    else:
        f["daily_src"] = "예상"
    f["daily"] = daily

    # ⚠️ 수정할 때 사용량을 모르면 공식이 None 을 낸다. 그대로 0 을 쓰면
    #    단가만 고치려던 사람이 안전재고를 말없이 지워 버린다.
    #    실적도 예상도 없으면 기존값이 그나마 가장 나은 값이다.
    keep = old["Sf_Num"] if (old is not None and old["Sf_Num"]) else None

    def _ss(g):
        v = safe_formula(g, f.get("Lead_Time"), daily)
        return keep if v is None else v

    ss = _ss(grade)
    if ss is not None and ss == keep and not daily:
        f["daily_src"] = "기존값 유지"
    # 수동 조정이 걸려 있으면 사람이 정한 등급·하한이 이긴다 (일괄 갱신과 같은 규칙)
    ovr = active_overrides(conn).get(pid) if pid else None
    if ovr is not None:
        f["ovr_lv"], f["ovr_min"] = ovr["Ovr_Lv"], ovr["Min_Qty"]
        if ovr["Ovr_Lv"]:
            grade = ovr["Ovr_Lv"]
            ss = _ss(grade)
        if ovr["Min_Qty"]:
            ss = max(ss or 0, ovr["Min_Qty"])
    f["eff_grade"] = grade
    f["Sf_Num"] = ss if ss is not None else 0
    f["cycle"] = REVIEW_CYCLE_DAYS.get(grade, 90)
    f["loc"] = LOC_BY_MAINCAT.get(f.get("MainCat"))

    warn = []
    if f.get("PkgUnit") and f.get("MinOrderQty") and f["MinOrderQty"] % f["PkgUnit"]:
        warn.append("최소 발주량(%s)이 포장단위(%s)의 배수가 아닙니다. 발주 수량이 올림됩니다."
                    % (format(f["MinOrderQty"], ","), format(f["PkgUnit"], ",")))
    if ss is None:
        warn.append("사용량을 알 수 없어 안전재고를 0 으로 둡니다. 일괄 갱신에서 다시 산정됩니다.")
    elif f.get("daily_src") == "기존값 유지":
        warn.append("불출 실적이 없어 안전재고를 기존값(%s개)으로 둡니다. "
                    "다시 계산하려면 월 예상 소요량을 넣으세요." % format(ss, ","))
    if old is not None:
        diff = []
        if old["P_Price"] != f.get("P_Price"):
            diff.append("단가 %s → %s원" % (format(int(old["P_Price"] or 0), ","),
                                            format(f.get("P_Price", 0), ",")))
            warn.append("⚠️ 단가를 바꾸면 재고자산·ABC 분석·연간 사용금액이 소급해서 바뀝니다.")
        if old["PkgUnit"] != f.get("PkgUnit"):
            diff.append("포장단위 %s → %s" % (old["PkgUnit"], f.get("PkgUnit")))
            warn.append("진행 중인 불출 요청은 예전 포장단위로 계산돼 있습니다.")
        if old["Sf_Lv"] != grade:
            diff.append("등급 %s → %s" % (old["Sf_Lv"], grade))
        if old["Sf_Num"] != f["Sf_Num"]:
            diff.append("안전재고 %s → %s개" % (old["Sf_Num"], f["Sf_Num"]))
        if old["BRN"] != f.get("BRN"):
            diff.append("협력사 변경")
        if old["P_N"] != f["P_N"]:
            diff.append("품명 %s → %s" % (old["P_N"], f["P_N"]))
        f["changes"] = diff
        f["old"] = {k: old[k] for k in
                    ("P_N", "Spec", "BRN", "P_Price", "MinOrderQty", "PkgUnit",
                     "Sf_Lv", "Sf_Num", "Lead_Time", "Usage_Score") + SCORE_KEYS}
    f["warn"] = warn
    return f, []


def create_product(conn, date, body, ep_id):
    """자재 등록 — Product_tb + Safe_tb + Update_Log_tb 한 묶음."""
    w, e = _worker(conn, ep_id)
    if e:
        return None, e
    f, e = preview_product(conn, body)
    if e:
        return None, e

    note = str(body.get("note") or "").strip() or "신규 등록"
    with conn:
        conn.execute(
            "INSERT INTO Product_tb (P_ID, P_N, Spec, BRN, P_Price,"
            " MainCat, SubCat, DetailCat, MinOrderQty, PkgUnit)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f["P_ID"], f["P_N"], f["Spec"], f["BRN"], f["P_Price"],
             f["MainCat"], f["SubCat"], f["DetailCat"],
             f["MinOrderQty"], f["PkgUnit"]))
        conn.execute(
            "INSERT INTO Safe_tb (P_ID, Lead_Time, Sf_Lv, Sf_Num,"
            " Price_Score, Sub_Score, Impact_Score, Supply_Score, Usage_Score)"
            " VALUES (?,?,?,?,?,?,?,?,NULL)",
            (f["P_ID"], f["Lead_Time"], f["grade"], f["Sf_Num"],
             f["Price_Score"], f["Sub_Score"], f["Impact_Score"], f["Supply_Score"]))
        conn.execute(
            "INSERT INTO Update_Log_tb (P_ID, Updated_Date, Next_Date,"
            " Old_Lv, New_Lv, Old_Num, New_Num, Old_Usage, New_Usage, EP_ID, Note)"
            " VALUES (?,?,?,NULL,?,NULL,?,NULL,NULL,?,?)",
            (f["P_ID"], date, _add_days(date, f["cycle"]),
             f["grade"], f["Sf_Num"], w["EP_ID"], note))

    f["EP_ID"], f["worker"] = w["EP_ID"], w["Name"]
    f["date"], f["note"] = date, note
    f["next_date"] = _add_days(date, f["cycle"])
    return f, []


def update_product(conn, date, pid, body, ep_id):
    """자재 수정. 분류·품번은 바꿀 수 없다 — 일곱 테이블이 품번으로 엮여 있다."""
    w, e = _worker(conn, ep_id)
    if e:
        return None, e
    f, e = preview_product(conn, body, pid)
    if e:
        return None, e

    old = conn.execute(
        "SELECT s.Sf_Lv, s.Sf_Num, s.Usage_Score FROM Safe_tb s WHERE s.P_ID = ?",
        (pid,)).fetchone()
    note = str(body.get("note") or "").strip() or "자재 정보 수정"

    with conn:
        conn.execute(
            "UPDATE Product_tb SET P_N=?, Spec=?, BRN=?, P_Price=?,"
            " MinOrderQty=?, PkgUnit=? WHERE P_ID=?",
            (f["P_N"], f["Spec"], f["BRN"], f["P_Price"],
             f["MinOrderQty"], f["PkgUnit"], pid))
        conn.execute(
            "UPDATE Safe_tb SET Lead_Time=?, Sf_Lv=?, Sf_Num=?,"
            " Price_Score=?, Sub_Score=?, Impact_Score=?, Supply_Score=? WHERE P_ID=?",
            (f["Lead_Time"], f["eff_grade"], f["Sf_Num"],
             f["Price_Score"], f["Sub_Score"], f["Impact_Score"],
             f["Supply_Score"], pid))
        # 등급이나 수량이 바뀌었으면 이력을 남긴다. 아무것도 안 바뀌었는데
        # 이력만 쌓으면 재검토 주기가 계속 밀린다.
        if old and (old["Sf_Lv"] != f["eff_grade"] or old["Sf_Num"] != f["Sf_Num"]):
            conn.execute(
                "INSERT OR REPLACE INTO Update_Log_tb (P_ID, Updated_Date, Next_Date,"
                " Old_Lv, New_Lv, Old_Num, New_Num, Old_Usage, New_Usage, EP_ID, Note)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (pid, date, _add_days(date, f["cycle"]),
                 old["Sf_Lv"], f["eff_grade"], old["Sf_Num"], f["Sf_Num"],
                 old["Usage_Score"], old["Usage_Score"], w["EP_ID"], note))
            f["logged"] = 1

    f["EP_ID"], f["worker"] = w["EP_ID"], w["Name"]
    f["date"], f["note"] = date, note
    return f, []


# 품번이 걸려 있는 곳. 하나라도 있으면 지우지 않는다 —
# 지우면 LOT·거래·BOM 이 가리킬 데가 없는 고아가 된다.
PRODUCT_REFS = [
    ("Lot_tb",               "P_ID = ?",   "LOT"),
    ("Purchase_Detail_tb",   "P_ID = ?",   "발주 품목"),
    ("BOM_tb",               "P_ID = ?",   "BOM"),
    ("Production_tb",        "P_ID = ?",   "생산 실적"),
    ("Disburse_Req_Item_tb", "P_ID = ?",   "불출 요청"),
    ("Inbound_Claim_tb",     "P_ID = ?",   "클레임"),
    ("Safe_Override_tb",     "P_ID = ? AND Status = '적용'", "안전재고 조정"),
]


def product_refs(conn, pid):
    """이 품번을 쓰고 있는 곳과 건수."""
    out = []
    for tbl, where, label in PRODUCT_REFS:
        n = conn.execute("SELECT COUNT(*) FROM %s WHERE %s" % (tbl, where),
                         (pid,)).fetchone()[0]
        if n:
            out.append({"table": tbl, "label": label, "n": n})
    return out


def delete_product(conn, pid, ep_id, note=None):
    """자재 삭제 — 참조가 하나도 없을 때만.

    등록·수정은 누구나 하지만 삭제는 직급을 본다. 되돌릴 수 없는 쪽만 조인다.
    """
    w, e = _approver(conn, ep_id, what="자재 삭제")
    if e:
        return None, e
    prod = conn.execute(
        "SELECT P_ID, P_N FROM Product_tb WHERE P_ID = ?", (pid,)).fetchone()
    if prod is None:
        return None, ["등록되지 않은 품번입니다: %s" % pid]

    refs = product_refs(conn, pid)
    if refs:
        return None, ["이미 쓰인 자재라 삭제할 수 없습니다 — %s. 정보를 수정하세요."
                      % " · ".join("%s %d건" % (r["label"], r["n"]) for r in refs)]

    with conn:
        conn.execute("DELETE FROM Update_Log_tb WHERE P_ID = ?", (pid,))
        conn.execute("DELETE FROM Safe_tb WHERE P_ID = ?", (pid,))
        conn.execute("DELETE FROM Product_tb WHERE P_ID = ?", (pid,))
    return {"P_ID": pid, "P_N": prod["P_N"], "worker": w["Name"],
            "note": str(note or "").strip()}, []


# ── CSV 일괄 등록 ────────────────────────────────────────────
#
# 자재 50종을 화면에서 하나씩 넣게 할 수는 없다. 다만 CSV 는
# 사람이 손으로 만드는 파일이라 틀린 채로 들어오는 게 정상이다.
# 그래서 저장 전에 전 줄을 검사해 보여주고, 한 줄이라도 틀리면
# 아무것도 저장하지 않는다 — 절반만 들어간 마스터가 제일 고치기 어렵다.

PRODUCT_CSV_COLS = [
    ("P_N",          "품명",          True,  "후방카메라 LCD"),
    ("Spec",         "규격",          False, "CMOS 1/3\" 720P"),
    ("MainCat",      "대분류",        True,  "E"),
    ("SubCat",       "중분류",        True,  "01"),
    ("DetailCat",    "소분류",        True,  "01"),
    ("BRN",          "협력사",        True,  "글로벌디스플레이"),
    ("P_Price",      "단가",          True,  "217800"),
    ("MinOrderQty",  "최소발주량",     False, "50"),
    ("PkgUnit",      "포장단위",       True,  "10"),
    ("Lead_Time",    "리드타임",       True,  "64"),
    ("Price_Score",  "단가점수",       True,  "3"),
    ("Sub_Score",    "대체점수",       True,  "3"),
    ("Impact_Score", "영향점수",       True,  "3"),
    ("Supply_Score", "공급점수",       True,  "3"),
    ("month_qty",    "월예상소요량",    True,  "300"),
]

# 엑셀에서 '다른 이름으로 저장 → CSV' 하면 한국어 윈도우는 cp949 로 쓴다.
# utf-8 로만 읽으면 한글이 전부 깨지거나 예외가 난다.
CSV_ENCODINGS = ("utf-8-sig", "cp949", "utf-8")


def csv_template():
    """받아서 그대로 채우면 되는 빈 양식 (머리글 + 예시 한 줄).

    따옴표·쉼표는 csv 모듈에 맡긴다. 직접 붙이면 규격이 1/3" 처럼
    따옴표를 품은 값에서 깨진다.
    """
    buf = io.StringIO()
    wr = csv.writer(buf, lineterminator="\r\n")
    wr.writerow([label for _k, label, _r, _ex in PRODUCT_CSV_COLS])
    wr.writerow([ex for _k, _l, _r, ex in PRODUCT_CSV_COLS])
    return buf.getvalue()


def parse_product_csv(raw):
    """바이트를 줄 목록으로. 인코딩과 열 이름만 여기서 본다."""
    if isinstance(raw, str):
        text = raw
    else:
        text = None
        for enc in CSV_ENCODINGS:
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            return [], ["파일을 읽을 수 없습니다. UTF-8 또는 CP949(엑셀 기본)로 저장하세요."]

    text = text.replace("\r\n", "\n").strip("\ufeff").strip()
    if not text:
        return [], ["빈 파일입니다."]

    rdr = csv.reader(io.StringIO(text))
    try:
        rows = [r for r in rdr if any(str(c).strip() for c in r)]
    except csv.Error as ex:
        return [], ["CSV 형식이 아닙니다: %s" % ex]
    if len(rows) < 2:
        return [], ["머리글 한 줄과 자료 한 줄 이상이 필요합니다."]

    label2key = {lb: k for k, lb, _r, _e in PRODUCT_CSV_COLS}
    label2key.update({k: k for k, _lb, _r, _e in PRODUCT_CSV_COLS})
    head = [str(c).strip().strip('"').replace(" ", "") for c in rows[0]]
    idx = {}
    for i, h in enumerate(head):
        if h in label2key:
            idx[label2key[h]] = i

    miss = [lb for k, lb, req, _e in PRODUCT_CSV_COLS if req and k not in idx]
    if miss:
        return [], ["머리글에 없는 열이 있습니다: %s" % ", ".join(miss),
                    "첫 줄은 %s 순서여야 합니다."
                    % ", ".join(lb for _k, lb, _r, _e in PRODUCT_CSV_COLS)]

    out = []
    for n, r in enumerate(rows[1:], start=2):
        d = {"_line": n}
        for k, i in idx.items():
            d[k] = str(r[i]).strip() if i < len(r) else ""
        out.append(d)
    return out, []


def _resolve_company(conn, v, cache):
    """협력사를 사업자번호로도 이름으로도 받는다. 사람이 쓰는 건 이름이다."""
    t = str(v or "").strip()
    if not t:
        return None
    if t in cache:
        return cache[t]
    r = conn.execute(
        "SELECT BRN FROM Company_tb WHERE BRN = ? OR CP_N = ?", (t, t)).fetchone()
    cache[t] = r["BRN"] if r else None
    return cache[t]


def preview_products_csv(conn, rows):
    """줄마다 판정한다. 저장은 하지 않는다."""
    taken, seen, cache = [], {}, {}
    out, bad = [], 0
    for r in rows:
        body = dict(r)
        body["BRN"] = _resolve_company(conn, r.get("BRN"), cache) or r.get("BRN")
        body["_taken"] = tuple(taken)
        f, e = preview_product(conn, body)

        # 파일 안에서 겹치는 것도 잡아 준다 — 복사해 붙이다 흔히 난다.
        # DB 와의 중복은 _check_product 가 이미 본다. 여기선 파일 안만.
        nm = str(r.get("P_N") or "").strip()
        key = (nm, str(r.get("Spec") or "").strip())
        if nm and key in seen:
            e = list(e) + ["%d번째 줄과 품명·규격이 같습니다." % seen[key]]
        elif nm:
            seen[key] = r["_line"]

        if e:
            bad += 1
            out.append({"line": r["_line"], "ok": 0, "P_N": nm,
                        "errors": e, "raw": r})
        else:
            taken.append(f["P_ID"])
            out.append({"line": r["_line"], "ok": 1, "P_ID": f["P_ID"],
                        "P_N": f["P_N"], "Spec": f["Spec"],
                        "supplier": f.get("supplier"), "grade": f["grade"],
                        "score_sum": f["score_sum"], "Sf_Num": f["Sf_Num"],
                        "P_Price": f["P_Price"], "PkgUnit": f["PkgUnit"],
                        "Lead_Time": f["Lead_Time"], "loc": f["loc"],
                        "warn": f.get("warn", []), "raw": r})
    return {"rows": out, "total": len(out), "ok": len(out) - bad, "bad": bad,
            "amount": sum(x.get("P_Price", 0) or 0 for x in out if x["ok"])}, []


def create_products_bulk(conn, date, rows, ep_id, note=None):
    """CSV 일괄 등록 — 전부 성공 아니면 전부 실패."""
    w, e = _worker(conn, ep_id)
    if e:
        return None, e
    if not rows:
        return None, ["등록할 줄이 없습니다."]

    plan, _ = preview_products_csv(conn, rows)
    if plan["bad"]:
        return None, ["%d줄 중 %d줄에 오류가 있습니다. 한 줄이라도 틀리면 저장하지 않습니다."
                      % (plan["total"], plan["bad"])] + [
            "%d번째 줄: %s" % (r["line"], r["errors"][0])
            for r in plan["rows"] if not r["ok"]][:10]

    note = str(note or "").strip() or "CSV 일괄 등록"
    taken, done = [], []
    with conn:                       # 한 줄이라도 실패하면 전부 되돌린다
        for r in rows:
            body = dict(r)
            body["BRN"] = _resolve_company(conn, r.get("BRN"), {}) or r.get("BRN")
            body["_taken"] = tuple(taken)
            f, e2 = preview_product(conn, body)
            if e2:
                raise ValueError("%d번째 줄: %s" % (r["_line"], e2[0]))
            taken.append(f["P_ID"])
            conn.execute(
                "INSERT INTO Product_tb (P_ID, P_N, Spec, BRN, P_Price,"
                " MainCat, SubCat, DetailCat, MinOrderQty, PkgUnit)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f["P_ID"], f["P_N"], f["Spec"], f["BRN"], f["P_Price"],
                 f["MainCat"], f["SubCat"], f["DetailCat"],
                 f["MinOrderQty"], f["PkgUnit"]))
            conn.execute(
                "INSERT INTO Safe_tb (P_ID, Lead_Time, Sf_Lv, Sf_Num,"
                " Price_Score, Sub_Score, Impact_Score, Supply_Score, Usage_Score)"
                " VALUES (?,?,?,?,?,?,?,?,NULL)",
                (f["P_ID"], f["Lead_Time"], f["grade"], f["Sf_Num"],
                 f["Price_Score"], f["Sub_Score"], f["Impact_Score"], f["Supply_Score"]))
            conn.execute(
                "INSERT INTO Update_Log_tb (P_ID, Updated_Date, Next_Date,"
                " Old_Lv, New_Lv, Old_Num, New_Num, Old_Usage, New_Usage, EP_ID, Note)"
                " VALUES (?,?,?,NULL,?,NULL,?,NULL,NULL,?,?)",
                (f["P_ID"], date, _add_days(date, f["cycle"]),
                 f["grade"], f["Sf_Num"], w["EP_ID"], note))
            done.append({"P_ID": f["P_ID"], "P_N": f["P_N"], "grade": f["grade"],
                         "Sf_Num": f["Sf_Num"], "supplier": f.get("supplier"),
                         "loc": f["loc"]})

    by_grade = {g: sum(1 for d in done if d["grade"] == g) for g in ("A", "B", "C")}
    return {"items": done, "cnt": len(done), "by_grade": by_grade,
            "worker": w["Name"], "date": date, "note": note}, []
