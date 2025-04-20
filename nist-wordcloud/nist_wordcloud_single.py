# ---
# 🧠 NIST WordCloud Generator
# Turn dense government PDFs into beautiful word art!
# For: test_NIST_800_30r1.pdf → test_NIST_800_30r1.png
# Updated to use pdfminer for cleaner extraction!
# ---

import os
import re
from collections import Counter
from wordcloud import WordCloud
import matplotlib.pyplot as plt
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS as SKLEARN_STOPWORDS
from pdfminer.high_level import extract_text  # ✅ Better PDF parser

# ---
# 📁 Config Section
# ---
TRANSCRIPT_PATH = './test_NIST_800_30r1.pdf'  # 👈 Your PDF input
OUTPUT_PATH = './test_NIST_800_30r1.png'      # 👈 Word cloud image output
MAX_OCCURRENCE_THRESHOLD = 100                # Words over this count can be filtered (future use)
MIN_WORD_LENGTH = 3                           # Minimum length of words to include

# ---
# 🧹 Talk-show filler word blocklist
# ---
STATIC_FILLERS = {
    'okay', 'yeah', 'uh', 'um', 'like', 'you', 'know', 'right', 'well', 'so', 'just',
    'got', 'get', 'gonna', 'wanna', 'kinda', 'sorta', 'thing', 'really', 'actually',
    'literally', 'basically', 'youknow', 'youknowwhatimean', 'alright', 'sure',
    'yep', 'nope', 'huh', 'hmmm', 'uhuh', 'uhhuh', 'ah', 'oh', 'ok', 'ohh', 'yup',
    'stuff', 'whatever', 'maybe', 'probably', 'look', 'mean', 'even', 'still', 'now',
    'thank', 'thanks', 'welcome', 'hi', 'hello', 'bye', 'today', 'tonight', 'morning',
    'i', 'm', 're', 've', 'll', 'd', 's'
}
FORCE_EXCLUDE = {'okay', 'right', 'like', 'so', 'just', 'well', 'know', 'you', 'get'}

# ---
# 📄 Convert PDF to text using pdfminer
# ---
def convert_pdf_to_text(pdf_path):
    """
    Converts a PDF to clean text using pdfminer.six.
    This handles encoding better than PyPDF2.
    """
    print(f"📖 Extracting text from PDF: {pdf_path}")
    try:
        text = extract_text(pdf_path)
    except Exception as e:
        print(f"❌ Error while extracting PDF text: {e}")
        return ""

    # Clean up the text: remove weird characters, extra whitespace
    text = re.sub(r'[^\x20-\x7E]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    # Save output to .txt for verification (optional)
    output_txt_path = os.path.splitext(pdf_path)[0] + '_output.txt'
    with open(output_txt_path, 'w', encoding='utf-8') as output_file:
        output_file.write(text)
        print(f"✅ Saved extracted text to: {output_txt_path}")

    return text

# ---
# 🧼 Clean and filter extracted text
# ---
def clean_and_filter(text, stopwords):
    """
    Filters out unwanted words, stopwords, and too-short tokens.
    """
    words = re.findall(r'\b\w+\b', text.lower())  # Lowercase and split into words
    filtered = [word for word in words if word not in stopwords and len(word) >= MIN_WORD_LENGTH]
    word_counts = Counter(filtered)
    return filtered, word_counts

# ---
# 🌈 Generate word cloud from filtered words
# ---
def generate_wordcloud(words, output_path):
    text = ' '.join(words)
    wordcloud = WordCloud(width=1600, height=800, background_color='white').generate(text)
    wordcloud.to_file(output_path)
    print(f"✅ Saved word cloud to: {output_path}")

# ---
# 🚀 Main routine
# ---
def main():
    # Create our unified stopword set
    stopwords = set(SKLEARN_STOPWORDS).union(STATIC_FILLERS).union(FORCE_EXCLUDE)

    # Extract and clean the text
    raw_text = convert_pdf_to_text(TRANSCRIPT_PATH)
    if not raw_text:
        print("🚫 No text extracted from PDF. Exiting.")
        return

    # Clean the words
    filtered_words, word_counts = clean_and_filter(raw_text, stopwords)

    # Check if anything made it through
    if not filtered_words:
        print("⚠️ No words left after filtering.")
        return

    # Show top 10 words in terminal
    print("🔍 Top words (after filtering):")
    for word, count in word_counts.most_common(10):
        print(f"   • {word} — {count}x")

    # Generate the word cloud!
    generate_wordcloud(filtered_words, OUTPUT_PATH)

# ---
# 🏁 Run the main function
# ---
if __name__ == "__main__":
    main()
