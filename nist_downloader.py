"""
NIST PDF Scraper: Crawl, Extract Links, and Download PDFs from the NIST SP800 Publications

This script:
1. Scrapes the NIST Special Publications (SP800) webpage.
2. Finds links to individual publication pages.
3. Extracts direct links to PDF files.
4. Downloads each PDF to a local directory.

Ideal for junior engineers exploring:
- Web scraping with requests and BeautifulSoup
- Link extraction
- HTTP file downloads
"""

import os
import random

import requests
from bs4 import BeautifulSoup

# NIST Publications URL
NIST_URL = "https://csrc.nist.gov/publications/sp"

# Directory where downloaded PDFs will be saved
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "pdfs")

# Optional toggle for limiting sample size during testing
ENABLE_SAMPLE_MODE = True
SAMPLE_SIZE = 5


def fetch_publication_links(url):
    """Fetch all publication page links from the NIST SP index."""
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    print(f"Fetched index: {url} — status {response.status_code}")
    soup = BeautifulSoup(response.text, "html.parser")
    return [
        a["href"] for a in soup.find_all("a", href=True)
        if "pub-title-link" in a.get("id", "") or "mobile-pub-title" in a.get("class", [])
    ]


def fetch_pdf_links(publication_links):
    """Visit each publication page and collect PDF download links."""
    pdf_links = []
    for link in publication_links:
        pub_url = link if link.startswith("http") else f"https://csrc.nist.gov{link}"
        print(f"Fetching publication page: {pub_url}")
        try:
            pub_response = requests.get(pub_url, timeout=30)
            pub_response.raise_for_status()
        except requests.RequestException as e:
            print(f"  Skipping {pub_url} — {e}")
            continue
        pub_soup = BeautifulSoup(pub_response.text, "html.parser")
        pdf_links.extend([
            a["href"] for a in pub_soup.find_all("a", href=True)
            if a["href"].endswith(".pdf")
        ])
    return pdf_links


def download_pdfs(pdf_links, download_dir):
    """Download each PDF into download_dir, skipping failures."""
    os.makedirs(download_dir, exist_ok=True)
    for pdf in pdf_links:
        filename = pdf.split("/")[-1]
        pdf_url = pdf if pdf.startswith("http") else f"https://csrc.nist.gov{pdf}"
        dest = os.path.join(download_dir, filename)
        print(f"Downloading: {pdf_url}")
        try:
            pdf_response = requests.get(pdf_url, timeout=60)
            pdf_response.raise_for_status()
            with open(dest, "wb") as f:
                f.write(pdf_response.content)
            print(f"  Saved: {dest}")
        except requests.RequestException as e:
            print(f"  Failed to download {pdf_url} — {e}")


def main():
    """Entry point: scrape NIST publications and download PDFs."""
    publication_links = fetch_publication_links(NIST_URL)
    print(f"Found {len(publication_links)} publication links")

    if ENABLE_SAMPLE_MODE:
        publication_links = random.sample(
            publication_links, min(SAMPLE_SIZE, len(publication_links))
        )
        print(f"Sample mode: processing {len(publication_links)} links")

    pdf_links = fetch_pdf_links(publication_links)
    print(f"Found {len(pdf_links)} PDF links")

    download_pdfs(pdf_links, DOWNLOAD_DIR)
    print("Done.")


if __name__ == "__main__":
    main()
