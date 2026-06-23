from __future__ import annotations

from copy import deepcopy
from typing import Dict, List

from speech_translate.log_helpers import logger

from ..helper import get_similar_in_list, up_first_case

try:
    from deep_translator import GoogleTranslator, MyMemoryTranslator
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency fallback
    GoogleTranslator = None  # type: ignore[assignment]
    MyMemoryTranslator = None  # type: ignore[assignment]


# This language is copied directly from whisper.tokenizer to speed up the import time on startup
LANGUAGES = {
    "en": "english",
    "zh": "chinese",
    "de": "german",
    "es": "spanish",
    "ru": "russian",
    "ko": "korean",
    "fr": "french",
    "ja": "japanese",
    "pt": "portuguese",
    "tr": "turkish",
    "pl": "polish",
    "ca": "catalan",
    "nl": "dutch",
    "ar": "arabic",
    "sv": "swedish",
    "it": "italian",
    "id": "indonesian",
    "hi": "hindi",
    "fi": "finnish",
    "vi": "vietnamese",
    "he": "hebrew",
    "uk": "ukrainian",
    "el": "greek",
    "ms": "malay",
    "cs": "czech",
    "ro": "romanian",
    "da": "danish",
    "hu": "hungarian",
    "ta": "tamil",
    "no": "norwegian",
    "th": "thai",
    "ur": "urdu",
    "hr": "croatian",
    "bg": "bulgarian",
    "lt": "lithuanian",
    "la": "latin",
    "mi": "maori",
    "ml": "malayalam",
    "cy": "welsh",
    "sk": "slovak",
    "te": "telugu",
    "fa": "persian",
    "lv": "latvian",
    "bn": "bengali",
    "sr": "serbian",
    "az": "azerbaijani",
    "sl": "slovenian",
    "kn": "kannada",
    "et": "estonian",
    "mk": "macedonian",
    "br": "breton",
    "eu": "basque",
    "is": "icelandic",
    "hy": "armenian",
    "ne": "nepali",
    "mn": "mongolian",
    "bs": "bosnian",
    "kk": "kazakh",
    "sq": "albanian",
    "sw": "swahili",
    "gl": "galician",
    "mr": "marathi",
    "pa": "punjabi",
    "si": "sinhala",
    "km": "khmer",
    "sn": "shona",
    "yo": "yoruba",
    "so": "somali",
    "af": "afrikaans",
    "oc": "occitan",
    "ka": "georgian",
    "be": "belarusian",
    "tg": "tajik",
    "sd": "sindhi",
    "gu": "gujarati",
    "am": "amharic",
    "yi": "yiddish",
    "lo": "lao",
    "uz": "uzbek",
    "fo": "faroese",
    "ht": "haitian creole",
    "ps": "pashto",
    "tk": "turkmen",
    "nn": "nynorsk",
    "mt": "maltese",
    "sa": "sanskrit",
    "lb": "luxembourgish",
    "my": "myanmar",
    "bo": "tibetan",
    "tl": "tagalog",
    "mg": "malagasy",
    "as": "assamese",
    "tt": "tatar",
    "haw": "hawaiian",
    "ln": "lingala",
    "ha": "hausa",
    "ba": "bashkir",
    "jw": "javanese",
    "su": "sundanese",
    "yue": "cantonese",
}

TO_LANGUAGE_CODE = {
    **{language: code for code, language in LANGUAGES.items()},
    "burmese": "my",
    "valencian": "ca",
    "flemish": "nl",
    "haitian": "ht",
    "letzeburgesch": "lb",
    "pushto": "ps",
    "panjabi": "pa",
    "moldavian": "ro",
    "moldovan": "ro",
    "sinhalese": "si",
    "castilian": "es",
    "mandarin": "zh",
}

WHISPER_LANG_LIST = list(TO_LANGUAGE_CODE.keys())
WHISPER_LANG_LIST.sort()
WHISPER_CODE_TO_NAME = {v: k for k, v in TO_LANGUAGE_CODE.items()}

_STATIC_GOOGLE_KEY_VAL = {
    "auto detect": "auto",
    "english": "en",
    "chinese": "zh-CN",
    "japanese": "ja",
    "korean": "ko",
    "french": "fr",
    "german": "de",
    "spanish": "es",
    "russian": "ru",
    "portuguese": "pt",
    "indonesian": "id",
    "arabic": "ar",
    "hindi": "hi",
    "italian": "it",
    "dutch": "nl",
    "turkish": "tr",
    "ukrainian": "uk",
    "vietnamese": "vi",
    "thai": "th",
    "polish": "pl",
    "filipino (tagalog)": "tl",
}

