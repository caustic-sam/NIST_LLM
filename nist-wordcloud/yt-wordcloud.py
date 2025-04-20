import os
import re
from collections import Counter, defaultdict
from wordcloud import WordCloud
import matplotlib.pyplot as plt
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS as SKLEARN_STOPWORDS

# 🔧 Paths
TRANSCRIPTS_DIR = '../output/transcripts'
OUTPUT_DIR = '../output/wordclouds'

# 🔁 Auto-stopword tuning
FREQUENCY_THRESHOLD = 0.8
MIN_WORD_LENGTH = 3

# 🧹 Static stopwords for talk-show speech
STATIC_FILLERS = {
    'okay', 'yeah', 'uh', 'um', 'like', 'you', 'know', 'right', 'well', 'so', 'just',
    'got', 'get', 'gonna', 'wanna', 'kinda', 'sorta', 'thing', 'really', 'actually',
    'literally', 'basically', 'youknow', 'youknowwhatimean', 'alright', 'sure',
    'yep', 'nope', 'huh', 'hmmm', 'uhuh', 'uhhuh', 'ah', 'oh', 'ok', 'ohh', 'yup',
    'stuff', 'whatever', 'maybe', 'probably', 'look', 'mean', 'even', 'still', 'now',
    'thank', 'thanks', 'welcome', 'hi', 'hello', 'bye', 'today', 'tonight', 'morning',
    'i', 'm', 're', 've', 'll', 'd', 's'
}

# 🔒 Final hard-block list (overrides everything)
FORCE_EXCLUDE = {
    'okay', 'right', 'like', 'so', 'just', 'well', 'know', 'you', 'get'
}

def get_all_transcripts():
    all_texts = {}
    for filename in os.listdir(TRANSCRIPTS_DIR):
        if filename.endswith('.txt'):
            with open(os.path.join(TRANSCRIPTS_DIR, filename), 'r', encoding='utf-8') as f:
                all_texts[filename] = f.read()
    return all_texts

def build_dynamic_stopwords(all_texts):
    file_count = len(all_texts)
    word_occurrences = defaultdict(int)

    for text in all_texts.values():
        words_in_file = set(re.findall(r'\b\w+\b', text.lower()))
        for word in words_in_file:
            if len(word) >= MIN_WORD_LENGTH:
                word_occurrences[word] += 1

    dynamic_stopwords = {
        word for word, count in word_occurrences.items()
        if count / file_count >= FREQUENCY_THRESHOLD
    }

    print(f"🧠 Auto-tuned stopwords: {len(dynamic_stopwords)} learned")
    return dynamic_stopwords

def clean_text(text, combined_stopwords):
    # Normalize: lowercase, remove punctuation, split on word tokens
    words = re.findall(r'\b\w+\b', text.lower())
    return ' '.join(word for word in words if word not in combined_stopwords)

def generate_wordcloud(text, output_path):
    wordcloud = WordCloud(width=1600, height=800, background_color='white').generate(text)
    wordcloud.to_file(output_path)
    print(f"✅ Saved: {output_path}")

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_texts = get_all_transcripts()
    dynamic_stopwords = build_dynamic_stopwords(all_texts)

    combined_stopwords = (
        set(SKLEARN_STOPWORDS)
        .union(STATIC_FILLERS)
        .union(dynamic_stopwords)
        .union(FORCE_EXCLUDE)
    )

    for filename, raw_text in all_texts.items():
        text = clean_text(raw_text, combined_stopwords)

        if not text.strip():
            print(f"⚠️ Skipping '{filename}' — no meaningful words after filtering.")
            continue

        print(f"🔎 Top words in '{filename}':")
        top_words = Counter(text.split()).most_common(10)
        for word, count in top_words:
            print(f"   • {word} — {count}x")

        output_path = os.path.join(OUTPUT_DIR, f"{filename}.png")
        generate_wordcloud(text, output_path)

if __name__ == "__main__":
    main()