# PDF Processor: Extract, Clean, and Analyze PDF Content

## 📌 Overview

This project processes a batch of PDF documents by:
- Extracting metadata (like title),
- Extracting and cleaning text,
- Scoring text quality (cleanliness),
- Saving a summary in an Excel spreadsheet.

Ideal for junior engineers and students learning:
- Text processing,
- Regular expressions,
- File I/O operations,
- Python automation with libraries.

---

## 📂 Directory Structure

```
pdf_processor/
├── PDF_Processing_Tutorial.ipynb   # Jupyter Notebook with explanations
├── PDF_Processing_Tutorial.md      # Markdown tutorial version
├── requirements.txt                # Python dependencies
├── README.md                       # This file
└── script.py                       # The main Python script
```

---

## 🚀 Quickstart

1. Clone the repository or copy the files.
2. Create a virtual environment (optional but recommended):

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Run the script:

```bash
python script.py
```

---

## 🛠️ Configuration

Update the `directory_path` in `script.py` to point to your folder of PDFs:

```python
directory_path = "/path/to/your/pdf/folder"
```

---

## 📤 Output

- An Excel file named `batch_summary.xlsx` is generated containing:
  - File name
  - PDF title (metadata)
  - Cleanliness score (percentage of readable characters)

---

## 🧪 Cleanliness Score Explained

The script measures the proportion of alphabetic characters in the extracted text. This is a simple proxy for how "clean" or readable the text is.

---

## 📚 Dependencies

- `PyPDF2` – Reading PDF files and metadata
- `pandas` – Creating and exporting DataFrames
- `openpyxl` – Required by `pandas` to export Excel files

---

## 👩‍💻 Author

Created with ❤️ for education and exploration. Packaged by Wren.
