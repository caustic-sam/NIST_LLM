"""
NIST PDF Scraper: Crawl, Extract Links, and Download PDFs from the NIST SP800 Publications

This script:
1. Scrapes the NIST Special Publications (SP800) webpage.
2. Finds links to individual publication pages.
3. Extracts direct links to PDF files.
4. Downloads each PDF to the local directory.

Ideal for junior engineers exploring:
- Web scraping with requests and BeautifulSoup
- Link extraction
- HTTP file downloads
"""

import requests
from bs4 import BeautifulSoup
import random  # Added for optional sampling

# NIST Publications URL
nist_url = "https://csrc.nist.gov/publications/sp"

# Optional toggle for limiting sample size
ENABLE_SAMPLE_MODE = True
SAMPLE_SIZE = 5  # Reduce this number during testing

# Step 1: Fetch the main NIST SP800 publications page
response = requests.get(nist_url)
print(f"Fetching URL: {nist_url}, Status Code: {response.status_code}")
soup = BeautifulSoup(response.text, 'html.parser')

# Step 2: Extract publication page links
publication_links = [
    a['href'] for a in soup.find_all('a', href=True)
    if 'pub-title-link' in a.get('id', '') or 'mobile-pub-title' in a.get('class', [])
]
print(f"Found {len(publication_links)} publication links")

# Sample subset of publication links if testing
if ENABLE_SAMPLE_MODE:
    publication_links = random.sample(publication_links, min(SAMPLE_SIZE, len(publication_links)))
    print(f"Sampling only {len(publication_links)} links for testing")

# Step 3: Extract PDF links from each publication page
pdf_links = []
for link in publication_links:
    publication_url = link if link.startswith('http') else f"https://csrc.nist.gov{link}"
    print(f"Fetching publication page: {publication_url}")
    pub_response = requests.get(publication_url)
    print(f"Status Code: {pub_response.status_code}")
    pub_soup = BeautifulSoup(pub_response.text, 'html.parser')

    # Find all PDF links
    pdf_links.extend([
        a['href'] for a in pub_soup.find_all('a', href=True)
        if a['href'].endswith('.pdf')
    ])

print(f"Found {len(pdf_links)} PDF links")

# Step 4: Download each PDF
for pdf in pdf_links:
    filename = pdf.split("/")[-1]
    pdf_url = pdf if pdf.startswith('http') else f"https://csrc.nist.gov{pdf}"
    print(f"Downloading PDF: {pdf_url}")
    pdf_response = requests.get(pdf_url)
    with open(filename, 'wb') as f:
        f.write(pdf_response.content)