_STATIC_MYMEMORY_KEY_VAL = {
    "english": "en",
    "chinese": "zh-CN",
    "japanese": "ja",
    "korean": "ko",
    "french": "fr",
    "german": "de",
    "spanish": "es",
    "russian": "ru",
    "portuguese": "pt",
    "indonesian": "id",
    "arabic": "ar",
    "hindi": "hi",
    "italian": "it",
    "dutch": "nl",
    "turkish": "tr",
    "ukrainian": "uk",
    "vietnamese": "vi",
    "thai": "th",
    "polish": "pl",
    "filipino (tagalog)": "tl",
}

LIBRE_KEY_VAL = {
    "auto detect": "auto",
    "english": "en",
    "albanian": "sq",
    "arabic": "ar",
    "azerbaijani": "az",
    "bengali": "bn",
    "bulgarian": "bg",
    "catalan": "ca",
    "chinese": "zh",
    "chinese (traditional)": "zt",
    "czech": "cs",
    "danish": "da",
    "dutch": "nl",
    "esperanto": "eo",
    "finnish": "fi",
    "french": "fr",
    "german": "de",
    "greek": "el",
    "hebrew": "he",
    "hindi": "hi",
    "hungarian": "hu",
    "indonesian": "id",
    "irish": "ga",
    "italian": "it",
    "japanese": "ja",
    "korean": "ko",
    "latvian": "lv",
    "lithuanian": "lt",
    "malay": "ms",
    "norwegian": "nb",
    "persian": "fa",
    "polish": "pl",
    "portuguese": "pt",
    "romanian": "ro",
    "russian": "ru",
    "serbian": "sr",
    "slovak": "sk",
    "slovenian": "sl",
    "spanish": "es",
    "swedish": "sv",
    "tagalog": "tl",
    "thai": "th",
    "turkish": "tr",
    "ukrainian": "uk",
    "urdu": "ur",
    "vietnamese": "vi",
}


def _load_translator_language_dict(translator_cls, fallback: Dict[str, str], *, include_auto_detect: bool) -> Dict[str, str]:
    if translator_cls is None:
        if include_auto_detect:
            logger.debug("deep_translator unavailable; using static fallback language table with auto detect")
        else:
            logger.debug("deep_translator unavailable; using static fallback language table")
        return deepcopy(fallback)

    try:
        resolved = deepcopy(translator_cls().get_supported_languages(as_dict=True))
        assert isinstance(resolved, Dict)
        if include_auto_detect:
            resolved["auto detect"] = "auto"
        if "filipino" in resolved:
            resolved["filipino (tagalog)"] = resolved.pop("filipino")
        return resolved
    except Exception as exc:
        logger.warning(f"Failed to load translator language table dynamically: {exc}")
        return deepcopy(fallback)


def _build_mymemory_key_val() -> Dict[str, str]:
    resolved = _load_translator_language_dict(
        MyMemoryTranslator,
        _STATIC_MYMEMORY_KEY_VAL,
        include_auto_detect=False,
    )
    for invalid_key in ("aymara", "dogri", "javanese", "konkani", "krio", "oromo"):
        resolved.pop(invalid_key, None)
    return resolved


GOOGLE_KEY_VAL = _load_translator_language_dict(
    GoogleTranslator,
    _STATIC_GOOGLE_KEY_VAL,
    include_auto_detect=True,
)
MYMEMORY_KEY_VAL = _build_mymemory_key_val()


def verify_language_in_key(search: str, engine: str) -> bool:
    if engine in {"Google Translate", "Selenium Chrome Translate"}:
        return search in GOOGLE_KEY_VAL
    if engine == "LibreTranslate":
        return search in LIBRE_KEY_VAL
    if engine == "MyMemoryTranslator":
        return search in MYMEMORY_KEY_VAL
    raise ValueError("Engine not found")


def get_whisper_lang_similar(similar: str, debug: bool = True) -> str:
    if debug:
        logger.debug("GETTING WHISPER LANGUAGE FROM SIMILAR LANGUAGE NAME")
    should_be_there = get_similar_in_list(WHISPER_LANG_LIST, similar.lower())
    if len(should_be_there) != 0:
        if debug:
            logger.debug(f"Found key {should_be_there[0]} while searching for {similar}")
            logger.debug(f"FULL KEY GET {should_be_there}")
        return should_be_there[0]
    raise ValueError(
        f"Fail to get whisper language from similar while searching for {similar}. "
        "Please report this as a bug to https://github.com/silverpoetry/speech-translate-rev/issues"
    )


