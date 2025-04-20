# 📄 PDF Processor: Extract, Clean, and Analyze PDF Content

## 🧠 Purpose
This script processes a random batch of PDF files from a directory. It:
1. Extracts metadata and text,
2. Cleans the extracted text,
3. Assesses the text quality,
4. Saves the results in an Excel spreadsheet.

This is ideal for junior engineers learning how to:
- Work with file systems,
- Use external libraries like `PyPDF2`, `pandas`, and `re`,
- Process unstructured data,
- Automate batch data collection.

---

## ✅ Prerequisites

Install these Python packages:
```bash
pip install PyPDF2 pandas openpyxl
```

---

## 🧰 Modules Used
```python
import PyPDF2       # Handles PDF file reading and metadata extraction
import re           # Regular expressions for cleaning text
import pandas as pd # For organizing and exporting data to Excel
import os           # OS-level operations like file checks and path handling
import random       # Selects random PDFs from the directory
```

---

## 🧼 remove_existing_file()
Ensures the output Excel file doesn’t overwrite existing results unexpectedly.

---

## 📖 extract_text()
Extracts visible text from a PDF file using `PyPDF2`. Skips files with no readable pages.

---

## 🏷️ extract_metadata()
Pulls metadata (like Title, Author) from a PDF’s internal properties.

---

## 🧹 clean_text()
Cleans up extracted text by removing weird or unreadable characters and extra spaces.

---

## 🧪 assess_cleanliness()
Creates a quality score for the text: (alphabetic characters ÷ total characters) × 100.

---

## 🎲 process_random_pdfs()
Main routine:
- Randomly selects 10 PDFs
- Extracts metadata and text
- Cleans and scores the text
- Saves results to Excel

---

## 📍 Usage
```python
directory_path = "/Users/jm/NIST_SP_DOCS/"
remove_existing_file("batch_summary.xlsx")
process_random_pdfs(directory_path)
```
