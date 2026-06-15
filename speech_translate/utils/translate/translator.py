# pylint: disable=protected-access, redefined-outer-name, import-outside-toplevel, invalid-name
from typing import Dict, List

from speech_translate.linker import sj
from speech_translate.log_helpers import logger

from ..helper import get_similar_keys
from .language import GOOGLE_KEY_VAL, LIBRE_KEY_VAL, MYMEMORY_KEY_VAL
from .selenium_runtime import SeleniumTranslatorManager

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency fallback
    requests = None  # type: ignore[assignment]

try:
    from tqdm.auto import tqdm
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency fallback
    def tqdm(iterable, **_kwargs):
        return iterable


def tl_batch_with_tqdm(self, batch: List[str], **kwargs) -> list:
    """Translate a batch of texts

    Args:
        batch (list): List of text to translate

    Returns:
        list: List of translated text
    """
    if not batch:
        raise Exception("Enter your text list that you want to translate")
    arr = []
    with_tqdm = kwargs.pop("with_tqdm", True)

    def _inner_tl(text: str):
        if text.isdigit():
            text += " "  # add a space in the end to prevent error
        return self.translate(text, **kwargs)

    if with_tqdm:
        for text in tqdm(batch, desc="Translating"):
            arr.append(_inner_tl(text))
    else:
        for text in batch:
            arr.append(_inner_tl(text))

    return arr


class TranslationConnection:
    """Translate Connections

    Attributes
    ----------
        GoogleTranslator (function): Google Translate
        MyMemoryTranslator (function): MyMemoryTranslator
    """
    def __init__(self, GoogleTranslator, MyMemoryTranslator):
        self.GoogleTranslator = GoogleTranslator
        self.MyMemoryTranslator = MyMemoryTranslator


def _load_deep_translator_classes():
    try:
        from deep_translator import GoogleTranslator as _GoogleTranslator, MyMemoryTranslator as _MyMemoryTranslator
    except ModuleNotFoundError:
        return None, None
    _GoogleTranslator._translate_batch = tl_batch_with_tqdm
    _MyMemoryTranslator._translate_batch = tl_batch_with_tqdm
    return _GoogleTranslator, _MyMemoryTranslator


TlCon = TranslationConnection(None, None)

def _create_selenium_translator(config):
    from .selenium_web_translator import SeleniumWebTranslator

    return SeleniumWebTranslator(config)


_selenium_translator_manager = SeleniumTranslatorManager(
    settings_snapshot_provider=lambda: sj.cache,
    translator_factory=_create_selenium_translator,
    logger_instance=logger,
)


def _get_selenium_translator():
    return _selenium_translator_manager.get()


def shutdown_selenium_translator() -> None:
    """Close and reset the singleton Selenium translator instance."""
    _selenium_translator_manager.shutdown()


def _resolve_language_code(
    language_map: dict[str, str],
    language_name: str,
    *,
    engine_label: str,
    fallback_to_auto: bool,
) -> str:
    try:
        return language_map[language_name]
    except KeyError:
        logger.warning(f"{engine_label} language code undefined for {language_name}. Trying similar keys")

    similar_keys = get_similar_keys(language_map, language_name)
    if similar_keys:
        resolved = language_map[similar_keys[0]]
        logger.debug(f"Got similar key for {engine_label} language {language_name}: {resolved}")
        return resolved

    if fallback_to_auto:
        logger.warning(f"{engine_label} source language code undefined. Using auto")
        return "auto"

    raise KeyError(language_name)


def _resolve_language_pair(
    language_map: dict[str, str],
    from_lang: str,
    to_lang: str,
    *,
    engine_label: str,
    fallback_source_to_auto: bool = True,
) -> tuple[str, str]:
    source_code = _resolve_language_code(
        language_map,
        from_lang,
        engine_label=engine_label,
        fallback_to_auto=fallback_source_to_auto,
    )
    target_code = _resolve_language_code(
        language_map,
        to_lang,
        engine_label=engine_label,
        fallback_to_auto=False,
    )
    return source_code, target_code


