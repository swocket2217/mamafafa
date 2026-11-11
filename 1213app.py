"""우리밭 예약 — 실행: streamlit run app.py

GitHub에는 이 파일과 requirements.txt만 올리세요. 비밀번호/주소/DB URL은 올리지 마세요.
Streamlit Community Cloud → App settings → Secrets에 아래 설정을 입력합니다.

DATABASE_URL = "postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require"
ADMIN_PASSWORD = "충분히-긴-관리자-비밀번호"
FARM_NAME = "우리밭 예약"
FARM_CONTACT = "농장 연락처를 여기에 입력"

DATABASE_URL 미설정: 메모리 체험 모드. 실제 개인정보 입력 금지, 재시작하면 소멸.
운영: 외부 PostgreSQL 필수. PostgreSQL 서비스의 백업/접근제어/TLS도 설정하세요.
최초 작물 가격/재고/날짜는 예시입니다. 관리자에서 반드시 실제 값으로 바꾸세요.
농장 전화, 개인정보 보관기간, 배송비/결제 안내도 운영 전에 설정하세요.
전화번호+비밀번호 로그인입니다. 전화번호 인증/비밀번호 재설정/온라인 결제는 미포함.
비밀번호 분실 시 새 계정을 임의 발급하지 말고 본인 확인 절차를 마련하세요.
"""
import calendar
import hashlib
import hmac
import html
import os
import re
import secrets
import time
from datetime import date, datetime, timedelta, timezone

import streamlit as st
from sqlalchemy import (create_engine, MetaData, Table, Column, String, Integer,
                        Text, select, insert, update, delete, and_, event)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.pool import StaticPool


def config(key, default=""):
    try:
        return st.secrets.get(key, os.getenv(key, default))
    except Exception:
        return os.getenv(key, default)


st.set_page_config(page_title="우리밭 예약 | 농작물 간편 예약", page_icon="🌾", layout="centered")
DB_URL = str(config("DATABASE_URL"))
DEMO = not DB_URL
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DB_URL.startswith("postgresql://"):
    DB_URL = DB_URL.replace("postgresql://", "postgresql+psycopg://", 1)
FARM = str(config("FARM_NAME", "우리밭 예약"))
CONTACT = str(config("FARM_CONTACT", "농장 연락처 미설정"))
ADMIN_PW = str(config("ADMIN_PASSWORD"))

meta = MetaData()
users = Table("farm_users", meta,
    Column("id", String(64), primary_key=True), Column("phone", String(20), unique=True, nullable=False),
    Column("name", String(60), nullable=False), Column("password", Text, nullable=False),
    Column("address", Text, nullable=False, default=""))
crops = Table("farm_crops", meta,
    Column("id", Integer, primary_key=True), Column("name", String(40), nullable=False),
    Column("icon", String(20), nullable=False), Column("stock", Integer, nullable=False),
    Column("price", Integer, nullable=False), Column("start", String(10), nullable=False),
    Column("end", String(10), nullable=False), Column("delivery", String(10), nullable=False))
orders = Table("farm_orders", meta,
    Column("id", String(64), primary_key=True), Column("user_id", String(64), nullable=False),
    Column("crop_id", Integer, nullable=False), Column("crop_name", String(40), nullable=False),
    Column("kg", Integer, nullable=False), Column("unit_price", Integer, nullable=False),
    Column("name", String(60), nullable=False), Column("phone", String(20), nullable=False),
    Column("address", Text, nullable=False), Column("status", String(20), nullable=False),
    Column("delivery", String(10), nullable=False), Column("created", String(40), nullable=False))
attempts = Table("farm_login_attempts", meta,
    Column("phone", String(20), primary_key=True), Column("failures", Integer, nullable=False),
    Column("blocked_until", Integer, nullable=False))


