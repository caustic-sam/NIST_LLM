"""
integrated_pipeline.py

This script coordinates the PDF processing workflow:
- Extracts metadata and text
- Cleans and assesses text quality
- Stores results into a SQLite database

✅ Updated to assume files live in scripts/ and are run from the project root
"""

import os
import sys

# Ensure this script can access sibling modules from the scripts/ folder
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.txt_processing import extract_text, extract_metadata, clean_text, assess_cleanliness
from scripts.store_results import init_db, store_result

import random

# Directory containing your PDFs
PDF_DIRECTORY = "../pdfs"
SAMPLE_SIZE = 20  # You can change this to process more/less files


def process_pdfs(directory: str, sample_size: int = 10):
    """
    Main processor: randomly selects PDFs, extracts and scores text, and stores results in DB.

    Args:
        directory (str): Folder path where PDFs are stored.
        sample_size (int): Number of random PDFs to process.
    """
    all_pdfs = [f for f in os.listdir(directory) if f.endswith('.pdf')]
    selected_pdfs = random.sample(all_pdfs, min(sample_size, len(all_pdfs)))

    print(f"📂 Scanning directory: {directory}")
    print(f"🎯 Processing {len(selected_pdfs)} files...")

    for pdf_file in selected_pdfs:
        pdf_path = os.path.join(directory, pdf_file)
        print(f"🔍 Processing {pdf_path}")

        metadata = extract_metadata(pdf_path)
        text = extract_text(pdf_path)

        if not text:
            print("⚠️ Skipped — no extractable text.")
            continue

        cleaned = clean_text(text)
        score = assess_cleanliness(cleaned)

        title = metadata.get('/Title', 'No title in metadata')
        store_result(pdf_file, title, score)

        print(f"✅ {pdf_file} → Score: {score:.2f}% stored.")


if __name__ == "__main__":
    # Initialize the database (will create if it doesn't exist)
    init_db()

    # Process PDF files
    process_pdfs(PDF_DIRECTORY, SAMPLE_SIZE)
