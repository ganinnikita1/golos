"""Тесты постобработки русского текста."""

from __future__ import annotations

import time

import pytest

from golos.config import DictionaryEntry, TextConfig
from golos.text.postprocess import postprocess


def make_cfg(**kwargs) -> TextConfig:
    """Дефолтный конфиг с переопределениями (None = не трогать)."""
    cfg = TextConfig()
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


# --------------------------------------------------------------------------- #
# Пустой ввод и пробелы
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["", "   ", "\n\n", "...", ", , .", "…"])
def test_empty_and_garbage_input_returns_empty(value: str) -> None:
    assert postprocess(value, make_cfg()) == ""


def test_none_input_is_safe() -> None:
    assert postprocess(None, make_cfg()) == ""  # type: ignore[arg-type]


def test_trailing_space_default() -> None:
    assert postprocess("привет", make_cfg()) == "Привет "
    assert postprocess("привет ", make_cfg()) == "Привет "


def test_no_trailing_space_when_disabled() -> None:
    assert postprocess("привет", make_cfg(trailing_space=False)) == "Привет"


def test_text_ending_with_newline_gets_no_extra_space() -> None:
    result = postprocess("привет\n", make_cfg())
    assert result == "Привет "


# --------------------------------------------------------------------------- #
# Регистр
# --------------------------------------------------------------------------- #
def test_capitalizes_start_and_sentences() -> None:
    assert postprocess("привет. как дела? хорошо!", make_cfg()) == "Привет. Как дела? Хорошо! "


def test_capitalizes_after_ellipsis_and_newline() -> None:
    assert postprocess("привет… как дела", make_cfg()) == "Привет… Как дела "
    assert postprocess("первая строка\nвторая строка", make_cfg()) == "Первая строка\nВторая строка "


def test_does_not_touch_uppercase_abbreviations() -> None:
    assert postprocess("рф и ит, gpt и api", make_cfg()) == "Рф и ит, gpt и api "
    assert postprocess("США и РФ работают", make_cfg()) == "США и РФ работают "


def test_abbreviation_before_dot_does_not_break_case() -> None:
    result = postprocess("это т.е. тест. он работает", make_cfg())
    assert result == "Это т.е. тест. Он работает "


def test_capitalize_can_be_disabled() -> None:
    assert postprocess("привет. мир", make_cfg(capitalize=False)) == "привет. мир "


def test_preserves_yo_letter() -> None:
    assert postprocess("ещё ёжик в тумане", make_cfg()) == "Ещё ёжик в тумане "


# --------------------------------------------------------------------------- #
# Мусор ASR
# --------------------------------------------------------------------------- #
def test_collapses_repeated_words() -> None:
    assert postprocess("привет привет как дела", make_cfg()) == "Привет как дела "
    assert postprocess("это очень очень хорошо", make_cfg()) == "Это очень хорошо "


def test_removes_trailing_silence_tail() -> None:
    assert postprocess("привет ...", make_cfg()) == "Привет "
    assert postprocess("привет,", make_cfg()) == "Привет "
    assert postprocess("привет -- ", make_cfg()) == "Привет "


def test_normalizes_ellipsis_and_punctuation_runs() -> None:
    # По контракту после «…» начинается новое предложение (регистр поднимаем).
    assert postprocess("привет... мир", make_cfg()) == "Привет… Мир "
    assert postprocess("да!!!", make_cfg()) == "Да! "
    assert postprocess("ага,,,", make_cfg()) == "Ага "


# --------------------------------------------------------------------------- #
# Типографика
# --------------------------------------------------------------------------- #
def test_tidy_spaces_around_punctuation() -> None:
    assert postprocess("привет , как дела", make_cfg()) == "Привет, как дела "
    assert postprocess("привет,как дела", make_cfg()) == "Привет, как дела "
    assert postprocess("много   пробелов", make_cfg()) == "Много пробелов "


def test_straight_quotes_become_guillemets() -> None:
    # «ё» не добавляем: текст меняется только типографикой.
    assert postprocess('он сказал "привет" и ушел', make_cfg()) == "Он сказал «привет» и ушел "


def test_dash_normalization() -> None:
    assert postprocess("привет -- как дела", make_cfg()) == "Привет — как дела "
    assert postprocess("привет - как дела", make_cfg()) == "Привет — как дела "
    # Дефис внутри слова не ломаем.
    assert postprocess("он интернет-магазин", make_cfg()) == "Он интернет-магазин "


