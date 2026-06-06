import streamlit as st
import datetime
import requests
import base64
from datetime import timedelta
from zoneinfo import ZoneInfo
import streamlit as st

# Hides the Main Menu, Footer, and the Deploy Button
hide_st_style = """
            <style>
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            header {visibility: hidden;}
            .stAppDeployButton {visibility: hidden;}
            </style>
            """
st.markdown(hide_st_style, unsafe_allow_html=True)
# PAGE CONFIG
st.set_page_config(
    page_title="Autonomy Project – Volunteer Portal",
    page_icon="💜",
    layout="centered",
)

st.markdown("""
<style>
    footer { visibility: hidden; }
    .custom-footer { text-align: center; font-size: 12px; color: #999; margin-top: 2rem; }
    [data-testid="metric-container"] { background-color: #f5f5f5; border-radius: 10px; padding: 12px 16px; }
</style>
""", unsafe_allow_html=True)

# TIMEZONE
LOCAL_TZ = ZoneInfo("America/New_York")

def utc_now():
    return datetime.datetime.now(tz=datetime.timezone.utc)

def to_local(dt):
    return dt.astimezone(LOCAL_TZ)

def fmt_time(dt):
    return to_local(dt).strftime("%I:%M %p")

def fmt_duration(td):
    total_seconds = int(abs(td.total_seconds()))
    h, rem = divmod(total_seconds, 3600)
    m, _   = divmod(rem, 60)
    return f"{h}h {m:02d}m"

