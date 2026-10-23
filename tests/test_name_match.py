from pathlib import Path

import pytest
import yaml

from core import compare_dob, compare_names
from core.name_match import devanagari_to_latin, normalise

PAIRS = yaml.safe_load((Path(__file__).parent / "name_pairs.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("a,b,label", PAIRS, ids=[f"{a}|{b}" for a, b, _ in PAIRS])
def test_labelled_pairs(a, b, label):
    assert compare_names(a, b).verdict == label


def test_differing_tokens_reported():
    r = compare_names("Sunil Patil", "Suneel Patil")
    assert ("sunil", "suneel") in r.differing_tokens


def test_symmetry():
    for a, b, _ in PAIRS:
        assert compare_names(a, b).verdict == compare_names(b, a).verdict, (a, b)


def test_normalise_strips_honorifics_and_relations():
    assert normalise("Shri. Sunil Patil S/O Ramesh") == ["sunil", "patil"]


def test_devanagari_transliteration():
    assert devanagari_to_latin("सुनील") == "suneel"


def test_empty_name_is_mismatch():
    assert compare_names("", "Sunil").verdict == "mismatch"


@pytest.mark.parametrize("a,b,verdict", [
    ("12/03/1968", "12-03-1968", "match"),
    ("12/03/1968", "1968-03-12", "match"),
    ("1968", "12/03/1968", "probable_mismatch"),
    ("12/03/1968", "03/12/1968", "probable_mismatch"),
    ("12/03/1968", "12/03/1970", "mismatch"),
    ("unknown", "12/03/1968", "unknown"),
])
def test_dob(a, b, verdict):
    assert compare_dob(a, b)[0] == verdict
