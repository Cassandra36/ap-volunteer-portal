import streamlit as st
import datetime
import requests
import base64
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

# ---------------------------------------------------------------------------
# CUSTOM CSS
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    [data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 12px !important;
        padding: 4px 8px !important;
    }
    div[data-testid="column"]:first-child .stButton > button {
        background-color: #1D9E75; color: white;
        border: none; font-weight: 600; border-radius: 8px;
    }
    div[data-testid="column"]:first-child .stButton > button:hover { background-color: #0F6E56; }
    div[data-testid="column"]:last-child .stButton > button {
        background-color: #E24B4A; color: white;
        border: none; font-weight: 600; border-radius: 8px;
    }
    div[data-testid="column"]:last-child .stButton > button:hover { background-color: #A32D2D; }
    [data-testid="metric-container"] { background-color: #f5f5f5; border-radius: 10px; padding: 12px 16px; }
    footer { visibility: hidden; }
    .custom-footer { text-align: center; font-size: 12px; color: #999; margin-top: 2rem; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# TIMEZONE  –  edit to match your org
# ---------------------------------------------------------------------------
LOCAL_TZ = ZoneInfo("America/New_York")

def local_now() -> datetime.datetime:
    return datetime.datetime.now(tz=LOCAL_TZ)

def fmt_time(dt: datetime.datetime) -> str:
    return dt.strftime("%I:%M %p")

def fmt_duration(td: timedelta) -> str:
    total_seconds = int(td.total_seconds())
    h, rem = divmod(total_seconds, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h {m:02d}m"

# ---------------------------------------------------------------------------
# NEONCRM v2 REST API HELPERS
# Credentials live in .streamlit/secrets.toml:
#   [neon]
#   org_id  = "your-org-id"
#   api_key = "your-api-key"
# ---------------------------------------------------------------------------

def _neon_headers() -> dict:
    org_id  = st.secrets["neon"]["org_id"]
    api_key = st.secrets["neon"]["api_key"]
    creds   = base64.b64encode(f"{org_id}:{api_key}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}

NEON_BASE = "https://api.neoncrm.com/v2"

def neon_configured() -> bool:
    try:
        _ = st.secrets["neon"]["api_key"]
        _ = st.secrets["neon"]["org_id"]
        return True
    except Exception:
        return False


# ── Fetch upcoming events ────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def fetch_neon_events() -> tuple[list[dict], str]:
    """Returns (events, error_string). error_string is '' on success."""
    today = datetime.date.today().strftime("%Y-%m-%d")
    url   = f"{NEON_BASE}/events"
    params = {
        "startDateAfter": today,
        "currentPage":    0,
        "pageSize":       50,
        "sortColumn":     "startDate",
        "sortDirection":  "ASC",
    }
    try:
        resp = requests.get(url, headers=_neon_headers(), params=params, timeout=10)
    except requests.exceptions.ConnectionError as e:
        return [], f"Connection error — {e}"
    except requests.exceptions.Timeout:
        return [], "Request timed out after 10 seconds."

    if resp.status_code == 401:
        return [], "401 Unauthorized — double-check your org_id and api_key in secrets.toml."
    if resp.status_code == 403:
        return [], "403 Forbidden — your API key may not have Events read permission in NeonCRM."
    if not resp.ok:
        return [], f"HTTP {resp.status_code} error: {resp.text[:400]}"

    try:
        data = resp.json()
    except Exception as e:
        return [], f"Could not parse JSON response: {e}\n\nRaw: {resp.text[:300]}"

    raw = data.get("events") or []
    parsed = []
    for ev in raw:
        name     = ev.get("name", "Untitled")
        reg_open = ev.get("enableEventRegistrationForm", False)
        # Skip template events and closed events
        if "template" in name.lower():
            continue
        if not reg_open:
            continue
        parsed.append({
            "Event Id":                 str(ev.get("id", "")),
            "Event Name":               name,
            "Event Start Date":         ev.get("startDate", ""),
            "Event End Date":           ev.get("endDate", ""),
            "Event Start Time":         ev.get("startTime", ""),
            "Event Registration Open":  "Yes",
            "Event Registration Count": str(ev.get("registrantCount", "") or ""),
            "Event Maximum Attendees":  str(ev.get("maximumAttendees", "") or ""),
        })
    return parsed, ""


# ── Look up account by email ─────────────────────────────────────────────────
@st.cache_data(ttl=60)
def fetch_account_id(email: str) -> tuple[str | None, str]:
    """Returns (account_id, error_string)."""
    url    = f"{NEON_BASE}/accounts"
    params = {"userType": "INDIVIDUAL", "email": email, "currentPage": 0, "pageSize": 1}
    try:
        resp = requests.get(url, headers=_neon_headers(), params=params, timeout=10)
    except Exception as e:
        return None, str(e)

    if resp.status_code == 401:
        return None, "401 Unauthorized — check credentials."
    if not resp.ok:
        return None, f"HTTP {resp.status_code}: {resp.text[:200]}"

    try:
        accounts = resp.json().get("accounts") or []
        if accounts:
            return str(accounts[0].get("accountId", "")), ""
        return None, ""
    except Exception as e:
        return None, f"Parse error: {e}"


# ── Fetch volunteer hours from NeonCRM ───────────────────────────────────────
@st.cache_data(ttl=60)
def fetch_volunteer_hours(account_id: str) -> float:
    """
    Reads volunteer hours from NeonCRM v2.
    NeonCRM stores volunteer hours as Activity records — adjust if your org
    uses a custom field instead.
    """
    url    = f"{NEON_BASE}/accounts/{account_id}/volunteerHours"
    params = {"currentPage": 0, "pageSize": 200}
    try:
        resp = requests.get(url, headers=_neon_headers(), params=params, timeout=10)
        if not resp.ok:
            return 0.0
        rows = resp.json().get("volunteerHours") or []
        return sum(float(r.get("hours", 0) or 0) for r in rows)
    except Exception:
        return 0.0


# ── Push a completed shift ───────────────────────────────────────────────────
def push_shift_to_neon(
    account_id: str,
    event_id:   str,
    start_dt:   datetime.datetime,
    end_dt:     datetime.datetime,
    hours:      float,
    note:       str = "",
) -> tuple[bool, str]:
    """Returns (success, error_message)."""
    url = f"{NEON_BASE}/accounts/{account_id}/volunteerHours"
    payload = {
        "hours":     round(hours, 2),
        "startDate": start_dt.strftime("%Y-%m-%d"),
        "endDate":   end_dt.strftime("%Y-%m-%d"),
        "note":      note,
    }
    if event_id:
        payload["eventId"] = event_id
    try:
        resp = requests.post(url, headers=_neon_headers(), json=payload, timeout=10)
        if resp.ok:
            return True, ""
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# DEMO DATA (shown when secrets.toml is not configured)
# ---------------------------------------------------------------------------
DEMO_EVENTS = [
    {"Event Id": "demo-1", "Event Name": "Community Outreach Day",
     "Event Start Date": "2026-05-28", "Event Start Time": "09:00",
     "Event Registration Open": "Yes", "Event Registration Count": "12", "Event Maximum Attendees": "30"},
    {"Event Id": "demo-2", "Event Name": "Empowerment Workshop",
     "Event Start Date": "2026-06-02", "Event Start Time": "14:00",
     "Event Registration Open": "Yes", "Event Registration Count": "8",  "Event Maximum Attendees": "20"},
    {"Event Id": "demo-3", "Event Name": "Peer Support Training",
     "Event Start Date": "2026-06-14", "Event Start Time": "10:00",
     "Event Registration Open": "Yes", "Event Registration Count": "5",  "Event Maximum Attendees": "15"},
]

# ---------------------------------------------------------------------------
# SESSION STATE
# ---------------------------------------------------------------------------
defaults = {
    "start_time": None, "account_id": None, "total_hours": 0.0,
    "history": [], "selected_event_id": "", "selected_event_name": "General Volunteer", "pending_event_select": None, "current_event_index": 0,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ---------------------------------------------------------------------------
# HEADER
# ---------------------------------------------------------------------------
left_sp, mid_col, right_sp = st.columns([1, 1, 1])
with mid_col:
    try:
        st.image("logo.png", width=120)
    except Exception:
        st.markdown("<h2 style='text-align:center'>💜</h2>", unsafe_allow_html=True)

st.header("Volunteer Portal", divider="violet")

# ---------------------------------------------------------------------------
# SIDEBAR
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("💜 Autonomy Project")
    st.divider()

    user_email = st.text_input("Volunteer Email", placeholder="your@email.com")

    if user_email and neon_configured():
        if st.button("🔍 Load My Profile", use_container_width=True):
            with st.spinner("Looking up account…"):
                acct, err = fetch_account_id(user_email)
                if err:
                    st.error(f"NeonCRM error: {err}")
                elif acct:
                    st.session_state.account_id  = acct
                    fetch_volunteer_hours.clear()
                    st.session_state.total_hours = fetch_volunteer_hours(acct)
                    st.success("Welcome back!")
                else:
                    st.warning("Email not found in NeonCRM. Check spelling or contact your coordinator.")
    elif user_email and not neon_configured():
        st.session_state.account_id = "demo-account"

    if not neon_configured():
        st.info("⚙️ **Demo mode** — add NeonCRM credentials in `.streamlit/secrets.toml` to go live.")

    st.divider()

    if st.session_state.account_id:
        st.metric("My Total Hours", f"{st.session_state.total_hours:.1f} hrs")

    st.caption("v2.1.0 | Secure Volunteer Portal")

# ---------------------------------------------------------------------------
# LOAD EVENTS  (with visible error if something goes wrong)
# ---------------------------------------------------------------------------
if neon_configured():
    events_raw, events_err = fetch_neon_events()
else:
    events_raw, events_err = DEMO_EVENTS, ""

if events_err:
    st.error(f"⚠️ Could not load events from NeonCRM:\n\n`{events_err}`")
    with st.expander("🔧 Troubleshooting tips"):
        st.markdown("""
**Check these in order:**
1. Open `.streamlit/secrets.toml` and confirm `org_id` and `api_key` are correct — no extra spaces or quotes.
2. In NeonCRM → Admin → API Keys, make sure the key has **Events** read permission.
3. Make sure your NeonCRM org subdomain matches (some orgs use a custom domain).
4. Try pasting this into your browser (replacing values) to test the raw API:
   `https://api.neoncrm.com/v2/events?startDateAfter=2026-01-01`
   — it should ask for Basic Auth credentials.
        """)

events_map    = {e["Event Name"]: e["Event Id"] for e in events_raw} if events_raw else {}
event_options = ["General Volunteer"] + list(events_map.keys())

# ---------------------------------------------------------------------------
# METRICS ROW
# ---------------------------------------------------------------------------
col_m1, col_m2, col_m3 = st.columns(3)
with col_m1:
    st.metric("Total Hours", f"{st.session_state.total_hours:.1f}")
with col_m2:
    st.metric("Shifts This Session", len(st.session_state.history))
with col_m3:
    if st.session_state.start_time:
        elapsed_str = fmt_duration(local_now() - st.session_state.start_time)
    else:
        elapsed_str = "—"
    st.metric("Current Shift", elapsed_str)

st.write("")

# ---------------------------------------------------------------------------
# SHIFT TRACKER
# ---------------------------------------------------------------------------
st.write("### 🕒 Shift Tracking")

with st.container(border=True):
    # Resolve any pending selection from the event list "Select" buttons
    if st.session_state.get("pending_event_select") and st.session_state["pending_event_select"] in event_options:
        st.session_state["current_event_index"] = event_options.index(st.session_state["pending_event_select"])
        st.session_state["pending_event_select"] = None

    selected_event_name = st.selectbox(
        "Which event are you volunteering for?",
        options=event_options,
        index=st.session_state.get("current_event_index", 0),
        help="Events pulled live from NeonCRM",
    )
    st.session_state["current_event_index"] = event_options.index(selected_event_name)
    st.session_state.selected_event_name = selected_event_name
    st.session_state.selected_event_id   = events_map.get(selected_event_name, "")

    col_in, col_out = st.columns(2)

    with col_in:
        if st.button("CLOCK IN", key="btn_in", use_container_width=True):
            if not user_email:
                st.error("Please enter your email in the sidebar first.")
            elif not st.session_state.account_id and neon_configured():
                st.error("Click **Load My Profile** in the sidebar.")
            elif st.session_state.start_time:
                st.warning("You're already clocked in!")
            else:
                now = local_now()
                st.session_state.start_time = now
                st.toast(f"✅ Clocked in at {fmt_time(now)}")

    with col_out:
        if st.button("CLOCK OUT", key="btn_out", use_container_width=True):
            if not st.session_state.start_time:
                st.error("You aren't clocked in!")
            else:
                end_time = local_now()
                duration = end_time - st.session_state.start_time
                hours    = duration.total_seconds() / 3600

                pushed, push_err = False, ""
                if neon_configured() and st.session_state.account_id:
                    with st.spinner("Saving shift to NeonCRM…"):
                        pushed, push_err = push_shift_to_neon(
                            account_id = st.session_state.account_id,
                            event_id   = st.session_state.selected_event_id,
                            start_dt   = st.session_state.start_time,
                            end_dt     = end_time,
                            hours      = hours,
                            note       = f"Logged via Volunteer Portal – {selected_event_name}",
                        )
                        if pushed:
                            fetch_volunteer_hours.clear()
                            st.session_state.total_hours = fetch_volunteer_hours(
                                st.session_state.account_id
                            )
                        elif push_err:
                            st.warning(f"Shift saved locally but NeonCRM push failed: {push_err}")

                st.session_state.history.append({
                    "date":       end_time.strftime("%b %d, %Y"),
                    "event":      selected_event_name,
                    "clock_in":   fmt_time(st.session_state.start_time),
                    "clock_out":  fmt_time(end_time),
                    "duration_h": round(hours, 2),
                })
                if not pushed:
                    st.session_state.total_hours = round(st.session_state.total_hours + hours, 2)
                st.session_state.start_time = None

                st.balloons()
                status_msg = "✅ Saved to NeonCRM!" if pushed else "✅ Shift logged locally."
                st.success(
                    f"{status_msg}  \n"
                    f"**Duration:** {fmt_duration(duration)}  \n"
                    f"**Event:** {selected_event_name}"
                )

    st.write("")
    if st.session_state.start_time:
        elapsed = local_now() - st.session_state.start_time
        st.success(
            f"🟢 Clocked in since **{fmt_time(st.session_state.start_time)}** "
            f"— {fmt_duration(elapsed)} elapsed"
        )
    else:
        st.info("⚪ Not currently clocked in")

# ---------------------------------------------------------------------------
# SESSION HISTORY
# ---------------------------------------------------------------------------
if st.session_state.history:
    st.write("")
    st.write("### 📋 Session History")
    with st.container(border=True):
        for entry in reversed(st.session_state.history):
            hcol1, hcol2, hcol3 = st.columns([3, 2, 1])
            with hcol1:
                st.write(f"**{entry['event']}**")
                st.caption(entry["date"])
            with hcol2:
                st.caption(f"{entry['clock_in']} → {entry['clock_out']}")
            with hcol3:
                st.write(f"**{entry['duration_h']:.2f}h**")

# ---------------------------------------------------------------------------
# UPCOMING EVENTS
# ---------------------------------------------------------------------------
st.write("")
st.write("### 📅 Upcoming Events")

if not events_raw and not events_err:
    st.info("No upcoming events found in NeonCRM. Events will appear here once they are created.")
elif events_raw:
    for event in events_raw:
        name       = event.get("Event Name", "Untitled Event")
        start_date = event.get("Event Start Date", "")
        start_time = event.get("Event Start Time", "")
        reg_open   = event.get("Event Registration Open", "")
        reg_count  = event.get("Event Registration Count", "")
        max_attend = event.get("Event Maximum Attendees", "")

        try:
            dt_obj       = datetime.datetime.strptime(start_date, "%Y-%m-%d")
            date_display = dt_obj.strftime("%B %d, %Y")
        except Exception:
            date_display = start_date

        capacity_str = ""
        if reg_count and max_attend:
            try:
                pct          = int(reg_count) / int(max_attend) * 100
                capacity_str = f"| {int(pct)}% full ({reg_count}/{max_attend})"
            except Exception:
                pass

        reg_label = "Registration Open" if reg_open == "Yes" else "Closed"

        with st.container(border=True):
            ev_col1, ev_col2 = st.columns([3, 1])
            with ev_col1:
                st.write(f"**{name}**")
                st.caption(f"{date_display}  {start_time}  |  {reg_label}  {capacity_str}")
            with ev_col2:
                if st.button("Select", key=f"select_{event.get('Event Id', name)}", use_container_width=True):
                    st.session_state.pending_event_select = name
                    st.toast(f"Event set to: {name}")
                    st.rerun()

# ---------------------------------------------------------------------------
# FOOTER
# ---------------------------------------------------------------------------
st.divider()
st.markdown(
    "<div class='custom-footer'>© 2026 Autonomy Project | Volunteer data is encrypted in transit</div>",
    unsafe_allow_html=True,
)