@st.cache_resource
def database(url):
    if not url:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    else:
        if not url.startswith("postgresql+psycopg://"):
            raise ValueError("운영 DB는 PostgreSQL 연결 주소를 사용하세요.")
        engine = create_engine(url, pool_pre_ping=True)
    meta.create_all(engine)
    today = date.today()
    with engine.begin() as con:
        if not con.execute(select(crops.c.id).limit(1)).first():
            rows = [("대추", "🟤", 100, 15000), ("감", "🍊", 150, 6000),
                    ("복숭아", "🍑", 80, 12000), ("고춧가루", "🌶️", 50, 30000), ("쌀", "🌾", 300, 5000)]
            for i, (name, icon, stock, price) in enumerate(rows, 1):
                con.execute(insert(crops).values(id=i, name=name, icon=icon, stock=stock, price=price,
                    start=today.isoformat(), end=(today + timedelta(days=30)).isoformat(),
                    delivery=(today + timedelta(days=40)).isoformat()))
    return engine


try:
    engine = database(DB_URL)
except Exception:
    st.error("저장소에 연결하지 못했습니다. 관리자에게 DB 주소와 연결 상태 확인을 요청해 주세요.")
    st.stop()


def read_rows(table, condition=None):
    stmt = select(table)
    if condition is not None:
        stmt = stmt.where(condition)
    with engine.connect() as con:
        return [dict(row) for row in con.execute(stmt).mappings()]


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600000).hex()
    return f"{salt}${digest}"


def verify(password, encoded):
    return hmac.compare_digest(hash_password(password, encoded.split("$")[0]), encoded)


def phone_number(value):
    return re.sub(r"[^0-9]", "", value)


def login(phone, password):
    # DB 기반 제한: 브라우저 새로고침/다른 세션에서도 같은 번호는 15분 제한됩니다.
    now = int(time.time())
    with engine.begin() as con:
        if engine.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            con.execute(pg_insert(attempts).values(phone=phone, failures=0, blocked_until=0)
                        .on_conflict_do_nothing(index_elements=["phone"]))
        elif not con.execute(select(attempts).where(attempts.c.phone == phone)).first():
            con.execute(insert(attempts).values(phone=phone, failures=0, blocked_until=0))
        entry = con.execute(select(attempts).where(attempts.c.phone == phone).with_for_update()).mappings().one()
        if entry["blocked_until"] > now:
            return None, "로그인 시도가 많습니다. 15분 뒤 다시 시도해 주세요."
        row = con.execute(select(users).where(users.c.phone == phone)).mappings().first()
        # 존재하지 않는 계정도 같은 비용으로 처리합니다.
        encoded = row["password"] if row else hash_password("dummy-password")
        valid = verify(password, encoded)
        if row and valid:
            con.execute(update(attempts).where(attempts.c.phone == phone).values(failures=0, blocked_until=0))
            return row["id"], ""
        failures = (0 if entry["blocked_until"] else entry["failures"]) + 1
        con.execute(update(attempts).where(attempts.c.phone == phone).values(
            failures=failures, blocked_until=now + 900 if failures >= 5 else 0))
    return None, "전화번호 또는 비밀번호를 확인해 주세요."


def book(user, crop_id, kg, address):
    today = date.today().isoformat()
    with engine.begin() as con:
        # 단일 조건부 갱신으로 동시 예약 시에도 재고가 음수가 되지 않습니다.
        result = con.execute(update(crops).where(and_(crops.c.id == crop_id, crops.c.stock >= kg,
            crops.c.start <= today, crops.c.end >= today)).values(stock=crops.c.stock - kg))
        if result.rowcount != 1:
            raise ValueError("재고가 부족하거나 예약 기간이 아닙니다. 다시 확인해 주세요.")
        crop = con.execute(select(crops).where(crops.c.id == crop_id)).mappings().one()
        oid = secrets.token_hex(12)
        con.execute(insert(orders).values(id=oid, user_id=user["id"], crop_id=crop_id,
            crop_name=crop["name"], kg=kg, unit_price=crop["price"], name=user["name"],
            phone=user["phone"], address=address, status="예약접수", delivery=crop["delivery"],
            created=datetime.now(timezone.utc).isoformat()))
        con.execute(update(users).where(users.c.id == user["id"]).values(address=address))
    return oid


def cancel(oid, uid):
    with engine.begin() as con:
        row = con.execute(select(orders).where(and_(orders.c.id == oid, orders.c.user_id == uid))
                          .with_for_update()).mappings().first()
        if not row or row["status"] != "예약접수":
            raise ValueError("예약접수 상태에서만 취소할 수 있습니다.")
        result = con.execute(update(orders).where(and_(orders.c.id == oid, orders.c.status == "예약접수"))
                             .values(status="취소"))
        if result.rowcount != 1:
            raise ValueError("이미 처리된 예약입니다.")
        con.execute(update(crops).where(crops.c.id == row["crop_id"]).values(stock=crops.c.stock + row["kg"]))


