"""Детерминированная постобработка русского текста, пришедшего от ASR.

Пайплайн (порядок важен):

1. нормализация символов (NBSP, CRLF, невидимые символы, кавычки);
2. удаление мусора ASR (повторы слов, хвосты «...», висящая пунктуация);
3. произнесённые знаки (``spoken_punctuation``);
4. слова-паразиты (``remove_fillers``);
5. словарь замен (``cfg.dictionary``);
6. заглавная в начале текста и после знаков конца предложения;
7. типографика: пробелы, кавычки-ёлочки, тире, ведущий пробел.

Функция обязана быть быстрой (единицы миллисекунд на 1000 символов),
не падать на пустом/битом вводе и никогда не менять смысл текста больше,
чем требуется для чистки.
"""

from __future__ import annotations

import functools
import logging
import re

from ..config import TextConfig
from .dictionary import CompiledEntry, compile_dictionary

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Произнесённые знаки
# --------------------------------------------------------------------------- #
#: Фразы — от длинных к коротким, чтобы «точка с запятой» не съела «точка».
_SPOKEN_PUNCTUATION: tuple[tuple[str, str], ...] = (
    ("точка с запятой", ";"),
    ("восклицательный знак", "!"),
    ("вопросительный знак", "?"),
    ("открыть кавычки", "\u201e"),
    ("закрыть кавычки", "\u201c"),
    ("открывающая кавычка", "\u201e"),
    ("закрывающая кавычка", "\u201c"),
    ("новая строка", "\n"),
    ("новый абзац", "\n\n"),
    ("двоеточие", ":"),
    ("запятая", ","),
    ("тире", "\u2014"),
    ("абзац", "\n\n"),
    ("точка", "."),
)

_SPOKEN_RE = re.compile(
    r"(?<!\w)(?:"
    + "|".join(re.escape(phrase) for phrase, _ in _SPOKEN_PUNCTUATION)
    + r")(?!\w)",
    re.IGNORECASE | re.UNICODE,
)
_SPOKEN_REPLACEMENTS: dict[str, str] = {}
for _phrase, _symbol in _SPOKEN_PUNCTUATION:
    # Пунктуация «прилипает» к предыдущему слову; пробел после знака добавит
    # _tidy_spaces. Кавычки — особый случай: у них пробелы разворачиваются.
    _SPOKEN_REPLACEMENTS[_phrase] = _symbol


def _spoken_replacement(match: re.Match[str]) -> str:
    symbol = _SPOKEN_REPLACEMENTS[match.group(0).lower()]
    if symbol in ("\u201e",):  # открывающая кавычка
        return " \u201e"
    if symbol in ("\u201c",):  # закрывающая кавычка
        return "\u201c "
    if symbol == "\n" or symbol == "\n\n":
        return symbol
    return symbol


# --------------------------------------------------------------------------- #
# Слова-паразиты
# --------------------------------------------------------------------------- #
#: Расширения одиночных «тянущихся» звуков: «э-э», «эээ», «ммм».
_FILLER_SOUND_EXPANSIONS = {
    "э": r"э+",
    "ээ": r"э+",
    "эээ": r"э+",
    "м": r"м+",
    "мм": r"м+",
    "ммм": r"м+",
}


def _filler_pattern(word: str) -> re.Pattern[str] | None:
    """Шаблон для одного слова-паразита: целые слова, повторы звуков, дефисы."""
    word = (word or "").strip()
    if not word:
        return None
    token = _FILLER_SOUND_EXPANSIONS.get(word.lower())
    if token is not None:
        # «эээ» и «э-э»: допускаем дефисы между повторами.
        token = token.replace("+", r"(?:-?" + token[0] + r")*")
        body = token
    else:
        parts = [re.escape(part) for part in re.split(r"[\s\-]+", word) if part]
        if not parts:
            return None
        body = r"[\s\-]+".join(parts)
    return re.compile(r"(?<!\w)" + body + r"(?!\w)", re.IGNORECASE | re.UNICODE)


