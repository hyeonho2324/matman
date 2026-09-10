# -*- coding: utf-8 -*-
"""
erp.db 조회 모듈.

화면에서 쓰는 SQL을 여기 모아둔다. app.py 는 이 함수들만 부른다.
"""
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


# ── 공통 조각 ────────────────────────────────────────────────
# 현재고 = LOT 입고량 - 그 LOT의 불출 합계
STOCK_SQL = """
    SELECT l.P_ID,
           SUM(l.P_Qty) - COALESCE(SUM(x.out_qty), 0) AS stock
      FROM Lot_tb l
      LEFT JOIN (SELECT Lot_ID, SUM(T_Num) AS out_qty
                   FROM Transaction_tb WHERE T_Type = '불출' GROUP BY Lot_ID) x
             ON x.Lot_ID = l.Lot_ID
     GROUP BY l.P_ID
"""


def operating_days(conn):
    """불출 이력이 존재하는 기간(일). 일평균사용량 d 의 분모."""
    r = conn.execute("""
        SELECT CAST(julianday(MAX(T_Date)) - julianday(MIN(T_Date)) AS INT) d
          FROM Transaction_tb WHERE T_Type = '불출'
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
                 WHERE t.T_Type = '불출'
                 GROUP BY l.P_ID
             ),
             lead AS (
                SELECT l.P_ID,
                       AVG(julianday(l.Lot_Date) - julianday(h.P_Date)) AS lt_real
                  FROM Lot_tb l JOIN Purchase_Header_tb h ON l.H_ID = h.H_ID
                 GROUP BY l.P_ID
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
          LEFT JOIN Update_Log_tb ul ON s.P_ID = ul.P_ID
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
                       SUM(CASE WHEN t.T_Type='입고' THEN t.T_Num ELSE 0 END) AS in_qty,
                       SUM(CASE WHEN t.T_Type='불출' THEN t.T_Num ELSE 0 END) AS out_qty,
                       MAX(CASE WHEN t.T_Type='불출' THEN t.T_Date END)       AS last_out
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
    """LOT 636건 + LOT별 잔량 (자재 상세에서 FIFO 확인용)."""
    return _rows(conn, """
        SELECT l.Lot_ID, l.P_ID, l.Lot_Date, l.Loc_ID, lo.Loc_N AS loc_name,
               l.P_Qty, l.H_ID,
               l.P_Qty - COALESCE(x.out_qty, 0) AS remain
          FROM Lot_tb l
          LEFT JOIN Location_tb lo ON l.Loc_ID = lo.Loc_ID
          LEFT JOIN (SELECT Lot_ID, SUM(T_Num) AS out_qty
                       FROM Transaction_tb WHERE T_Type='불출' GROUP BY Lot_ID) x
                 ON x.Lot_ID = l.Lot_ID
         ORDER BY l.P_ID, l.Lot_Date
    """)


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


def abc_analysis(conn):
    """불출 이력 × 단가로 파레토 분석해 Usage 등급을 산정한다."""
    rows = _rows(conn, """
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
                      WHERE t.T_Type = '불출'
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
