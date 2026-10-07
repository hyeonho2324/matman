# -*- coding: utf-8 -*-
"""3년 9개월치 더미데이터 생성 — 2023-01-01 개업 ~ 2026-10-07.

원본 `data/erp.db` 는 건드리지 않는다. 복사본에 만들고, 검증을 통과한 뒤에
사람이 교체한다.

── 유지하는 것 (조건 1) ─────────────────────────────────
  Product_tb · BOM_tb · FG_tb · Company_tb · User_tb · Cat_tb · Location_tb
  Safe_tb 의 Lead_Time + 4개 점수

── 다시 만드는 것 ───────────────────────────────────────
  Lot · Transaction · Purchase(Header/Detail/Change) · Production
  Disburse_Req(+Item) · Inbound_Claim · Site_Return · Stock_Count(+Item)
  Update_Log · Price_Log · Safe_Override
  Safe_tb 의 등급 · 안전재고 · Usage_Score (개업 시점 기준 재계산 후 주기 갱신)

⚠️ 난수는 씨앗을 고정한다. 같은 입력이면 같은 DB 가 나와야 다시 돌려 비교할 수 있다.
"""
import io, os, random, shutil, sqlite3, sys
from datetime import date, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"C:/claude code accept/matman"
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT); sys.path.insert(0, ROOT)
import db as D

SEED = 20260107
START = date(2023, 1, 1)
END = date(2026, 10, 7)
OUT = os.environ.get("MATMAN_OUT") or os.path.join(HERE, "erp3y.db")
# ⚠️ 밑바탕은 **마스터만 든 DB** 여야 한다 (자재·BOM·협력사·사용자·분류·창고).
#    이 생성기의 결과물을 다시 밑바탕으로 쓰면 설계 변경 자재가 **한 종 더**
#    생기고 BOM 이 두 번 바뀐다. 기본값은 data/erp.db 라, 한 번 돌린 뒤 다시
#    돌릴 때는 MATMAN_BASE 로 생성 이전 DB 를 가리켜야 한다.
BASE = os.environ.get("MATMAN_BASE") or os.path.join(ROOT, "data", "erp.db")

rnd = random.Random(SEED)
d2s = lambda d: d.isoformat()
MON = lambda d: d.year * 12 + d.month


def months(a, b):
    y, m = a.year, a.month
    while (y, m) <= (b.year, b.month):
        yield date(y, m, 1)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def wd(d, n=0):
    """주말을 피해 n 일 뒤 — 창고는 평일에 움직인다."""
    d = d + timedelta(days=n)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


# ══════════════════════════════════════════════════════════
#  0. 복사 · 초기화
# ══════════════════════════════════════════════════════════
shutil.copy(BASE, OUT)
print("밑바탕 %s" % BASE)
con = sqlite3.connect(OUT)
con.row_factory = sqlite3.Row
cur = con.cursor()

WIPE = ["Transaction_tb", "Lot_tb", "Purchase_Change_tb", "Purchase_Detail_tb",
        "Purchase_Header_tb", "Production_tb", "Disburse_Req_Item_tb",
        "Disburse_Req_tb", "Inbound_Claim_tb", "Site_Return_tb",
        "Stock_Count_Item_tb", "Stock_Count_tb", "Update_Log_tb",
        "Order_Plan_tb", "Order_Policy_tb",
        "Price_Log_tb", "Safe_Override_tb"]
for t in WIPE:
    cur.execute("DELETE FROM %s" % t)
print("초기화 %d개 테이블" % len(WIPE))

# ══════════════════════════════════════════════════════════
#  1. 마스터 읽기
# ══════════════════════════════════════════════════════════
PROD = {r["P_ID"]: dict(r) for r in cur.execute("SELECT * FROM Product_tb")}
SAFE = {r["P_ID"]: dict(r) for r in cur.execute("SELECT * FROM Safe_tb")}
FGS = [dict(r) for r in cur.execute("SELECT * FROM FG_tb ORDER BY FG_ID")]
BOM = [dict(r) for r in cur.execute("SELECT * FROM BOM_tb")]
USERS = [dict(r) for r in cur.execute("SELECT * FROM User_tb")]
LOCS = [r[0] for r in cur.execute("SELECT Loc_ID FROM Location_tb ORDER BY Loc_ID")]

POS = lambda p: [u["EP_ID"] for u in USERS if u["Position"] == p]
WORKER = [u["EP_ID"] for u in USERS if u["Position"] in ("사원", "주임", "대리")]
MANAGER = [u["EP_ID"] for u in USERS if u["Position"] in ("과장", "차장")]
BOSS = POS("부장")
APPROVER = MANAGER + BOSS

# 표준 BOM 만 소요량 계산에 쓴다 (대체는 실제 투입에서만 섞는다)
STD = {}
ALT = {}
ALTQ = {}      # (완제품, 대체품) -> 그 대체품의 1대당 소요량
for b in BOM:
    if str(b["BOM_Type"] or "").startswith("대체"):
        tgt = str(b["BOM_Type"]).split("→")[-1].strip(" )")
        ALT.setdefault((b["FG_ID"], tgt), []).append(b["P_ID"])
        ALTQ[(b["FG_ID"], b["P_ID"])] = float(b["BOM_Qty"] or 0)
    else:
        STD.setdefault(b["FG_ID"], []).append((b["P_ID"], float(b["BOM_Qty"] or 0)))

