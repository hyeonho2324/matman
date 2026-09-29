from flask import Flask, render_template, jsonify, request
import json, os, re, sqlite3

import db

app = Flask(__name__)

# ── 메뉴 구조 ───────────────────────────────────────────────
MENUS = [
    {
        "group": "대시보드",
        "menu_items": [
            {"id": "dashboard",    "label": "메인 대시보드",      "icon": "ti-dashboard",         "url": "/"},
        ]
    },
    {
        "group": "자재 관리",
        "menu_items": [
            {"id": "products",     "label": "자재 목록",          "icon": "ti-package",           "url": "/products"},
            {"id": "lot",          "label": "LOT 상세",           "icon": "ti-box",               "url": "/lot"},
            {"id": "bom",          "label": "BOM 관리",           "icon": "ti-sitemap",           "url": "/bom"},
            {"id": "safety_stock", "label": "안전재고",           "icon": "ti-shield-check",      "url": "/safety-stock"},
            {"id": "abc",          "label": "ABC 분석",           "icon": "ti-chart-bar",         "url": "/abc"},
        ]
    },
    {
        "group": "입출고",
        "menu_items": [
            {"id": "inbound",      "label": "입고 처리",          "icon": "ti-arrow-bar-to-down", "url": "/inbound"},
            {"id": "disburse_request", "label": "불출 요청",      "icon": "ti-clipboard-list",    "url": "/disburse-request"},
            {"id": "disburse",     "label": "불출 처리",          "icon": "ti-arrow-bar-up",      "url": "/disburse"},
            {"id": "tx_history",   "label": "입출고 이력",        "icon": "ti-history",           "url": "/tx-history"},
            {"id": "picking",      "label": "피킹리스트",         "icon": "ti-list-check",        "url": "/picking"},
            {"id": "approval",     "label": "불출 승인",          "icon": "ti-check",             "url": "/approval"},
            {"id": "scanner",      "label": "스캐너",             "icon": "ti-scan",              "url": "/scanner"},
        ]
    },
    {
        "group": "구매 / 협력사",
        "menu_items": [
            {"id": "purchase",     "label": "구매 발주",          "icon": "ti-truck-delivery",    "url": "/purchase"},
            {"id": "suppliers",    "label": "협력사",             "icon": "ti-building-store",    "url": "/suppliers"},
            {"id": "calendar",     "label": "발주 캘린더",        "icon": "ti-calendar",          "url": "/calendar"},
        ]
    },
    {
        "group": "생산",
        "menu_items": [
            {"id": "production",   "label": "생산 실적",          "icon": "ti-tool",              "url": "/production"},
        ]
    },
    {
        "group": "재고 현황",
        "menu_items": [
            {"id": "stock_map",    "label": "재고 현황 지도",     "icon": "ti-building-warehouse", "url": "/stock-map"},
        ]
    },
    {
        "group": "분석 / 예측",
        "menu_items": [
            {"id": "risk_radar",   "label": "납기 리스크 레이더", "icon": "ti-radar",             "url": "/risk-radar"},
            {"id": "forecast",     "label": "수요 예측",          "icon": "ti-chart-line",        "url": "/forecast"},
            {"id": "simulator",    "label": "발주 시뮬레이터",    "icon": "ti-adjustments",       "url": "/simulator"},
            {"id": "report",       "label": "월간 리포트",        "icon": "ti-file-text",         "url": "/report"},
            {"id": "wizard",       "label": "안전재고 일괄 갱신", "icon": "ti-wand",              "url": "/wizard"},
        ]
    },
    {
        "group": "관리",
        "menu_items": [
            {"id": "users",        "label": "사용자 관리",        "icon": "ti-users",             "url": "/users"},
        ]
    },
]

def get_menu_context(active_id):
    return {"menus": MENUS, "active_id": active_id}

# ── 라우팅 ──────────────────────────────────────────────────
@app.route("/")
def dashboard():
    conn = db.connect()
    try:
        data = db.dashboard(conn)
    finally:
        conn.close()
    return render_template("dashboard.html", **get_menu_context("dashboard"),
                           page_title="메인 대시보드", d=data)

