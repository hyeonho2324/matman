from flask import Flask, render_template, jsonify, request
import json, os

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
    return render_template("dashboard.html", **get_menu_context("dashboard"), page_title="메인 대시보드")

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


def _ctx_abc():
    conn = db.connect()
    try:
        rows = db.abc_analysis(conn)
        return {"rows": rows, "summary": db.abc_summary(rows)}
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
            "summary": db.tx_summary(rows, lots, prods),
        }
    finally:
        conn.close()


def _ctx_suppliers():
    conn = db.connect()
    try:
        comps = db.supplier_list(conn)
        return {
            "comps": comps,
            "items": db.supplier_items(conn),
            "summary": db.supplier_summary(comps),
        }
    finally:
        conn.close()


EMBED_CONTEXT = {
    "safety_stock": _ctx_safety_stock,
    "products":     _ctx_products,
    "bom":          _ctx_bom,
    "abc":          _ctx_abc,
    "tx_history":   _ctx_tx_history,
    "suppliers":    _ctx_suppliers,
}


@app.route("/embed/<screen>")
def embed(screen):
    tmpl = f"embed/{screen}.html"
    provider = EMBED_CONTEXT.get(screen)
    ctx = provider() if provider else {}
    try:
        return render_template(tmpl, **ctx)
    except Exception:
        return f"<div style='padding:20px;color:#888;font-family:sans-serif'>embed/{screen}.html 준비 중...</div>"

if __name__ == "__main__":
    # 로컬 개발용. 배포 환경에서는 gunicorn 이 app 객체를 직접 실행하므로
    # 이 블록은 실행되지 않는다.
    # DEBUG 는 기본 꺼짐 — 켜고 싶으면 환경변수로: FLASK_DEBUG=1 py app.py
    debug = os.environ.get("FLASK_DEBUG") == "1"
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=debug, host="0.0.0.0", port=port)