print("마스터 — 자재 %d · 완제품 %d · BOM 표준 %d · 대체 %d"
      % (len(PROD), len(FGS), sum(len(v) for v in STD.values()),
         sum(len(v) for v in ALT.values())))

# ══════════════════════════════════════════════════════════
#  2. 조건 6 — 설계 변경용 신규 자재 1종
# ══════════════════════════════════════════════════════════
# A등급 자재 하나가 2025-06 에 설계 변경된다. 새 품번을 분류 체계대로 채번해
# Product_tb 에 더하고, BOM 을 그 품번으로 바꾼다. 그 전 생산은 구 자재를,
# 이후는 신 자재를 투입한다 — 생산 실적에서 교체 시점이 드러난다.
a_cands = [p for p in PROD.values()
           if SAFE.get(p["P_ID"], {}).get("Sf_Lv") == "A"
           and any(p["P_ID"] == x[0] for v in STD.values() for x in v)]
OLD_PID = sorted(a_cands, key=lambda p: -float(p["P_Price"] or 0))[0]["P_ID"]
_o = PROD[OLD_PID]
seq = max(int(k[5:]) for k in PROD
          if k[:5] == OLD_PID[:5]) + 1
NEW_PID = OLD_PID[:5] + "%04d" % seq
cur.execute("INSERT INTO Product_tb (P_ID,P_N,Spec,BRN,P_Price,MainCat,SubCat,"
            "DetailCat,MinOrderQty,PkgUnit) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (NEW_PID, _o["P_N"], (str(_o["Spec"] or "") + " [설계변경]").strip(),
             _o["BRN"], round(float(_o["P_Price"] or 0) * 1.08), _o["MainCat"],
             _o["SubCat"], _o["DetailCat"], _o["MinOrderQty"], _o["PkgUnit"]))
_s = SAFE[OLD_PID]
cur.execute("INSERT INTO Safe_tb (P_ID,Lead_Time,Sf_Lv,Sf_Num,Price_Score,"
            "Sub_Score,Impact_Score,Supply_Score,Usage_Score)"
            " VALUES (?,?,?,?,?,?,?,?,NULL)",
            (NEW_PID, _s["Lead_Time"], _s["Sf_Lv"], _s["Sf_Num"], _s["Price_Score"],
             _s["Sub_Score"], _s["Impact_Score"], _s["Supply_Score"]))
PROD[NEW_PID] = dict(cur.execute("SELECT * FROM Product_tb WHERE P_ID=?", (NEW_PID,)).fetchone())
SAFE[NEW_PID] = dict(cur.execute("SELECT * FROM Safe_tb WHERE P_ID=?", (NEW_PID,)).fetchone())
SWAP_FG = next(fg for fg, items in STD.items() if any(x[0] == OLD_PID for x in items))
SWAP_DATE = date(2025, 6, 1)
cur.execute("UPDATE BOM_tb SET P_ID=?, Spec=? WHERE FG_ID=? AND P_ID=?",
            (NEW_PID, PROD[NEW_PID]["Spec"], SWAP_FG, OLD_PID))
print("설계 변경 — %s(%s) → %s · %s 부터 · 완제품 %s"
      % (OLD_PID, _o["P_N"], NEW_PID, SWAP_DATE, SWAP_FG))

# ══════════════════════════════════════════════════════════
#  3. 생산 계획 (조건 3·5·6·7)
# ══════════════════════════════════════════════════════════
MONTHS = list(months(START, END))
STOP_FG = FGS[4]["FG_ID"]            # 1년만 생산하고 중단
STOP_FROM = date(2024, 1, 1)
STOP_TO = date(2024, 12, 31)
SURGE = {(2023, 11), (2025, 3), (2026, 6)}   # 1.5~2배가 한 달 지속된 시기

base = {}
for i, fg in enumerate(FGS):
    need = sum(q for _, q in STD.get(fg["FG_ID"], [])) or 1
    base[fg["FG_ID"]] = max(12, min(260, int(1800 / max(need, 1)) + 10 + i * 3))

PLAN = {}            # (FG_ID, month) -> 생산 대수
for m in MONTHS:
    ramp = min(1.0, 0.55 + 0.45 * ((MON(m) - MON(START)) / 14.0))   # 개업 후 서서히
    for fg in FGS:
        f = fg["FG_ID"]
        if f == STOP_FG and not (STOP_FROM <= m <= STOP_TO):
            continue
        q = base[f] * ramp * rnd.uniform(0.85, 1.15)
        if (m.year, m.month) in SURGE:
            q *= rnd.uniform(1.5, 2.0)
        if m == MONTHS[-1]:
            q *= 0.3                      # 이번 달은 아직 진행 중
        PLAN[(f, m)] = max(5, int(round(q)))