@app.route("/products")
def products():
    return render_template("products.html", **get_menu_context("products"), page_title="자재 목록")

@app.route("/products/<pid>")
def product_detail(pid):
    return render_template("product_detail.html", **get_menu_context("products"), page_title="품목 상세", pid=pid)

@app.route("/lot")
def lot():
    return render_template("lot.html", **get_menu_context("lot"), page_title="LOT 상세")

@app.route("/bom")
def bom():
    return render_template("bom.html", **get_menu_context("bom"), page_title="BOM 관리")

@app.route("/safety-stock")
def safety_stock():
    return render_template("safety_stock.html", **get_menu_context("safety_stock"), page_title="안전재고")

@app.route("/abc")
def abc():
    return render_template("abc.html", **get_menu_context("abc"), page_title="ABC 분석")

@app.route("/inbound")
def inbound():
    return render_template("inbound.html", **get_menu_context("inbound"), page_title="입고 처리")

@app.route("/disburse-request")
def disburse_request():
    return render_template("disburse_request.html",
                           **get_menu_context("disburse_request"), page_title="불출 요청")


@app.route("/disburse")
def disburse():
    return render_template("disburse.html", **get_menu_context("disburse"), page_title="불출 처리")

@app.route("/tx-history")
def tx_history():
    return render_template("tx_history.html", **get_menu_context("tx_history"), page_title="입출고 이력")

@app.route("/picking")
def picking():
    return render_template("picking.html", **get_menu_context("picking"), page_title="피킹리스트")

@app.route("/approval")
def approval():
    return render_template("approval.html", **get_menu_context("approval"), page_title="불출 승인")

@app.route("/scanner")
def scanner():
    return render_template("scanner.html", **get_menu_context("scanner"), page_title="스캐너")

@app.route("/purchase")
def purchase():
    return render_template("purchase.html", **get_menu_context("purchase"), page_title="구매 발주")

@app.route("/suppliers")
def suppliers():
    return render_template("suppliers.html", **get_menu_context("suppliers"), page_title="협력사")

@app.route("/calendar")
def calendar():
    return render_template("calendar.html", **get_menu_context("calendar"), page_title="발주 캘린더")

@app.route("/production")
def production():
    return render_template("production.html", **get_menu_context("production"), page_title="생산 실적")

@app.route("/stock-map")
def stock_map():
    return render_template("stock_map.html", **get_menu_context("stock_map"), page_title="재고 현황 지도")

@app.route("/risk-radar")
def risk_radar():
    return render_template("risk_radar.html", **get_menu_context("risk_radar"), page_title="납기 리스크 레이더")

@app.route("/forecast")
def forecast():
    return render_template("forecast.html", **get_menu_context("forecast"), page_title="수요 예측")

@app.route("/simulator")
def simulator():
    return render_template("simulator.html", **get_menu_context("simulator"), page_title="발주 시뮬레이터")

@app.route("/report")
def report():
    return render_template("report.html", **get_menu_context("report"), page_title="월간 리포트")

@app.route("/wizard")
def wizard():
    return render_template("wizard.html", **get_menu_context("wizard"), page_title="안전재고 일괄 갱신")

@app.route("/users")
def users():
    return render_template("users.html", **get_menu_context("users"), page_title="사용자 관리")

# ── Embed 라우팅 (iframe 내부용) ────────────────────────────
EMBED_MAP = {
    "products":     "products",
    "lot":          "lot",
    "bom":          "bom",
    "safety_stock": "safety_stock",
    "abc":          "abc",
    "inbound":      "inbound",
    "disburse":     "disburse",
    "tx_history":   "tx_history",
    "picking":      "picking",
    "approval":     "approval",
    "scanner":      "scanner",
    "purchase":     "purchase",
    "suppliers":    "suppliers",
    "calendar":     "calendar",
    "production":   "production",
    "stock_map":    "stock_map",
    "risk_radar":   "risk_radar",
    "forecast":     "forecast",
    "simulator":    "simulator",
    "report":       "report",
    "wizard":       "wizard",
    "users":        "users",
}