st.markdown("""<style>
html, body, [class*="css"] {font-family: sans-serif;}
.stApp {background: #FAFAF4; color: #193524;}
[data-testid="stSidebar"] {background: #EDF3E8;}
.stButton button, .stFormSubmitButton button {min-height: 54px; font-size: 1.1rem; font-weight: 700; border-radius: 12px;}
[data-testid="stWidgetLabel"] p, .stRadio label p, .stCheckbox label p {font-size: 1.08rem;}
.stTextInput input, .stTextArea textarea {font-size: 1.1rem;}
[data-testid="stMetricValue"] {font-size: 1.7rem;}
.block-container {max-width: 900px; padding-top: 2rem;}
</style>""", unsafe_allow_html=True)
st.title(f"🌾 {FARM}")
st.write("우리 농작물, 수량 확인부터 집 앞 배송 예약까지 간편하게.")
if DEMO:
    st.warning("체험 모드 · 가격·재고·일정은 예시입니다. 실제 이름·전화번호·주소를 입력하지 마세요. 데이터는 재시작하면 사라집니다.")
else:
    st.caption("가격·재고·예약 일정은 농장 관리자가 등록한 내용입니다.")
if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))
with st.sidebar:
    st.header("🌾 쉬운 메뉴")
    page = st.radio("원하는 메뉴를 눌러 주세요", ["🌾 작물 종류", "📦 내 예약·구매", "👤 로그인·내 정보", "📅 예약·배송 달력"])
    st.divider()
    st.write(f"☎ {CONTACT}")
    st.caption("결제는 이 앱에서 진행하지 않습니다. 배송비와 결제 방법은 농장에 확인해 주세요.")
    if st.button("메뉴 사용 안내", use_container_width=True):
        st.info("① 로그인 → ② 작물 선택 → ③ kg 선택 → ④ 주소 확인 → ⑤ 예약 확정")
    admin_open = st.checkbox("🔧 농장 관리자")

uid = st.session_state.get("uid")
user = next(iter(read_rows(users, users.c.id == uid)), None) if uid else None
all_crops = sorted(read_rows(crops), key=lambda c: c["id"])


def require_login():
    if not user:
        st.info("왼쪽 메뉴의 ‘👤 로그인·내 정보’에서 먼저 로그인해 주세요. 휴대폰에서는 왼쪽 위 메뉴를 열어 주세요.")
        return False
    return True