# ⚠️ 생산 규모를 실제 리듬에 맞춘다.
#    처음엔 완제품 월 12~260대로 잡았는데, 그러면 자재 월 소요가 6,756개뿐이라
#    MOQ·포장단위(상자 1,000개짜리도 있다)에 비해 너무 작았다. 초기 재고가
#    25개월치로 쌓여 **3년 9개월 동안 자재당 발주가 2.8회**밖에 안 일어났다.
#    지금 데이터(208일에 불출 650,046개 = 월 9.4만개)를 기준으로 되맞춘다.
TARGET_MONTHLY = 90000
FIFO_SKIP = 0.07      # 창고가 선입선출을 건너뛰는 비율 — 위반 판정이 쓰일 데이터
STRAND = 0.03         # 선반 뒤에 묻혀 아무도 집지 않는 LOT 비율
# ⚠️ FIFO 를 건너뛰기만 하면 위반이 거의 안 남는다. 건너뛴 LOT 은 다음 불출에서
#    또 맨 앞이라 결국 나가 버리고, 판정은 **지금 잔량이 남아 있는** LOT 만 보기
#    때문이다(첫 시도에서 2,502개 중 위반 1건). 영구히 묻히는 LOT 이 있어야
#    "오래된 게 남아 있는데 새것이 먼저 나갔다" 가 성립한다.
_raw = sum(bq * q for (f, m), q in PLAN.items() for _, bq in STD.get(f, []))
_scale = TARGET_MONTHLY * len(MONTHS) / max(_raw, 1)
for k in PLAN:
    PLAN[k] = max(5, int(round(PLAN[k] * _scale)))
print("생산 규모 x%.1f — 완제품 월 %d~%d대"
      % (_scale, min(PLAN.values()), max(PLAN.values())))

# ══════════════════════════════════════════════════════════
#  3b. 대체품 교체 (조건 6) — 완제품 2종이 중간중간 대체품을 쓴다
# ══════════════════════════════════════════════════════════
# ⚠️ 교체를 **투입 단계에서** 정하면 안 된다. 그렇게 짰다가 대체품이 한 번도
#    안 쓰였다 — 요청·발주가 표준 자재만 보고 돌아서 대체품은 창고에도 현장에도
#    없었고, fifo_site 가 빈손으로 돌아와 전부 원자재로 되돌아갔다.
#    생산 계획이 먼저 정하고 소요량·발주·요청·투입이 그걸 따라야 한다.
def _g4(pid):
    s = SAFE.get(pid)
    return D.new_grade({k: s[k] for k in D.SCORE_KEYS})[0] if s else "C"

_cand = {}
for f, items in STD.items():
    if f in (STOP_FG, SWAP_FG):
        continue
    ps = [pid for pid, _ in items if ALT.get((f, pid)) and _g4(pid) in ("B", "C")]
    if ps:
        _cand[f] = ps
ALT_FGS = sorted(_cand, key=lambda f: (-len(_cand[f]), f))[:2]

SUB = {}       # (완제품, 월, 원자재) -> 대체품
for f in ALT_FGS:
    for m in MONTHS:
        for pid in _cand[f]:
            if rnd.random() < 0.25:
                SUB[(f, m, pid)] = rnd.choice(ALT[(f, pid)])
print("대체품 — 완제품 %s · 자재 %d종 · 교체 %d개월분"
      % ("/".join(ALT_FGS), len({p for _, _, p in SUB}), len(SUB)))

# 자재별 월 소요량 → 일평균(d)
need_m = {}
for (f, m), q in PLAN.items():
    for pid, bq in STD.get(f, []):
        pid2 = NEW_PID if (pid == OLD_PID and f == SWAP_FG and m >= SWAP_DATE) else pid
        pid2 = SUB.get((f, m, pid2), pid2)
        need_m[(pid2, m)] = need_m.get((pid2, m), 0) + ALTQ.get((f, pid2), bq) * q
DAILY = {}
for pid in PROD:
    tot = sum(v for (p, _), v in need_m.items() if p == pid)
    DAILY[pid] = round(tot / max((END - START).days, 1), 4)
用 = [p for p in PROD if DAILY[p] > 0]
print("생산 계획 — %d개월 · 작업지시 %d건 · 쓰이는 자재 %d종"
      % (len(MONTHS), len(PLAN), len(用)))

# ══════════════════════════════════════════════════════════
#  4. 안전재고 재계산 (개업 시점 · 신규 4항목 컷오프)
# ══════════════════════════════════════════════════════════
CYCLE = {"A": 30, "B": 90, "C": 180}
for pid, s in SAFE.items():
    sc = {k: s[k] for k in D.SCORE_KEYS}
    g, _pts = D.new_grade(sc)
    ss = D.safe_formula(g, s["Lead_Time"], DAILY.get(pid, 0)) or 0
    cur.execute("UPDATE Safe_tb SET Sf_Lv=?, Sf_Num=?, Usage_Score=NULL WHERE P_ID=?",
                (g, ss, pid))
    s["Sf_Lv"], s["Sf_Num"] = g, ss
    nxt = START + timedelta(days=CYCLE[g])
    cur.execute("INSERT INTO Update_Log_tb (P_ID,Updated_Date,Next_Date,Old_Lv,New_Lv,"
                "Old_Num,New_Num,Old_Usage,New_Usage,EP_ID,Note)"
                " VALUES (?,?,?,NULL,?,NULL,?,NULL,NULL,NULL,?)",
                (pid, d2s(START), d2s(nxt), g, ss, "개업 시 등록"))
print("안전재고 재계산 — A %d · B %d · C %d"
      % tuple(sum(1 for s in SAFE.values() if s["Sf_Lv"] == g) for g in "ABC"))