# ── embed 화면별 데이터 공급 ────────────────────────────────
# 실제 DB(data/erp.db)를 읽어 템플릿에 넘긴다.
# 여기에 등록되지 않은 화면은 데이터 없이 렌더링된다(아직 더미데이터 화면).

def _ctx_safety_stock():
    conn = db.connect()
    try:
        rows = db.safety_stock_list(conn)
        return {
            "rows": rows,
            "summary": db.safety_stock_summary(rows),
            "op_days": db.operating_days(conn),
            "z_by_grade": db.Z_BY_GRADE,
        }
    finally:
        conn.close()


def _ctx_products():
    conn = db.connect()
    try:
        rows = db.product_list(conn)
        return {
            "rows": rows,
            "summary": db.product_summary(rows),
            "lots": db.lot_list(conn),
            "bom_usage": db.bom_usage(conn),
            "cats": db.category_tree(conn),
            "maincat": db.MAINCAT,
        }
    finally:
        conn.close()


def _ctx_bom():
    conn = db.connect()
    try:
        brows = db.bom_rows(conn)
        fgs = db.fg_list(conn, brows)
        return {
            "bom": brows,
            "fgs": fgs,
            "summary": db.bom_summary(fgs, brows),
        }
    finally:
        conn.close()


def _ctx_abc(period=""):
    conn = db.connect()
    try:
        rows = db.abc_analysis(conn, period)
        base = conn.execute(
            f"SELECT MAX(T_Date) FROM Transaction_tb WHERE {db.DEMAND_IN}").fetchone()[0]
        return {"rows": rows, "summary": db.abc_summary(rows),
                "period": period, "period_label": db.period_label(base, period),
                "months": db.month_list(conn, "Transaction_tb", "T_Date", db.DEMAND_IN)}
    finally:
        conn.close()


def _ctx_tx_history():
    conn = db.connect()
    try:
        rows = db.transaction_list(conn)
        lots = db.lot_trace(conn)
        prods = db.tx_products(conn)
        return {
            "rows": rows,
            "lots": lots,
            "prods": prods,
            "locs": db.tx_locations(conn),
            "monthly": db.tx_monthly(conn),
            "tx_meta": db.TX_META,
            "summary": db.tx_summary(rows, lots, prods),
        }
    finally:
        conn.close()


def _ctx_suppliers(period=""):
    conn = db.connect()
    try:
        comps = db.supplier_list(conn, period)
        base = conn.execute("SELECT MAX(P_Date) FROM Purchase_Header_tb").fetchone()[0]
        return {
            "comps": comps,
            "items": db.supplier_items(conn),
            "summary": db.supplier_summary(comps),
            "period": period, "period_label": db.period_label(base, period),
            "months": db.month_list(conn, "Purchase_Header_tb", "P_Date"),
        }
    finally:
        conn.close()


def _ctx_production():
    conn = db.connect()
    try:
        orders = db.production_orders(conn)
        return {
            "orders": orders,
            "prods": db.prod_products(conn),
            "monthly": db.production_monthly(conn),
            "summary": db.production_summary(orders),
        }
    finally:
        conn.close()


def _ctx_purchase():
    conn = db.connect()
    try:
        orders = db.purchase_orders(conn)
        cands, base = db.order_candidates(conn)
        return {
            "orders": orders,
            "prods": db.prod_products(conn),
            "monthly": db.purchase_monthly(conn),
            "summary": db.purchase_summary(orders),
            # 발주하기 워크플로우용 — 자재 200종 + 권장 발주량 + 미입고 발주
            "cands": cands,
            "base_date": base,
        }
    finally:
        conn.close()


def _ctx_stock_map():
    conn = db.connect()
    try:
        zones = db.stock_zones(conn)
        return {
            "zones": zones,
            "items": db.stock_items(conn),
            "summary": db.stock_summary(zones),
        }
    finally:
        conn.close()


def _ctx_lot():
    conn = db.connect()
    try:
        rows, base = db.lot_detail(conn)
        return {
            "rows": rows,
            "summary": db.lot_summary(rows, base),
        }
    finally:
        conn.close()


