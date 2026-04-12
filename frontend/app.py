"""Streamlit frontend for the NIST_LLM document explorer."""
import os
import sqlite3

import streamlit as st

st.set_page_config(page_title="NIST LLM Frontend", layout="wide")

st.title("🔍 NIST_LLM Explorer")
st.markdown("Welcome to the NIST_LLM frontend interface. Use the sidebar to explore functionality.")

# Sidebar navigation
st.sidebar.title("Navigation")
page = st.sidebar.radio("Go to", ["Home", "Search", "Upload", "About"])

# Resolve DB path relative to this file so it works regardless of cwd
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "nist_llm.db")

# --- DB Access ---
def get_documents():
    """Query the documents table (mock/seed data)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT title, source, published FROM documents "
            "ORDER BY published DESC LIMIT 10"
        )
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as e:
        return f"Error: {e}"

def get_pipeline_results():
    """Query the document_results table (pipeline-processed data)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT file_name, metadata_title, cleanliness_score, timestamp "
            "FROM document_results ORDER BY timestamp DESC LIMIT 10"
        )
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
    st.subheader("🔎 NIST Reference Documents")
    data = get_documents()
    if isinstance(data, str):
        st.error(data)
    elif data:
        for title, source, published in data:
            st.markdown(f"**{title}**  \n_Source: {source} — Published: {published}_")
    else:
        st.info("No documents found in the database.")

    st.divider()
    st.subheader("🗂 Pipeline-Processed Results")
    results = get_pipeline_results()
    if isinstance(results, str):
        st.error(results)
    elif results:
        for file_name, metadata_title, cleanliness_score, timestamp in results:
            st.markdown(
                f"**{metadata_title or file_name}**  \n"
                f"_File: {file_name} — Cleanliness: {cleanliness_score:.1f}%"
                f" — Processed: {timestamp}_"
            )
    else:
        st.info("No pipeline results yet. Run integrated_pipeline.py to populate.")

elif page == "Upload":
    st.subheader("📁 Upload a Document")
    uploaded_file = st.file_uploader("Choose a file")
    if uploaded_file:
        st.info(f"You uploaded: `{uploaded_file.name}`")
        # Add processing logic here

elif page == "About":
    st.subheader("ℹ️ About")
    st.write("This is the MVP frontend for the NIST_LLM project. Built with ❤️ in Python and Streamlit.")
