import streamlit as st
import sqlite3
import pandas as pd
import requests
import os
import time
import hashlib
import json
import re
import plotly.express as px
from datetime import datetime, timedelta
from google import genai
from google.genai.errors import ClientError
from dotenv import load_dotenv
from fpdf import FPDF

load_dotenv()
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
WEATHER_KEY = os.getenv("WEATHER_API_KEY")

client = genai.Client(api_key=GEMINI_KEY)

st.set_page_config(page_title="LuxeTravel AI", layout="wide", page_icon="🌏")

def init_db():
    conn = sqlite3.connect('travel_app.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (username TEXT PRIMARY KEY, password TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS plans 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  username TEXT, 
                  destination TEXT, 
                  start_date TEXT, 
                  end_date TEXT, 
                  details TEXT, 
                  raw_json TEXT,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

def make_hashes(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def check_hashes(password, hashed_text):
    if make_hashes(password) == hashed_text: return hashed_text
    return False

def add_user(username, password):
    conn = sqlite3.connect('travel_app.db')
    c = conn.cursor()
    try:
        c.execute('INSERT INTO users(username, password) VALUES (?,?)', (username, make_hashes(password)))
        conn.commit()
        return True
    except:
        return False
    finally:
        conn.close()

def login_user(username, password):
    conn = sqlite3.connect('travel_app.db')
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE username =? AND password = ?', (username, make_hashes(password)))
    data = c.fetchall()
    conn.close()
    return data

def save_plan(username, dest, start, end, text, json_str):
    conn = sqlite3.connect('travel_app.db')
    c = conn.cursor()
    c.execute('INSERT INTO plans(username, destination, start_date, end_date, details, raw_json) VALUES (?,?,?,?,?,?)', 
              (username, dest, start, end, text, json_str))
    conn.commit()
    conn.close()

def get_history(username):
    conn = sqlite3.connect('travel_app.db')
    c = conn.cursor()
    c.execute('SELECT id, destination, start_date, details, raw_json FROM plans WHERE username=? ORDER BY id DESC LIMIT 10', (username,))
    data = c.fetchall()
    conn.close()
    return data

def delete_plan(plan_id):
    conn = sqlite3.connect('travel_app.db')
    c = conn.cursor()
    c.execute('DELETE FROM plans WHERE id=?', (plan_id,))
    conn.commit()
    conn.close()

class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 16)
        self.cell(0, 10, 'LuxeTravel AI - Premium Itinerary', 0, 1, 'C')
        self.ln(5)

def create_pdf(text):
    pdf = PDF()
    pdf.add_page()
    pdf.set_font("Arial", size=11)
    clean_text = text.replace("₹", "INR ").replace("’", "'").encode('latin-1', 'replace').decode('latin-1')
    pdf.multi_cell(0, 7, clean_text)
    return pdf.output(dest='S').encode('latin-1')

def get_weather(city, start_date_obj):
    days_diff = (start_date_obj - datetime.now().date()).days
    if days_diff < 0: return "Historical Trip"
    if days_diff > 5: return "Seasonal Average (Too far for live forecast)"
    
    url = f"http://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_KEY}&units=metric"
    try:
        r = requests.get(url).json()
        if r.get("cod") != "200": return "Data Unavailable"
        item = r['list'][0] 
        return f"{item['weather'][0]['description'].title()}, ~{item['main']['temp']}°C"
    except:
        return "Standard Seasonal Weather"

def generate_ai_plan(inputs):
    prompt = f"""
    Act as an elite travel concierge.
    Destination: {inputs['city']} | Dates: {inputs['start']} to {inputs['end']}
    Travelers: {inputs['people']} ({inputs['category']}) | Trip Vibe: {inputs['type']}
    Budget: ₹{inputs['budget']} Total | Weather Context: {inputs['weather']}

    GENERATE 5 SECTIONS IN MARKDOWN:

    SECTION 1: 🗣️ Local Lingo & Culture
    - 3 Essential phrases in the local language of {inputs['city']} (with English meaning).
    - 1 Important "Do's and Don'ts" rule.

    SECTION 2: 📅 Detailed Itinerary
    - Day-by-day plan. Mention costs in INR.
    - Highlight 1 "Hidden Gem".

    SECTION 3: 🎒 Smart Packing List
    - 5 bullet points based strictly on {inputs['weather']} and {inputs['type']}.
    - Format: "- Item Name: Reason"

    SECTION 4: 🍽️ Eat Like a Local
    - 3 specific dishes to try (not just restaurants).

    SECTION 5: BUDGET_JSON_BLOCK
    (Strictly at the end, provide a valid JSON object for cost allocation)
    {{
      "Accommodation": 0,
      "Food": 0,
      "Transport": 0,
      "Activities": 0,
      "Shopping": 0
    }}
    """
    
    for attempt in range(3):
        try:
            response = client.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt)
            return response.text
        except ClientError as e:
            if "429" in str(e): time.sleep(5)
            else: return f"Error: {e}"
    return "Service Busy."

def parse_ai_response(full_text):
    json_part = {}
    display_text = full_text
    
    try:
        match = re.search(r'\{.*"Accommodation":.*\}', full_text, re.DOTALL)
        if match:
            json_str = match.group(0)
            json_part = json.loads(json_str)
            display_text = full_text.replace(json_str, "").replace("SECTION 5: BUDGET_JSON_BLOCK", "")
    except:
        pass
        
    return display_text, json_part

