import os
import re
from collections import Counter, defaultdict
from wordcloud import WordCloud
import matplotlib.pyplot as plt
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS as SKLEARN_STOPWORDS


# Paths inside the container
TRANSCRIPTS_DIR = 'output/transcriptions'
BASE_OUTPUT_DIR = 'output/wordclouds'
FILTERED_TEXT_BASE_DIR = 'output/filtered_transcripts'

# 🔁 Frequency thresholds to test
FREQ_THRESHOLDS = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]

# 🧹 Talk-show static stopwords
STATIC_FILLERS = {
    'okay', 'reckon', 'manage', 'yeah', 'uh', 'um', 'like', 'you', 'now', 'know', 'right', 'well', 'so', 'just',
    'got', 'get', 'gonna', 'wanna', 'kinda', 'sorta', 'thing', 'really', 'actually',
    'literally', 'basically', 'youknow', 'youknowwhatimean', 'alright', 'sure',
    'yep', 'nope', 'huh', 'hmmm', 'uhuh', 'uhhuh', 'ah', 'oh', 'ok', 'ohh', 'yup',
    'stuff', 'whatever', 'maybe', 'probably', 'look', 'mean', 'even', 'still', 'now',
    'thank', 'thanks', 'welcome', 'hi', 'hello', 'bye', 'today', 'tonight', 'morning',
    'i', 'm', 're', 've', 'll', 'd', 's', 't', 'o'
}

# 🔒 Hard filter (never include these)
FORCE_EXCLUDE = {'okay', 'right', 'like', 'so', 'just', 'well', 'know', 'you', 'get', 'dem', 'gotta', 'gonna', 'wanna', 'kinda', 'sorta'}

def get_all_transcripts():
    all_texts = {}
    for filename in os.listdir(TRANSCRIPTS_DIR):
        if filename.endswith('.txt'):
            with open(os.path.join(TRANSCRIPTS_DIR, filename), 'r', encoding='utf-8') as f:
                all_texts[filename] = f.read()
    return all_texts

def build_dynamic_stopwords(all_texts, threshold, min_word_length=3):
    file_count = len(all_texts)
    word_occurrences = defaultdict(int)

    for text in all_texts.values():
        words_in_file = set(re.findall(r'\b\w+\b', text.lower()))
        for word in words_in_file:
            if len(word) >= min_word_length:
                word_occurrences[word] += 1

    dynamic_stopwords = {
        word for word, count in word_occurrences.items()
        if count / file_count >= threshold
    }

    print(f"🧠 FREQ {int(threshold*100)}: {len(dynamic_stopwords)} dynamic stopwords learned")
    return dynamic_stopwords

def clean_text(text, combined_stopwords):
    words = re.findall(r'\b\w+\b', text.lower())
    return ' '.join(word for word in words if word not in combined_stopwords)

def generate_wordcloud(text, output_path):
    wordcloud = WordCloud(width=1600, height=800, background_color='white').generate(text)
    wordcloud.to_file(output_path)
    print(f"✅ Saved cloud: {output_path}")

def process_threshold(threshold, all_texts):
    dynamic_stopwords = build_dynamic_stopwords(all_texts, threshold)
    combined_stopwords = (
        set(SKLEARN_STOPWORDS)
        .union(STATIC_FILLERS)
        .union(dynamic_stopwords)
        .union(FORCE_EXCLUDE)
    )

    # Create output folders
    output_dir = os.path.join(BASE_OUTPUT_DIR, f'freq{int(threshold*100)}')
    os.makedirs(output_dir, exist_ok=True)

    filtered_text_dir = os.path.join(FILTERED_TEXT_BASE_DIR, f'freq{int(threshold*100)}')
    os.makedirs(filtered_text_dir, exist_ok=True)

    for filename, raw_text in all_texts.items():
        text = clean_text(raw_text, combined_stopwords)

        if not text.strip():
            print(f"⚠️ FREQ {int(threshold*100)} — Skipped '{filename}' (empty after filtering)")
            continue

        print(f"🔍 FREQ {int(threshold*100)} — Top words in '{filename}':")
        top_words = Counter(text.split()).most_common(10)
        for word, count in top_words:
            print(f"   • {word} — {count}x")

        # Save filtered transcript
        cleaned_file_path = os.path.join(filtered_text_dir, f"{filename}.cleaned.txt")
        with open(cleaned_file_path, 'w', encoding='utf-8') as f:
            f.write(text)

        # Generate word cloud
        output_path = os.path.join(output_dir, f"{filename}.png")
        generate_wordcloud(text, output_path)

def main():
    all_texts = get_all_transcripts()
    for threshold in FREQ_THRESHOLDS:
        print(f"\n=== 🔄 GENERATING WORDCLOUDS + FILTERED TEXT @ FREQ {int(threshold*100)}% ===")
        process_threshold(threshold, all_texts)

if __name__ == "__main__":
    main()