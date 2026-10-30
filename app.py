"""장보기 알람메모 — streamlit run app.py
배포: GitHub에 app.py와 requirements.txt 업로드 → Streamlit Community Cloud 배포.
QR 공유: 앱의 QR 버튼에서 실제 배포 주소를 입력하세요.
저장: SQLite(서버 로컬). Community Cloud 재시작/재배포 시 소실될 수 있습니다.
목록 URL의 list 값은 비밀 공유 키입니다. URL을 아는 사람은 함께 수정할 수 있습니다.
이 앱의 '알람'은 화면 내 구매 안내이며 백그라운드 푸시 알림은 아닙니다.
"""
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from pathlib import Path
import re
import sqlite3
import uuid
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import qrcode
import streamlit as st

st.set_page_config(page_title="장보기 알람메모", page_icon="🛒", layout="centered", initial_sidebar_state="collapsed")
DB = Path(__file__).with_name("shopping.sqlite3")
CATEGORIES = {"🥬 야채": "🥬", "🍎 과일": "🍎", "🥩 정육·수산": "🥩", "🥚 유제품·달걀": "🥚", "🍚 곡류·식품": "🍚", "🧻 생활용품": "🧻", "📦 기타": "📦"}
PRIORITIES = {1: "1 · 꼭 구매", 2: "2 · 가격 보고", 3: "3 · 여유 있을 때"}

