import streamlit as st
import datetime

# --- PAGE SETUP ---
st.set_page_config(
    page_title="Autonomy Project Portal",
    page_icon="💜",
    layout="centered" 
)

# --- HEADER SECTION ---
# We use columns to center the logo
left_sp, mid_col, right_sp = st.columns([1, 1, 1])
with mid_col:
    try:
        #logo
        st.image("logo.png", use_container_width=True)
    except Exception as e:
        st.header("Autonomy Project")
        st.error("Missing 'logo.jpg' in the AP/WE folder")

st.header("Volunteer Portal", divider="violet")

# --- STATE & SIDEBAR ---
if 'start_time' not in st.session_state:
    st.session_state.start_time = None

with st.sidebar:
    user_email = st.text_input("Enter Email to Begin", placeholder="your@email.com")

# --- MAIN INTERFACE ---
st.write("### 🕒 Shift Tracking")
with st.container(border=True):
    col_in, col_out = st.columns(2)
    with col_in:
        if st.button("Clock In", key="btn_in", use_container_width=True):
            if not user_email:
                st.error("Enter email in sidebar!")
            else:
                st.session_state.start_time = datetime.datetime.now()
                st.toast("Clocked In!")
    with col_out:
        if st.button("Clock Out", key="btn_out", use_container_width=True):
            if st.session_state.start_time:
                st.session_state.start_time = None
                st.balloons()
                st.toast("Clocked Out!")
            else:
                st.info("Not clocked in.")

st.write("### 📅 Available Events")
with st.container(border=True):
    c1, c2 = st.columns([3, 1])
    with c1:
        st.write("**Community Gala**")
        st.caption("Sign Up Open | May 20, 2026")
    with c2:
        st.button("View", key="ev1", use_container_width=True)

st.divider()
st.caption("Autonomy Project | autonomyproject.org")