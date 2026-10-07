# -*- coding: utf-8 -*-
"""3년치 데이터 마무리 — 안전재고 주기 갱신 + 시연용 대기 건.

gen3y.py 다음에 돌린다. erp3y.db 를 제자리에서 고친다.

  ① 안전재고 일괄 갱신 (조건 9) — 반기마다 주기 도래분을 실적으로 재계산
  ② 시연용 대기 건 — 미입고 발주 · 승인 대기 요청 · 피킹용 승인 요청 · 미정 클레임
     (전부 '지금 할 일이 있는' 상태라야 대시보드가 비어 보이지 않는다)
"""
import os, random, sqlite3, sys
from datetime import date, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"C:/claude code accept/matman"
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT); sys.path.insert(0, ROOT)
import db as D

DBF = os.environ.get("MATMAN_OUT") or os.path.join(HERE, "erp3y.db")
rnd = random.Random(777)
con = sqlite3.connect(DBF); con.row_factory = sqlite3.Row
cur = con.cursor()
d2s = lambda d: d.isoformat()
END = date.fromisoformat(cur.execute("SELECT MAX(T_Date) FROM Transaction_tb").fetchone()[0])
CYCLE = {"A": 30, "B": 90, "C": 180}

# ⚠️ 이미 들어 있는 번호 다음부터 매긴다. 0 부터 세면 그날 번호와 부딪힌다.
_COL = {"T": ("Transaction_tb", "T_ID"), "PO": ("Purchase_Header_tb", "H_ID"),
        "LOT": ("Lot_tb", "Lot_ID"), "REQ": ("Disburse_Req_tb", "Req_ID"),
        "WO": ("Disburse_Req_tb", "Work_Order"), "RMA": ("Inbound_Claim_tb", "Claim_ID"),
        "CNT": ("Stock_Count_tb", "Count_ID"), "RET": ("Site_Return_tb", "Ret_ID"),
        "PR": ("Production_tb", "Prod_ID")}
_seq = {}
def nid(pre, d, n=4):
    k = pre + d.replace("-", "")
    if k not in _seq:
        t, c = _COL[pre]
        last = cur.execute("SELECT MAX(%s) FROM %s WHERE %s LIKE ?" % (c, t, c),
                           (k + "%",)).fetchone()[0]
        _seq[k] = int(last[-4:]) if last else 0
    _seq[k] += 1
    return "%s%0*d" % (k, n, _seq[k])

USERS = [dict(r) for r in cur.execute("SELECT * FROM User_tb")]
BOSS = [u["EP_ID"] for u in USERS if u["Position"] == "부장"]
MGR = [u["EP_ID"] for u in USERS if u["Position"] in ("과장", "차장")]
WORKER = [u["EP_ID"] for u in USERS if u["Position"] in ("사원", "주임", "대리")]

# ══════════════════════════════════════════════════════════
#  ① 안전재고 일괄 갱신 — 반기마다
# ══════════════════════════════════════════════════════════
# wizard_data() 는 '지금' 기준으로만 대상을 고른다. 과거 실행을 재현하려면
# 그 날짜의 실적으로 직접 계산해야 한다. 규칙은 같다 —
#   Usage_Score 는 그 시점까지의 불출 금액 파레토(75%/90%), 등급은 5항목
#   컷오프(13/8/7), 수량은 공식. 변경 전/후를 Update_Log 에 남긴다.
RUNS = [d for d in (date(2023, 7, 3), date(2024, 1, 2), date(2024, 7, 1),
                    date(2025, 1, 2), date(2025, 7, 1), date(2026, 1, 2),
                    date(2026, 7, 1)) if d <= END]