def extract_packing_list(text):
    try:
        match = re.search(r"SECTION 3:.*?(?=SECTION 4)", text, re.DOTALL)
        if match:
            section_text = match.group(0)
            items = re.findall(r'- (.*)', section_text)
            return items
    except:
        return []
    return []

init_db()

if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ''

if not st.session_state.logged_in:
    col1, col2 = st.columns([1, 1])
    with col1:
        st.title("LuxeTravel AI 🌍")
        st.subheader("Next-Gen Travel Intelligence")
        st.markdown("""
        * **Smart Spend Analytics** 📊
        * **Weather-Adaptive Packing** 🎒
        * **Hyper-Local Culture Cards** 🗣️
        """)
        st.info("Login to access the Engine.")
    
    with col2:
        tab1, tab2 = st.tabs(["Login", "Sign Up"])
        with tab1:
            u = st.text_input("Username")
            p = st.text_input("Password", type="password")
            if st.button("Login"):
                if login_user(u, p):
                    st.session_state.logged_in = True
                    st.session_state.username = u
                    st.rerun()
                else:
                    st.error("Invalid credentials.")
        with tab2:
            nu = st.text_input("New Username")
            np = st.text_input("New Password", type="password")
            if st.button("Create Account"):
                if add_user(nu, np): st.success("Created! Login now.")
                else: st.error("Username taken.")

else:
    with st.sidebar:
        st.title(f"👤 {st.session_state.username}")
        if st.button("Log Out"):
            st.session_state.logged_in = False
            st.rerun()
        st.divider()
        st.success("🟢 AI Engine: Ready")
        st.caption("Model: v2.5-Lite (Encrypted)")

    st.title("🌏 Smart Travel Assistant")
    
    tab_gen, tab_hist = st.tabs(["✨ Trip Planner", "📂 Trip History"])

    with tab_gen:
        with st.form("main_form"):
            c1, c2, c3 = st.columns(3)
            with c1:
                city = st.text_input("Destination", "Kerala")
                start = st.date_input("Start Date", min_value=datetime.today()+timedelta(days=1))
            with c2:
                budget = st.number_input("Budget (INR)", 5000, 500000, 20000, step=1000)
                end = st.date_input("End Date", min_value=start)
            with c3:
                people = st.number_input("Travelers", 1, 15, 2)
                cat = st.selectbox("Category", ["Backpackers", "Family", "Couple", "Friends"])
            
            trip_type = st.select_slider("Trip Style", ["Relaxed", "Balanced", "Adventure", "Extreme"])
            
            gen_btn = st.form_submit_button("Generate Intelligence Report 🚀")

        if gen_btn:
            with st.status("Initializing Agents...", expanded=True) as status:
                st.write("📡 Satellite Uplink: Analyzing Weather...")
                weather = get_weather(city, start)
                
                st.write("🧠 Neural Core: Computing Budget & Culture...")
                inputs = {
                    "city": city, "start": str(start), "end": str(end),
                    "budget": budget, "people": people, "category": cat,
                    "type": trip_type, "weather": weather
                }
                
                raw_text = generate_ai_plan(inputs)
                clean_text, json_data = parse_ai_response(raw_text)
                
                save_plan(st.session_state.username, city, str(start), str(end), clean_text, json.dumps(json_data))
                
                st.session_state.current_view = {'text': clean_text, 'json': json_data}
                status.update(label="Report Generated!", state="complete", expanded=False)

        if 'current_view' in st.session_state:
            data = st.session_state.current_view
            
            if data['json']:
                st.subheader("📊 Smart Spend Analytics")
                df = pd.DataFrame(list(data['json'].items()), columns=['Category', 'Amount'])
                fig = px.pie(df, values='Amount', names='Category', hole=0.4, 
                             color_discrete_sequence=px.colors.sequential.RdBu)
                st.plotly_chart(fig, use_container_width=True)

            col_main, col_side = st.columns([2, 1])
            
            with col_main:
                st.markdown(data['text'])
            
            with col_side:
                pack_items = extract_packing_list(data['text'])
                if pack_items:
                    with st.expander("🎒 Interactive Packing List", expanded=True):
                        st.caption("Check items as you pack:")
                        for item in pack_items:
                            st.checkbox(item, key=item[:10]) 
                else:
                    st.info("Packing list details in main text.")

                st.info(f"🌥️ **Weather Context**\n{get_weather(city, start)}")
                
            c1, c2 = st.columns(2)
            with c1:
                pdf_bytes = create_pdf(data['text'])
                st.download_button("📥 Export PDF", pdf_bytes, "plan.pdf", "application/pdf", use_container_width=True)
            with c2:
                if st.button("🗑️ Clear Screen", use_container_width=True):
                    del st.session_state.current_view
                    st.rerun()

    with tab_hist:
        history = get_history(st.session_state.username)
        if not history: st.info("No saved trips yet.")
        
        for pid, dest, sdate, det, raw_j in history:
            with st.expander(f"📍 {dest} | {sdate}"):
                try:
                    j_data = json.loads(raw_j)
                    if j_data:
                        df_h = pd.DataFrame(list(j_data.items()), columns=['Cat', 'Amt'])
                        st.bar_chart(df_h.set_index('Cat'))
                except:
                    pass
                
                st.markdown(det)
                
                hc1, hc2 = st.columns(2)
                with hc1:
                    pdf_h = create_pdf(det)
                    st.download_button("📥 PDF", pdf_h, f"{dest}.pdf", "application/pdf", key=f"p_{pid}")
                with hc2:
                    if st.button("Delete", key=f"d_{pid}"):
                        delete_plan(pid)
                        st.rerun()