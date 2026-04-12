"""
integrated_pipeline.py

Coordinates the PDF processing workflow:
- Extracts metadata and text
- Cleans and assesses text quality
- Stores results into a SQLite database
"""

import os
import random
import sys

# Allow importing sibling modules (txt_processing, store_results) from the project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from store_results import init_db, store_result  # noqa: E402
from txt_processing import (  # noqa: E402
    assess_cleanliness,
    clean_text,
    extract_metadata,
    extract_text,
)

# Directory containing PDFs — resolved relative to this file, not cwd
PDF_DIRECTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdfs")
SAMPLE_SIZE = 20


def process_pdfs(directory: str, sample_size: int = 10):
    """
    Randomly selects PDFs from directory, extracts and scores text, stores results in DB.

    Args:
        directory: Folder path where PDFs are stored.
        sample_size: Number of random PDFs to process.
    """
    all_pdfs = [f for f in os.listdir(directory) if f.endswith(".pdf")]
    selected_pdfs = random.sample(all_pdfs, min(sample_size, len(all_pdfs)))

    print(f"Scanning directory: {directory}")
    print(f"Processing {len(selected_pdfs)} files...")

    for pdf_file in selected_pdfs:
        pdf_path = os.path.join(directory, pdf_file)
        print(f"Processing {pdf_path}")

        metadata = extract_metadata(pdf_path)
        text = extract_text(pdf_path)

        if not text:
            print(f"  Skipped {pdf_file} — no extractable text.")
            continue

        cleaned = clean_text(text)
        score = assess_cleanliness(cleaned)
        title = metadata.get("/Title", "No title in metadata")
        store_result(pdf_file, title, score)

        print(f"  {pdf_file} -> Score: {score:.2f}% stored.")


if __name__ == "__main__":
    init_db()
    process_pdfs(PDF_DIRECTORY, SAMPLE_SIZE)
