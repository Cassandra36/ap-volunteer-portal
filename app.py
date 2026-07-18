import streamlit as st
import datetime
import requests
import base64
import json
import os
import re  # <--- ADD THIS LINE HERE
from datetime import timedelta
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Autonomy Project – Volunteer Portal",
    page_icon="💜",
    layout="centered",
)

# Custom CSS targeting app aesthetics while keeping mobile sidebar triggers intact
st.markdown("""
<style>
    /* Safely hide deployment options and default footer without breaking menu toggles */
    .stAppDeployButton { display: none !important; }
    footer { visibility: hidden !important; }
    
    .custom-footer { text-align: center; font-size: 12px; color: #999; margin-top: 2rem; }
    
    /* Responsive Mobile Metric Containers */
    [data-testid="stMetricValue"] {
        font-size: 1.3rem !important;
        line-height: 1.1 !important;
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.8rem !important;
        white-space: nowrap !important;
    }
    [data-testid="metric-container"] { 
        background-color: #f5f5f5; 
        border-radius: 10px; 
        padding: 8px 12px; 
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# TIMEZONE
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# PERSISTENT SHIFT MANAGER (JSON-BASED)
# ---------------------------------------------------------------------------
ACTIVE_SHIFTS_FILE = "active_shifts.json"

def get_active_shift(account_id):
    """Retrieves an ongoing shift for a specific user."""
    if not os.path.exists(ACTIVE_SHIFTS_FILE):
        return None
    with open(ACTIVE_SHIFTS_FILE, "r") as f:
        shifts = json.load(f)
    return shifts.get(str(account_id))

def save_active_shift(account_id, start_time_dt, event_name):
    """Saves a user's clock-in time to the server."""
    shifts = {}
    if os.path.exists(ACTIVE_SHIFTS_FILE):
        with open(ACTIVE_SHIFTS_FILE, "r") as f:
            shifts = json.load(f)
            
    shifts[str(account_id)] = {
        "start_time": start_time_dt.isoformat(),
        "event_name": event_name
    }
    with open(ACTIVE_SHIFTS_FILE, "w") as f:
        json.dump(shifts, f)

def clear_active_shift(account_id):
    """Removes the active shift once they clock out."""
    if os.path.exists(ACTIVE_SHIFTS_FILE):
        with open(ACTIVE_SHIFTS_FILE, "r") as f:
            shifts = json.load(f)
        if str(account_id) in shifts:
            del shifts[str(account_id)]
            with open(ACTIVE_SHIFTS_FILE, "w") as f:
                json.dump(shifts, f)

# ---------------------------------------------------------------------------
# SESSION STATE
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# NEONCRM API INTERFACE
# ---------------------------------------------------------------------------
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

# ── Opportunities ────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def fetch_neon_opportunities():
    url = f"{NEON_BASE}/opportunities"
    parsed = []
    current_page = 0
    today = datetime.datetime.now(tz=LOCAL_TZ).date()
    
    while True:
        params = {"currentPage": current_page, "pageSize": 50}
        try:
            resp = requests.get(url, headers=_neon_headers(), params=params, timeout=10)
        except Exception as e:
            dbg(f"API Error on page {current_page}: {e}")
            return parsed, str(e)
            
        if not resp.ok:
            dbg(f"API Failed HTTP {resp.status_code}")
            return parsed, f"HTTP {resp.status_code}: {resp.text[:200]}"
            
        raw = resp.json().get("opportunityList") or []
        if not raw:
            break
            
        dbg(f"Page {current_page}: Fetched {len(raw)} raw items from API.")
        
        for opp in raw:
            name   = opp.get("name", "Untitled")
            status = str(opp.get("status", "")).strip().upper()
            
            # 1. Drop ONLY explicitly closed/inactive ones. Do NOT strictly require "ACTIVE".
            if "template" in name.lower() or status in ["INACTIVE", "CLOSED", "CANCELED"]:
                dbg(f"HIDDEN (Inactive/Template): {name} [Status: {status}]")
                continue
                
            end_date_str = opp.get("endDate")
            start_date_str = opp.get("startDate")
            is_past = False
            
            # 2. If it has an explicit end date, judge it entirely on that.
            if end_date_str:
                try:
                    end_date = datetime.datetime.strptime(str(end_date_str).split("T")[0], "%Y-%m-%d").date()
                    if end_date < today:
                        is_past = True
                except Exception:
                    pass
            # 3. If NO end date, but it has a start date AND a date in the title (like your June events), it's a 1-day event.
            elif start_date_str and re.search(r'\d{1,2}/\d{1,2}/\d{4}', name):
                try:
                    start_date = datetime.datetime.strptime(str(start_date_str).split("T")[0], "%Y-%m-%d").date()
                    if start_date < today:
                        is_past = True
                except Exception:
                    pass
                    
            if is_past:
                dbg(f"HIDDEN (Past Date): {name} | End: {end_date_str} | Start: {start_date_str}")
                continue
                
            # 4. It passed! Keep the event.
            parsed.append({
                "id":   str(opp.get("id", "")),
                "name": name,
                "date": "",
                "time": "",
                "type": "opportunity",
            })
            
        if len(raw) < 50:
            break
        current_page += 1
        
    dbg(f"SUCCESS: Total valid opportunities parsed: {len(parsed)}")
    return parsed, ""
# ── Shifts Lookup ────────────────────────────────────────────────────────────
def get_or_create_active_shift_neon(opportunity_id):
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

# ── Account lookup ───────────────────────────────────────────────────────────
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

# ── Fetch Existing Hours via TimeSheets API (Exhaustive Calendar Year Scan) ──
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

# ── Smart Push Entry (POST or PUT depending on presence) ────────────────────
def push_shift_to_neon(account_id, opportunity_id, target_dt, hours):
    local_date = target_dt.astimezone(LOCAL_TZ)
    monday = local_date - timedelta(days=local_date.weekday())
    week_of_str = monday.strftime("%Y-%m-%d")

    shift_id, _ = get_or_create_active_shift_neon(opportunity_id)

    _, existing_sheets = fetch_volunteer_hours(account_id)
    matched_sheet = None
    
    for sheet in existing_sheets:
        if str(sheet.get("projectId")) == str(opportunity_id) and str(sheet.get("weekOf")) == week_of_str:
            matched_sheet = sheet
            break

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

# ---------------------------------------------------------------------------
# UI LOGO AND HEADER SECTION
# ---------------------------------------------------------------------------
try:
    st.markdown(
        """
        <div style="display: flex; justify-content: center; align-items: center; margin-bottom: 10px;">
            <img src="data:image/png;base64,{}" width="120">
        </div>
        """.format(base64.b64encode(open("logo.png", "rb").read()).decode()), 
        unsafe_allow_html=True
    )
except Exception:
    st.markdown("<h2 style='text-align:center; margin-bottom: 10px;'>💜</h2>", unsafe_allow_html=True)

st.header("Volunteer Portal", divider="violet")

# ---------------------------------------------------------------------------
# SIDEBAR CONTROL PANEL (SECURED & VISIBILITY RESTRICTED)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("💜 Autonomy Project")
    st.divider()

    # PUBLIC PORTAL SIGN-IN (Always accessible to all users)
    with st.form(key="profile_lookup_form", clear_on_submit=False):
        email_input = st.text_input(
            "Volunteer Email Address",
            value=st.session_state.user_email,
            placeholder="your@email.com",
            key="email_field",
        )
        submit_email = st.form_submit_button("➡️ Log In & Load Profile", use_container_width=True)

    if submit_email:
        if not email_input.strip():
            st.error("Please enter a valid email address.")
        else:
            st.session_state.user_email = email_input
            dbg(f"Email profile queried: {email_input}")
            
            if neon_configured():
                with st.spinner("Connecting to database registry..."):
                    acct, err = fetch_account_id(email_input)
                    if err:
                        st.error(f"NeonCRM Sync Error: {err}")
                    elif acct:
                        st.session_state.account_id = acct
                        fetch_volunteer_hours.clear()
                        hours, _ = fetch_volunteer_hours(acct)
                        st.session_state.total_hours = hours
                        
                        # RESTORE ACTIVE SHIFT IF PRESENT
                        active_shift = get_active_shift(acct)
                        if active_shift:
                            st.session_state.start_time = datetime.datetime.fromisoformat(active_shift["start_time"])
                            st.session_state.selected_event_name = active_shift["event_name"]
                            st.session_state.pending_event = active_shift["event_name"]
                            st.toast("⚠️ Restored an active shift from your previous session.")
                            
                        st.success("Profile loaded successfully!")
                        st.rerun()
                    else:
                        st.warning("Email not registered in database framework.")
            else:
                st.session_state.account_id = "demo-account"
                
                # RESTORE ACTIVE SHIFT FOR DEMO ACCOUNT
                active_shift = get_active_shift("demo-account")
                if active_shift:
                    st.session_state.start_time = datetime.datetime.fromisoformat(active_shift["start_time"])
                    st.session_state.selected_event_name = active_shift["event_name"]
                    st.session_state.pending_event = active_shift["event_name"]
                    st.toast("⚠️ Restored an active shift from your previous session.")
                    
                st.success("Local sandbox profile initialized.")
                st.rerun()

    # User Profile Metric Banner
    if st.session_state.user_email and st.session_state.account_id:
        st.caption("Active Session Context:")
        st.info(f"👤 {st.session_state.user_email}")
        st.metric("My YTD Total Hours", f"{st.session_state.total_hours:.2f} hrs")
    else:
        st.warning("🔒 Portal Standby Mode. Please enter your volunteer email to authenticate session context.")

    st.divider()

    # ADMINISTRATIVE ACCESS NODE (Hidden/Locked under a master passkey)
    admin_toggle = st.checkbox("⚙️ Access Administrative Suite", value=False)
    is_admin = False
    
    if admin_toggle:
        pass_token = st.text_input("Enter Master Credential Key", type="password")
        if pass_token == "autonomy2026":
            is_admin = True
            st.success("Authorized: Administrative Engine Active.")
        elif pass_token:
            st.error("Authentication Failure: Access Denied.")

    # SECURED ADMIN-ONLY UTILITIES
    if is_admin:
        st.subheader("🛠️ Management Controls")

        if st.button("🔄 Purge System Cache", use_container_width=True):
            fetch_neon_opportunities.clear()
            fetch_volunteer_hours.clear()
            st.toast("System architecture caches successfully flushed!")
            st.rerun()

        with st.expander("🔧 Core Debug Arrays", expanded=False):
            if st.session_state.debug_log:
                for line in reversed(st.session_state.debug_log[-40:]):
                    st.caption(line)
            else:
                st.caption("Buffer stack empty.")
            if st.button("Clear Log Stack"):
                st.session_state.debug_log = []
            st.caption("**State Dump Data Matrix:**")
            st.json({
                "user_email":  st.session_state.user_email,
                "account_id":  st.session_state.account_id,
                "start_time":  str(st.session_state.start_time),
                "total_hours": st.session_state.total_hours,
                "event":       st.session_state.selected_event_name,
                "history_len": len(st.session_state.history),
            })

    st.divider()
    st.caption("v5.2.1 | Secured Administrative Node")

# ---------------------------------------------------------------------------
# DATA PIPELINE COLLECTION
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# COMPACT ANALYTICS DASHBOARD (2-COLUMN MOBILE SAFE)
# ---------------------------------------------------------------------------
m1, m2 = st.columns(2)
with m1:
    st.metric("YTD Hours Balance", f"{st.session_state.total_hours:.2f}")
with m2:
    st.metric("Session Shifts", len(st.session_state.history))

st.write("")

# ---------------------------------------------------------------------------
# LIVE TIMER FRAGMENT
# ---------------------------------------------------------------------------
@st.fragment(run_every=60)
def live_timer_display():
    if st.session_state.start_time is not None:
        elapsed_live = fmt_duration(utc_now() - st.session_state.start_time)
        st.success(f"✅ Clocked In at: {fmt_time(st.session_state.start_time)} ({elapsed_live})")

# ---------------------------------------------------------------------------
# CORE TRACKING ENGINE
# ---------------------------------------------------------------------------
st.write("### 🕒 Shift Tracking Engine")

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
            # Fallback if no account context exists yet to prevent loose data telemetry
            if not st.session_state.user_email:
                st.error("🚨 Account Context Required. Enter your email address in the sidebar menu to authenticate.")
            else:
                st.session_state.start_time = utc_now()
                # Save to persistent storage
                save_active_shift(st.session_state.account_id, st.session_state.start_time, chosen)
                
                dbg(f"Clocked IN | assignment: {chosen}")
                st.rerun()
    else:
        # Calls the auto-refreshing UI component
        live_timer_display()

    if st.session_state.start_time is not None:
        if st.button("🔴 CLOCK OUT", use_container_width=True):
            captured_start = st.session_state.start_time
            captured_end   = utc_now()
            duration       = captured_end - captured_start
            hours          = duration.total_seconds() / 3600

            pushed, push_err = False, ""
            if neon_configured() and st.session_state.account_id not in [None, "demo-account"]:
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

            # Clear state and persistent storage
            st.session_state.start_time = None
            clear_active_shift(st.session_state.account_id)
            
            st.balloons()
            
            if pushed:
                st.success(f"🎉 Metrics Saved! Data Synchronized.\n\n**Duration:** {fmt_duration(duration)} recorded on **{chosen}**")
            else:
                st.warning(f"⚠️ Saved locally — Core CRM Push failed: {push_err}\n\n**Duration:** {fmt_duration(duration)}")
            
            st.rerun()

    st.write("")
    if not st.session_state.start_time:
        st.info("⚪ Please Clock In.")

# ---------------------------------------------------------------------------
# LOCAL SESSION HISTORY MONITOR
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# REMOTE SCHEDULE FEED EXPLORER
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# SYSTEM FOOTER
# ---------------------------------------------------------------------------
st.divider()
st.markdown(
    "<div class='custom-footer'>© 2026 Autonomy Project | Data encrypted in transit</div>",
    unsafe_allow_html=True,
)