START = date(2023, 1, 1)
total_chg = 0
for run in RUNS:
    rs = d2s(run)
    # 그 시점까지의 불출 실적
    use = {r["P_ID"]: (r["qty"], r["amt"]) for r in cur.execute("""
        SELECT l.P_ID, SUM(t.T_Num) qty, SUM(t.T_Num * p.P_Price) amt
          FROM Transaction_tb t JOIN Lot_tb l USING(Lot_ID)
          JOIN Product_tb p ON p.P_ID = l.P_ID
         WHERE t.T_Type = '불출' AND t.T_Date <= ? GROUP BY l.P_ID""", (rs,))}
    days = max((run - START).days, 1)
    # 파레토 — 누적 75% A, 90% B
    order = sorted(use.items(), key=lambda kv: -kv[1][1])
    tot = sum(v[1] for _, v in order) or 1
    usage, acc = {}, 0.0
    for pid, v in order:
        acc += v[1]
        usage[pid] = 3 if acc / tot <= 0.75 else (2 if acc / tot <= 0.90 else 1)
    # 대상 — 주기가 도래한 품목
    due = [dict(r) for r in cur.execute("""
        SELECT s.*, (SELECT MAX(Next_Date) FROM Update_Log_tb u WHERE u.P_ID = s.P_ID) nx
          FROM Safe_tb s""")]
    ep = rnd.choice(BOSS)
    n = 0
    for s in due:
        if not s["nx"] or s["nx"] > rs:
            continue
        if cur.execute("SELECT 1 FROM Update_Log_tb WHERE P_ID=? AND Updated_Date=?",
                       (s["P_ID"], rs)).fetchone():
            continue
        u = usage.get(s["P_ID"], 1)
        sc = {k: s[k] for k in D.SCORE_KEYS}
        g, _ = D.new_grade(sc, u)
        d = round(use.get(s["P_ID"], (0, 0))[0] / days, 2)
        ss = D.safe_formula(g, s["Lead_Time"], d)
        ss = s["Sf_Num"] if ss is None else ss        # 실적이 없으면 기존값을 지킨다
        nxt = run + timedelta(days=CYCLE[g])
        cur.execute("INSERT INTO Update_Log_tb (P_ID,Updated_Date,Next_Date,Old_Lv,New_Lv,"
                    "Old_Num,New_Num,Old_Usage,New_Usage,EP_ID,Note)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (s["P_ID"], rs, d2s(nxt), s["Sf_Lv"], g, s["Sf_Num"], ss,
                     s["Usage_Score"], u, ep, "%d년 %s기 정기 갱신"
                     % (run.year, "상반" if run.month < 7 else "하반")))
        cur.execute("UPDATE Safe_tb SET Sf_Lv=?, Sf_Num=?, Usage_Score=? WHERE P_ID=?",
                    (g, ss, u, s["P_ID"]))
        n += 1
    total_chg += n
    print("  %s 갱신 %3d종" % (rs, n))
print("안전재고 일괄 갱신 %d회 · 연 %d종" % (len(RUNS), total_chg))

# ══════════════════════════════════════════════════════════
#  ② 시연용 대기 건
# ══════════════════════════════════════════════════════════
PROD = {r["P_ID"]: dict(r) for r in cur.execute("SELECT * FROM Product_tb")}
SAFE = {r["P_ID"]: dict(r) for r in cur.execute("SELECT * FROM Safe_tb")}

def stock(pid):
    r = cur.execute("""SELECT COALESCE(SUM(l.P_Qty),0) - COALESCE((SELECT SUM(
          CASE WHEN T_Type IN ('불출','불량','폐기') THEN T_Num
               WHEN T_Type IN ('반납') THEN -T_Num ELSE 0 END)
          FROM Transaction_tb t JOIN Lot_tb l2 USING(Lot_ID) WHERE l2.P_ID=?),0)
        FROM Lot_tb l WHERE l.P_ID=?""", (pid, pid)).fetchone()[0]
    return int(r or 0)

# ── 미입고 발주 2건 (입고 대기) ───────────────────────────
ord_day = END - timedelta(days=4)
low = sorted(((pid, stock(pid)) for pid in PROD if SAFE.get(pid, {}).get("Sf_Num")),
             key=lambda kv: kv[1])[:40]
by_brn = {}
for pid, _ in low:
    by_brn.setdefault(PROD[pid]["BRN"], []).append(pid)
made = 0
for brn, pids in sorted(by_brn.items(), key=lambda kv: -len(kv[1]))[:2]:
    hid = nid("PO", d2s(ord_day))
    cur.execute("INSERT INTO Purchase_Header_tb (H_ID,BRN,P_Date) VALUES (?,?,?)",
                (hid, brn, d2s(ord_day)))
    for n, pid in enumerate(pids[:4], start=1):
        p = PROD[pid]
        q = D.round_order_qty((SAFE[pid]["Sf_Num"] or 100) * 2, p["MinOrderQty"], p["PkgUnit"])
        cur.execute("INSERT INTO Purchase_Detail_tb (H_ID,Purchase_num,P_ID,P_Qty,Unit_Price)"
                    " VALUES (?,?,?,?,?)", (hid, n, pid, q, p["P_Price"]))
    made += 1
    print("  미입고 발주 %s (%s · %d품목)" % (hid, brn, min(4, len(pids))))