@contextmanager
def connect():
    con = sqlite3.connect(DB, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def initialize():
    with connect() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, list_id TEXT NOT NULL,
            category TEXT NOT NULL, name TEXT NOT NULL, priority INTEGER NOT NULL CHECK(priority BETWEEN 1 AND 3),
            ceiling INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'todo',
            unit_price INTEGER NOT NULL DEFAULT 0, qty TEXT NOT NULL DEFAULT '0',
            amount INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_items_list ON items(list_id)")

def execute(sql, args=()):
    with connect() as con:
        con.execute(sql, args)

def get_items():
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM items WHERE list_id=? ORDER BY priority, id", (LIST_ID,)).fetchall()]

def money(value):
    return f"{int(value):,}원"

def amount_for(unit_price, quantity):
    return int((Decimal(str(unit_price)) * Decimal(str(quantity))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

def qty_label(value):
    return format(Decimal(str(value)).normalize(), "f")

initialize()
raw_key = st.query_params.get("list", "")
if not re.fullmatch(r"[0-9a-f]{32}", raw_key):
    raw_key = uuid.uuid4().hex
    st.query_params["list"] = raw_key
LIST_ID = raw_key

st.markdown("""<style>
[data-testid="stAppViewContainer"] {background:#f6f8f4;}
.block-container {max-width:600px;padding:0.65rem 0.7rem 0.5rem;}
header[data-testid="stHeader"] {display:none;}
[data-testid="stVerticalBlock"] {gap:0.4rem;}
[data-testid="stHorizontalBlock"] {flex-wrap:nowrap !important;gap:0.35rem;align-items:center;}
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {min-width:0 !important;}
.stButton button, [data-testid="stPopover"] button {min-height:2.25rem;padding:0.2rem 0.4rem;font-size:0.83rem;}
[data-testid="stMetric"] {background:white;border-radius:14px;padding:0.45rem 0.7rem;}
[data-testid="stMetricValue"] {font-size:1.6rem;}
[data-testid="stMetricLabel"] {font-size:0.8rem;}
h3 {font-size:1.15rem !important;padding:0 !important;}
[data-testid="stCaptionContainer"] p {font-size:0.73rem;line-height:1.25;}
[data-testid="stDialog"] [data-testid="stVerticalBlock"] {gap:0.55rem;}
[data-testid="stDialog"] [data-testid="stHorizontalBlock"] {flex-wrap:wrap !important;}
[data-testid="stElementContainer"]:has(.item-title) {margin-bottom:0;}
.item-title {font-size:0.9rem;line-height:1.3;overflow-wrap:anywhere;}
</style>""", unsafe_allow_html=True)

@st.dialog("➕ 장보기 메모", width="small")
def add_item():
    with st.form("new_item", clear_on_submit=False):
        category = st.selectbox("카테고리", list(CATEGORIES))
        name = st.text_input("상품명", max_chars=60, placeholder="예: 방울토마토 1팩")
        priority = st.radio("중요도", [1, 2, 3], format_func=lambda p: PRIORITIES[p], horizontal=True)
        ceiling = st.number_input("허용 단가(원) · 0이면 제한 없음", min_value=0, max_value=100000000, value=0, step=100)
        st.caption("단가는 1개·1팩 등 구매 수량 1단위의 가격입니다. 비싸면 보류하세요.")
        submitted = st.form_submit_button("메모 추가", use_container_width=True, type="primary")
        if submitted:
            if not name.strip():
                st.error("상품명을 입력해 주세요.")
            else:
                execute("INSERT INTO items(list_id,category,name,priority,ceiling) VALUES(?,?,?,?,?)", (LIST_ID, category, name.strip(), priority, int(ceiling)))
                st.rerun()

@st.dialog("🛒 카트에 담기", width="small")
def buy_item(item):
    st.write(f"**{item['name']}** · {PRIORITIES[item['priority']]}")
    if item['ceiling']:
        st.caption(f"설정한 허용 단가: {money(item['ceiling'])}")
    # 폼 밖 입력으로 단가·수량 변경 시 예상 합계와 초과 경고를 즉시 갱신합니다.
    price = st.number_input("구매 단가(원)", min_value=0, max_value=100000000, value=int(item['unit_price']), step=100, key=f"price_{item['id']}")
    quantity = st.number_input("수량 · 개/팩/kg 등", min_value=0.01, max_value=100000.0, value=float(item['qty']) if Decimal(item['qty']) > 0 else 1.0, step=0.5, format="%.2f", key=f"qty_{item['id']}")
    quantity = round(quantity, 2)
    amount = amount_for(price, quantity)
    st.info(f"이번 상품 합계  {money(amount)}")
    over = item['ceiling'] > 0 and price > item['ceiling']
    if over:
        st.warning("허용 단가보다 비싸요. 오늘은 보류해도 괜찮아요.")
    elif item['priority'] >= 2:
        st.caption("가격이 아쉽다면 담지 않고 보류할 수 있어요.")
    free_confirm = True
    if price == 0:
        free_confirm = st.checkbox("무료 상품(0원)이 맞습니다", key=f"free_{item['id']}")
    override = True
    if over:
        override = st.checkbox("허용 단가를 초과해도 구매합니다", key=f"override_{item['id']}")
    a, b = st.columns(2)
    if a.button("담기 확정", type="primary", use_container_width=True, disabled=not(free_confirm and override)):
        execute("UPDATE items SET status='cart',unit_price=?,qty=?,amount=? WHERE id=? AND list_id=?", (int(price), str(quantity), amount, item['id'], LIST_ID))
        st.rerun()
    if b.button("비싸요 · 보류", use_container_width=True):
        execute("UPDATE items SET status='hold' WHERE id=? AND list_id=?", (item['id'], LIST_ID))
        st.rerun()

@st.dialog("QR로 같은 장보기 목록 열기", width="small")
def share_qr():
    st.caption("배포된 앱 주소를 입력하세요. QR을 찍으면 이 목록을 함께 열고 수정합니다.")
    url = st.text_input("앱 주소", placeholder="https://your-app.streamlit.app", key="public_url")
    if url:
        parsed = urlsplit(url.strip())
        if parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.username or parsed.password:
            st.warning("https://로 시작하는 실제 배포 주소를 입력해 주세요.")
        else:
            params = dict(parse_qsl(parsed.query, keep_blank_values=True))
            params['list'] = LIST_ID
            link = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params), ""))
            buffer = BytesIO()
            qrcode.make(link).save(buffer, format="PNG")
            st.image(buffer.getvalue(), width=210)
            st.code(link, language=None, wrap_lines=True)
            st.download_button("QR 이미지 저장", buffer.getvalue(), file_name="shopping-qr.png", mime="image/png")
    st.warning("링크를 아는 사람은 목록을 수정할 수 있습니다. 공개 게시하지 마세요.")
    st.caption("북마크로 같은 목록에 재접속하세요. 서버 임시 저장은 재배포·재시작 시 사라질 수 있습니다.")