# --------------------------------------------------------------------------- #
# Произнесённые знаки
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("привет запятая мир точка", "Привет, мир. "),
        ("стоп точка с запятой дальше", "Стоп; дальше "),
        ("внимание двоеточие текст", "Внимание: текст "),
        ("как дела вопросительный знак", "Как дела? "),
        ("ура восклицательный знак", "Ура! "),
        ("привет тире мир", "Привет — мир "),
        ("первая строка новая строка вторая", "Первая строка\nВторая "),
        ("первый абзац второй", "Первый\n\nВторой "),
    ],
)
def test_spoken_punctuation(raw: str, expected: str) -> None:
    assert postprocess(raw, make_cfg(spoken_punctuation=True)) == expected


def test_spoken_quotes() -> None:
    result = postprocess("он сказал открыть кавычки привет закрыть кавычки точка",
                         make_cfg(spoken_punctuation=True))
    assert "„" in result and "“" in result
    assert "«" not in result
    assert result == "Он сказал „привет“. "


def test_spoken_punctuation_disabled_by_flag() -> None:
    result = postprocess("привет запятая мир", make_cfg(punctuation=False, spoken_punctuation=True))
    assert "запятая" in result
    assert "," not in result


def test_spoken_punctuation_not_expanded_when_off() -> None:
    result = postprocess("привет запятая мир", make_cfg(spoken_punctuation=False))
    assert "запятая" in result


# --------------------------------------------------------------------------- #
# Слова-паразиты
# --------------------------------------------------------------------------- #
def test_removes_fillers() -> None:
    # Список задаём явно, чтобы тест проверял логику, а не текущий дефолт конфига.
    cfg = make_cfg(
        remove_fillers=True,
        filler_words=["э", "ээ", "эээ", "эм", "мм", "м", "ну", "как бы", "типа", "в общем-то"],
    )
    assert postprocess("э-э, ну как бы привет", cfg) == "Привет "
    assert postprocess("эээ ну это типа тест", cfg) == "Это тест "


def test_fillers_keep_enabled_by_default() -> None:
    result = postprocess("ну привет", make_cfg())
    assert result == "Ну привет "


# --------------------------------------------------------------------------- #
# Словарь
# --------------------------------------------------------------------------- #
def test_dictionary_whole_word_case_insensitive() -> None:
    cfg = make_cfg(dictionary=[DictionaryEntry(source="голос", target="Голос")])
    assert postprocess("включи голос и голосование", cfg) == "Включи Голос и голосование "


def test_dictionary_substring_mode() -> None:
    cfg = make_cfg(dictionary=[DictionaryEntry(source="апи", target="API", whole_word=False)])
    assert postprocess("проверь апи и апишка", cfg) == "Проверь API и APIшка "


def test_dictionary_latin_and_multiword() -> None:
    cfg = make_cfg(
        dictionary=[
            DictionaryEntry(source="гигаам", target="GigaAM"),
            DictionaryEntry(source="машинное обучение", target="ML"),
        ]
    )
    assert postprocess("гигаам и машинное обучение", cfg) == "GigaAM и ML "


def test_dictionary_applied_before_capitalization() -> None:
    cfg = make_cfg(dictionary=[DictionaryEntry(source="привет", target="здравствуй")])
    assert postprocess("привет мир", cfg) == "Здравствуй мир "


# --------------------------------------------------------------------------- #
# Производительность и устойчивость
# --------------------------------------------------------------------------- #
def test_postprocess_is_fast() -> None:
    sample = ("Это довольно длинный текст для проверки скорости постобработки. " * 20)[:1000]
    cfg = make_cfg(spoken_punctuation=True, remove_fillers=True,
                   dictionary=[DictionaryEntry(source="текст", target="текст")])
    # Прогрев
    for _ in range(5):
        postprocess(sample, cfg)
    start = time.perf_counter()
    runs = 50
    for _ in range(runs):
        postprocess(sample, cfg)
    average_ms = (time.perf_counter() - start) / runs * 1000.0
    assert average_ms < 5.0, f"постобработка 1000 символов заняла {average_ms:.2f} мс"


def test_no_exceptions_on_pathological_input() -> None:
    cfg = make_cfg()
    weird = "«„\"“”…!!!,,, ...   \n\n\t" + "ё" * 50 + " API-вызов №5 100%"
    result = postprocess(weird, cfg)
    assert isinstance(result, str)