@functools.lru_cache(maxsize=64)
def _compiled_fillers(words: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    patterns: list[re.Pattern[str]] = []
    for word in words:
        pattern = _filler_pattern(word)
        if pattern is not None:
            patterns.append(pattern)
    return tuple(patterns)


# --------------------------------------------------------------------------- #
# Регулярки чистки
# --------------------------------------------------------------------------- #
_RE_DUP_WORDS = re.compile(r"(?<!\w)([^\W\d_]{2,})(\s+\1)+(?!\w)", re.IGNORECASE | re.UNICODE)
_RE_DOTS = re.compile(r"[.]{2,}")
_RE_PUNCT_RUNS = re.compile(r"([,;:])\1+")
_RE_TERMINAL_RUNS = re.compile(r"([!?])\1+")
_RE_DASH_RUNS = re.compile(r"[-–—]{2,}")
_RE_TRAILING_GARBAGE = re.compile(r"[\s,;:—–\-]+$")
_RE_LEADING_GARBAGE = re.compile(r"^[\s,;:—–\-]+")
_RE_LINE_LEADING_GARBAGE = re.compile(r"(?<=\n)[ \t]*[,;:—–-]+[ \t]*")
_RE_SPACE_ELLIPSIS_TAIL = re.compile(r"(?<=\s)…\s*$")
_RE_STRAIGHT_QUOTES = re.compile(r'"([^"\n]*)"')
_RE_SPACES = re.compile(r"[ \t]+")
_RE_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([,.;:!?…])")
_RE_SPACE_AFTER_PUNCT = re.compile(r"([,;:!?])(?=[^\s\d])")
_RE_SPACE_AFTER_OPEN = re.compile(r"([«„(])\s+")
_RE_SPACE_BEFORE_CLOSE = re.compile(r"\s+([»“)])")
_RE_SPACE_BEFORE_DASH = re.compile(r"\s+[–—-]\s+")
_RE_MULTI_NEWLINES = re.compile(r"\n{3,}")
_RE_SPACES_AROUND_NL = re.compile(r" *\n *")
_RE_QUOTE_OPEN_FIX = re.compile(r"(?<=\w)„")
_RE_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_RE_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060\ufeff]")

#: Аббревиатуры/сокращения, после точки которых НЕ надо делать заглавную.
_ABBREVIATIONS = frozenset(
    {
        "т", "е", "д", "п", "г", "ул", "им", "пр", "стр", "рис", "табл", "см",
        "др", "ед", "тыс", "руб", "коп", "млн", "млрд", "проц", "гг", "в", "н",
        "т.е", "т.д", "т.п", "т.к", "п.э", "н.э", "до.н.э",
    }
)

_TERMINALS = ".!?…"