# SESSION STATE
_DEFAULTS = {
    "user_email":          "",
    "account_id":          None,
    "total_hours":         0.0,
    "history":             [],
    "start_time":          None,
    "selected_event_id":   "",
    "selected_event_name": "General Volunteer",
    "event_index":         0,
    "pending_event":       None,
    "debug_log":           [],
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

def dbg(msg: str):
    ts = utc_now().astimezone(LOCAL_TZ).strftime("%H:%M:%S")
    st.session_state.debug_log.append(f"[{ts}] {msg}")

# NEONCRM API INTERFACE
NEON_BASE    = "https://api.neoncrm.com/v2"
NEON_VERSION = "2.9"

def neon_configured():
    try:
        return "neon" in st.secrets and "api_key" in st.secrets["neon"] and "org_id" in st.secrets["neon"]
    except Exception:
        return False

def _neon_headers():
    org_id  = st.secrets["neon"]["org_id"]
    api_key = st.secrets["neon"]["api_key"]
    token   = base64.b64encode(f"{org_id}:{api_key}".encode()).decode()
    return {
        "Authorization":    f"Basic {token}",
        "Content-Type":     "application/json",
        "NEON-API-VERSION": NEON_VERSION,
    }

# Opportunities 
@st.cache_data(ttl=300)
def fetch_neon_opportunities():
    url    = f"{NEON_BASE}/opportunities"
    params = {"currentPage": 0, "pageSize": 200}
    try:
        resp = requests.get(url, headers=_neon_headers(), params=params, timeout=10)
    except Exception as e:
        return [], str(e)
    dbg(f"GET /opportunities → HTTP {resp.status_code}")
    if not resp.ok:
        return [], f"HTTP {resp.status_code}: {resp.text[:200]}"
    raw = resp.json().get("opportunityList") or []
    parsed = []
    for opp in raw:
        name   = opp.get("name", "Untitled")
        status = str(opp.get("status", "")).strip().upper()
        if "template" in name.lower() or status != "ACTIVE":
            continue
        parsed.append({
            "id":   str(opp.get("id", "")),
            "name": name,
            "date": "",
            "time": "",
            "type": "opportunity",
        })
    return parsed, ""

# Shifts Lookup 
def get_or_create_active_shift(opportunity_id):
    url = f"{NEON_BASE}/opportunities/{opportunity_id}/shifts"
    try:
        resp = requests.get(url, headers=_neon_headers(), timeout=10)
        if resp.ok:
            shifts = resp.json().get("opportunityShiftList") or []
            if shifts:
                return shifts[0].get("id"), ""
            return None, ""
    except Exception as e:
        return None, str(e)
    return None, ""

# Account lookup 
@st.cache_data(ttl=60)
def fetch_account_id(email):
    url    = f"{NEON_BASE}/accounts"
    params = {"userType": "INDIVIDUAL", "email": email, "currentPage": 0, "pageSize": 1}
    try:
        resp = requests.get(url, headers=_neon_headers(), params=params, timeout=10)
    except Exception as e:
        return None, str(e)
    if not resp.ok:
        return None, f"HTTP {resp.status_code}: {resp.text[:200]}"
    accounts = resp.json().get("accounts") or []
    if accounts:
        return str(accounts[0].get("accountId", "")), ""
    return None, ""

# Fetch Existing Hours via TimeSheets API (Exhaustive Calendar Year Scan)
@st.cache_data(ttl=60)
def fetch_volunteer_hours(account_id):
    url = f"{NEON_BASE}/timeSheets"
    params = {"currentPage": 0, "pageSize": 200}
    
    current_year = datetime.datetime.now(tz=LOCAL_TZ).year
    
    try:
        resp = requests.get(url, headers=_neon_headers(), params=params, timeout=10)
        if not resp.ok:
            dbg(f"GET /timeSheets → HTTP {resp.status_code}")
            return 0.0, []
            
        sheets = resp.json().get("timeSheetApiList") or []
        total = 0.0
        user_sheets = []
        
        for sheet in sheets:
            if str(sheet.get("accountId")) == str(account_id):
                user_sheets.append(sheet)
                
                week_of = sheet.get("weekOf", "")
                try:
                    sheet_year = int(week_of.split("-")[0])
                except Exception:
                    sheet_year = current_year
                
                if sheet_year == current_year:
                    total += float(sheet.get("totalHours", 0) or 0)
                else:
                    dbg(f"Skipping sheet from year {sheet_year} (Current tracking target: {current_year})")
                
        dbg(f"Exhaustive Scan Complete. YTD Total for {current_year}: {total} hrs")
        return round(total, 2), user_sheets
    except Exception as e:
        dbg(f"fetch_volunteer_hours error: {e}")
        return 0.0, []

# Smart Push Entry (POST or PUT depending on presence) 
def push_shift_to_neon(account_id, opportunity_id, target_dt, hours):
    local_date = target_dt.astimezone(LOCAL_TZ)
    monday = local_date - timedelta(days=local_date.weekday())
    week_of_str = monday.strftime("%Y-%m-%d")

    # Resolve Shift tracking dependency safely
    shift_id, _ = get_or_create_active_shift(opportunity_id)

    _, existing_sheets = fetch_volunteer_hours(account_id)
    matched_sheet = None
    
    for sheet in existing_sheets:
        if str(sheet.get("projectId")) == str(opportunity_id) and str(sheet.get("weekOf")) == week_of_str:
            matched_sheet = sheet
            break

    # Build individual time entry item dynamically based on Shift function availability
    new_item = {
        "roleId": "", 
        "date": local_date.strftime("%Y-%m-%d"),
        "hours": round(hours, 2),
        "expenses": 0,
        "mileages": 0
    }
    if shift_id:
        new_item["shiftId"] = str(shift_id)

    if matched_sheet:
        # ── PUT WORKFLOW (APPEND TO EXISTING TIMESHEET) ──
        timesheet_id = matched_sheet.get("id")
        url = f"{NEON_BASE}/timeSheets/{timesheet_id}"
        
        cleaned_items = []
        for item in matched_sheet.get("timeSheetItems", []):
            cleaned_entry = {
                "id": item.get("id"),
                "timeSheetId": item.get("timeSheetId"),
                "roleId": item.get("roleId", ""),
                "date": item.get("date"),
                "hours": item.get("hours"),
                "expenses": item.get("expenses", 0),
                "mileages": item.get("mileages", 0)
            }
            if item.get("shiftId"):
                cleaned_entry["shiftId"] = str(item.get("shiftId"))
            cleaned_items.append(cleaned_entry)
            
        cleaned_items.append(new_item)

        payload = {
            "id": str(timesheet_id),
            "accountId": str(account_id),
            "projectId": str(opportunity_id), 
            "weekOf": week_of_str,
            "status": "Pending",  
            "timeSheetItems": cleaned_items
        }
        
        dbg(f"PUT /timeSheets/{timesheet_id} payload: {payload}")
        try:
            resp = requests.put(url, headers=_neon_headers(), json=payload, timeout=10)
            dbg(f"PUT /timeSheets/{timesheet_id} → HTTP {resp.status_code} | {resp.text[:300]!r}")
            if resp.ok:
                return True, ""
            return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
        except Exception as e:
            return False, str(e)
            
    else:
        # ── POST WORKFLOW (CREATE NEW TIMESHEET) ──
        url = f"{NEON_BASE}/timeSheets"
        payload = {
            "accountId": str(account_id),
            "projectId": str(opportunity_id), 
            "weekOf": week_of_str,
            "status": "Pending",  
            "timeSheetItems": [new_item]
        }

        dbg(f"POST /timeSheets payload: {payload}")
        try:
            resp = requests.post(url, headers=_neon_headers(), json=payload, timeout=10)
            dbg(f"POST /timeSheets → HTTP {resp.status_code} | {resp.text[:300]!r}")
            if resp.ok:
                return True, ""
            return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
        except Exception as e:
            return False, str(e)

# UI LOGO AND HEADER SECTION
c1, c2, c3 = st.columns([1, 1, 1])
with c2:
    try:
        st.image("logo.png", width=120)
    except Exception:
        st.markdown("<h2 style='text-align:center'>💜</h2>", unsafe_allow_html=True)

st.header("Volunteer Portal", divider="violet")

# SIDEBAR CONTROL PANEL
with st.sidebar:
    st.title("💜 Autonomy Project")
    st.divider()

    with st.form(key="profile_lookup_form", clear_on_submit=False):
        email_input = st.text_input(
            "Volunteer Email",
            value=st.session_state.user_email,
            placeholder="your@email.com",
            key="email_field",
        )
        submit_email = st.form_submit_button("➡️ Enter & Load Profile", use_container_width=True)

    if submit_email:
        if not email_input.strip():
            st.error("Please enter a valid email address.")
        else:
            st.session_state.user_email = email_input
            dbg(f"Email submitted via entry form: {email_input}")
            
            if neon_configured():
                with st.spinner("Looking up account…"):
                    acct, err = fetch_account_id(email_input)
                    if err:
                        st.error(f"NeonCRM error: {err}")
                    elif acct:
                        st.session_state.account_id = acct
                        fetch_volunteer_hours.clear()
                        hours, _ = fetch_volunteer_hours(acct)
                        st.session_state.total_hours = hours
                        st.success("Profile loaded!")
                        st.rerun()
                    else:
                        st.warning("Email not found in NeonCRM.")
            else:
                st.session_state.account_id = "demo-account"
                st.success("Demo profile initialized!")
                st.rerun()

    if not neon_configured() and st.session_state.user_email:
        st.info("⚙️ Demo mode active")

    st.divider()
    if st.session_state.account_id:
        st.metric("My YTD Total Hours", f"{st.session_state.total_hours:.2f} hrs")

    if st.button("🔄 Refresh System Data", use_container_width=True):
        fetch_neon_opportunities.clear()
        fetch_volunteer_hours.clear()
        st.toast("Cache flushed and refreshed!")
        st.rerun()

    with st.expander("🔧 Debug Console", expanded=False):
        if st.session_state.debug_log:
            for line in reversed(st.session_state.debug_log[-40:]):
                st.caption(line)
        else:
            st.caption("No log data available.")
        if st.button("Clear Systems Log"):
            st.session_state.debug_log = []
        st.caption("**State Dump:**")
        st.json({
            "user_email":  st.session_state.user_email,
            "account_id":  st.session_state.account_id,
            "start_time":  str(st.session_state.start_time),
            "total_hours": st.session_state.total_hours,
            "event":       st.session_state.selected_event_name,
            "history_len": len(st.session_state.history),
        })

    st.caption("v4.9.0 | Safe Architecture Hotfix")

# DATA PIPELINE COLLECTION
if neon_configured():
    opps_raw, opps_err = fetch_neon_opportunities()
    if opps_err:
        st.warning(f"⚠️ Opportunities Link error: `{opps_err}`")
else:
    opps_raw = [
        {"id": "o1", "name": "At-Home Volunteer Hours", "date": "", "time": "", "type": "opportunity"},
        {"id": "o2", "name": "Website Maintenance Support", "date": "", "time": "", "type": "opportunity"}
    ]

all_items     = sorted(opps_raw, key=lambda x: x["name"])
item_lookup   = {item["name"]: item for item in all_items}
display_opts  = [o["name"] for o in all_items]

if not display_opts:
    display_opts = ["General Volunteer Support"]

if st.session_state.event_index >= len(display_opts):
    st.session_state.event_index = 0

# ANALYTICS DASHBOARD METRICS
m1, m2, m3 = st.columns(3)
with m1:
    st.metric("YTD Hours Balance", f"{st.session_state.total_hours:.2f}")
with m2:
    st.metric("Session Shifts Tracked", len(st.session_state.history))
with m3:
    elapsed = fmt_duration(utc_now() - st.session_state.start_time) if st.session_state.start_time else "—"
    st.metric("Active Run Duration", elapsed)

st.write("")

# TRACKING ENGINE
st.write("### 🕒")

with st.container(border=True):
    if st.session_state.pending_event:
        if st.session_state.pending_event in display_opts:
            st.session_state.event_index = display_opts.index(st.session_state.pending_event)
        st.session_state.pending_event = None

    chosen = st.selectbox(
        "Choose Event:",
        options=display_opts,
        index=st.session_state.event_index,
        help="Synchronized live from active Cloud Registry",
    )

    st.session_state.event_index          = display_opts.index(chosen)
    st.session_state.selected_event_name  = chosen
    selected_item                         = item_lookup.get(chosen, {})
    st.session_state.selected_event_id    = selected_item.get("id", "")

    st.write("")

    if st.session_state.start_time is None:
        if st.button("🟢 CLOCK IN", use_container_width=True):
            if not st.session_state.user_email:
                st.sidebar.error("🚨 Account Verification Required! Enter Email first.")
            else:
                st.session_state.start_time = utc_now()
                dbg(f"Clocked IN | assignment: {chosen}")
                st.rerun()
    else:
        elapsed_live = fmt_duration(utc_now() - st.session_state.start_time)
        st.success(f"✅ Clocked in at {fmt_time(st.session_state.start_time)} ({elapsed_live} current duration)")

    if st.session_state.start_time is not None:
        if st.button("🔴 CLOCK OUT", use_container_width=True):
            captured_start = st.session_state.start_time
            captured_end   = utc_now()
            duration       = captured_end - captured_start
            hours          = duration.total_seconds() / 3600

            pushed, push_err = False, ""
            if neon_configured() and st.session_state.account_id != "demo-account":
                item = item_lookup.get(chosen, {})
                with st.spinner("Syncing timeline metrics directly to CRM TimeSheets Engine..."):
                    pushed, push_err = push_shift_to_neon(
                        account_id     = st.session_state.account_id,
                        opportunity_id = item.get("id", ""),
                        target_dt      = captured_end,
                        hours          = hours
                    )
                    if pushed:
                        fetch_volunteer_hours.clear()
                        h, _ = fetch_volunteer_hours(st.session_state.account_id)
                        st.session_state.total_hours = h

            st.session_state.history.append({
                "date":       to_local(captured_end).strftime("%b %d, %Y"),
                "event":      chosen,
                "clock_in":   fmt_time(captured_start),
                "clock_out":  fmt_time(captured_end),
                "duration_h": round(hours, 2),
            })
            
            if not pushed:
                st.session_state.total_hours = round(st.session_state.total_hours + hours, 2)

            st.session_state.start_time = None
            st.balloons()
            
            if pushed:
                st.success(f"🎉 Metrics Saved! Data Synchronized.\n\n**Duration:** {fmt_duration(duration)} recorded on **{chosen}**")
            else:
                st.warning(f"⚠️ Saved locally — Core CRM Push failed: {push_err}\n\n**Duration:** {fmt_duration(duration)}")
            
            st.rerun()

    st.write("")
    if not st.session_state.start_time:
        st.info("⚪ Please Clock In.")
        
# LOCAL SESSION HISTORY MONITOR
if st.session_state.history:
    st.write("")
    st.write("### 📋 Current Session Log Entries")
    with st.container(border=True):
        for entry in reversed(st.session_state.history):
            c1, c2, c3 = st.columns([3, 2, 1])
            with c1:
                st.write(f"**{entry['event']}**")
                st.caption(entry["date"])
            with c2:
                st.caption(f"{entry['clock_in']} → {entry['clock_out']}")
            with c3:
                st.write(f"**{entry['duration_h']:.2f}h**")

# REMOTE SCHEDULE
st.write("")
st.write("### 📅 Live Schedule Feed & Opportunities")

if not all_items:
    st.info("No active registry entries located on remote pipeline server.")
else:
    for opp in all_items:
        with st.container(border=True):
            ec1, ec2 = st.columns([3, 1])
            with ec1:
                st.write(f"**{opp['name']}**")
                st.caption("Ongoing Project Campaign Track")
            with ec2:
                if st.button("Select Target", key=f"sel_{opp['id']}", use_container_width=True):
                    st.session_state.pending_event = opp["name"]
                    st.rerun()

# SYSTEM FOOTER
st.divider()
st.markdown(
    "<div class='custom-footer'>© 2026 Autonomy Project | Data encrypted in transit</div>",
    unsafe_allow_html=True,
)
