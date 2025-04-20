# 🕸️ NIST PDF Scraper: SP800 Downloader

## 🧠 Purpose

This script:
1. Scrapes the [NIST Special Publications](https://csrc.nist.gov/publications/sp) site,
2. Extracts links to individual publication pages,
3. Finds the PDF download links from those pages,
4. Downloads all available PDFs to the local directory.

## 🔧 Prerequisites

Install required Python packages:

```bash
pip install -r requirements.txt
```

## 🧰 Tech Stack

- `requests` – Fetch web pages and PDFs
- `beautifulsoup4` – Parse HTML content
- `random` – Sample PDFs for testing

---

## ⚙️ Script Overview

- `ENABLE_SAMPLE_MODE = True`: Toggle to enable/disable test mode
- `SAMPLE_SIZE = 20`: Downloads only 20 random PDFs in test mode

---

## 🚀 Running the Script

```bash
python nist_scraper.py
```

---

## 🗃 Output

Downloads PDF files into the current working directory.

---

## 📁 Files Included

- `nist_scraper.py` – The main script
- `NIST_Scraper_Tutorial.ipynb` – Jupyter Notebook version
- `NIST_Scraper_Tutorial.md` – Markdown version
- `README.md` – Project overview
- `requirements.txt` – Dependencies

---

## 👩‍💻 Author

Packaged with ❤️ by Wren