def _ensure_deep_translator_connection() -> tuple[object | None, object | None]:
    if TlCon.GoogleTranslator is None or TlCon.MyMemoryTranslator is None:
        TlCon.GoogleTranslator, TlCon.MyMemoryTranslator = _load_deep_translator_classes()
    return TlCon.GoogleTranslator, TlCon.MyMemoryTranslator


def _log_translation_debug(query: List[str], result: object, debug_log: bool) -> None:
    if not debug_log:
        return
    logger.info("-" * 50)
    logger.debug("Query: " + str(query))
    logger.debug("Translation Get: " + str(result))


def google_tl(text: List[str], from_lang: str, to_lang: str, proxies: Dict, debug_log: bool = False, **kwargs):
    """Translate Using Google Translate

    Args
    ----
        text (List[str]): Text to translate
        from_lang (str): Language From
        to_lang (str): Language to translate
        proxies (Dict): Proxies. Defaults to None.
        debug_log (bool, optional): Debug Log. Defaults to False.

    Returns
    -------
        is_success: Success or not
        result: Translation result
    """
    is_success = False
    result = ""
    # --- Get lang code ---
    try:
        LCODE_FROM, LCODE_TO = _resolve_language_pair(
            GOOGLE_KEY_VAL,
            from_lang,
            to_lang,
            engine_label="Google",
        )
    except KeyError as e:
        logger.exception(e)
        return is_success, "Error Language Code Undefined"

    # using deep_translator v 1.11.1
    # --- Translate ---
    try:
        google_translator, _ = _ensure_deep_translator_connection()
        if google_translator is None:
            return is_success, "Error: deep_translator is unavailable"

        tl_kwargs = {}
        if kwargs.pop("live_input", False):
            tl_kwargs["with_tqdm"] = False
        prefer_full_text = kwargs.pop("prefer_full_text", False)

        translator = google_translator(source=LCODE_FROM, target=LCODE_TO, proxies=proxies)
        if prefer_full_text and len(text) == 1:
            # Full-text request usually gives better contextual quality than line-by-line translation.
            result = [translator.translate(text[0])]
        else:
            result = translator.translate_batch(text, **tl_kwargs)
        is_success = True
    except Exception as e:
        logger.exception(e)
        result = str(e)
    finally:
        _log_translation_debug(text, result, debug_log)

    return is_success, result


def memory_tl(text: List[str], from_lang: str, to_lang: str, proxies: Dict, debug_log: bool = False, **kwargs):
    """Translate Using MyMemoryTranslator

    Args
    ----
        text (List[str]): Text to translate
        from_lang (str): Language From
        to_lang (str): Language to translate
        proxies (Dict): Proxies. Defaults to None.
        debug_log (bool, optional): Debug Log. Defaults to False.

    Returns
    -------
        is_success: Success or not
        result: Translation result
    """
    is_success = False
    result = ""
    # --- Get lang code ---
    try:
        LCODE_FROM, LCODE_TO = _resolve_language_pair(
            MYMEMORY_KEY_VAL,
            from_lang,
            to_lang,
            engine_label="MyMemory",
        )
    except KeyError as e:
        logger.exception(e)
        return is_success, "Error Language Code Undefined"

    # using deep_translator v 1.11.1
    # --- Translate ---
    try:
        _, mymemory_translator = _ensure_deep_translator_connection()
        if mymemory_translator is None:
            return is_success, "Error: deep_translator is unavailable"

        tl_kwargs = {}
        if kwargs.pop("live_input", False):
            tl_kwargs["with_tqdm"] = False

        result = mymemory_translator(source=LCODE_FROM, target=LCODE_TO, proxies=proxies).translate_batch(text, **tl_kwargs)
        is_success = True
    except Exception as e:
        result = str(e)
        logger.exception(e)
    finally:
        _log_translation_debug(text, result, debug_log)
    return is_success, result


