from __future__ import annotations

from dataclasses import dataclass
from html import escape
from importlib import import_module
from pathlib import Path
import re
import sys
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
    engine_margin_bottom: int = 48
    engine_dock_bottom: bool = True
    engine_content_opacity: float = 0.75
    engine_page_zoom: float = 0.86
    win_native_compact: bool = True
    win_alpha: int = 176
    win_borderless: bool = False
    win_z_order_mode: str = "behind-main"


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
        self._window_marker = "ST_ENGINE_WINDOW"

    @staticmethod
    def _iter_windows_hwnd_by_title_keyword(keyword: str) -> list[int]:
        if not keyword:
            return []

        try:
            import ctypes
            from ctypes import wintypes
        except Exception:
            return []

        user32 = ctypes.windll.user32
        results: list[int] = []
        key_lower = keyword.lower()

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _enum_proc(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = str(buf.value or "")
            if key_lower in title.lower():
                results.append(int(hwnd))
            return True

        user32.EnumWindows(_enum_proc, 0)
        return results

    @staticmethod
    def _window_title(hwnd: int) -> str:
        try:
            import ctypes
        except Exception:
            return ""

        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return str(buf.value or "")

    def _find_main_app_hwnd(self) -> Optional[int]:
        keywords = []
        try:
            from speech_translate._constants import APP_NAME

            keywords.append(str(APP_NAME))
        except Exception:
            pass
        keywords.extend(["Speech Translate", "语音翻译"])

        for keyword in keywords:
            if not keyword:
                continue
            for hwnd in self._iter_windows_hwnd_by_title_keyword(keyword):
                title = self._window_title(hwnd)
                if self._window_marker in title:
                    continue
                return int(hwnd)
        return None

    def _apply_windows_native_style(self) -> None:
        if sys.platform != "win32" or not self.config.win_native_compact:
            return

        try:
            import ctypes
        except Exception:
            return

        hwnd_list = self._iter_windows_hwnd_by_title_keyword(self._window_marker)
        if not hwnd_list:
            return

        user32 = ctypes.windll.user32
        set_window_long = user32.SetWindowLongW
        get_window_long = user32.GetWindowLongW

        GWL_STYLE = -16
        GWL_EXSTYLE = -20

        WS_CAPTION = 0x00C00000
        WS_THICKFRAME = 0x00040000
        WS_SYSMENU = 0x00080000
        WS_MINIMIZEBOX = 0x00020000
        WS_MAXIMIZEBOX = 0x00010000

        WS_EX_LAYERED = 0x00080000
        LWA_ALPHA = 0x2
        HWND_BOTTOM = 1
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOZORDER = 0x0004
        SWP_FRAMECHANGED = 0x0020

        alpha = int(max(96, min(255, int(self.config.win_alpha))))

        for hwnd in hwnd_list:
            try:
                style = int(get_window_long(hwnd, GWL_STYLE))
                ex_style = int(get_window_long(hwnd, GWL_EXSTYLE))

                if self.config.win_borderless:
                    style &= ~WS_CAPTION
                    style &= ~WS_THICKFRAME
                    style &= ~WS_SYSMENU
                    style &= ~WS_MINIMIZEBOX
                    style &= ~WS_MAXIMIZEBOX

                ex_style |= WS_EX_LAYERED

                set_window_long(hwnd, GWL_STYLE, style)
                set_window_long(hwnd, GWL_EXSTYLE, ex_style)
                user32.SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA)

                z_mode = str(self.config.win_z_order_mode or "behind-main").strip().lower()
                insert_after = 0
                pos_flags = SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED | SWP_NOZORDER
                if z_mode in {"bottom", "always-bottom", "all-bottom"}:
                    insert_after = HWND_BOTTOM
                    pos_flags = SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED
                elif z_mode in {"behind-main", "behind_main", "main-behind", "behind-main-window"}:
                    main_hwnd = self._find_main_app_hwnd()
                    if main_hwnd and int(main_hwnd) != int(hwnd):
                        insert_after = int(main_hwnd)
                        pos_flags = SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED

                user32.SetWindowPos(
                    hwnd,
                    insert_after,
                    0,
                    0,
                    0,
                    0,
                    pos_flags,
                )
            except Exception:
                continue

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
            # Keep normal Chrome frame/taskbar presence while still using compact size/position.
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
        width = max(260, int(self.config.engine_width))
        height = max(140, int(self.config.engine_height))

        x = 20
        y = max(0, int(self.config.engine_margin_top))
        try:
            screen = driver.execute_script(
                "return {w: window.screen.availWidth || 1920, h: window.screen.availHeight || 1080};"
            )
            sw = int((screen or {}).get("w", 1920))
            sh = int((screen or {}).get("h", 1080))
            x = max(0, sw - width - int(self.config.engine_margin_right))
            if bool(self.config.engine_dock_bottom):
                y = max(0, sh - height - int(self.config.engine_margin_bottom))
        except Exception:
            pass

        try:
            driver.set_window_rect(x=x, y=y, width=width, height=height)
        except Exception:
            try:
                driver.set_window_size(width, height)
            except Exception:
                pass

        # Browser window transparency is not available in Selenium/Chrome directly.
        # Reduce visual presence by making page content slightly transparent and scaled down.
        try:
            opacity = float(self.config.engine_content_opacity)
            zoom = float(self.config.engine_page_zoom)
            opacity = max(0.4, min(1.0, opacity))
            zoom = max(0.65, min(1.0, zoom))
            driver.execute_script(
                """
                document.documentElement.style.background = '#f8fafc';
                document.body.style.background = '#f8fafc';
                document.body.style.opacity = arguments[0];
                document.body.style.zoom = arguments[1];
                document.body.style.overflow = 'hidden';
                """,
                opacity,
                zoom,
            )
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
    <title>{self._window_marker} | {title}</title>
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
        self._apply_windows_native_style()
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
