"""Compare names and dates of birth across documents.

compare_names("Sunil Patil", "Suneel Patil") ->
    NameCheck(verdict="probable_mismatch", differing_tokens=[("sunil", "suneel")], ...)

Verdicts (from the user's point of view):
    match              same spelling after cleanup -> no action needed
    probable_mismatch  same person, spelling differs -> likely to block payment, fix it
    mismatch           names look like different people -> serious, check documents
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime

from rapidfuzz import fuzz

HONORIFICS = {
    "shri", "sri", "shree", "smt", "shrimati", "srimati", "mr", "mrs", "ms", "miss", "kumari",
    "km", "kum", "dr", "late", "sau", "sou", "chi", "kumar_title",
    "श्री", "श्रीमती", "कुमारी", "सौ", "स्व", "डॉ", "कु", "चि",
}
# relation markers like "S/O", "W/O", "D/O" and everything after them (father's / husband's name)
RELATION_RE = re.compile(r"\b(s/o|d/o|w/o|c/o|son of|daughter of|wife of)\b.*$", re.IGNORECASE)

# ---- Devanagari -> Latin (simple, enough for names) ---------------------------------
_VOWELS = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri",
           "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ॲ": "a", "ऑ": "o"}
_MATRAS = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri",
           "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॅ": "a", "ॉ": "o"}
_CONS = {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j",
         "झ": "jh", "ञ": "n", "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t",
         "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh",
         "म": "m", "य": "y", "र": "r", "ल": "l", "ळ": "l", "व": "v", "श": "sh", "ष": "sh",
         "स": "s", "ह": "h", "क्ष": "ksh", "ज्ञ": "dny"}
_VIRAMA, _ANUSVARA, _VISARGA, _NUKTA = "्", "ं", "ः", "़"


def devanagari_to_latin(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch == _NUKTA:
            i += 1
            continue
        if ch in _CONS:
            out.append(_CONS[ch])
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if nxt == _NUKTA:
                i += 1
                nxt = text[i + 1] if i + 1 < len(text) else ""
            if nxt in _MATRAS:
                out.append(_MATRAS[nxt])
                i += 2
                continue
            if nxt == _VIRAMA:
                i += 2
                continue
            # inherent 'a'; dropped at word end (schwa deletion: "सुनील" -> "suneel")
            if nxt and ("ऀ" <= nxt <= "ॿ") and nxt not in (" ",):
                out.append("a")
            i += 1
            continue
        if ch in _VOWELS:
            out.append(_VOWELS[ch])
        elif ch in (_ANUSVARA, "ँ"):
            out.append("n")
        elif ch == _VISARGA:
            out.append("h")
        elif ch in _MATRAS:          # stray matra
            out.append(_MATRAS[ch])
        else:
            out.append(ch)
        i += 1
    return "".join(out)


# ---- normalisation --------------------------------------------------------------------
def normalise(name: str) -> list[str]:
    """Lowercase, transliterate, strip honorifics/relations/punctuation. Returns tokens."""
    if not name:
        return []
    text = unicodedata.normalize("NFC", name.strip())
    text = RELATION_RE.sub("", text)
    # drop Devanagari honorifics before transliterating
    tokens = [t for t in re.split(r"[\s,]+", text) if t and t.strip(".") not in HONORIFICS]
    text = " ".join(tokens)
    if re.search(r"[ऀ-ॿ]", text):
        text = devanagari_to_latin(text)
    text = text.lower()
    text = re.sub(r"[^a-z\s.]", " ", text)
    # "s.patil" -> "s patil"; keep initials as single letters
    text = text.replace(".", " ")
    return [t for t in text.split() if t and t not in HONORIFICS]


def phonetic(token: str) -> str:
    """Collapse spelling variants common in Indian names (Sunil/Suneel, Vijay/Wijay ...)."""
    t = token
    for a, b in (("ph", "f"), ("w", "v"), ("z", "j"), ("q", "k"), ("ck", "k"),
                 ("ee", "i"), ("ii", "i"), ("oo", "u"), ("ou", "u"), ("aa", "a"),
                 ("y", "i"), ("sh", "s"), ("th", "t"), ("dh", "d"), ("bh", "b"),
                 ("kh", "k"), ("gh", "g"), ("chh", "ch"), ("x", "ks")):
        t = t.replace(a, b)
    t = re.sub(r"(.)\1+", r"\1", t)       # double letters: "laxmii" -> "laksmi"
    if len(t) > 3 and t.endswith("a"):    # "Ramesha" vs "Ramesh" (schwa)
        t = t[:-1]
    return t


@dataclass
class NameCheck:
    name_a: str
    name_b: str
    verdict: str
    score: float
    differing_tokens: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _pair_tokens(a: list[str], b: list[str]) -> list[tuple[str, str]]:
    """Greedy best-match pairing of tokens (handles surname-first order)."""
    pairs, used = [], set()
    for ta in a:
        best_j, best_s = None, -1.0
        for j, tb in enumerate(b):
            if j in used:
                continue
            if len(ta) == 1 or len(tb) == 1:          # initial vs full token
                s = 90.0 if ta[0] == tb[0] else 0.0
            else:
                s = max(fuzz.ratio(ta, tb), fuzz.ratio(phonetic(ta), phonetic(tb)))
            if s > best_s:
                best_j, best_s = j, s
        if best_j is not None and best_s >= 60:
            used.add(best_j)
            pairs.append((ta, b[best_j]))
        else:
            pairs.append((ta, ""))
    pairs += [("", tb) for j, tb in enumerate(b) if j not in used]
    return pairs


def compare_names(name_a: str, name_b: str, mismatch_below: float = 75.0) -> NameCheck:
    ta, tb = normalise(name_a), normalise(name_b)
    if not ta or not tb:
        return NameCheck(name_a, name_b, "mismatch", 0.0, notes=["one name is empty"])

    if ta == tb:
        return NameCheck(name_a, name_b, "match", 100.0)

    notes: list[str] = []
    # Devanagari vs Latin of the same name (Aadhaar prints both) is a match, not a mismatch
    is_dev = lambda s: bool(re.search(r"[ऀ-ॿ]", s or ""))  # noqa: E731
    if is_dev(name_a) != is_dev(name_b) and sorted(map(phonetic, ta)) == sorted(map(phonetic, tb)):
        return NameCheck(name_a, name_b, "match", 100.0, notes=["same name in different scripts"])

    if sorted(ta) == sorted(tb):
        notes.append("same words in a different order")
        return NameCheck(name_a, name_b, "match", 100.0, notes=notes)

    pairs = _pair_tokens(ta, tb)
    differing = [(x, y) for x, y in pairs if x != y]

    pa, pb = " ".join(sorted(map(phonetic, ta))), " ".join(sorted(map(phonetic, tb)))
    score = max(fuzz.token_sort_ratio(" ".join(ta), " ".join(tb)), fuzz.token_sort_ratio(pa, pb))

    for x, y in differing:
        if not x or not y:
            notes.append(f"extra word '{x or y}' on one document")
        elif len(x) == 1 or len(y) == 1:
            notes.append(f"initial '{x if len(x) == 1 else y}' vs full name '{y if len(x) == 1 else x}'")
        elif phonetic(x) == phonetic(y):
            notes.append(f"spelling variant '{x}' vs '{y}'")
        else:
            notes.append(f"different word '{x}' vs '{y}'")

    unpaired = [p for p in differing if not p[0] or not p[1]]
    # one missing word (e.g. middle name) with everything else equal is a probable mismatch
    core_same = all(phonetic(x) == phonetic(y) or len(x) == 1 or len(y) == 1
                    for x, y in differing if x and y)
    if core_same and len(unpaired) <= 1:
        verdict = "probable_mismatch"
    elif score >= mismatch_below:
        verdict = "probable_mismatch"
    else:
        verdict = "mismatch"
    return NameCheck(name_a, name_b, verdict, float(score), differing, notes)


# ---- dates of birth -------------------------------------------------------------------
_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y")


def parse_dob(value: str) -> date | int | None:
    """Return a date, or an int year if only the year is printed (common on Aadhaar)."""
    if value is None:
        return None
    v = str(value).strip()
    if re.fullmatch(r"\d{4}", v):
        return int(v)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def compare_dob(dob_a: str, dob_b: str) -> tuple[str, str]:
    """Return (verdict, note). verdict: match / probable_mismatch / mismatch / unknown."""
    a, b = parse_dob(dob_a), parse_dob(dob_b)
    if a is None or b is None:
        return "unknown", "could not read one of the dates"
    if isinstance(a, int) or isinstance(b, int):
        ya = a if isinstance(a, int) else a.year
        yb = b if isinstance(b, int) else b.year
        if ya == yb:
            return "probable_mismatch", "one document has only the year of birth"
        return "mismatch", f"birth year differs ({ya} vs {yb})"
    if a == b:
        return "match", ""
    if a.day == b.month and a.month == b.day and a.year == b.year:
        return "probable_mismatch", "day and month look swapped"
    return "mismatch", f"date of birth differs ({a.isoformat()} vs {b.isoformat()})"
