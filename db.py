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
    lots = _rows(conn, """
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
          LEFT JOIN (SELECT Lot_ID, SUM(T_Num) out_qty, COUNT(*) out_cnt
                       FROM Transaction_tb WHERE T_Type='불출' GROUP BY Lot_ID) x
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
    return _rows(conn, """
        SELECT substr(t.T_Date,1,7) AS ym,
               SUM(CASE WHEN t.T_Type='입고' THEN t.T_Num ELSE 0 END) AS in_qty,
               SUM(CASE WHEN t.T_Type='불출' THEN t.T_Num ELSE 0 END) AS out_qty,
               SUM(CASE WHEN t.T_Type='입고' THEN 1 ELSE 0 END)       AS in_cnt,
               SUM(CASE WHEN t.T_Type='불출' THEN 1 ELSE 0 END)       AS out_cnt,
               SUM(CASE WHEN t.T_Type='입고' THEN t.T_Num*p.P_Price ELSE 0 END) AS in_amt,
               SUM(CASE WHEN t.T_Type='불출' THEN t.T_Num*p.P_Price ELSE 0 END) AS out_amt
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


def supplier_list(conn):
    """협력사 20개사 + 리드타임 통계 + 발주 실적 + 공급 리스크."""
    comps = _rows(conn, "SELECT BRN, CP_N, Is_Foreign FROM Company_tb ORDER BY CP_N")

    # 발주-입고 쌍에서 리드타임 실측
    lt_raw = _rows(conn, """
        SELECT h.BRN, l.Lot_ID, h.H_ID, h.P_Date, l.Lot_Date,
               CAST(julianday(l.Lot_Date) - julianday(h.P_Date) AS INT) AS lt
          FROM Lot_tb l JOIN Purchase_Header_tb h ON l.H_ID = h.H_ID
         ORDER BY h.P_Date
    """)
    by_brn = {}
    for r in lt_raw:
        by_brn.setdefault(r["BRN"], []).append(r)

    # 발주 실적 (금액은 발주상세 × 단가)
    orders = {r["BRN"]: r for r in _rows(conn, """
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
         GROUP BY h.BRN
    """)}

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
    lines = _rows(conn, """
        SELECT h.H_ID, h.BRN, h.P_Date,
               c.CP_N AS supplier, c.Is_Foreign AS is_foreign,
               d.Purchase_num, d.P_ID, d.P_Qty AS ord_qty,
               p.P_N, p.P_Price, p.PkgUnit, p.MinOrderQty,
               s.Sf_Lv AS grade,
               l.Lot_ID, l.P_Qty AS in_qty, l.Lot_Date,
               lo.Loc_N AS loc_name,
               CAST(julianday(l.Lot_Date) - julianday(h.P_Date) AS INT) AS lead_days
          FROM Purchase_Header_tb h
          JOIN Purchase_Detail_tb d ON h.H_ID = d.H_ID
          JOIN Product_tb p         ON d.P_ID = p.P_ID
          LEFT JOIN Company_tb c    ON h.BRN = c.BRN
          LEFT JOIN Safe_tb s       ON p.P_ID = s.P_ID
          LEFT JOIN Lot_tb l        ON d.H_ID = l.H_ID AND d.P_ID = l.P_ID
          LEFT JOIN Location_tb lo  ON l.Loc_ID = lo.Loc_ID
         ORDER BY h.P_Date DESC, h.H_ID DESC, d.Purchase_num
    """)

    grouped = {}
    for r in lines:
        grouped.setdefault(r["H_ID"], []).append(r)

    orders = []
    for hid, ls in grouped.items():
        h = ls[0]
        items, short, ok, pending = [], 0, 0, 0
        for l in ls:
            gap = (l["in_qty"] - l["ord_qty"]) if l["in_qty"] is not None else None
            if l["in_qty"] is None:
                st = "미입고"; pending += 1
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


def purchase_summary(orders):
    lines = [i for o in orders for i in o["items"]]
    ok = len([i for i in lines if i["status"] == "일치"])
    short = [i for i in lines if i["status"] == "부족"]
    dates = [o["date"] for o in orders if o["date"]]
    lds = [o["lead_days"] for o in orders if o["lead_days"] is not None]
    return {
        "order_cnt": len(orders),
        "line_cnt": len(lines),
        "amount": sum(o["amount"] for o in orders),
        "in_amount": sum(o["in_amount"] for o in orders),
        "ok": ok,
        "ok_pct": round(ok / len(lines) * 100, 1) if lines else 0,
        "short": len(short),
        "short_qty": sum(abs(i["gap"] or 0) for i in short),
        "short_pkg_ok": len([i for i in short if i["pkg_multiple"]]),
        "pending": len([i for i in lines if i["status"] == "미입고"]),
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
              LEFT JOIN (SELECT Lot_ID, SUM(T_Num) AS out_qty FROM Transaction_tb
                          WHERE T_Type='불출' GROUP BY Lot_ID) x ON x.Lot_ID = l.Lot_ID
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
            "T_Num": r["T_Num"], "P_ID": r["P_ID"],
            "P_N": p.get("P_N"), "grade": p.get("grade"),
            "worker": r["worker"],
        })

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

    rows = _rows(conn, """
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
          LEFT JOIN (SELECT Lot_ID, SUM(T_Num) AS out_qty, MIN(T_Date) AS out_date
                       FROM Transaction_tb WHERE T_Type='불출' GROUP BY Lot_ID) x
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
                 WHERE t.T_Type = '불출'
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
    for r in _rows(conn, """
        SELECT l.P_ID, substr(t.T_Date,1,7) AS ym, SUM(t.T_Num) AS qty
          FROM Transaction_tb t JOIN Lot_tb l ON t.Lot_ID = l.Lot_ID
         WHERE t.T_Type = '불출'
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
    return _rows(conn, """
        SELECT substr(t.T_Date,1,7) AS ym,
               COUNT(*) AS cnt, SUM(t.T_Num) AS qty,
               SUM(t.T_Num * p.P_Price) AS amount
          FROM Transaction_tb t
          JOIN Lot_tb l ON t.Lot_ID = l.Lot_ID
          JOIN Product_tb p ON l.P_ID = p.P_ID
         WHERE t.T_Type = '불출'
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