def _ctx_forecast():
    conn = db.connect()
    try:
        rows, base, span = db.forecast_list(conn)
        return {
            "rows": rows,
            "monthly": db.forecast_monthly(conn),
            "summary": db.forecast_summary(rows, base, span),
        }
    finally:
        conn.close()


def _ctx_calendar():
    conn = db.connect()
    try:
        cal = db.calendar_data(conn)
        return {"cal": cal, "summary": db.calendar_summary(cal)}
    finally:
        conn.close()


def _ctx_risk_radar():
    conn = db.connect()
    try:
        rows, base, span = db.risk_radar(conn)
        return {"rows": rows, "summary": db.risk_summary(rows), "base": base}
    finally:
        conn.close()


def _ctx_users(period=""):
    conn = db.connect()
    try:
        rows = db.user_list(conn, period)
        base = db.user_base(conn)      # user_list 와 같은 기준일을 쓴다
        return {"rows": rows, "summary": db.user_summary(rows, conn, period, base),
                "tx_meta": db.TX_META,
                # 직급 표시 순서. dict 키 순서에 기대면 안 된다 —
                # Flask 의 tojson 이 키를 가나다순으로 정렬해버린다.
                "pos_order": db.POSITION_ORDER,
                "period": period, "period_label": db.period_label(base, period),
                "months": db.month_list(conn, "Transaction_tb", "T_Date")}
    finally:
        conn.close()


def _ctx_report():
    conn = db.connect()
    try:
        months = db.monthly_report(conn)
        return {"months": months, "summary": db.report_summary(months)}
    finally:
        conn.close()


def _ctx_wizard():
    conn = db.connect()
    try:
        rows, base, span, due = db.wizard_data(conn)
        targets, _b, entry = db.safety_targets(conn)
        skip = {r["P_ID"]: r["skip"] for r in targets}
        for r in rows:
            r["skip"] = skip.get(r["P_ID"])
        return {
            "rows": rows, "summary": db.wizard_summary(rows, base, due), "span": span,
            "entry": entry,
            "target_cnt": sum(1 for r in rows if not r["skip"]),
            "runs": db.safety_runs(conn),
            "workers": db._rows(conn, "SELECT EP_ID, Name, Position FROM User_tb ORDER BY Name"),
        }
    finally:
        conn.close()


def _ctx_simulator():
    conn = db.connect()
    try:
        items, base, span = db.simulator_items(conn)
        return {"items": items, "base": base, "span": span}
    finally:
        conn.close()


def _ctx_picking():
    conn = db.connect()
    try:
        items, base = db.picking_source(conn)
        return {"items": items, "base": base}
    finally:
        conn.close()


_WB_CACHE = {}


def _ctx_disburse_request():
    """불출 요청 화면 — 완제품·BOM·요청 현황."""
    conn = db.connect()
    try:
        ctx = db.request_source(conn)
        ctx["requests"] = db.request_list(conn)
        return ctx
    finally:
        conn.close()


def _ctx_approval():
    """불출 승인 화면 — 승인 대기 요청 · 처리 이력 · 승인 권한자.

    승인은 요청 데이터만 보면 되므로 workbench 공용 조회를 쓰지 않는다.
    """
    conn = db.connect()
    try:
        return db.approval_queue(conn)
    finally:
        conn.close()


def _ctx_workbench():
    """입고/불출/스캐너 공용 참조 데이터.
    세 화면이 같은 조회를 하므로 한 번만 읽어 재사용한다."""
    conn = db.connect()
    try:
        return db.workbench(conn)
    finally:
        conn.close()



# ── 발주 등록 API ───────────────────────────────────────────
# 이 앱에서 유일하게 DB 를 쓰는 엔드포인트다.
# 나머지 화면은 전부 조회 전용이라 GET 만 있다.