def account_page():
    st.header("👤 로그인·내 정보")
    if user:
        st.success(f"{user['name']} 님, 반갑습니다.")
        st.write(f"전화번호: {user['phone']}")
        with st.form("profile"):
            address = st.text_area("기본 배송 주소 (우편번호·도로명·상세주소)", value=user["address"], max_chars=300)
            if st.form_submit_button("주소 저장", use_container_width=True):
                with engine.begin() as con:
                    con.execute(update(users).where(users.c.id == uid).values(address=address.strip()))
                st.session_state.flash = "기본 주소를 저장했습니다. 이미 접수된 예약의 배송지는 바뀌지 않습니다."
                st.rerun()
        if st.button("로그아웃", use_container_width=True):
            st.session_state.pop("uid", None)
            st.rerun()
        st.caption("예약 배송지 변경·계정 삭제·비밀번호 분실은 농장에 문의하세요. 관리자만 개인정보를 확인할 수 있습니다.")
        return
    tab_login, tab_join = st.tabs(["로그인", "처음 오셨나요? 회원가입"])
    with tab_login:
        with st.form("login"):
            phone = st.text_input("전화번호", max_chars=20)
            password = st.text_input("비밀번호", type="password", max_chars=128)
            if st.form_submit_button("로그인", type="primary", use_container_width=True):
                normalized = phone_number(phone)
                if not re.fullmatch(r"0\d{8,10}", normalized):
                    st.error("전화번호를 확인해 주세요.")
                else:
                    logged_uid, message = login(normalized, password)
                    if logged_uid:
                        st.session_state.uid = logged_uid
                        st.rerun()
                    st.error(message)
    with tab_join:
        st.caption("체험할 때는 가상의 정보만 입력하세요." if DEMO else "입력한 정보는 예약·배송 및 본인 계정 확인을 위해 저장됩니다.")
        with st.form("join"):
            name = st.text_input("받으실 분 이름", max_chars=60)
            phone = st.text_input("전화번호", key="join_phone", max_chars=20)
            password = st.text_input("새 비밀번호 (10자 이상)", type="password", max_chars=128)
            confirm = st.text_input("비밀번호 한 번 더", type="password", max_chars=128)
            agree = st.checkbox("이름·전화번호·주소를 예약과 배송에 사용하는 데 동의합니다.")
            st.caption("보관기간: 회원 탈퇴·삭제 요청 시까지. 법정 보존 대상은 별도 보관할 수 있습니다. 운영 전 실제 농장의 개인정보 처리 안내를 별도로 게시하세요.")
            if st.form_submit_button("회원가입", type="primary", use_container_width=True):
                normalized = phone_number(phone)
                if not name.strip() or not re.fullmatch(r"0\d{8,10}", normalized):
                    st.error("이름과 올바른 전화번호를 입력해 주세요.")
                elif len(password) < 10 or password != confirm or not agree:
                    st.error("10자 이상 비밀번호, 비밀번호 일치 및 동의를 확인해 주세요.")
                else:
                    try:
                        new_id = secrets.token_hex(16)
                        with engine.begin() as con:
                            con.execute(insert(users).values(id=new_id, name=name.strip(), phone=normalized,
                                        password=hash_password(password), address=""))
                        st.session_state.uid = new_id
                        st.rerun()
                    except IntegrityError:
                        st.error("이 전화번호로 가입할 수 없습니다. 기존 계정이나 농장에 문의해 주세요.")


def crop_page():
    st.header("🌾 어떤 작물을 예약할까요?")
    selected = st.selectbox("작물 고르기", all_crops, format_func=lambda c: f"{c['icon']} {c['name']}")
    if not selected:
        return
    c = selected
    with st.container(border=True):
        st.subheader(f"{c['icon']} {c['name']}")
        left, right = st.columns(2)
        left.metric("예약 가능 수량", f"{c['stock']:,} kg")
        right.metric("1kg 가격", f"{c['price']:,}원")
        st.write(f"📅 예약: {c['start']} ~ {c['end']}")
        st.write(f"🚚 배송 예정: {c['delivery']} (농장 사정에 따라 변동 가능)")
    if not c["start"] <= date.today().isoformat() <= c["end"]:
        st.info("지금은 예약 기간이 아닙니다. 달력에서 예약 기간을 확인해 주세요.")
        return
    if c["stock"] <= 0:
        st.warning("준비한 수량이 모두 예약되었습니다.")
        return
    kg = st.selectbox("몇 kg 구매하시겠어요?", range(1, min(c["stock"], 100) + 1), format_func=lambda x: f"{x} kg")
    st.subheader(f"작물 금액: {kg * c['price']:,}원")
    st.caption("배송비 별도 · 실제 결제 및 배송비는 농장 안내 후 확정됩니다. 한 번에 최대 100kg까지 예약할 수 있습니다.")
    if require_login():
        with st.form("reservation", clear_on_submit=False):
            st.write(f"받으실 분: {user['name']} / {user['phone']}")
            address = st.text_area("배송 주소 (우편번호·도로명·건물 동·호수까지)", value=user["address"], max_chars=300)
            st.caption("이 주소를 내 기본 주소와 이번 예약 배송지로 저장합니다.")
            agree = st.checkbox(f"{c['name']} {kg}kg 예약 내용과 배송 주소를 확인했습니다.")
            if st.form_submit_button("예약 확정하기", type="primary", use_container_width=True):
                if len(address.strip()) < 8 or not agree:
                    st.error("상세 주소를 입력하고 예약 내용 확인에 체크해 주세요.")
                else:
                    try:
                        oid = book(user, c["id"], kg, address.strip())
                        st.session_state.flash = f"예약 완료! 예약번호 {oid[:8]} · ‘내 예약·구매’에서 확인하세요."
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))