# LibreTranslator
def libre_tl(
    text: List[str],
    from_lang: str,
    to_lang: str,
    proxies: Dict,
    debug_log: bool,
    libre_link: str,
    libre_api_key: str,
    **kwargs,
):
    """Translate Using LibreTranslate

    Args
    ----
        text (List[str]): Text to translate
        from_lang (str): Language From
        to_lang (str): Language to translate
        proxies (Dict): Proxies. Defaults to None.
        debug_log (bool): Debug Log. Defaults to False.
        libre_link (str): LibreTranslate Link
        libre_api_key (str): LibreTranslate API Key

    Returns
    -------
        is_success: Success or not
        result: Translation result
    """
    is_success = False
    result = ""
    # --- Get lang code ---
    try:
        LCODE_FROM, LCODE_TO = _resolve_language_pair(
            LIBRE_KEY_VAL,
            from_lang,
            to_lang,
            engine_label="Libre",
        )
    except KeyError as e:
        logger.exception(e)
        return is_success, "Error Language Code Undefined"

    # shoot from API directly using requests
    # --- Translate ---
    try:
        if requests is None:
            return is_success, "Error: requests is unavailable"
        req = {"q": text, "source": LCODE_FROM, "target": LCODE_TO, "format": "text"}
        libre_link += "/translate"

        if libre_api_key != "":
            req["api_key"] = libre_api_key

        arr = []
        if kwargs.pop("live_input", False):
            for q in text:
                req["q"] = q
                response = requests.post(libre_link, json=req, proxies=proxies, timeout=5).json()
                if "error" in response:
                    raise Exception(response["error"])
                translated = response["translatedText"]
                arr.append(translated)
        else:
            for q in tqdm(text, desc="Translating"):
                req["q"] = q
                response = requests.post(libre_link, json=req, proxies=proxies, timeout=5).json()
                if "error" in response:
                    raise Exception(response["error"])
                translated = response["translatedText"]
                arr.append(translated)

        result = arr
        is_success = True
    except Exception as e:
        result = str(e)
        logger.exception(e)
        if "NewConnectionError" in str(e):
            result = "Error: Could not connect. Please make sure that the server is running and the port is correct." \
            " If you are not hosting it yourself, please try again with an internet connection."
        if "request expecting value" in str(e):
            result = "Error: Invalid parameter value. Check for https, host, port, and apiKeys. " \
                "If you use external server, make sure https is set to True."
    finally:
        _log_translation_debug(text, result, debug_log)
    return is_success, result


def selenium_chrome_tl(text: List[str], from_lang: str, to_lang: str, proxies: Dict, debug_log: bool = False, **kwargs):
    """Translate using Selenium + Google Translate web page."""
    _ = proxies
    is_success = False
    result = ""

    try:
        source_code, target_code = _resolve_language_pair(
            GOOGLE_KEY_VAL,
            from_lang,
            to_lang,
            engine_label="Selenium",
        )
    except Exception as e:
        logger.exception(e)
        return is_success, "Error Language Code Undefined"

    try:
        translator = _get_selenium_translator()
        result = translator.translate_lines_via_page_translate(
            text,
            source_lang=source_code,
            target_lang=target_code,
        )
        is_success = True
    except Exception as e:
        logger.exception(e)
        result = str(e)
    finally:
        _log_translation_debug(text, result, debug_log)

    return is_success, result


tl_dict = {
    "Selenium Chrome Translate": selenium_chrome_tl,
    "Google Translate": google_tl,
    "MyMemoryTranslator": memory_tl,
    "LibreTranslate": libre_tl,
}


def translate(engine: str, text: List[str], from_lang: str, to_lang: str, proxies: Dict, debug_log: bool = False, **kwargs):
    """Translate

    Args
    ----
        engine (str): Engine to use
        text (str): Text to translate
        from_lang (str): Language From
        to_lang (str): Language to translate
        proxies (Dict): Proxies. Defaults to None.
        debug_log (bool, optional): Debug Log. Defaults to False.
        **libre_kwargs: LibreTranslate kwargs

    Returns
    -------
        is_success: Success or not
        result: Translation result
    """
    if engine not in tl_dict:
        raise ValueError(f"Invalid engine. Engine {engine} not found")

    # making sure that it is in lower case
    from_lang = from_lang.lower()
    to_lang = to_lang.lower()

    return tl_dict[engine](text, from_lang, to_lang, proxies, debug_log, **kwargs)