# ══════════════════════════════════════════════════════════
#  채번 · 기록 도우미
# ══════════════════════════════════════════════════════════
_seq = {}
def nid(pre, d, n=4):
    k = pre + d.replace("-", "")
    _seq[k] = _seq.get(k, 0) + 1
    return "%s%0*d" % (k, n, _seq[k])

TX = []        # (T_ID, Lot_ID, T_Type, T_Date, T_Num, EP_ID)
def tx(lot, typ, dt, num, ep):
    t = nid("T", d2s(dt))
    TX.append((t, lot, typ, d2s(dt), int(num), ep))
    return t

LOTS = {}      # Lot_ID -> dict(P_ID, date, qty, loc, H_ID, remain, site)
def new_lot(pid, dt, qty, hid, ep):
    lid = nid("LOT", d2s(dt))
    loc = D.LOC_BY_MAINCAT.get(pid[0], "L01")
    cur.execute("INSERT INTO Lot_tb (Lot_ID,P_ID,Lot_Date,Loc_ID,P_Qty,EP_ID,H_ID)"
                " VALUES (?,?,?,?,?,?,?)", (lid, pid, d2s(dt), loc, int(qty), ep, hid))
    LOTS[lid] = {"P_ID": pid, "date": dt, "qty": int(qty), "loc": loc,
                 "H_ID": hid, "remain": int(qty), "site": 0,
                 "hide": rnd.random() < STRAND}
    tx(lid, "입고", dt, qty, ep)
    return lid


def stock(pid, asof=None):
    """창고가 **집을 수 있다고 아는** 재고. 묻힌 LOT 은 빠진다.

    ⚠️ 묻힌 LOT 을 여기서 빼지 않으면, 승인 수량은 그걸 믿고 잡히는데 fifo 가
       못 꺼내서 '불출완료' 로 적어 둔 요청이 실제로는 덜 나간다."""
    return sum(l["remain"] for l in LOTS.values()
               if l["P_ID"] == pid and not l["hide"]
               and (not asof or l["date"] <= asof))


def fifo(pid, qty, asof=None):
    """오래된 LOT 부터 꺼낸다. [(lot_id, n), ...]

    ⚠️ `asof` 를 꼭 넘긴다. 입고는 달 단위로 몰아 처리하므로 **그 달 하순에
       도착할 LOT 이 월초 불출 시점에 이미 만들어져 있다.** 날짜를 안 보면
       아직 안 온 물건을 꺼내 쓰게 된다 — 생산일이 입고일보다 빨라졌다."""
    cand = [(lid, l) for lid, l
            in sorted(LOTS.items(), key=lambda kv: (kv[1]["date"], kv[0]))
            if l["P_ID"] == pid and l["remain"] > 0 and not l["hide"]
            and not (asof and l["date"] > asof)]
    # 창고는 앞에 놓인 상자를 집는다 — 선입선출이 늘 지켜지지는 않는다.
    # 뒤에 남은 LOT 이 이번 요청을 덮을 수 있을 때만 건너뛴다. 그러지 않으면
    # 승인 수량만큼 못 꺼내 '불출완료' 로 적어 둔 요청이 실제로는 덜 나간다.
    if (len(cand) > 1 and rnd.random() < FIFO_SKIP
            and sum(l["remain"] for _, l in cand[1:]) >= int(qty)):
        cand = cand[1:]
    out, left = [], int(qty)
    for lid, l in cand:
        if left <= 0:
            break
        n = min(l["remain"], left)
        out.append((lid, n)); left -= n
    return out, left


def fifo_site(pid, qty, asof=None):
    """현장에 나가 있는 LOT 에서 FIFO — 생산 투입·반납이 여기서만 나온다.

    ⚠️ 꺼내는 그 자리에서 깎는다. 밖에서 깎게 두면 이 함수를 두 번 부를 때
       (대체품이 모자라 원자재로 보충하는 경우) **같은 LOT 이 두 번 배정**돼
       현장 보유가 음수가 된다 — 실제로 투입이 불출보다 많아졌다."""
    out, left = [], int(qty)
    for lid, l in sorted(LOTS.items(), key=lambda kv: (kv[1]["date"], kv[0])):
        if left <= 0:
            break
        if l["P_ID"] != pid or l["site"] <= 0:
            continue
        if asof and l["date"] > asof:
            continue
        n = min(l["site"], left)
        l["site"] -= n
        out.append((lid, n)); left -= n
    return out, left


# ══════════════════════════════════════════════════════════
#  5. 초기 재고 (조건 4) — 개업 전에 들여놓는다
# ══════════════════════════════════════════════════════════
PRE_IN = date(2022, 12, 20)
by_brn = {}
for pid in 用:
    s, p = SAFE[pid], PROD[pid]
    cover = (s["Sf_Num"] or 0) + DAILY[pid] * (s["Lead_Time"] or 14) * 1.6
    q = D.round_order_qty(cover, p["MinOrderQty"], p["PkgUnit"])
    if q > 0:
        by_brn.setdefault((p["BRN"], int(s["Lead_Time"] or 14)), []).append((pid, q))