def my_orders():
    st.header("📦 내 예약·구매")
    if not require_login():
        return
    history = sorted(read_rows(orders, orders.c.user_id == uid), key=lambda o: o["created"], reverse=True)
    mode = st.radio("보기", ["진행 중 예약", "구매 완료", "취소 내역"], horizontal=True)
    visible = [o for o in history if (o["status"] == "배송완료" if mode == "구매 완료" else
               o["status"] == "취소" if mode == "취소 내역" else o["status"] in ["예약접수", "배송준비", "배송중"])]
    if not visible:
        st.info("아직 내역이 없습니다.")
    for o in visible:
        with st.container(border=True):
            st.subheader(f"{o['crop_name']} {o['kg']}kg · {o['status']}")
            st.write(f"작물 금액 {o['kg'] * o['unit_price']:,}원 · 배송 예정 {o['delivery']}")
            st.write(f"📍 {o['address']}")
            st.caption(f"예약번호 {o['id'][:8]} · 예약일 {o['created'][:10]}")
            if o["status"] == "예약접수":
                check = st.checkbox("이 예약을 취소하겠습니다", key="cancel_check_" + o["id"])
                if st.button("예약 취소", key="cancel_" + o["id"], disabled=not check):
                    try:
                        cancel(o["id"], uid)
                        st.session_state.flash = "예약이 취소되었습니다."
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
            elif o["status"] in ["배송준비", "배송중"]:
                st.caption("배송 준비 이후의 취소·주소 변경은 농장에 연락해 주세요.")


def calendar_page():
    st.header("📅 예약·배송 달력")
    month = st.date_input("보고 싶은 달", value=date.today())
    selected = st.selectbox("작물", [None] + all_crops,
                            format_func=lambda c: "전체 작물" if c is None else f"{c['icon']} {c['name']}")
    shown = [selected] if selected else all_crops
    grid = calendar.Calendar(firstweekday=0).monthdatescalendar(month.year, month.month)
    cells = []
    for week in grid:
        line = []
        for day in week:
            events = []
            if day.month == month.month:
                for c in shown:
                    if c["start"] <= day.isoformat() <= c["end"]:
                        events.append(f"예약 {html.escape(c['name'])}")
                    if day.isoformat() == c["delivery"]:
                        events.append(f"🚚 {html.escape(c['name'])}")
            color = "#F0F6E9" if day == date.today() else "#FFFFFF"
            label = str(day.day) if day.month == month.month else ""
            line.append(f'<td style="background:{color};padding:6px;border:1px solid #CCD7C5;vertical-align:top"><b>{label}</b><br><span style="font-size:12px">' + "<br>".join(events) + "</span></td>")
        cells.append("<tr>" + "".join(line) + "</tr>")
    st.markdown(f"### {month.year}년 {month.month}월")
    st.caption("‘예약’은 접수 가능한 날, 🚚는 배송 예정일입니다. 아래 목록에서도 크게 확인할 수 있습니다.")
    st.markdown('<div style="overflow-x:auto"><table style="width:100%;min-width:550px;border-collapse:collapse;table-layout:fixed"><thead><tr>' +
                "".join(f"<th>{day}</th>" for day in ["월", "화", "수", "목", "금", "토", "일"]) +
                "</tr></thead><tbody>" + "".join(cells) + "</tbody></table></div>", unsafe_allow_html=True)
    for c in shown:
        with st.container(border=True):
            st.write(f"{c['icon']} **{c['name']}**")
            st.write(f"예약 {c['start']} ~ {c['end']}")
            st.write(f"🚚 배송 예정 {c['delivery']}")