@app.route("/api/purchase/preview", methods=["POST"])
def api_purchase_preview():
    """등록 전 미리보기 — 협력사별로 발주서가 어떻게 나뉘는지 보여준다."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        groups, errors = db.preview_purchase(
            conn, _entry_date(body), body.get("items") or [])
        return jsonify({"ok": not errors, "groups": groups, "errors": errors})
    finally:
        conn.close()


@app.route("/api/purchase", methods=["POST"])
def api_purchase_create():
    """발주 등록. 협력사별로 발주서를 나눠 저장한다."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        created, errors = db.create_purchase(
            conn, _entry_date(body), body.get("items") or [])
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        return jsonify({"ok": True, "orders": created})
    except sqlite3.OperationalError as e:
        # 배포 환경에서 DB 파일이 읽기 전용이면 여기로 온다
        return jsonify({"ok": False, "errors": ["DB에 쓸 수 없습니다: %s" % e]}), 500
    finally:
        conn.close()


def _entry_date(body):
    """등록일(발주일·불출일). 지정이 없으면 데이터의 마지막 거래일 다음 날.

    더미데이터의 시간축(2025-07 ~ 2026-02)과 실제 오늘이 다르므로
    오늘 날짜를 그대로 쓰면 등록분이 목록 맨 위에 동떨어져 붙는다.
    """
    d = (body.get("date") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        return d
    conn = db.connect()
    try:
        base = conn.execute("SELECT MAX(T_Date) FROM Transaction_tb").fetchone()[0]
    finally:
        conn.close()
    return db._add_days(base, 1)

# ── 불출 등록 API ───────────────────────────────────────────
# 발주에 이은 두 번째 쓰기 엔드포인트.
# FIFO 배분은 화면이 보낸 값을 쓰지 않고 서버가 다시 계산한다.

@app.route("/api/disburse/preview", methods=["POST"])
def api_disburse_preview():
    """등록 전 미리보기 — 어느 LOT 에서 얼마씩 나가는지 보여준다."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        plan, errors = db.preview_disburse(
            conn, body.get("P_ID"), body.get("qty"), body.get("EP_ID"),
            body.get("req"))
        return jsonify({"ok": not errors, "plan": plan, "errors": errors})
    finally:
        conn.close()


@app.route("/api/disburse", methods=["POST"])
def api_disburse_create():
    """불출 등록. FIFO 로 나눈 LOT 마다 거래 행을 남긴다."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        plan, errors = db.create_disburse(
            conn, _entry_date(body), body.get("P_ID"),
            body.get("qty"), body.get("EP_ID"), body.get("req"))
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        return jsonify({"ok": True, "plan": plan})
    except sqlite3.OperationalError as e:
        # 배포 환경에서 DB 파일이 읽기 전용이면 여기로 온다
        return jsonify({"ok": False, "errors": ["DB에 쓸 수 없습니다: %s" % e]}), 500
    finally:
        conn.close()

@app.route("/api/disburse/batch/preview", methods=["POST"])
def api_disburse_batch_preview():
    """일괄 불출 미리보기 — 자재마다 FIFO 배분을 계산한다."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        plan, errors = db.preview_disburse_batch(
            conn, body.get("lines") or [], body.get("EP_ID"))
        return jsonify({"ok": not errors, "plan": plan, "errors": errors})
    finally:
        conn.close()


@app.route("/api/disburse/batch", methods=["POST"])
def api_disburse_batch_create():
    """일괄 불출 등록. 한 줄이라도 걸리면 전체를 반려한다."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        plan, errors = db.create_disburse_batch(
            conn, _entry_date(body), body.get("lines") or [], body.get("EP_ID"))
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        return jsonify({"ok": True, "plan": plan})
    except sqlite3.OperationalError as e:
        return jsonify({"ok": False, "errors": ["DB에 쓸 수 없습니다: %s" % e]}), 500
    finally:
        conn.close()


@app.route("/api/lot-events")
def api_lot_events():
    """자재 하나의 전 LOT 이벤트 타임라인 (조회 전용).

    전 자재를 화면에 미리 실으면 응답이 수 MB 가 되므로 모달을 열 때만 부른다.
    """
    conn = db.connect()
    try:
        data, errors = db.lot_events(conn, request.args.get("P_ID"))
        return jsonify({"ok": not errors, "data": data, "errors": errors})
    finally:
        conn.close()