# ⚠️ 발주일을 한 날짜로 못박으면 **리드타임이 짧은 자재가 전부 납기 지연**이 된다.
#    협력사 화면은 자재별 계획 리드타임으로 줄마다 판정하는데, 개업 재고 176줄이
#    35일 전 발주 한 장에 묶여 있어서 준수율을 5%p 끌어내리고 있었다.
#    장납기 자재를 더 일찍 발주하는 건 구매 담당자가 실제로 하는 일이다.
ep0 = rnd.choice(WORKER)
for (brn, lead), items in sorted(by_brn.items()):
    od = PRE_IN - timedelta(days=lead)
    while od.weekday() >= 5:
        od -= timedelta(days=1)
    hid = nid("PO", d2s(od))
    cur.execute("INSERT INTO Purchase_Header_tb (H_ID,BRN,P_Date) VALUES (?,?,?)",
                (hid, brn, d2s(od)))
    for n, (pid, q) in enumerate(items, start=1):
        cur.execute("INSERT INTO Purchase_Detail_tb (H_ID,Purchase_num,P_ID,P_Qty,Unit_Price)"
                    " VALUES (?,?,?,?,?)", (hid, n, pid, q, PROD[pid]["P_Price"]))
        new_lot(pid, PRE_IN, q, hid, ep0)
print("초기 재고 — 발주 %d장 · LOT %d개 · %s개"
      % (len(by_brn), len(LOTS), format(sum(l['qty'] for l in LOTS.values()), ",")))

# ══════════════════════════════════════════════════════════
#  6. 월 루프 — 발주 · 입고 · 요청 → 승인 → 불출 · 생산
# ══════════════════════════════════════════════════════════
ARRIVE = {}          # 입고 예정 (도착일 -> [(hid, num, pid, 발주량, 실입고, ep)])
OPEN_PO = []         # 미입고로 남길 발주
claims, returns, counts, prices = 0, 0, 0, 0
req_rows, prod_rows = 0, 0
ontime_ok, ontime_all = 0, 0