def admin_page():
    st.header("🔧 농장 관리")
    if len(ADMIN_PW) < 16:
        st.warning("관리자 기능은 Secrets에 16자 이상 ADMIN_PASSWORD를 설정해야 사용할 수 있습니다.")
        return
    if not st.session_state.get("admin_until", 0) > time.time():
        if st.session_state.get("admin_retry", 0) > time.time():
            st.warning("잠시 뒤 다시 시도하세요.")
            return
        with st.form("admin_login"):
            pw = st.text_input("관리자 비밀번호", type="password", max_chars=128)
            if st.form_submit_button("관리자 로그인"):
                if hmac.compare_digest(pw, ADMIN_PW):
                    st.session_state.admin_until = time.time() + 1800
                    st.rerun()
                st.session_state.admin_retry = time.time() + 30
                st.error("비밀번호가 올바르지 않습니다.")
        return
    st.caption("관리자 세션은 30분 후 만료됩니다. 공용 기기에서는 사용을 마치면 로그아웃하세요.")
    if st.button("관리자 로그아웃"):
        st.session_state.pop("admin_until", None)
        st.rerun()
    tab_crops, tab_orders = st.tabs(["가격·수량·일정", "예약 처리"])
    with tab_crops:
        st.warning("재고는 ‘추가/차감’ 방식입니다. 배송일 변경은 새 예약에만 적용되며 기존 예약은 개별 변경하세요.")
        for c in all_crops:
            with st.expander(f"{c['icon']} {c['name']} · 남은 수량 {c['stock']}kg"):
                with st.form(f"edit_{c['id']}"):
                    price = st.number_input("kg당 가격(원)", min_value=1, value=c["price"], step=100)
                    delta = st.number_input("재고 추가(+)/차감(-) kg", min_value=-100000, max_value=100000, value=0)
                    start = st.date_input("예약 시작일", date.fromisoformat(c["start"]))
                    end = st.date_input("예약 마감일", date.fromisoformat(c["end"]))
                    delivery = st.date_input("배송 예정일", date.fromisoformat(c["delivery"]))
                    if st.form_submit_button("작물 정보 저장"):
                        if end < start or delivery < end:
                            st.error("시작일 ≤ 마감일 ≤ 배송일 순서로 입력하세요.")
                        else:
                            with engine.begin() as con:
                                result = con.execute(update(crops).where(and_(crops.c.id == c["id"], crops.c.stock + delta >= 0))
                                    .values(price=price, stock=crops.c.stock + delta, start=start.isoformat(),
                                            end=end.isoformat(), delivery=delivery.isoformat()))
                            if result.rowcount != 1:
                                st.error("차감할 재고가 부족합니다.")
                            else:
                                st.session_state.flash = "작물 정보를 저장했습니다."
                                st.rerun()
    with tab_orders:
        st.caption("주소·전화번호는 배송 목적에만 사용하세요. 개인정보가 포함된 화면을 공유하지 마세요.")
        history = sorted(read_rows(orders), key=lambda o: o["created"], reverse=True)
        status_filter = st.selectbox("상태 필터", ["전체", "예약접수", "배송준비", "배송중", "배송완료", "취소"])
        for o in history:
            if status_filter != "전체" and o["status"] != status_filter:
                continue
            with st.expander(f"{o['id'][:8]} | {o['crop_name']} {o['kg']}kg | {o['status']}"):
                st.write(f"{o['name']} / {o['phone']} / {o['address']}")
                st.write(f"작물 금액 {o['kg'] * o['unit_price']:,}원")
                if o["status"] == "취소":
                    continue
                with st.form("status_" + o["id"]):
                    choices = {"예약접수": ["예약접수", "배송준비"], "배송준비": ["배송준비", "배송중"],
                               "배송중": ["배송중", "배송완료"], "배송완료": ["배송완료"]}[o["status"]]
                    status = st.selectbox("처리 상태", choices)
                    delivery = st.date_input("이 예약 배송 예정일", date.fromisoformat(o["delivery"]))
                    if st.form_submit_button("예약 처리 저장"):
                        with engine.begin() as con:
                            result = con.execute(update(orders).where(and_(orders.c.id == o["id"], orders.c.status == o["status"]))
                                .values(status=status, delivery=delivery.isoformat()))
                        st.session_state.flash = "저장했습니다." if result.rowcount == 1 else "상태가 변경되었습니다. 다시 확인하세요."
                        st.rerun()


try:
    if admin_open:
        admin_page()
    elif page == "🌾 작물 종류":
        crop_page()
    elif page == "📦 내 예약·구매":
        my_orders()
    elif page == "👤 로그인·내 정보":
        account_page()
    else:
        calendar_page()
except SQLAlchemyError:
    st.error("저장소 처리 중 문제가 발생했습니다. 내 예약 내역을 확인한 뒤 다시 시도하거나 농장에 문의해 주세요.")
st.divider()
st.caption(f"🌾 {FARM} · 예약은 간편하게, 배송은 정성껏 · ☎ {CONTACT}")