# ── 입고 클레임 API (불량 · 반품 · 대체) ────────────────────
# 입고 검수에서 나온 불량은 입고 등록이 한 트랜잭션으로 함께 남긴다.
# 여기 두 엔드포인트는 (1) 이미 입고된 LOT 에서 뒤늦게 발견한 불량 접수와
# (2) 접수된 클레임의 처리(대체입고·환불·폐기)를 맡는다.

@app.route("/api/claim", methods=["POST"])
def api_claim_create():
    """사용 중 발견한 불량 접수 — LOT 에서 차감하고 클레임을 연다."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        out, errors = db.create_claim(
            conn, _entry_date(body), body.get("Lot_ID"), body.get("qty"),
            body.get("EP_ID"), body.get("resolution"), body.get("reason"))
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        return jsonify({"ok": True, "claim": out})
    except sqlite3.OperationalError as e:
        return jsonify({"ok": False, "errors": ["DB에 쓸 수 없습니다: %s" % e]}), 500
    finally:
        conn.close()


@app.route("/api/claim/resolve", methods=["POST"])
def api_claim_resolve():
    """클레임 처리 — 대체입고(새 LOT 생성) / 환불 / 폐기."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        out, errors = db.resolve_claim(
            conn, _entry_date(body), body.get("Claim_ID"),
            body.get("resolution"), body.get("EP_ID"), body.get("reason"))
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        return jsonify({"ok": True, "result": out})
    except sqlite3.OperationalError as e:
        return jsonify({"ok": False, "errors": ["DB에 쓸 수 없습니다: %s" % e]}), 500
    finally:
        conn.close()


# ── 불출 요청 API ───────────────────────────────────────────
# 생산 계획(목표 대수)으로 BOM 소요량을 뽑아 요청서를 만든다.
# 요청 근거(소요량·현재고)는 화면이 보낸 값을 쓰지 않고 서버가 다시 계산한다.

@app.route("/api/request/plan", methods=["POST"])
def api_request_plan():
    """완제품 + 목표 대수 → 자재 소요량 초안."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        plan, errors = db.request_plan(conn, body.get("FG_ID"), body.get("plan_qty"))
        return jsonify({"ok": not errors, "plan": plan, "errors": errors})
    finally:
        conn.close()


@app.route("/api/request/preview", methods=["POST"])
def api_request_preview():
    """등록 전 미리보기 — 포장단위 검증과 합계."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        plan, errors = db.preview_request(
            conn, _entry_date(body), body.get("FG_ID"), body.get("plan_qty"),
            body.get("items") or [], body.get("EP_ID"),
            body.get("Work_Order"), body.get("note"))
        return jsonify({"ok": not errors, "plan": plan, "errors": errors})
    finally:
        conn.close()


@app.route("/api/request", methods=["POST"])
def api_request_create():
    """불출 요청 등록."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        plan, errors = db.create_request(
            conn, _entry_date(body), body.get("FG_ID"), body.get("plan_qty"),
            body.get("items") or [], body.get("EP_ID"),
            body.get("Work_Order"), body.get("note"))
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        return jsonify({"ok": True, "plan": plan})
    except sqlite3.OperationalError as e:
        return jsonify({"ok": False, "errors": ["DB에 쓸 수 없습니다: %s" % e]}), 500
    finally:
        conn.close()


@app.route("/api/request/cancel", methods=["POST"])
def api_request_cancel():
    """요청 취소. 한 건이라도 불출됐으면 취소하지 않는다."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        res, errors = db.cancel_request(conn, body.get("Req_ID"), body.get("EP_ID"))
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        return jsonify({"ok": True, "result": res})
    except sqlite3.OperationalError as e:
        return jsonify({"ok": False, "errors": ["DB에 쓸 수 없습니다: %s" % e]}), 500
    finally:
        conn.close()


# ── 입고 등록 API ───────────────────────────────────────────
# 발주(입구) → 입고 → 불출(출구) 중 가운데 토막.
# Lot_tb 와 Transaction_tb 를 한 트랜잭션에 함께 쓴다.