# ── 승인 대기 요청 1건 + 피킹용 승인 요청 2건 ─────────────
FGS = [dict(r) for r in cur.execute("SELECT * FROM FG_tb ORDER BY FG_ID")]
BOM = {}
for r in cur.execute("SELECT * FROM BOM_tb WHERE BOM_Type NOT LIKE '대체%'"):
    BOM.setdefault(r["FG_ID"], []).append((r["P_ID"], float(r["BOM_Qty"] or 0)))

def make_req(fg, qty, day, status, appr):
    rid = nid("REQ", d2s(day)); wo = nid("WO", d2s(day))
    reqer = rnd.choice(WORKER)
    a = rnd.choice([x for x in (MGR + BOSS) if x != reqer]) if appr else None
    cur.execute("INSERT INTO Disburse_Req_tb (Req_ID,Req_Date,FG_ID,Plan_Qty,Work_Order,"
                "EP_ID,Status,Note,Appr_EP_ID,Appr_Date,Appr_Note)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (rid, d2s(day), fg, qty, wo, reqer, status, "다음 주 투입분",
                 a, d2s(day) if a else None, "승인" if a else None))
    for n, (pid, bq) in enumerate(BOM.get(fg, []), start=1):
        need = int(round(bq * qty))
        pkg = int(PROD[pid]["PkgUnit"] or 1)
        rq = ((need + pkg - 1) // pkg) * pkg
        cur.execute("INSERT INTO Disburse_Req_Item_tb (Req_ID,Req_num,P_ID,Need_Qty,"
                    "Site_Qty,Stock_Qty,Req_Qty,Pkg_Unit,Is_Manual,Appr_Qty,Done_Qty)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,0)",
                    (rid, n, pid, need, 0, stock(pid), rq, pkg, "N",
                     min(rq, stock(pid)) if appr else None))
    return rid

live = [f for f in FGS if cur.execute(
    "SELECT COUNT(*) FROM Production_tb WHERE FG_ID=? AND Prod_Date>?",
    (f["FG_ID"], d2s(END - timedelta(days=120)))).fetchone()[0] > 0]
rnd.shuffle(live)
r1 = make_req(live[0]["FG_ID"], 120, END - timedelta(days=2), "요청", False)
r2 = make_req(live[1]["FG_ID"], 90, END - timedelta(days=3), "승인", True)
r3 = make_req(live[2]["FG_ID"], 60, END - timedelta(days=3), "승인", True)
print("  승인 대기 %s · 피킹 대기 %s · %s" % (r1, r2, r3))

# ── 미정 클레임 1건 ───────────────────────────────────────
row = cur.execute("""SELECT l.Lot_ID, l.P_ID, l.H_ID, l.Lot_Date FROM Lot_tb l
                     WHERE l.Lot_Date > ? ORDER BY l.Lot_Date DESC LIMIT 1""",
                  (d2s(END - timedelta(days=40)),)).fetchone()
if row:
    cday = date.fromisoformat(row["Lot_Date"]) + timedelta(days=2)
    if cday > END:
        cday = END
    qn = 30
    tid = nid("T", d2s(cday))
    cur.execute("INSERT INTO Transaction_tb (T_ID,Lot_ID,T_Type,T_Date,T_Num,EP_ID)"
                " VALUES (?,?,?,?,?,?)", (tid, row["Lot_ID"], "불량", d2s(cday), qn,
                                          rnd.choice(MGR)))
    rid = nid("RMA", d2s(cday))
    cur.execute("INSERT INTO Inbound_Claim_tb (Claim_ID,Lot_ID,H_ID,P_ID,Claim_Qty,"
                "Claim_Type,Resolution,Status,T_ID,New_Lot_ID,Amount,Reason,Claim_Date,"
                "Done_Date,EP_ID) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?)",
                (rid, row["Lot_ID"], row["H_ID"], row["P_ID"], qn, "사용중발견",
                 "미정", "접수", tid, None,
                 round(qn * float(PROD[row["P_ID"]]["P_Price"] or 0)),
                 "사용 중 치수 불량 발견 — 거래처 통보", d2s(cday), rnd.choice(MGR)))
    print("  미정 클레임 %s (%s %d개)" % (rid, row["P_ID"], qn))

con.commit()
print("\n마무리 완료 — %s (%.1fMB)" % (DBF, os.path.getsize(DBF) / 1024 / 1024))
con.close()
