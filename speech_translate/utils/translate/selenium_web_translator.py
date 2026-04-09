from __future__ import annotations

from dataclasses import dataclass
from html import escape
from importlib import import_module
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Iterable, List, Optional
from urllib.parse import quote


@dataclass
class SeleniumTranslatorConfig:
    source_lang: str = "auto"
    target_lang: str = "zh-CN"
    headless: bool = False
    page_timeout_sec: float = 20.0
    force_chinese_ui: bool = True
    chrome_user_data_dir: Optional[str] = None
    engine_compact_mode: bool = True
    engine_width: int = 420
    engine_height: int = 240
    engine_margin_right: int = 16
    engine_margin_top: int = 56


class SeleniumWebTranslator:
    """Experimental translator driven by Selenium + Google Translate web page.

    Notes:
    - This implementation intentionally uses the Google Translate web UI instead of
      native browser context-menu translation because context menus are OS-native
      and not reliably controllable from Selenium.
    - The translator keeps one browser instance and translates lines sequentially.
    """

    def __init__(self, config: Optional[SeleniumTranslatorConfig] = None):
        self.config = config or SeleniumTranslatorConfig()
        self._driver = None
        self._injected_html_path: Optional[Path] = None
        self._page_template_loaded = False
        self._template_lang_hint = ""

    @staticmethod
    def _default_chrome_user_data_dir() -> Path:
        project_root = Path(__file__).resolve().parents[3]
        return project_root / "speech_translate" / "_user" / "selenium_chrome_profile"

    def _ensure_driver(self):
        if self._driver is not None:
            return self._driver

        try:
            webdriver = import_module("selenium.webdriver")
            chrome_options_mod = import_module("selenium.webdriver.chrome.options")
            Options = getattr(chrome_options_mod, "Options")
        except Exception as exc:
            raise RuntimeError("Selenium is not installed. Please install `selenium` first.") from exc

        options = Options()
        if self.config.headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1280,900")

        if self.config.engine_compact_mode:
            # App mode removes most browser chrome and behaves like a lightweight tool window.
            options.add_argument("--app=about:blank")
            options.add_argument("--disable-infobars")
            options.add_argument("--disable-features=TranslateUI")

        if self.config.force_chinese_ui:
            options.add_argument("--lang=zh-CN")
            options.add_experimental_option(
                "prefs",
                {
                    "intl.accept_languages": "zh-CN,zh,en-US,en",
                },
            )
        else:
            options.add_argument("--lang=en-US")

        user_data_dir = Path(self.config.chrome_user_data_dir) if self.config.chrome_user_data_dir else self._default_chrome_user_data_dir()
        user_data_dir.mkdir(parents=True, exist_ok=True)
        options.add_argument(f"--user-data-dir={user_data_dir}")
        options.add_argument("--profile-directory=Default")

        # Selenium Manager (Selenium 4.6+) can resolve chromedriver automatically.
        self._driver = webdriver.Chrome(options=options)
        self._driver.set_page_load_timeout(self.config.page_timeout_sec)
        return self._driver

    def _apply_engine_window_layout(self) -> None:
        if not self.config.engine_compact_mode:
            return

        driver = self._ensure_driver()
        width = max(300, int(self.config.engine_width))
        height = max(180, int(self.config.engine_height))

        x = 20
        y = max(0, int(self.config.engine_margin_top))
        try:
            screen = driver.execute_script(
                "return {w: window.screen.availWidth || 1920, h: window.screen.availHeight || 1080};"
            )
            sw = int((screen or {}).get("w", 1920))
            x = max(0, sw - width - int(self.config.engine_margin_right))
        except Exception:
            pass

        try:
            driver.set_window_rect(x=x, y=y, width=width, height=height)
        except Exception:
            try:
                driver.set_window_size(width, height)
            except Exception:
                pass

    def close(self) -> None:
        if self._injected_html_path is not None:
            try:
                self._injected_html_path.unlink(missing_ok=True)
            except Exception:
                pass
            self._injected_html_path = None
        if self._driver is not None:
            try:
                self._driver.quit()
            finally:
                self._driver = None
        self._page_template_loaded = False

    def _open_translate_page(self, source_lang: str, target_lang: str) -> None:
        driver = self._ensure_driver()
        url = (
            "https://translate.google.com/"
            f"?sl={quote(source_lang)}&tl={quote(target_lang)}&op=translate"
        )
        driver.get(url)

    def _translate_one(self, text: str) -> str:
        if text.strip() == "":
            return ""

        driver = self._ensure_driver()

        by_mod = import_module("selenium.webdriver.common.by")
        keys_mod = import_module("selenium.webdriver.common.keys")
        ec = import_module("selenium.webdriver.support.expected_conditions")
        wait_mod = import_module("selenium.webdriver.support.ui")
        By = getattr(by_mod, "By")
        Keys = getattr(keys_mod, "Keys")
        WebDriverWait = getattr(wait_mod, "WebDriverWait")

        wait = WebDriverWait(driver, self.config.page_timeout_sec)

        # Source textarea in current Google Translate UI.
        source = wait.until(
            ec.presence_of_element_located((By.CSS_SELECTOR, "textarea[aria-label]"))
        )

        source.click()
        source.send_keys(Keys.CONTROL, "a")
        source.send_keys(Keys.DELETE)
        source.send_keys(text)

        # Read translated text from result spans (Google UI specific, may evolve).
        def read_result(drv):
            value = drv.execute_script(
                """
                const nodes = Array.from(document.querySelectorAll("span[jsname='W297wb']"));
                const text = nodes.map(n => (n.innerText || "").trim()).filter(Boolean).join(" ");
                return text;
                """
            )
            if value and value.strip():
                return value.strip()
            return False

        translated = wait.until(read_result)
        return translated

    def _template_title_by_lang(self, lang_hint: str) -> str:
        mapping = {
            "ja": "日本語の本文",
            "ko": "한국어 본문",
            "zh-cn": "中文正文",
            "zh": "中文正文",
            "en": "English text",
            "es": "Texto en español",
            "fr": "Texte en français",
            "de": "Deutscher Text",
            "ru": "Русский текст",
        }
        return mapping.get(lang_hint.lower(), "Source text")

    def _template_html(self, lang_hint: str = "") -> str:
        html_lang = (lang_hint or "").strip()
        title = self._template_title_by_lang(html_lang)
        html_lang_attr = f' lang="{html_lang}"' if html_lang else ""
        return f"""
<!doctype html>
<html translate="yes"{html_lang_attr}>
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 0; line-height: 1.5; }}
        main {{ max-width: 100%; }}
        #payload {{ white-space: pre-wrap; word-break: break-word; }}
    </style>
</head>
<body>
    <main>
        <section id="payload"></section>
    </main>
</body>
</html>
"""

    def _normalize_template_lang(self, source_lang: Optional[str]) -> str:
        lang = (source_lang or "").strip().lower()
        if not lang or lang in {"auto", "auto detect"}:
            return ""
        return lang

    def _open_blank_page_with_html(self, html: str) -> None:
        driver = self._ensure_driver()
        # Use a real HTML file URL so browser page-translation logic can recognize it as a webpage.
        if self._injected_html_path is not None:
            try:
                self._injected_html_path.unlink(missing_ok=True)
            except Exception:
                pass

        with NamedTemporaryFile("w", suffix=".html", encoding="utf-8", delete=False) as f:
            f.write(html)
            self._injected_html_path = Path(f.name)

        driver.get(self._injected_html_path.as_uri())

    def _ensure_page_template_loaded(self, source_lang: Optional[str] = None) -> None:
        lang_hint = self._normalize_template_lang(source_lang)
        should_reload = (not self._page_template_loaded) or (self._template_lang_hint != lang_hint)
        if not should_reload:
            return

        self._open_blank_page_with_html(self._template_html(lang_hint))
        self._apply_engine_window_layout()
        driver = self._ensure_driver()
        driver.execute_script(
            """
            if (!document.getElementById('payload')) {
                throw new Error('Template payload root not found');
            }
            """
        )
        self._page_template_loaded = True
        self._template_lang_hint = lang_hint

    def _set_payload_text(self, lines: Iterable[str]) -> str:
        driver = self._ensure_driver()
        payload_lines = [str(x) for x in lines]
        payload_html = "<br/>".join(
            f'<span data-st-line="{idx}">{escape(line)}</span>' for idx, line in enumerate(payload_lines)
        )
        payload_text = "\n".join(payload_lines)
        driver.execute_script(
            """
            const payload = document.getElementById('payload');
            payload.innerHTML = arguments[0];
            """,
            payload_html,
        )
        return payload_text

    def _detect_source_lang_hint(self, lines: Iterable[str]) -> str:
        sample = "\n".join(str(x) for x in lines)
        if not sample.strip():
            return ""

        # Japanese: Hiragana/Katakana/Kanji mix.
        if re.search(r"[\u3040-\u30ff]", sample):
            return "ja"
        # Korean Hangul.
        if re.search(r"[\uac00-\ud7af]", sample):
            return "ko"
        # Chinese Han ideographs (rough heuristic fallback).
        if re.search(r"[\u4e00-\u9fff]", sample):
            return "zh-CN"
        return ""

    def _normalize_page_text(self, text: str) -> str:
        return str(text).replace("\r", "").strip()

    def _read_payload_lines(self) -> List[str]:
        driver = self._ensure_driver()
        lines = driver.execute_script(
            """
            const payload = document.getElementById('payload');
            if (!payload) return [];
            const nodes = Array.from(payload.querySelectorAll('span[data-st-line]'));
            return nodes.map((node) => String(node.innerText || node.textContent || '').replaceAll(String.fromCharCode(13), '').trim());
            """
        )

        if not isinstance(lines, list):
            return []

        cleaned = [str(line).strip() for line in lines]
        return cleaned

    def _read_payload_text(self) -> str:
        driver = self._ensure_driver()
        text = driver.execute_script(
            """
            const payload = document.getElementById('payload');
            if (!payload) return '';
            return (payload.innerText || payload.textContent || '');
            """
        )
        return self._normalize_page_text(str(text or ""))

    def _wait_translation_event(self, baseline_text: str, wait_timeout_sec: float) -> str:
        driver = self._ensure_driver()
        timeout_ms = max(1, int(wait_timeout_sec * 1000))
        driver.set_script_timeout(wait_timeout_sec + 1.0)

        result = driver.execute_async_script(
            """
            const timeoutMs = arguments[0];
            const baseline = String(arguments[1] || '').replaceAll(String.fromCharCode(13), '').trim();
            const done = arguments[arguments.length - 1];
            const payload = document.getElementById('payload');

            if (!payload) {
                done({ok: false, text: '', reason: 'payload-not-found'});
                return;
            }

            const read = () => String(payload.innerText || payload.textContent || '').replaceAll(String.fromCharCode(13), '').trim();
            let finished = false;
            let observer = null;
            let timer = null;

            const finish = (ok, reason) => {
                if (finished) return;
                finished = true;
                try { if (observer) observer.disconnect(); } catch (_) {}
                try { if (timer) clearTimeout(timer); } catch (_) {}
                done({ok, text: read(), reason});
            };

            const initial = read();
            if (initial && initial !== baseline) {
                finish(true, 'already-changed');
                return;
            }

            observer = new MutationObserver(() => {
                const now = read();
                if (now && now !== baseline) {
                    finish(true, 'mutation');
                }
            });

            observer.observe(payload, {childList: true, subtree: true, characterData: true});
            timer = setTimeout(() => finish(false, 'timeout'), timeoutMs);
            """,
            timeout_ms,
            baseline_text,
        )

        if isinstance(result, dict):
            text = self._normalize_page_text(result.get("text", ""))
            if result.get("ok") and text and text != baseline_text:
                return text

        # Final best-effort read after async wait exits.
        return self._read_payload_text()

    def translate_lines_via_page_translate(
        self,
        lines: Iterable[str],
        source_lang: Optional[str] = None,
        target_lang: str = "zh-CN",
        wait_timeout_sec: float = 20.0,
    ) -> List[str]:
        """Translate by using Chrome page-translation flow on an injected HTML page.

        This is experimental and depends on browser locale/menu behavior.
        """
        raw_lines = [str(x) for x in lines]
        lang_hint = (source_lang or "").strip().lower()
        if lang_hint in {"", "auto", "auto detect"}:
            lang_hint = self._detect_source_lang_hint(raw_lines)
        self._ensure_page_template_loaded(lang_hint)
        baseline = self._normalize_page_text(self._set_payload_text(raw_lines))
        _ = self._wait_translation_event(baseline, wait_timeout_sec)
        translated_lines = self._read_payload_lines()
        if not any(line.strip() for line in translated_lines):
            # Fallback if page translation changed DOM unexpectedly.
            translated_text = self._read_payload_text()
            translated_lines = [line.strip() for line in translated_text.splitlines()]
        if translated_lines:
            return translated_lines

        raise RuntimeError(
            "Page-translate mode timeout: browser translation did not complete. "
            "You may need to manually choose 'Translate to Chinese' in Chrome."
        )

    def translate_lines(
        self,
        lines: Iterable[str],
        source_lang: Optional[str] = None,
        target_lang: Optional[str] = None,
    ) -> List[str]:
        src = source_lang or self.config.source_lang
        tgt = target_lang or self.config.target_lang

        self._open_translate_page(src, tgt)

        out: List[str] = []
        for line in lines:
            out.append(self._translate_one(line))
        return out
