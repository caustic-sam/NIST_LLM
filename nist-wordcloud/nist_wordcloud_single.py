"""NIST WordCloud Generator — turns a NIST SP PDF into a word cloud image."""

import os
import re
import sys
from collections import Counter

from pdfminer.high_level import extract_text
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS as SKLEARN_STOPWORDS
from wordcloud import WordCloud

# ---------------------------------------------------------------------------
# Config — override input path via CLI: python nist_wordcloud_single.py my.pdf
# ---------------------------------------------------------------------------
_default_pdf = os.path.join(os.path.dirname(__file__), "test_NIST_800_30r1.pdf")
TRANSCRIPT_PATH = sys.argv[1] if len(sys.argv) > 1 else _default_pdf
OUTPUT_PATH = os.path.splitext(TRANSCRIPT_PATH)[0] + ".png"
MIN_WORD_LENGTH = 3

# ---------------------------------------------------------------------------
# Stopword lists
# ---------------------------------------------------------------------------
STATIC_FILLERS = {
    "okay", "yeah", "uh", "um", "like", "you", "know", "right", "well", "so", "just",
    "got", "get", "gonna", "wanna", "kinda", "sorta", "thing", "really", "actually",
    "literally", "basically", "youknow", "youknowwhatimean", "alright", "sure",
    "yep", "nope", "huh", "hmmm", "uhuh", "uhhuh", "ah", "oh", "ok", "ohh", "yup",
    "stuff", "whatever", "maybe", "probably", "look", "mean", "even", "still", "now",
    "thank", "thanks", "welcome", "hi", "hello", "bye", "today", "tonight", "morning",
    "i", "m", "re", "ve", "ll", "d", "s",
}
FORCE_EXCLUDE = {"okay", "right", "like", "so", "just", "well", "know", "you", "get"}


def convert_pdf_to_text(pdf_path):
    """Extract and clean text from a PDF using pdfminer.six."""
    print(f"Extracting text from: {pdf_path}")
    try:
        text = extract_text(pdf_path)
    except (OSError, ValueError) as e:
        print(f"Error extracting PDF text: {e}")
        return ""

    text = re.sub(r"[^\x20-\x7E]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    output_txt_path = os.path.splitext(pdf_path)[0] + "_output.txt"
    with open(output_txt_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"Saved extracted text to: {output_txt_path}")

    return text


def clean_and_filter(text, stopwords):
    """Tokenise text and remove stopwords and short tokens."""
    words = re.findall(r"\b\w+\b", text.lower())
    filtered = [w for w in words if w not in stopwords and len(w) >= MIN_WORD_LENGTH]
    return filtered, Counter(filtered)


def generate_wordcloud(words, output_path):
    """Render a word cloud PNG from a list of words."""
    wordcloud = WordCloud(width=1600, height=800, background_color="white").generate(
        " ".join(words)
    )
    wordcloud.to_file(output_path)
    print(f"Saved word cloud to: {output_path}")


def main():
    """Entry point: extract, filter, and visualise words from TRANSCRIPT_PATH."""
    stopwords = set(SKLEARN_STOPWORDS) | STATIC_FILLERS | FORCE_EXCLUDE

    raw_text = convert_pdf_to_text(TRANSCRIPT_PATH)
    if not raw_text:
        print("No text extracted from PDF. Exiting.")
        return

    filtered_words, word_counts = clean_and_filter(raw_text, stopwords)
    if not filtered_words:
        print("No words left after filtering.")
        return

    print("Top words (after filtering):")
    for word, count in word_counts.most_common(10):
        print(f"  {word} — {count}x")

    generate_wordcloud(filtered_words, OUTPUT_PATH)


if __name__ == "__main__":
    main()