for m in MONTHS:
    mdays = (date(m.year + (m.month == 12), m.month % 12 + 1, 1) - m).days
    last = min(END, m + timedelta(days=mdays - 1))

    # ── 6a. 이번 달 도착분 입고 ────────────────────────────
    # ⚠️ `m <= k` 로 자르면 안 된다. 입고(6a)가 발주(6c)보다 먼저 도는데,
    #    리드타임이 짧은 발주는 **그 달 안에** 도착한다. 그걸 다음 달에 찾으면
    #    이미 지난 날짜라 영영 안 집힌다 — 리드타임 14일짜리 자재는 초기 재고를
    #    쓰고 나서 한 번도 입고되지 않았다. 지난 것은 전부 집는다.
    for dt in sorted(k for k in list(ARRIVE) if k <= last):
        for hid, num, pid, ordq, realq, ep in ARRIVE.pop(dt):
            if realq <= 0:
                continue
            lid = new_lot(pid, dt, realq, hid, ep)
            if realq != ordq:
                chg = nid("CHG", d2s(dt))
                diff = round((realq - ordq) * float(PROD[pid]["P_Price"] or 0))
                cur.execute(
                    "INSERT INTO Purchase_Change_tb (Chg_ID,H_ID,Purchase_num,Ord_P_ID,"
                    "In_P_ID,Ord_Qty,In_Qty,Ord_Amt,In_Amt,Diff_Amt,Chg_Type,Settle,"
                    "Reason,Chg_Date,EP_ID,Lot_ID) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (chg, hid, num, pid, pid, ordq, realq,
                     round(ordq * float(PROD[pid]["P_Price"] or 0)),
                     round(realq * float(PROD[pid]["P_Price"] or 0)), diff,
                     "수량변경", "차감" if diff < 0 else "추가청구",
                     "협력사 사정으로 일부만 출하", d2s(dt), ep, lid))
            # 입고 검수 불량 (조건 9)
            if rnd.random() < 0.035 and realq > 40:
                badq = max(1, int(realq * rnd.uniform(0.02, 0.07)))
                tid = tx(lid, "불량", dt, badq, ep)
                LOTS[lid]["remain"] -= badq
                rid = nid("RMA", d2s(dt))
                res = rnd.choice(["대체입고", "환불", "폐기"])
                done = wd(dt, rnd.randint(5, 20))
                cur.execute(
                    "INSERT INTO Inbound_Claim_tb (Claim_ID,Lot_ID,H_ID,P_ID,Claim_Qty,"
                    "Claim_Type,Resolution,Status,T_ID,New_Lot_ID,Amount,Reason,"
                    "Claim_Date,Done_Date,EP_ID) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, lid, hid, pid, badq, "입고검수", res, "완료", tid, None,
                     round(badq * float(PROD[pid]["P_Price"] or 0)),
                     "입고 검수에서 치수 불량 발견", d2s(dt), d2s(done), rnd.choice(MANAGER)))
                claims += 1

    # ── 6b. 불출 요청 → 승인 → 불출 → 생산 ────────────────
    req_day = wd(m, 1)
    for fg in FGS:
        f = fg["FG_ID"]
        q = PLAN.get((f, m))
        if not q:
            continue
        items = []
        for pid, bq in STD.get(f, []):
            use = NEW_PID if (pid == OLD_PID and f == SWAP_FG and m >= SWAP_DATE) else pid
            use = SUB.get((f, m, use), use)      # 조건 6 — 이번 달은 대체품으로 간다
            need = int(round(ALTQ.get((f, use), bq) * q))
            site = sum(l["site"] for l in LOTS.values() if l["P_ID"] == use)
            want = max(0, need - site)
            pkg = int(PROD[use]["PkgUnit"] or 1)
            rq = ((want + pkg - 1) // pkg) * pkg if want else 0
            items.append({"pid": use, "need": need, "site": site,
                          "stock": stock(use, req_day), "req": rq, "pkg": pkg})
        if not any(i["req"] for i in items):
            continue
        rid = nid("REQ", d2s(req_day))
        wo = nid("WO", d2s(req_day))
        reqer = rnd.choice(WORKER)
        appr = rnd.choice([a for a in APPROVER if a != reqer])
        cur.execute("INSERT INTO Disburse_Req_tb (Req_ID,Req_Date,FG_ID,Plan_Qty,"
                    "Work_Order,EP_ID,Status,Note,Appr_EP_ID,Appr_Date,Appr_Note)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, d2s(req_day), f, q, wo, reqer, "불출완료", "월간 생산 투입분",
                     appr, d2s(wd(req_day, 1)), "승인"))
        dis_day = wd(req_day, 2)
        for n, it in enumerate(items, start=1):
            # 승인 수량 — 재고가 모자라면 재고만큼 깎는다
            # ⚠️ 포장 배수로 내리면 안 된다. 포장단위 1,000 짜리 자재는 재고가
            #    900 이어도 승인이 0 이 돼 **창고가 비어도 불출이 안 일어났다.**
            #    포장 배수는 '요청' 의 규칙이지 '승인' 의 규칙이 아니다.
            appr_q = min(it["req"], it["stock"]) if it["req"] else 0
            alloc, short = fifo(it["pid"], appr_q, dis_day)
            done = sum(n2 for _, n2 in alloc)
            cur.execute("INSERT INTO Disburse_Req_Item_tb (Req_ID,Req_num,P_ID,Need_Qty,"
                        "Site_Qty,Stock_Qty,Req_Qty,Pkg_Unit,Is_Manual,Appr_Qty,Done_Qty)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (rid, n, it["pid"], it["need"], it["site"], it["stock"],
                         it["req"], it["pkg"], "N", appr_q, done))
            req_rows += 1
            for lid, cnt in alloc:
                tx(lid, "불출", dis_day, cnt, reqer)
                LOTS[lid]["remain"] -= cnt
                LOTS[lid]["site"] += cnt

        # 생산 실적 — 현장에 나간 것에서 FIFO 로 투입
        prod_day = wd(dis_day, rnd.randint(2, 6))
        if prod_day > END:
            prod_day = END
        # ⚠️ **자재가 모자라면 그만큼만 만든다.** 계획 대수를 그대로 적고 투입만 모자라게
        #    두면 BOM 대조가 줄마다 '차이' 로 뜬다 — 첫 3년치가 일치율 84% 였고,
        #    투입/소요 비율이 0.4~1.1 로 퍼져 있었다. 현실의 공장도 자재가 없으면
        #    그만큼 덜 만든다. 못 쓴 자재는 현장에 남아 다음 달 생산으로 넘어간다.
        uses = []
        for pid, bq in STD.get(f, []):
            use = NEW_PID if (pid == OLD_PID and f == SWAP_FG and m >= SWAP_DATE) else pid
            use = SUB.get((f, m, use), use)      # 요청에 올린 그것을 투입한다
            uses.append((pid, use, ALTQ.get((f, use), bq)))
        made = q
        for pid, use, bq in uses:
            if bq <= 0:
                continue
            avail = sum(l["site"] for l in LOTS.values()
                        if l["P_ID"] == use and l["date"] <= prod_day)
            if use != pid:        # 대체품이 모자라면 원자재로 메운다
                avail += sum(l["site"] for l in LOTS.values()
                             if l["P_ID"] == pid and l["date"] <= prod_day)
            made = min(made, int(avail // bq))
        for pid, use, bq in uses:
            if made <= 0:
                break
            take = int(round(bq * made))
            alloc, short = fifo_site(use, take, prod_day)
            if short and use != pid:
                alloc2, _ = fifo_site(pid, short, prod_day)   # 대체품이 모자라면 원자재로
                alloc += alloc2
            for lid, cnt in alloc:
                pr = nid("PR", d2s(prod_day))
                cur.execute("INSERT INTO Production_tb (Prod_ID,FG_ID,P_ID,Lot_ID,"
                            "Prod_Date,Prod_Qty,EP_ID,Work_Order,Note)"
                            " VALUES (?,?,?,?,?,?,?,?,?)",
                            (pr, f, LOTS[lid]["P_ID"], lid, d2s(prod_day), cnt,
                             reqer, wo, None))
                prod_rows += 1

    # ── 6c. 발주 (조건 7) ─────────────────────────────────
    # ⚠️ **마지막 달은 발주를 돌리지 않는다.** 여기서 전부 발주해 버리면
    #    오늘 기준으로 '발주해야 하는 자재' 가 한 종도 안 남아서, 등급별
    #    발주 정책(A 수동 · B 승인 · C 자동) 화면이 텅 빈 채로 열린다.
    #    마지막 한 바퀴는 **사람이 아니라 제안 엔진이 내는 것**으로 둔다 —
    #    마무리 스크립트가 make_plans() 를 돌려 그 상태를 만든다.
    if m == MONTHS[-1]:
        continue

    ord_day = wd(m, 7)
    if ord_day > END:
        ord_day = END
    pend = {}
    for pid in 用:
        s, p = SAFE[pid], PROD[pid]
        lead = int(s["Lead_Time"] or 14)
        have = stock(pid) + sum(
            r[4] for k, v in ARRIVE.items() for r in v if r[2] == pid)
        trigger = (s["Sf_Num"] or 0) + DAILY[pid] * lead
        if have > trigger:
            continue
        # 한 번에 75일치를 채우면 자재당 발주가 두세 달에 한 번뿐이라,
        # 어느 시점을 찍어도 '지금 발주할 자재' 가 몇 종 안 된다(7종이었다).
        # 30일치로 줄이면 발주가 자주 돌고 재고도 과하게 쌓이지 않는다.
        target = (s["Sf_Num"] or 0) + DAILY[pid] * (lead + 30)
        q = D.round_order_qty(target - have, p["MinOrderQty"], p["PkgUnit"])
        if q > 0:
            pend.setdefault(p["BRN"], []).append((pid, q, lead))
    for brn, items in sorted(pend.items()):
        hid = nid("PO", d2s(ord_day))
        cur.execute("INSERT INTO Purchase_Header_tb (H_ID,BRN,P_Date) VALUES (?,?,?)",
                    (hid, brn, d2s(ord_day)))
        ep = rnd.choice(WORKER)
        for n, (pid, q, lead) in enumerate(items, start=1):
            cur.execute("INSERT INTO Purchase_Detail_tb (H_ID,Purchase_num,P_ID,P_Qty,"
                        "Unit_Price) VALUES (?,?,?,?,?)",
                        (hid, n, pid, q, PROD[pid]["P_Price"]))
            # ── 조건 8 — 준수율 80%
            ontime_all += 1
            r = rnd.random()
            delay, realq, keep = 0, q, False
            if r < 0.80:
                delay = -rnd.randint(0, 2); keep = True
            elif r < 0.92:
                delay = rnd.randint(2, 12)                     # 납기 지연
            else:
                pkg = int(PROD[pid]["PkgUnit"] or 1)           # 부족 입고
                realq = max(pkg, q - pkg * rnd.randint(1, 3))
                delay = rnd.randint(0, 5)
            # ⚠️ 협력사 화면은 **자재별 계획 리드타임**으로 줄마다 납기를 판정한다.
            #    wd() 가 주말을 피해 **뒤로** 미루기 때문에 제때 온 것으로 뽑혔어도
            #    하루 이틀 넘겨 지연으로 찍혔다 — 준수율이 80% 가 아니라 44% 로
            #    나온 원인이다. 넘겼으면 계획 납기 안으로 당긴다.
            due = ord_day + timedelta(days=lead)
            arr = wd(ord_day + timedelta(days=lead + delay))
            if keep and arr > due:
                back = due
                while back.weekday() >= 5:
                    back -= timedelta(days=1)
                if back > ord_day:
                    arr = back
            if (arr - ord_day).days <= lead:
                ontime_ok += 1
            if arr <= END:
                ARRIVE.setdefault(arr, []).append((hid, n, pid, q, realq, ep))
            else:
                OPEN_PO.append(hid)

    # ── 6d. 현장 반납 (분기 1회) ──────────────────────────
    if m.month % 3 == 2:
        site_lots = [(lid, l) for lid, l in LOTS.items() if l["site"] > 0]
        rnd.shuffle(site_lots)
        pick = site_lots[:rnd.randint(2, 5)]
        if pick:
            rday = wd(m, 20)
            if rday <= END:
                ret = nid("RET", d2s(rday))
                ep = rnd.choice(WORKER)
                for ln, (lid, l) in enumerate(pick, start=1):
                    qn = max(1, int(l["site"] * rnd.uniform(0.3, 1.0)))
                    good, bad = qn, 0
                    if rnd.random() < 0.3:
                        bad = max(1, int(qn * rnd.uniform(0.03, 0.1))); good = qn - bad
                    t1 = tx(lid, "반납", rday, qn, ep)
                    l["site"] -= qn; l["remain"] += qn
                    t2 = None
                    if bad:
                        t2 = tx(lid, "불량", rday, bad, ep); l["remain"] -= bad
                    cur.execute(
                        "INSERT INTO Site_Return_tb (Ret_ID,Line,Ret_Date,Lot_ID,P_ID,"
                        "Ret_Qty,Reason_Cd,Reason,Work_Order,Amount,Good_Qty,Bad_Qty,"
                        "Bad_Cd,Bad_Note,Bad_T_ID,Claim_ID,T_ID,EP_ID)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (ret, ln, d2s(rday), lid, l["P_ID"], qn, "잔여반납",
                         "생산 후 남은 자재", None,
                         round(qn * float(PROD[l["P_ID"]]["P_Price"] or 0)),
                         good, bad, "변질" if bad else None,
                         "표면 변색" if bad else None, t2, None, t1, ep))
                returns += 1

    # ── 6e. 재고 실사 (분기 1회 · 등급 순환) ───────────────
    if m.month % 3 == 0:
        cday = wd(m, 24)
        if cday <= END:
            grade = "ABC"[(m.month // 3 - 1) % 3]
            # ⚠️ `date <= cday` 를 빼면 **그 달 하순에 도착할 LOT 을 실사일에 센다.**
            #    차이 거래가 입고일보다 앞선 날짜로 찍혔다 (fifo 의 asof 와 같은 함정).
            tgt = [(lid, l) for lid, l in LOTS.items()
                   if l["remain"] > 0 and l["date"] <= cday
                   and SAFE.get(l["P_ID"], {}).get("Sf_Lv") == grade]
            rnd.shuffle(tgt); tgt = tgt[:rnd.randint(8, 20)]
            if tgt:
                cid = nid("CNT", d2s(cday))
                w = rnd.choice(WORKER)
                a = rnd.choice([x for x in BOSS if x != w])
                cur.execute("INSERT INTO Stock_Count_tb (Count_ID,Count_Date,Scope,"
                            "Scope_Val,Status,EP_ID,Appr_EP_ID,Appr_Date,Appr_Note,Note)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (cid, d2s(cday), "등급", grade, "완료", w, a, d2s(cday),
                             "정기 순환 실사", None))
                for ln, (lid, l) in enumerate(tgt, start=1):
                    # 사유 6종이 전부 돈다 — 부족(폐기·분실·미기록불출) ·
                    # 과다(미기록반납·과다입고) · 수량은 맞고 자리가 틀린 구역오류.
                    # 거래 유형은 db 의 COUNT_REASONS 를 그대로 따른다.
                    book = l["remain"]
                    diff, cd, loc_to = 0, None, None
                    r2 = rnd.random()
                    if r2 < 0.15:
                        diff = -max(1, int(book * rnd.uniform(0.01, 0.05)))
                        cd = rnd.choice(["폐기", "분실", "미기록불출"])
                    elif r2 < 0.23:
                        diff = max(1, int(book * rnd.uniform(0.01, 0.04)))
                        cd = rnd.choice(["미기록반납", "과다입고"])
                    elif r2 < 0.26:
                        cd = "구역오류"
                        loc_to = rnd.choice([x for x in LOCS if x != l["loc"]])
                    real = book + diff
                    tid = None
                    if cd:
                        meta = D.COUNT_REASONS[cd]
                        tid = tx(lid, meta["tx"], cday, abs(diff) or book, a)
                        l["remain"] += diff
                        if loc_to:
                            cur.execute("UPDATE Lot_tb SET Loc_ID=? WHERE Lot_ID=?",
                                        (loc_to, lid))
                            l["loc"] = loc_to
                    cur.execute(
                        "INSERT INTO Stock_Count_Item_tb (Count_ID,Line,Lot_ID,P_ID,"
                        "Book_Qty,Real_Qty,Skipped,Reason_Cd,Reason,Loc_To,T_ID,Counted_At)"
                        " VALUES (?,?,?,?,?,?,NULL,?,?,?,?,?)",
                        (cid, ln, lid, l["P_ID"], book, real, cd,
                         "실사 차이" if cd else None, loc_to, tid, d2s(cday)))
                counts += 1

    # ── 6f. 단가 변경 (반기 1회 몇 종) ────────────────────
    if m.month in (3, 9) and m.year > 2023:
        pday = wd(m, 12)
        if pday <= END:
            for pid in rnd.sample(用, min(4, len(用))):
                old = float(PROD[pid]["P_Price"] or 0)
                new = round(old * rnd.uniform(1.03, 1.12))
                if new == old:
                    continue
                cur.execute("INSERT INTO Price_Log_tb (Price_ID,P_ID,Old_Price,New_Price,"
                            "Diff,Diff_Pct,Reason_Cd,Reason,Start_Date,EP_ID)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (nid("PRC", d2s(pday)), pid, old, new, new - old,
                             round((new - old) / old * 100, 1),
                             rnd.choice(["협력사인상", "원자재시세", "환율변동", "계약갱신"]),
                             "정기 단가 재산정", d2s(pday), rnd.choice(MANAGER)))
                cur.execute("UPDATE Product_tb SET P_Price=? WHERE P_ID=?", (new, pid))
                PROD[pid]["P_Price"] = new
                prices += 1

print("월 루프 끝 — 거래 %s · LOT %s · 요청라인 %s · 생산 %s"
      % tuple(format(x, ",") for x in (len(TX), len(LOTS), req_rows, prod_rows)))
print("  클레임 %d · 반납 %d · 실사 %d · 단가변경 %d" % (claims, returns, counts, prices))
print("  입고 준수 %d/%d = %.1f%%" % (ontime_ok, ontime_all, ontime_ok / max(ontime_all, 1) * 100))

# ══════════════════════════════════════════════════════════
#  7. 거래 일괄 적재 · 미입고 발주 남기기
# ══════════════════════════════════════════════════════════
TX.sort(key=lambda r: (r[3], r[0]))
cur.executemany("INSERT INTO Transaction_tb (T_ID,Lot_ID,T_Type,T_Date,T_Num,EP_ID)"
                " VALUES (?,?,?,?,?,?)", TX)

# 아직 도착하지 않은 발주는 미입고로 남는다 (시연에 쓸 대기 건)
left = sum(len(v) for v in ARRIVE.values())
print("미입고로 남은 발주 라인 %d개" % left)

con.commit()
print("\n저장 — %s (%.1fMB)" % (OUT, os.path.getsize(OUT) / 1024 / 1024))
con.close()
