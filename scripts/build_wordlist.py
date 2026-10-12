"""Download the Crossword Nexus Collaborative Word List (MIT) and write the filtered copy we bundle.

Run once: python scripts/build_wordlist.py
"""
import re
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/Crossword-Nexus/collaborative-word-list/main/"
OUT = Path(__file__).resolve().parents[1] / "src/morning_edition/puzzles/data"
MIN_SCORE = 50


def fetch(name: str) -> str:
    with urllib.request.urlopen(BASE + name, timeout=60) as resp:
        return resp.read().decode("utf-8", "ignore")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    words = set()
    for line in fetch("xwordlist.dict").splitlines():
        word, _, score = line.strip().partition(";")
        if score.isdigit() and int(score) >= MIN_SCORE and re.fullmatch(r"[A-Z]{3,11}", word):
            words.add(word)
    (OUT / "wordlist.txt").write_text("\n".join(sorted(words)) + "\n")
    (OUT / "LICENSE-wordlist.txt").write_text(
        "Crossword Nexus Collaborative Word List\n"
        "https://github.com/Crossword-Nexus/collaborative-word-list\n"
        f"Filtered to A-Z entries of 3 to 11 letters with score >= {MIN_SCORE}.\n\n"
        + fetch("LICENSE")
    )
    print(f"{len(words)} words")


if __name__ == "__main__":
    main()
