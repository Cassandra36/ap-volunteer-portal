import streamlit as st
import datetime
import requests
import base64
from datetime import timedelta
from zoneinfo import ZoneInfo
# Imports the cookie manager backend
import extra_streamlit_components as stx

# ---------------------------------------------------------------------------
# PAGE CONFIG & STYLING
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Autonomy Project – Volunteer Portal",
    page_icon="💜",
    layout="centered",
)

st.markdown("""
<style>
    [data-testid="stVerticalBlockBorderWrapper"] { border-radius: 12px !important; padding: 16px !important; }
    div[data-testid="column"]:first-child .stButton > button { background-color: #1D9E75; color: white; border: none; font-weight: 600; border-radius: 8px; }
    div[data-testid="column"]:first-child .stButton > button:hover { background-color: #0F6E56; }
    div[data-testid="column"]:last-child .stButton > button { background-color: #E24B4A; color: white; border: none; font-weight: 600; border-radius: 8px; }
    div[data-testid="column"]:last-child .stButton > button:hover { background-color: #A32D2D; }
    [data-testid="metric-container"] { background-color: #f5f5f5; border-radius: 10px; padding: 12px 16px; }
    footer { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

LOCAL_TZ = ZoneInfo("America/New_York")
NEON_BASE = "https://api.neoncrm.com/v2"

# Initialize Cookie Manager
cookie_manager = stx.CookieManager()

# ---------------------------------------------------------------------------
# NEONCRM HELPERS
# ---------------------------------------------------------------------------
def neon_configured() -> bool:
    try:
        return "api_key" in st.secrets["neon"] and "org_id" in st.secrets["neon"]
    except Exception:
        return False

def _neon_headers() -> dict:
    org_id  = st.secrets["neon"]["org_id"]
    api_key = st.secrets["neon"]["api_key"]
    creds   = base64.b64encode(f"{org_id}:{api_key}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

@st.cache_data(ttl=60)
def fetch_account_id(email: str) -> tuple[str | None, str]:
    url    = f"{NEON_BASE}/accounts"
    params = {"userType": "INDIVIDUAL", "email": email, "currentPage": 0, "pageSize": 1}
    try:
        resp = requests.get(url, headers=_neon_headers(), params=params, timeout=10)
        if resp.status_code == 401: return None, "401 Unauthorized — check secrets.toml."
        if not resp.ok: return None, f"HTTP {resp.status_code}"
        accounts = resp.json().get("accounts") or []
        if accounts: return str(accounts[0].get("accountId", "")), ""
        return None, ""
    except Exception as e:
        return None, str(e)

@st.cache_data(ttl=60)
def fetch_volunteer_hours(account_id: str) -> float:
    url    = f"{NEON_BASE}/accounts/{account_id}/volunteerHours"
    params = {"currentPage": 0, "pageSize": 200}
    try:
        resp = requests.get(url, headers=_neon_headers(), params=params, timeout=10)
        if not resp.ok: return 0.0
        rows = resp.json().get("volunteerHours") or []
        return sum(float(r.get("hours", 0) or 0) for r in rows)
    except Exception:
        return 0.0

def push_shift_to_neon(account_id: str, start_dt: datetime.datetime, end_dt: datetime.datetime, hours: float) -> tuple[bool, str]:
    url = f"{NEON_BASE}/accounts/{account_id}/volunteerHours"
    payload = {
        "hours":     round(hours, 2),
        "startDate": start_dt.strftime("%Y-%m-%d"),
        "endDate":   end_dt.strftime("%Y-%m-%d"),
        "note":      "Logged via Volunteer Portal",
    }
    try:
        resp = requests.post(url, headers=_neon_headers(), json=payload, timeout=10)
        if resp.ok: return True, ""
        return False, f"HTTP {resp.status_code}"
    except Exception as e:
        return False, str(e)

# ---------------------------------------------------------------------------
# PERSISTENT STORAGE COOKIE HANDLING
# ---------------------------------------------------------------------------
# 1. Retrieve saved details from the user's browser if they exist
saved_email = cookie_manager.get("volunteer_email")
saved_start = cookie_manager.get("clock_in_time")

# 2. Synchronize cookies into active Session State variables
if "start_time" not in st.session_state:
    if saved_start:
        st.session_state.start_time = datetime.datetime.fromisoformat(saved_start)
    else:
        st.session_state.start_time = None

if "account_id" not in st.session_state: st.session_state.account_id = None
if "total_hours" not in st.session_state: st.session_state.total_hours = 0.0
if "shifts_logged" not in st.session_state: st.session_state.shifts_logged = 0

# ---------------------------------------------------------------------------
# APP HEADER
# ---------------------------------------------------------------------------
st.title("💜 Autonomy Project")
st.subheader("Volunteer Time Clock")
st.divider()

# ---------------------------------------------------------------------------
# VOLUNTEER LOGIN (SIDEBAR)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Volunteer Sign-In")
    
    # Pre-populate with cookie email if returning
    email_default = saved_email if saved_email else ""
    user_email = st.text_input("Enter Email Address", value=email_default, placeholder="name@domain.com")
    
    if user_email:
        # Save email to cookies so they don't have to retype it next time
        if user_email != saved_email:
            cookie_manager.set("volunteer_email", user_email, expires_at=datetime.datetime.now() + timedelta(days=30))
        
        if neon_configured():
            # Automatically attempt loading details if profile is known but unlinked in current state
            if not st.session_state.account_id or (user_email != saved_email):
                acct, err = fetch_account_id(user_email)
                if acct:
                    st.session_state.account_id = acct
                    st.session_state.total_hours = fetch_volunteer_hours(acct)
            
            if st.button("🔄 Refresh Total Hours", use_container_width=True):
                if st.session_state.account_id:
                    fetch_volunteer_hours.clear()
                    st.session_state.total_hours = fetch_volunteer_hours(st.session_state.account_id)
                    st.success("Hours updated!")
        else:
            st.session_state.account_id = "demo-account"
            st.info("⚙️ Demo Mode Active")

# ---------------------------------------------------------------------------
# METRICS & TIME CLOCK (MAIN INTERFACE)
# ---------------------------------------------------------------------------
if not st.session_state.account_id:
    st.info("👋 Please enter your email address in the sidebar to load your profile and begin tracking your hours.")
else:
    # 1. Hours Display
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Your Lifetime Total Hours", f"{st.session_state.total_hours:.1f} hrs")
    with col2:
        st.metric("Shifts Logged This Session", st.session_state.shifts_logged)
        
    st.write("")

    # 2. Clock In/Out UI Box
    with st.container(border=True):
        st.write("### ⏱️ Shift Controls")
        
        c_in, c_out = st.columns(2)
        
        with c_in:
            if st.button("CLOCK IN", use_container_width=True):
                if st.session_state.start_time:
                    st.warning("You are already clocked in.")
                else:
                    now = datetime.datetime.now(tz=LOCAL_TZ)
                    st.session_state.start_time = now
                    
                    # Store timestamp string natively in browser cookie (remains for 1 day max)
                    cookie_manager.set("clock_in_time", now.isoformat(), expires_at=datetime.datetime.now() + timedelta(days=1))
                    
                    st.toast("🟢 Clocked in successfully!")
                    st.rerun()
                    
        with c_out:
            if st.button("CLOCK OUT", use_container_width=True):
                if not st.session_state.start_time:
                    st.error("You must clock in first.")
                else:
                    end_time = datetime.datetime.now(tz=LOCAL_TZ)
                    duration = end_time - st.session_state.start_time
                    hours_worked = duration.total_seconds() / 3600
                    
                    if hours_worked <= 0.001:
                        hours_worked = 0.01 

                    pushed = False
                    if neon_configured() and st.session_state.account_id != "demo-account":
                        with st.spinner("Saving hours to NeonCRM..."):
                            pushed, _ = push_shift_to_neon(
                                account_id=st.session_state.account_id,
                                start_dt=st.session_state.start_time,
                                end_dt=end_time,
                                hours=hours_worked
                            )
                    
                    if pushed:
                        fetch_volunteer_hours.clear()
                        st.session_state.total_hours = fetch_volunteer_hours(st.session_state.account_id)
                    else:
                        st.session_state.total_hours += hours_worked
                    
                    # Clear browser data on shift end
                    cookie_manager.delete("clock_in_time")
                    st.session_state.start_time = None
                    st.session_state.shifts_logged += 1
                    
                    st.balloons()
                    st.success(f"✅ Shift saved! Worked: {hours_worked:.2f} hours.")
                    st.rerun()

        # 3. Status Footer Message
        st.write("---")
        if st.session_state.start_time:
            elapsed = datetime.datetime.now(tz=LOCAL_TZ) - st.session_state.start_time
            hours, remainder = divmod(int(elapsed.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            st.success(f"🟢 Active Shift: Working since **{st.session_state.start_time.strftime('%I:%M %p')}** ({hours}h {minutes:02d}m elapsed)")
        else:
            st.info("⚪ Status: Not clocked in")
