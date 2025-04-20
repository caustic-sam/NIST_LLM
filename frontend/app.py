import streamlit as st
import sqlite3

st.set_page_config(page_title="NIST LLM Frontend", layout="wide")

st.title("🔍 NIST_LLM Explorer")
st.markdown("Welcome to the NIST_LLM frontend interface. Use the sidebar to explore functionality.")

# Sidebar navigation
st.sidebar.title("Navigation")
page = st.sidebar.radio("Go to", ["Home", "Search", "Upload", "About"])

# --- DB Access ---
def get_documents():
    try:
        conn = sqlite3.connect("data/nist_llm.db")
        cursor = conn.cursor()
        cursor.execute("SELECT title, source, published FROM documents ORDER BY published DESC LIMIT 10")
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as e:
        return f"Error: {e}"

# --- Page Rendering ---
if page == "Home":
    st.subheader("🏠 Home")
    st.write("This is the landing page. Customize it with stats, charts, or quick links.")

elif page == "Search":
    st.subheader("🔎 Search NIST Documents")
    data = get_documents()
    if isinstance(data, str):
        st.error(data)
    elif data:
        for title, source, published in data:
            st.markdown(f"**{title}**  \n_Source: {source} — Published: {published}_")
    else:
        st.info("No documents found in the database.")

elif page == "Upload":
    st.subheader("📁 Upload a Document")
    uploaded_file = st.file_uploader("Choose a file")
    if uploaded_file:
        st.info(f"You uploaded: `{uploaded_file.name}`")
        # Add processing logic here

elif page == "About":
    st.subheader("ℹ️ About")
    st.write("This is the MVP frontend for the NIST_LLM project. Built with ❤️ in Python and Streamlit.")