@app.route("/api/safety/preview", methods=["POST"])
def api_safety_preview():
    """안전재고 일괄 갱신 미리보기 — 대상 판정과 변경 건수 (저장 안 함)."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        plan, errors = db.preview_safety_update(
            conn, _entry_date(body), body.get("P_IDs"), body.get("EP_ID"), body.get("note"))
        return jsonify({"ok": not errors, "plan": plan, "errors": errors})
    finally:
        conn.close()


@app.route("/api/safety/apply", methods=["POST"])
def api_safety_apply():
    """안전재고 일괄 갱신 실행 — Safe_tb 갱신 + Update_Log_tb 이력."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        plan, errors = db.apply_safety_update(
            conn, _entry_date(body), body.get("P_IDs"), body.get("EP_ID"), body.get("note"))
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        return jsonify({"ok": True, "plan": plan})
    except sqlite3.OperationalError as e:
        return jsonify({"ok": False, "errors": ["DB에 쓸 수 없습니다: %s" % e]}), 500
    finally:
        conn.close()


@app.route("/api/safety/revert", methods=["POST"])
def api_safety_revert():
    """갱신 되돌리기 — 그 실행분의 변경 전 값을 되돌린다."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        res, errors = db.revert_safety_update(
            conn, body.get("run_date"), body.get("EP_ID"))
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        return jsonify({"ok": True, "result": res})
    except sqlite3.OperationalError as e:
        return jsonify({"ok": False, "errors": ["DB에 쓸 수 없습니다: %s" % e]}), 500
    finally:
        conn.close()


@app.route("/api/request/approve/preview", methods=["POST"])
def api_request_approve_preview():
    """승인 전 미리보기 — 라인별 조정 수량과 승인 금액·한도를 확인한다."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        plan, errors = db.preview_approval(
            conn, body.get("Req_ID"), body.get("EP_ID"),
            body.get("lines") or {}, body.get("note"))
        return jsonify({"ok": not errors, "plan": plan, "errors": errors})
    finally:
        conn.close()


@app.route("/api/request/approve", methods=["POST"])
def api_request_approve():
    """불출 승인. 승인된 요청만 불출 처리 화면으로 내려간다."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        plan, errors = db.approve_request(
            conn, _entry_date(body), body.get("Req_ID"), body.get("EP_ID"),
            body.get("lines") or {}, body.get("note"))
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        return jsonify({"ok": True, "plan": plan})
    except sqlite3.OperationalError as e:
        return jsonify({"ok": False, "errors": ["DB에 쓸 수 없습니다: %s" % e]}), 500
    finally:
        conn.close()


@app.route("/api/request/reject", methods=["POST"])
def api_request_reject():
    """불출 반려. 사유가 없으면 받지 않는다."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        res, errors = db.reject_request(
            conn, _entry_date(body), body.get("Req_ID"), body.get("EP_ID"),
            body.get("reason"))
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        return jsonify({"ok": True, "result": res})
    except sqlite3.OperationalError as e:
        return jsonify({"ok": False, "errors": ["DB에 쓸 수 없습니다: %s" % e]}), 500
    finally:
        conn.close()


