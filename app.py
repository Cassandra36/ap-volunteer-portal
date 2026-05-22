import streamlit as st
import datetime
import requests
import base64
import json
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
    /* Card-style containers */
    [data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 12px !important;
        padding: 4px 8px !important;
    }
    /* Clock-in button green */
    div[data-testid="column"]:first-child .stButton > button {
        background-color: #1D9E75;
        color: white;
        border: none;
        font-weight: 600;
        border-radius: 8px;
    }
    div[data-testid="column"]:first-child .stButton > button:hover {
        background-color: #0F6E56;
    }
    /* Clock-out button red */
    div[data-testid="column"]:last-child .stButton > button {
        background-color: #E24B4A;
        color: white;
        border: none;
        font-weight: 600;
        border-radius: 8px;
    }
    div[data-testid="column"]:last-child .stButton > button:hover {
        background-color: #A32D2D;
    }
    /* Metric cards */
    [data-testid="metric-container"] {
        background-color: #f5f5f5;
        border-radius: 10px;
        padding: 12px 16px;
    }
    /* Footer */
    footer { visibility: hidden; }
    .custom-footer {
        text-align: center;
        font-size: 12px;
        color: #999;
        margin-top: 2rem;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# TIMEZONE  –  edit this to match your org's timezone
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
# NEONCRM API HELPERS
# ---------------------------------------------------------------------------
# Store your NeonCRM credentials in Streamlit secrets:
#   .streamlit/secrets.toml
#   [neon]
#   org_id   = "your-org-id"
#   api_key  = "your-api-key"

def _neon_headers() -> dict:
    org_id  = st.secrets["neon"]["org_id"]
    api_key = st.secrets["neon"]["api_key"]
    creds   = base64.b64encode(f"{org_id}:{api_key}".encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Content-Type":  "application/json",
    }

def _neon_base() -> str:
    org_id = st.secrets["neon"]["org_id"]
    return f"https://api.neoncrm.com/neonws/services/api"


# ── Fetch active/upcoming events from NeonCRM ──────────────────────────────
@st.cache_data(ttl=300)   # cache for 5 minutes
def fetch_neon_events() -> list[dict]:
    """
    Calls NeonCRM's Event Search endpoint and returns a list of upcoming events.
    Docs: https://developer.neoncrm.com/api/events/search/
    """
    try:
        today = datetime.date.today().strftime("%Y-%m-%d")
        url   = f"{_neon_base()}/event/listEvents"
        payload = {
            "listEventsRequest": {
                "userCredentials": {
                    "apiKey":         st.secrets["neon"]["api_key"],
                    "orgId":          st.secrets["neon"]["org_id"],
                },
                "outputFields": {
                    "idNamePair": [
                        {"id": "Event Id"},
                        {"id": "Event Name"},
                        {"id": "Event Start Date"},
                        {"id": "Event End Date"},
                        {"id": "Event Start Time"},
                        {"id": "Event Registration Count"},
                        {"id": "Event Maximum Attendees"},
                        {"id": "Event Registration Open"},
                    ]
                },
                "searchFields": {
                    "searchField": [
                        {
                            "field": "Event Start Date",
                            "operator": "GREATER_AND_EQUAL",
                            "value": today,
                        }
                    ]
                },
                "page": {
                    "currentPage": 1,
                    "pageSize": 20,
                    "sortColumn": "Event Start Date",
                    "sortDirection": "ASC",
                },
            }
        }
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        data   = resp.json()
        events = (
            data
            .get("listEventsResponse", {})
            .get("searchResults", {})
            .get("nameValuePairs", [])
        )
        # Normalise each event row into a flat dict
        parsed = []
        for row in events:
            pairs = row.get("nameValuePair", [])
            d = {p["name"]: p.get("value", "") for p in pairs}
            parsed.append(d)
        return parsed
    except Exception as e:
        return []   # handled gracefully in the UI


# ── Look up a volunteer's NeonCRM account ID by email ──────────────────────
@st.cache_data(ttl=60)
def fetch_account_id(email: str) -> str | None:
    try:
        url = f"{_neon_base()}/account/listAccounts"
        payload = {
            "listAccountsRequest": {
                "userCredentials": {
                    "apiKey": st.secrets["neon"]["api_key"],
                    "orgId":  st.secrets["neon"]["org_id"],
                },
                "searches": {
                    "search": [{"field": "Email", "operator": "EQUAL", "value": email}]
                },
                "outputFields": {
                    "idNamePair": [{"id": "Account ID"}]
                },
                "page": {"currentPage": 1, "pageSize": 1},
            }
        }
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        pairs = (
            resp.json()
            .get("listAccountsResponse", {})
            .get("searchResults", {})
            .get("nameValuePairs", [{}])[0]
            .get("nameValuePair", [])
        )
        d = {p["name"]: p.get("value", "") for p in pairs}
        return d.get("Account ID")
    except Exception:
        return None


# ── Fetch total volunteer hours for a given account ────────────────────────
@st.cache_data(ttl=60)
def fetch_volunteer_hours(account_id: str) -> float:
    """
    Queries NeonCRM's volunteer hour logs for the given account.
    Adjust the endpoint/field names to match your NeonCRM field setup.
    """
    try:
        url = f"{_neon_base()}/volunteer/listVolunteerHours"
        payload = {
            "listVolunteerHoursRequest": {
                "userCredentials": {
                    "apiKey": st.secrets["neon"]["api_key"],
                    "orgId":  st.secrets["neon"]["org_id"],
                },
                "searches": {
                    "search": [
                        {"field": "Account ID", "operator": "EQUAL", "value": account_id}
                    ]
                },
                "outputFields": {
                    "idNamePair": [{"id": "Hours"}]
                },
                "page": {"currentPage": 1, "pageSize": 200},
            }
        }
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        rows = (
            resp.json()
            .get("listVolunteerHoursResponse", {})
            .get("searchResults", {})
            .get("nameValuePairs", [])
        )
        total = 0.0
        for row in rows:
            for p in row.get("nameValuePair", []):
                if p["name"] == "Hours":
                    try:
                        total += float(p.get("value", 0))
                    except (TypeError, ValueError):
                        pass
        return total
    except Exception:
        return 0.0


# ── Push a completed shift into NeonCRM ────────────────────────────────────
def push_shift_to_neon(
    account_id: str,
    event_id:   str,
    start_dt:   datetime.datetime,
    end_dt:     datetime.datetime,
    hours:      float,
    note:       str = "",
) -> bool:
    """
    Creates a volunteer hour record in NeonCRM.
    Docs: https://developer.neoncrm.com/api/volunteers/hours/
    """
    try:
        url = f"{_neon_base()}/volunteer/createVolunteerHours"
        payload = {
            "createVolunteerHoursRequest": {
                "userCredentials": {
                    "apiKey": st.secrets["neon"]["api_key"],
                    "orgId":  st.secrets["neon"]["org_id"],
                },
                "volunteerHours": {
                    "accountId": account_id,
                    "eventId":   event_id if event_id else "",
                    "hours":     round(hours, 2),
                    "startDate": start_dt.strftime("%Y-%m-%d"),
                    "startTime": start_dt.strftime("%H:%M:%S"),
                    "endDate":   end_dt.strftime("%Y-%m-%d"),
                    "endTime":   end_dt.strftime("%H:%M:%S"),
                    "note":      note,
                    "status":    "Approved",
                },
            }
        }
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        result = resp.json().get("createVolunteerHoursResponse", {})
        return result.get("operationResult") == "SUCCESS"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# FALLBACK DEMO EVENTS (used when NeonCRM credentials are not configured)
# ---------------------------------------------------------------------------
DEMO_EVENTS = [
    {
        "Event Id":                   "demo-1",
        "Event Name":                 "Community Outreach Day",
        "Event Start Date":           "2026-05-28",
        "Event Start Time":           "09:00",
        "Event Registration Open":    "Yes",
        "Event Registration Count":   "12",
        "Event Maximum Attendees":    "30",
    },
    {
        "Event Id":                   "demo-2",
        "Event Name":                 "Empowerment Workshop",
        "Event Start Date":           "2026-06-02",
        "Event Start Time":           "14:00",
        "Event Registration Open":    "Yes",
        "Event Registration Count":   "8",
        "Event Maximum Attendees":    "20",
    },
    {
        "Event Id":                   "demo-3",
        "Event Name":                 "Peer Support Training",
        "Event Start Date":           "2026-06-14",
        "Event Start Time":           "10:00",
        "Event Registration Open":    "Yes",
        "Event Registration Count":   "5",
        "Event Maximum Attendees":    "15",
    },
]

def neon_configured() -> bool:
    try:
        _ = st.secrets["neon"]["api_key"]
        _ = st.secrets["neon"]["org_id"]
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# SESSION STATE INITIALISATION
# ---------------------------------------------------------------------------
defaults = {
    "start_time":    None,
    "account_id":    None,
    "total_hours":   0.0,
    "history":       [],          # list of dicts: {date, event, duration_h}
    "selected_event_id":   "",
    "selected_event_name": "General Volunteer",
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
# SIDEBAR – login & info
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("💜 Autonomy Project")
    st.divider()

    user_email = st.text_input("Volunteer Email", placeholder="your@email.com")

    # Look up account when email is provided
    if user_email and neon_configured():
        if st.button("🔍 Load My Profile", use_container_width=True):
            with st.spinner("Looking up account…"):
                acct = fetch_account_id(user_email)
                if acct:
                    st.session_state.account_id  = acct
                    st.session_state.total_hours = fetch_volunteer_hours(acct)
                    # Invalidate hours cache so next clock-out reflects new total
                    fetch_volunteer_hours.clear()
                    st.success(f"Welcome back!")
                else:
                    st.warning("Email not found in NeonCRM.\nCheck spelling or contact your coordinator.")
    elif user_email and not neon_configured():
        # Demo mode – simulate a found account
        st.session_state.account_id = "demo-account"

    if not neon_configured():
        st.info("⚙️ **Demo mode** – add NeonCRM credentials in `.streamlit/secrets.toml` to go live.")

    st.divider()

    # Show hours in sidebar too
    if st.session_state.account_id:
        st.metric("My Total Hours", f"{st.session_state.total_hours:.1f} hrs")

    st.caption("v2.0.0 | Secure Volunteer Portal")

# ---------------------------------------------------------------------------
# METRICS ROW
# ---------------------------------------------------------------------------
col_m1, col_m2, col_m3 = st.columns(3)
with col_m1:
    st.metric("Total Hours", f"{st.session_state.total_hours:.1f}")
with col_m2:
    shifts_done = len(st.session_state.history)
    st.metric("Shifts This Session", shifts_done)
with col_m3:
    if st.session_state.start_time:
        elapsed = local_now() - st.session_state.start_time
        elapsed_str = fmt_duration(elapsed)
    else:
        elapsed_str = "—"
    st.metric("Current Shift", elapsed_str)

st.write("")

# ---------------------------------------------------------------------------
# SHIFT TRACKER
# ---------------------------------------------------------------------------
st.write("### 🕒 Shift Tracking")

# Event selector – populated from NeonCRM (or demo data)
events_raw = fetch_neon_events() if neon_configured() else DEMO_EVENTS
events_map  = {e["Event Name"]: e["Event Id"] for e in events_raw} if events_raw else {}
event_options = ["General Volunteer"] + list(events_map.keys())

with st.container(border=True):
    selected_event_name = st.selectbox(
        "Which event are you volunteering for?",
        options=event_options,
        key="event_selector",
        help="Events are pulled live from NeonCRM",
    )
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

                pushed = False
                if neon_configured() and st.session_state.account_id:
                    with st.spinner("Saving shift to NeonCRM…"):
                        pushed = push_shift_to_neon(
                            account_id = st.session_state.account_id,
                            event_id   = st.session_state.selected_event_id,
                            start_dt   = st.session_state.start_time,
                            end_dt     = end_time,
                            hours      = hours,
                            note       = f"Logged via Volunteer Portal – {selected_event_name}",
                        )
                        if pushed:
                            # Refresh total hours from NeonCRM
                            fetch_volunteer_hours.clear()
                            st.session_state.total_hours = fetch_volunteer_hours(
                                st.session_state.account_id
                            )

                # Record locally regardless
                st.session_state.history.append({
                    "date":       end_time.strftime("%b %d, %Y"),
                    "event":      selected_event_name,
                    "clock_in":   fmt_time(st.session_state.start_time),
                    "clock_out":  fmt_time(end_time),
                    "duration_h": round(hours, 2),
                })
                st.session_state.total_hours = round(
                    st.session_state.total_hours + hours, 2
                )
                st.session_state.start_time = None

                st.balloons()
                status_msg = "✅ Saved to NeonCRM!" if pushed else "✅ Shift logged locally."
                st.success(
                    f"{status_msg}  \n"
                    f"**Duration:** {fmt_duration(duration)}  \n"
                    f"**Event:** {selected_event_name}"
                )

    # Status indicator
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
# SHIFT HISTORY (this session)
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

if not events_raw:
    st.info("No upcoming events found. Check your NeonCRM connection.")
else:
    for event in events_raw:
        name        = event.get("Event Name", "Untitled Event")
        start_date  = event.get("Event Start Date", "")
        start_time  = event.get("Event Start Time", "")
        reg_open    = event.get("Event Registration Open", "")
        reg_count   = event.get("Event Registration Count", "")
        max_attend  = event.get("Event Maximum Attendees", "")

        # Format date nicely
        try:
            dt_obj = datetime.datetime.strptime(start_date, "%Y-%m-%d")
            date_display = dt_obj.strftime("%B %d, %Y")
        except Exception:
            date_display = start_date

        # Capacity pill
        capacity_str = ""
        if reg_count and max_attend:
            try:
                pct = int(reg_count) / int(max_attend) * 100
                capacity_str = f"| {int(pct)}% full ({reg_count}/{max_attend})"
            except Exception:
                capacity_str = ""

        reg_label = "Registration Open" if reg_open == "Yes" else "Closed"

        with st.container(border=True):
            ev_col1, ev_col2 = st.columns([3, 1])
            with ev_col1:
                st.write(f"**{name}**")
                st.caption(f"{date_display}  {start_time}  |  {reg_label}  {capacity_str}")
            with ev_col2:
                if st.button("Select", key=f"select_{event.get('Event Id', name)}", use_container_width=True):
                    st.session_state.event_selector = name
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
