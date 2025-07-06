import os
import re
import random
import PyPDF2
import pandas as pd
from store_results import init_db, store_result  # Ensure store_results.py handles DB interactions

# ✅ Function to remove an existing file before processing
def remove_existing_file(file_path):
    if os.path.exists(file_path):
        os.remove(file_path)
        print(f"Removed existing file: {file_path}")

# ✅ Function to extract text from a given PDF file
def extract_text(pdf_path):
    try:
        with open(pdf_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            if len(reader.pages) == 0:
                raise ValueError("PDF file contains no text pages.")  # Handle empty PDFs
            text = ""
            for page in reader.pages:
                text += page.extract_text() or ""  # Ensure no NoneType issues
        return text.strip()
    except (PyPDF2.errors.PdfReadError, ValueError) as e:
        print(f"Error processing {pdf_path}: {e}")
        return None

# ✅ Function to extract metadata from a PDF file
def extract_metadata(pdf_path):
    try:
        with open(pdf_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            return reader.metadata if reader.metadata else {}  # Return empty dict instead of None
    except Exception as e:
        print(f"Warning: Could not extract metadata for {pdf_path}. Error: {e}")
        return {}  # Ensure return is always a dictionary

# ✅ Function to clean extracted text
def clean_text(text):
    text = re.sub(r'[^\x20-\x7E]', ' ', text)  # Remove non-printable characters
    text = re.sub(r'\s+', ' ', text).strip()  # Replace multiple spaces with a single space
    return text

# ✅ Function to calculate text cleanliness score
def assess_cleanliness(text):
    alpha_count = len(re.findall(r'[a-zA-Z]', text))  # Count alphabetic characters
    text_length = len(text)
    return round((alpha_count / text_length) * 100, 2) if text_length > 0 else 0

# ✅ Main function to process a batch of random PDFs and store results
def process_random_pdfs(directory, max_files=500):
    connection = connect_db()
    if connection is None:
        return  # Stop if DB connection fails

    pdf_files = [f for f in os.listdir(directory) if f.endswith('.pdf')]
    selected_files = random.sample(pdf_files, min(max_files, len(pdf_files)))  # Process up to `max_files` PDFs
    results = []

    for pdf in selected_files:
        pdf_path = os.path.join(directory, pdf)
        print(f"Processing: {pdf_path}")

        metadata = extract_metadata(pdf_path)  # Extract metadata safely
        extracted_text = extract_text(pdf_path)  # Extract text from PDF

        if extracted_text:
            cleaned_text = clean_text(extracted_text)  # Clean extracted text
            cleanliness_score = assess_cleanliness(cleaned_text)  # Assess cleanliness

            # ✅ Ensure metadata is always a dictionary to prevent `.get()` errors
            title = metadata.get('/Title', 'No title in metadata')

            # ✅ Insert document into the database
            insert_document(title, cleaned_text, connection)

            results.append({
                "File": pdf,
                "Metadata Title": title,
                "Cleanliness Score": cleanliness_score
            })
        else:
            print(f"Skipping {pdf} due to extraction failure.")
            results.append({
                "File": pdf,
                "Metadata Title": "N/A due to file error",
                "Cleanliness Score": "N/A due to file error"
            })

    connection.close()  # ✅ Ensure DB connection is closed after processing

