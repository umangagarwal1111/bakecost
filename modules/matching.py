"""
Match messy bill text ("AMUL BUTTR 500G PKT") to ingredients in the library.

Why not just compare strings?
-----------------------------
Shop bills abbreviate, add brand names and pack sizes, and OCR adds typos.
"Amul Butter 500g" and "Butter" are the same ingredient for a baker, but a
plain string comparison says they are only ~55 % similar. So we score each
candidate with three ideas and keep the best:

1. Normalisation - lowercase, drop digits, units and filler words
   ("pkt", "pack", "loose") so only the meaningful words remain.
2. Containment - if every word of a library name/alias appears in the bill
   line (allowing small typos per word), it is very likely the same thing.
   "amul butter" contains "butter"  ->  high score.
3. Fuzzy similarity - otherwise, blend a character-level similarity
   (difflib.SequenceMatcher, robust to OCR typos) with a word-overlap score
   (robust to words being in a different order).

Every ingredient's name and each of its aliases are tried and the best
score wins. The result is a confidence between 0 and 1.

Complexity: for B bill lines, I ingredients with on average A names/aliases
and strings of length L, matching is O(B * I * A * L^2) in the worst case
(SequenceMatcher is quadratic). With ~25-200 ingredients and short names
that's a few milliseconds - far below what a person notices.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from modules.units import UNIT_ALIASES

# Suggestions at or above this confidence are pre-selected in the review
# table. Nothing is ever saved without the user pressing "Save", and rows
# below the threshold start as "skip" so a weak guess can't slip through.
AUTO_SELECT_THRESHOLD = 0.75
# Below this we don't even suggest anything - it would only be noise.
MIN_SUGGESTION_SCORE = 0.45
# Two words count as "the same word" if they are at least this similar
# (lets "buttr" match "butter" and "maidaa" match "maida").
WORD_SIMILARITY = 0.8

# Words that appear on bills but say nothing about *what* the item is.
FILLER_WORDS = {
    "pkt", "pack", "packet", "pouch", "box", "bag", "jar", "tin", "bottle",
    "loose", "fresh", "premium", "best", "quality", "special", "new", "pure",
    "x", "of", "the", "and", "with", "rs", "inr", "mrp", "net", "wt", "qty",
}
_UNIT_WORDS = set(UNIT_ALIASES)


@dataclass
class Candidate:
    """Something we can match against: an ingredient and all its known names."""

    id: int
    name: str
    aliases: list[str]


@dataclass
class Match:
    ingredient_id: int
    ingredient_name: str
    score: float           # 0..1 confidence
    matched_on: str        # which name/alias produced the score (shown in UI)


def normalize(text: str) -> list[str]:
    """Reduce a bill or ingredient name to its meaningful words.

    "AMUL Butter 500g PKT." -> ["amul", "butter"]
    Units glued to numbers ("500g") disappear with the digits.
    """
    # Anything that isn't a letter (digits, punctuation) becomes a space, so
    # "500g" -> "g", which the unit filter below then drops.
    text = re.sub(r"[^a-z\s]", " ", text.lower())
    words = text.split()
    return [w for w in words if w not in FILLER_WORDS and w not in _UNIT_WORDS and len(w) > 1]


def _word_similarity(a: str, b: str) -> float:
    return 1.0 if a == b else SequenceMatcher(None, a, b).ratio()


def _words_found(needles: list[str], haystack: list[str]) -> int:
    """How many words of `needles` have a close match somewhere in `haystack`."""
    return sum(1 for n in needles if any(_word_similarity(n, h) >= WORD_SIMILARITY for h in haystack))


def similarity(bill_text: str, name: str) -> float:
    """Confidence (0..1) that `bill_text` refers to the ingredient called `name`."""
    bill_words, name_words = normalize(bill_text), normalize(name)
    if not bill_words or not name_words:
        return 0.0

    bill_joined, name_joined = " ".join(bill_words), " ".join(name_words)
    if bill_joined == name_joined:
        return 1.0

    # Containment: every word of the library name is on the bill line.
    # Extra bill words (brand names) only lower the score slightly.
    found = _words_found(name_words, bill_words)
    if found == len(name_words):
        coverage = len(name_words) / len(bill_words)
        return min(0.99, 0.85 + 0.14 * coverage)

    # Fuzzy fallback: characters (typos) + word overlap (word order).
    char_score = SequenceMatcher(None, bill_joined, name_joined).ratio()
    word_score = found / max(len(name_words), len(bill_words))
    return 0.5 * char_score + 0.5 * word_score


def rank_matches(bill_text: str, candidates: list[Candidate], top_n: int = 3) -> list[Match]:
    """Best `top_n` ingredients for one bill line, highest confidence first."""
    results: list[Match] = []
    for cand in candidates:
        best_score, best_name = 0.0, cand.name
        for option in [cand.name, *cand.aliases]:
            score = similarity(bill_text, option)
            if score > best_score:
                best_score, best_name = score, option
        if best_score >= MIN_SUGGESTION_SCORE:
            results.append(Match(cand.id, cand.name, round(best_score, 3), best_name))
    results.sort(key=lambda m: m.score, reverse=True)
    return results[:top_n]


def best_match(bill_text: str, candidates: list[Candidate]) -> Match | None:
    """The single best suggestion, or None when nothing is similar enough."""
    ranked = rank_matches(bill_text, candidates, top_n=1)
    return ranked[0] if ranked else None


def should_preselect(match: Match | None) -> bool:
    """Whether the review table should tick this row for saving by default."""
    return match is not None and match.score >= AUTO_SELECT_THRESHOLD