# --------------------------------------------------------------------------- #
# Этапы
# --------------------------------------------------------------------------- #
def _normalize_chars(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u202f", " ").replace("\u2009", " ")
    text = _RE_ZERO_WIDTH.sub("", text)
    text = _RE_CONTROL.sub("", text)
    # Кавычки: типографские пары и прямые "..." → ёлочки.
    text = _RE_STRAIGHT_QUOTES.sub(r"«\1»", text)
    text = text.replace("\u201c", "«").replace("\u201d", "»")
    return text


def _clean_garbage(text: str) -> str:
    if not text:
        return ""
    # ASR любит повторять слово: «привет привет как дела».
    text = _RE_DUP_WORDS.sub(r"\1", text)
    # Хвосты тишины: «...» → «…», повторы знаков склеиваем.
    text = _RE_DOTS.sub("…", text)
    text = _RE_PUNCT_RUNS.sub(r"\1", text)
    text = _RE_TERMINAL_RUNS.sub(r"\1", text)
    text = _RE_DASH_RUNS.sub("—", text)
    # Висящая пунктуация в конце («привет,» / «...» от тишины).
    text = _RE_TRAILING_GARBAGE.sub("", text)
    text = _RE_LEADING_GARBAGE.sub("", text)
    text = _RE_SPACE_ELLIPSIS_TAIL.sub("", text)
    return text


def _expand_spoken_punctuation(text: str) -> str:
    return _SPOKEN_RE.sub(_spoken_replacement, text)


def _remove_fillers(text: str, words: tuple[str, ...]) -> str:
    patterns = _compiled_fillers(words)
    if not patterns:
        return text
    for pattern in patterns:
        text = pattern.sub("", text)
    return text


@functools.lru_cache(maxsize=32)
def _compiled_dict(entries: tuple[tuple[str, str, bool], ...]) -> tuple[CompiledEntry, ...]:
    from ..config import DictionaryEntry

    return tuple(compile_dictionary(DictionaryEntry(*item) for item in entries))


def _capitalize_sentences(text: str) -> str:
    """Заглавная в начале текста и после . ! ? … (аббревиатуры не ломаем)."""
    out: list[str] = []
    capitalize = True
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if ch == "\n":
            out.append(ch)
            capitalize = True
            i += 1
            continue
        if capitalize and ch.isalpha():
            out.append(ch.upper())
            capitalize = False
            i += 1
            continue
        out.append(ch)
        if ch in _TERMINALS:
            capitalize = True
            if ch == ".":
                # Смотрим слово перед точкой: «т.е.» / «И.» — не начало предложения.
                j = i - 1
                while j >= 0 and text[j].isalpha():
                    j -= 1
                word = text[j + 1 : i].lower()
                if word and word in _ABBREVIATIONS:
                    capitalize = False
        i += 1
    return "".join(out)


def _tidy_spaces(text: str) -> str:
    if not text:
        return ""
    text = _RE_SPACES.sub(" ", text)
    text = _RE_SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _RE_SPACE_AFTER_PUNCT.sub(r"\1 ", text)
    text = _RE_SPACES_AROUND_NL.sub("\n", text)
    text = _RE_MULTI_NEWLINES.sub("\n\n", text)
    # После удаления филлеров/слов в начале могла остаться висящая запятая.
    text = _RE_LEADING_GARBAGE.sub("", text)
    text = _RE_LINE_LEADING_GARBAGE.sub("", text)
    # Типографика: «слово - слово» → «слово — слово», «--» → «—».
    text = _RE_SPACE_BEFORE_DASH.sub(" — ", text)
    # Кавычки-ёлочки: без пробела внутри, с пробелом перед открывающей.
    text = _RE_SPACE_AFTER_OPEN.sub(r"\1", text)
    text = _RE_SPACE_BEFORE_CLOSE.sub(r"\1", text)
    text = _RE_QUOTE_OPEN_FIX.sub(" „", text)
    text = text.strip(" ")
    # Убираем пробелы в конце каждой строки.
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text


# --------------------------------------------------------------------------- #
# Публичная функция
# --------------------------------------------------------------------------- #
def postprocess(text: str, cfg: TextConfig) -> str:
    """Чистит и оформляет русский текст после ASR.

    Возвращает строку; пустой/мусорный ввод → ``""``. Никогда не бросает
    исключение наружу (логирует и возвращает то, что успели обработать).
    """
    if not isinstance(text, str) or not text:
        return ""
    cfg = cfg or TextConfig()
    try:
        result = _normalize_chars(text)
        result = _clean_garbage(result)
        if cfg.punctuation and cfg.spoken_punctuation:
            result = _expand_spoken_punctuation(result)
        if cfg.remove_fillers:
            words = tuple(str(w) for w in (cfg.filler_words or ()))
            result = _remove_fillers(result, words)
        if cfg.dictionary:
            entries = tuple(
                (str(e.source), str(e.target), bool(e.whole_word))
                for e in cfg.dictionary
                if str(e.source or "").strip()
            )
            if entries:
                compiled = _compiled_dict(entries)
                for pattern, replacement in compiled:
                    result = pattern.sub(replacement, result)
        if cfg.capitalize:
            result = _capitalize_sentences(result)
        if cfg.tidy_spaces:
            result = _tidy_spaces(result)
        else:
            result = result.strip()
        # Если после чистки остались только знаки — это мусор.
        if not any(ch.isalnum() for ch in result):
            return ""
        if cfg.trailing_space and not result.endswith(("\n", " ")):
            result += " "
        return result
    except Exception:  # pragma: no cover - защита пайплайна от битого ввода
        logger.exception("Постобработка текста не удалась, возвращаю исходный текст")
        return text.strip()
