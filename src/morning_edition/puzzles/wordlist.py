from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "wordlist.txt"


@lru_cache(maxsize=1)
def load_words(path: Path = DATA) -> tuple[str, ...]:
    return tuple(w for w in path.read_text().split() if w)
