import streamlit as st
import datetime
from datetime import timedelta


# --- PAGE SETUP ---
st.set_page_config(
    page_title="Autonomy Project Portal",
    page_icon="💜",
    layout="centered" 
)

# --- HEADER SECTION ---
left_sp, mid_col, right_sp = st.columns([1, 1, 1]) 
with mid_col:
    try:
        st.image("logo.png", width=120) 
    except:
        st.header("💜")

# Standard header with a built-in purple divider
st.header("Volunteer Portal", divider="violet")

# --- STATE & SIDEBAR ---
if 'start_time' not in st.session_state:
    st.session_state.start_time = None

with st.sidebar:
    st.title("Autonomy Project")
    user_email = st.sidebar.text_input("Volunteer Email", placeholder="your@email.com")
    st.divider()
    st.caption("v1.0.2 | Secure Portal")

# --- MAIN INTERFACE ---

st.write("### 🕒 Shift Tracking")
with st.container(border=True):
    col_in, col_out = st.columns(2)
    
    with col_in:
        if st.button("CLOCK IN", key="btn_in", use_container_width=True):
            if not user_email:
                st.error("Please enter email in sidebar")
            else:
                # 1. First, define what 'local_now' is
                local_now = datetime.datetime.now() - timedelta(hours=4)
                
                # 2. Now you can use it
                st.session_state.start_time = local_now
                st.toast(f"Shift Started at {local_now.strftime('%I:%M %p')}")

    with col_out:
        if st.button("CLOCK OUT", key="btn_out", use_container_width=True):
            if st.session_state.start_time:
                # Use the same 'local_now' logic for the end time
                end_time = datetime.datetime.now() - timedelta(hours=4)
                duration = end_time - st.session_state.start_time
                
                # Format the duration to be readable
                st.balloons()
                st.success(f"Shift logged! Duration: {duration}")
                st.session_state.start_time = None
            else:
                st.error("You aren't clocked in!")

    if st.session_state.start_time:
        st.success(f"● Currently Clocked In since {st.session_state.start_time.strftime('%H:%M')}")
    else:
        st.write("○ Status: Not Clocked In")

st.write("") 

st.write("### 📅 Available Events")
# Event 1
with st.container(border=True):
    ev_col1, ev_col2 = st.columns([3, 1])
    with ev_col1:
        st.write("**Community Outreach**")
        st.caption("Sign Up Open | May 20, 2026")
    with ev_col2:
        st.button("View", key="ev1", use_container_width=True)

# Event 2
with st.container(border=True):
    ev2_col1, ev2_col2 = st.columns([3, 1])
    with ev2_col1:
        st.write("**Empowerment Workshop**")
        st.caption("Registration Required | June 02, 2026")
    with ev2_col2:
        st.button("View", key="ev2", use_container_width=True)

st.divider()
st.caption("© 2026 Autonomy Project")