@st.dialog("목록 관리", width="small")
def manage():
    st.caption("구매 취소는 카트에서 '복원'을 누르세요. 아래 작업은 되돌릴 수 없습니다.")
    confirmed = st.checkbox("삭제할 내용을 확인했습니다")
    if st.button("구매 내역만 삭제", disabled=not confirmed, use_container_width=True):
        execute("DELETE FROM items WHERE list_id=? AND status='cart'", (LIST_ID,))
        st.rerun()
    if st.button("전체 목록 비우기", disabled=not confirmed, use_container_width=True):
        execute("DELETE FROM items WHERE list_id=?", (LIST_ID,))
        st.rerun()

left, right = st.columns([4, 1])
left.markdown("### 🛒 장보기 알람메모")
if right.button("QR", use_container_width=True):
    share_qr()
items = get_items()
cart = [i for i in items if i['status'] == 'cart']
todo = [i for i in items if i['status'] == 'todo']
hold = [i for i in items if i['status'] == 'hold']
st.metric("현재 카트 총액", money(sum(i['amount'] for i in cart)), help="실제 담은 상품만 합산합니다. 보류·미구매는 제외됩니다.")
a, b, c = st.columns([3, 3, 1])
if a.button("＋ 상품 추가", use_container_width=True, type="primary"):
    add_item()
if b.button("↻ 새로고침", use_container_width=True):
    st.rerun()
if c.button("⚙", use_container_width=True):
    manage()
# 페이지 이동 없이 동일 화면의 목록만 필터링합니다.
labels = [f"메모 {len(todo)}", f"카트 {len(cart)}", f"보류 {len(hold)}"]
selected = st.radio("목록 보기", ["todo", "cart", "hold"], format_func=lambda s: dict(zip(['todo','cart','hold'],labels))[s], horizontal=True, label_visibility="collapsed")
st.caption("① 꼭 구매  ② 가격 보고  ③ 여유 있을 때 · 비싸면 보류")
# 긴 목록도 문서 전체가 아니라 이 영역 안에서 스크롤됩니다.
with st.container(height=265, border=True):
    visible = [i for i in items if i['status'] == selected]
    if not visible:
        st.caption({"todo": "메모가 비었어요. ＋ 상품 추가로 시작하세요.", "cart": "담은 상품이 없어요. 메모에서 '담기'를 눌러 주세요.", "hold": "보류한 상품이 없어요."}[selected])
    for item in visible:
        from html import escape
        col1, col2, col3 = st.columns([5, 1.65, 1.65])
        icon = CATEGORIES.get(item['category'], '📦')
        col1.markdown(f"<div class='item-title'>{icon} <b>{escape(item['name'])}</b> <small>· {item['priority']}순위</small></div>", unsafe_allow_html=True)
        if selected == 'cart':
            col1.caption(f"{money(item['unit_price'])} × {qty_label(item['qty'])} = {money(item['amount'])}")
            if col2.button("수정", key=f"edit_{item['id']}", use_container_width=True):
                buy_item(item)
            if col3.button("복원", key=f"restore_{item['id']}", use_container_width=True):
                execute("UPDATE items SET status='todo',unit_price=0,qty='0',amount=0 WHERE id=? AND list_id=?", (item['id'], LIST_ID))
                st.rerun()
        elif selected == 'hold':
            col1.caption("오늘은 패스 · 총액 미포함")
            if col2.button("복원", key=f"unhold_{item['id']}", use_container_width=True):
                execute("UPDATE items SET status='todo' WHERE id=? AND list_id=?", (item['id'], LIST_ID))
                st.rerun()
            if col3.button("삭제", key=f"deletehold_{item['id']}", use_container_width=True):
                execute("DELETE FROM items WHERE id=? AND list_id=?", (item['id'], LIST_ID))
                st.rerun()
        else:
            col1.caption(f"허용 단가 {money(item['ceiling'])}" if item['ceiling'] else "가격 제한 없음")
            if col2.button("담기", key=f"buy_{item['id']}", use_container_width=True):
                buy_item(item)
            with col3.popover("⋯", use_container_width=True):
                if st.button("비싸요 · 보류", key=f"hold_{item['id']}"):
                    execute("UPDATE items SET status='hold' WHERE id=? AND list_id=?", (item['id'], LIST_ID))
                    st.rerun()
                if st.button("메모 삭제", key=f"del_{item['id']}"):
                    execute("DELETE FROM items WHERE id=? AND list_id=?", (item['id'], LIST_ID))
                    st.rerun()
st.caption("단가 × 수량을 원 단위 반올림해 합산 · 링크 보관 필수 · 화면 내 알림 전용")