def get_whisper_lang_name(search: str) -> str:
    if len(search) > 3:
        return search
    return WHISPER_CODE_TO_NAME[search]


def _sorted_titlecase(values: List[str]) -> List[str]:
    result = [up_first_case(value) for value in values]
    result.sort()
    return result


def _filter_whisper_compatible(values: List[str]) -> List[str]:
    compatible: list[str] = []
    for lang in values:
        if get_similar_in_list(WHISPER_LANG_LIST, lang):
            compatible.append(lang)
    return compatible


WHISPER_TARGET = ["English"]

GOOGLE_TARGET = _sorted_titlecase([key for key in GOOGLE_KEY_VAL.keys() if key != "auto detect"])
LIBRE_TARGET = _sorted_titlecase([key for key in LIBRE_KEY_VAL.keys() if key != "auto detect"])
MY_MEMORY_TARGET = _sorted_titlecase(list(MYMEMORY_KEY_VAL.keys()))

TL_ENGINE_TARGET_DICT = {
    "⚡ Tiny [1GB VRAM] (Fastest)": WHISPER_TARGET,
    "🚀 Base [1GB VRAM] (Faster)": WHISPER_TARGET,
    "⛵ Small [2GB VRAM] (Moderate)": WHISPER_TARGET,
    "🌀 Medium [5GB VRAM] (Accurate)": WHISPER_TARGET,
    "🐌 Large V1 [10GB VRAM] (Most Accurate)": WHISPER_TARGET,
    "🐌 Large V2 [10GB VRAM] (Most Accurate)": WHISPER_TARGET,
    "🐌 Large V3 [10GB VRAM] (Most Accurate)": WHISPER_TARGET,
    "Google Translate": GOOGLE_TARGET,
    "Selenium Chrome Translate": GOOGLE_TARGET,
    "LibreTranslate": LIBRE_TARGET,
    "MyMemoryTranslator": MY_MEMORY_TARGET,
}

GOOGLE_WHISPER_COMPATIBLE = _filter_whisper_compatible(GOOGLE_TARGET.copy())
LIBRE_WHISPER_COMPATIBLE = _filter_whisper_compatible(LIBRE_TARGET.copy())
MYMEMORY_WHISPER_COMPATIBLE = _filter_whisper_compatible(MY_MEMORY_TARGET.copy())

WHISPER_LIST_UPPED = _sorted_titlecase(WHISPER_LANG_LIST.copy())
WHISPER_SOURCE = ["Auto detect"] + [lang for lang in WHISPER_LIST_UPPED if lang != "Cantonese"]
WHISPER_SOURCE_V3 = ["Auto detect"] + WHISPER_LIST_UPPED.copy()
GOOGLE_SOURCE = ["Auto detect"] + _sorted_titlecase(GOOGLE_WHISPER_COMPATIBLE.copy())
LIBRE_SOURCE = ["Auto detect"] + _sorted_titlecase(LIBRE_WHISPER_COMPATIBLE.copy())
MYMEMORY_SOURCE = _sorted_titlecase(MYMEMORY_WHISPER_COMPATIBLE.copy())

TL_ENGINE_SOURCE_DICT = {
    "⚡ Tiny [1GB VRAM] (Fastest)": WHISPER_SOURCE,
    "🚀 Base [1GB VRAM] (Faster)": WHISPER_SOURCE,
    "⛵ Small [2GB VRAM] (Moderate)": WHISPER_SOURCE,
    "🌀 Medium [5GB VRAM] (Accurate)": WHISPER_SOURCE,
    "🐌 Large V1 [10GB VRAM] (Most Accurate)": WHISPER_SOURCE,
    "🐌 Large V2 [10GB VRAM] (Most Accurate)": WHISPER_SOURCE,
    "🐌 Large V3 [10GB VRAM] (Most Accurate)": WHISPER_SOURCE_V3,
    "Google Translate": GOOGLE_SOURCE,
    "Selenium Chrome Translate": GOOGLE_SOURCE,
    "LibreTranslate": LIBRE_SOURCE,
    "MyMemoryTranslator": MYMEMORY_SOURCE,
}


def get_whisper_lang_source(cur_model: str) -> List[str]:
    if "V3" in cur_model:
        return WHISPER_SOURCE_V3
    return WHISPER_SOURCE