@app.route("/api/inbound/preview", methods=["POST"])
def api_inbound_preview():
    """등록 전 미리보기 — 판정·배정 창고·정산 금액을 보여준다."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        plan, errors = db.preview_inbound(
            conn, _entry_date(body), body.get("H_ID"),
            body.get("lines") or [], body.get("EP_ID"),
            body.get("settle"), body.get("settle_note"))
        return jsonify({"ok": not errors, "plan": plan, "errors": errors})
    finally:
        conn.close()


@app.route("/api/inbound", methods=["POST"])
def api_inbound_create():
    """입고 등록. 라인마다 LOT·입고거래를, 변경이 있으면 변경이력을 남긴다."""
    body = request.get_json(silent=True) or {}
    conn = db.connect()
    try:
        plan, errors = db.create_inbound(
            conn, _entry_date(body), body.get("H_ID"),
            body.get("lines") or [], body.get("EP_ID"),
            body.get("settle"), body.get("settle_note"))
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        return jsonify({"ok": True, "plan": plan})
    except sqlite3.OperationalError as e:
        # 배포 환경에서 DB 파일이 읽기 전용이면 여기로 온다
        return jsonify({"ok": False, "errors": ["DB에 쓸 수 없습니다: %s" % e]}), 500
    finally:
        conn.close()


EMBED_CONTEXT = {
    "safety_stock": _ctx_safety_stock,
    "products":     _ctx_products,
    "bom":          _ctx_bom,
    "abc":          _ctx_abc,
    "tx_history":   _ctx_tx_history,
    "suppliers":    _ctx_suppliers,
    "production":   _ctx_production,
    "purchase":     _ctx_purchase,
    "stock_map":    _ctx_stock_map,
    "lot":          _ctx_lot,
    "forecast":     _ctx_forecast,
    "calendar":     _ctx_calendar,
    "risk_radar":   _ctx_risk_radar,
    "users":        _ctx_users,
    "report":       _ctx_report,
    "wizard":       _ctx_wizard,
    "simulator":    _ctx_simulator,
    "picking":      _ctx_picking,
    "disburse_request": _ctx_disburse_request,
    "approval":     _ctx_approval,
    # 입출고 작업 화면 3종은 같은 참조 데이터를 공유한다
    "inbound":      _ctx_workbench,
    "disburse":     _ctx_workbench,
    "scanner":      _ctx_workbench,
}


@app.route("/embed/<screen>")
def embed(screen):
    tmpl = f"embed/{screen}.html"
    provider = EMBED_CONTEXT.get(screen)
    # 서버에서 집계하는 화면(협력사·사용자·ABC)은 기간을 인자로 받는다.
    # 화면에서 계산하는 화면은 이 값을 쓰지 않으므로 인자를 받지 않는다.
    ctx = {}
    if provider:
        period = (request.args.get("period") or "").strip()
        try:
            ctx = provider(period)
        except TypeError:
            ctx = provider()
    try:
        return render_template(tmpl, **ctx)
    except Exception:
        return f"<div style='padding:20px;color:#888;font-family:sans-serif'>embed/{screen}.html 준비 중...</div>"

if __name__ == "__main__":
    # 로컬 개발용. 배포 환경에서는 gunicorn(Render) 또는 PythonAnywhere 가
    # app 객체를 직접 실행하므로 이 블록은 실행되지 않는다.
    # DEBUG 는 기본 꺼짐 — 켜고 싶으면 환경변수로: FLASK_DEBUG=1 py app.py
    debug = os.environ.get("FLASK_DEBUG") == "1"
    port = int(os.environ.get("PORT", 5000))

    # ⚠️ 윈도우에서 Werkzeug 개발 서버는 응답을 간헐적으로 멈춘다.
    #    화면마다 3~5번에 한 번꼴로 CSS·HTML 응답이 끝나지 않는데,
    #    스타일시트가 pending 이면 브라우저가 그 뒤의 인라인 <script> 실행을
    #    막아버려서 embed 화면이 통째로 빈 채로 굳는다(데이터 렌더링을 전부
    #    인라인 스크립트가 하기 때문). 코드 문제로 보이지만 서버 문제다.
    #    waitress 가 깔려 있으면 그걸 쓴다 — 순수 파이썬이고 윈도우에서 안정적이다.
    #        py -m pip install waitress
    #    디버그 모드는 자동 리로더가 필요하므로 그때만 개발 서버를 쓴다.
    if debug:
        app.run(debug=True, host="0.0.0.0", port=port)
    else:
        try:
            from waitress import serve
        except ImportError:
            print("[warn] waitress 가 없어 개발 서버로 실행합니다. "
                  "화면이 비어 보이면 'py -m pip install waitress' 후 다시 실행하세요.")
            app.run(debug=False, host="0.0.0.0", port=port)
        else:
            # debug 가 꺼져 있으면 Jinja 가 템플릿을 캐시한다. 로컬에서 화면을
            # 고쳐도 서버가 옛 버전을 계속 내보내므로 개발 중에는 켜 둔다.
            # (이 블록은 배포 환경에서 실행되지 않는다)
            app.jinja_env.auto_reload = True
            app.config["TEMPLATES_AUTO_RELOAD"] = True
            print("Serving on http://127.0.0.1:%d  (waitress)" % port)
            serve(app, host="0.0.0.0", port=port, threads=8)
