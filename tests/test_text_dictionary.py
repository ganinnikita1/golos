"""Тесты словаря замен: компиляция, применение, JSON-персистенция."""

from __future__ import annotations

import json

import pytest

from golos.config import DictionaryEntry
from golos.text.dictionary import (
    apply_dictionary,
    compile_dictionary,
    entries_from_dicts,
    load_dictionary,
    save_dictionary,
)


def test_apply_whole_word_case_insensitive() -> None:
    entries = [DictionaryEntry(source="гтс", target="ГТС")]
    assert apply_dictionary("наша гтс и гтсик", entries) == "наша ГТС и гтсик"
    assert apply_dictionary("Наша ГТС", entries) == "Наша ГТС"


def test_apply_substring_mode() -> None:
    entries = [DictionaryEntry(source="апи", target="API", whole_word=False)]
    assert apply_dictionary("апи и апишка", entries) == "API и APIшка"


def test_apply_empty_and_no_entries() -> None:
    assert apply_dictionary("", [DictionaryEntry(source="а", target="б")]) == ""
    assert apply_dictionary("текст", []) == "текст"


def test_apply_skips_empty_source() -> None:
    entries = compile_dictionary([DictionaryEntry(source="", target="x"), DictionaryEntry(source="  ", target="y")])
    assert entries == []


def test_multiword_and_latin() -> None:
    entries = [
        DictionaryEntry(source="машинное обучение", target="ML"),
        DictionaryEntry(source="api", target="API"),
    ]
    assert apply_dictionary("машинное обучение и api", entries) == "ML и API"


def test_compiled_dictionary_reusable() -> None:
    # «ё» внутри слова не меняем в whole_word-режиме — это корректно.
    compiled = compile_dictionary([DictionaryEntry(source="ё", target="е", whole_word=False)])
    assert apply_dictionary("ёжик ёж", compiled) == "ежик еж"


def test_entries_from_dicts_soft_parsing() -> None:
    entries = entries_from_dicts(
        [
            {"source": "х", "target": "y"},
            {"source": "", "target": "skip"},
            {"target": "no source"},
            "мусор",
        ]
    )
    assert len(entries) == 1
    assert entries[0].target == "y"
    assert entries[0].whole_word is True


def test_save_and_load_roundtrip(tmp_path) -> None:
    path = tmp_path / "dict.json"
    entries = [
        DictionaryEntry(source="голос", target="Голос"),
        DictionaryEntry(source="апи", target="API", whole_word=False),
    ]
    assert save_dictionary(path, entries) == path
    loaded = load_dictionary(path)
    assert [(e.source, e.target, e.whole_word) for e in loaded] == [
        ("голос", "Голос", True),
        ("апи", "API", False),
    ]
    raw = path.read_text(encoding="utf-8")
    assert "голос" in raw  # кириллица не экранируется
    assert json.loads(raw)[0]["source"] == "голос"


def test_save_supports_entries_object_format(tmp_path) -> None:
    path = tmp_path / "dict.json"
    path.write_text(json.dumps({"entries": [{"source": "а", "target": "б"}]}), encoding="utf-8")
    loaded = load_dictionary(path)
    assert len(loaded) == 1
    assert loaded[0].source == "а"


def test_load_missing_or_broken_file(tmp_path) -> None:
    assert load_dictionary(tmp_path / "missing.json") == []
    broken = tmp_path / "broken.json"
    broken.write_text("{не json", encoding="utf-8")
    assert load_dictionary(broken) == []
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"entries": "не список"}), encoding="utf-8")
    assert load_dictionary(wrong) == []


def test_save_empty_list(tmp_path) -> None:
    path = tmp_path / "empty.json"
    save_dictionary(path, iter([]))
    assert load_dictionary(path) == []
    assert json.loads(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("source", ["ё", "ж", "gpt", "API"])
def test_cyrillic_and_latin_replacements(source: str) -> None:
    entry = DictionaryEntry(source=source, target="X")
    assert apply_dictionary(f"тут {source} тут", [entry]).count("X") == 1
