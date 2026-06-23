from __future__ import annotations

from html import escape
from importlib import import_module
from pathlib import Path
import re
from shutil import rmtree
import sys
from tempfile import NamedTemporaryFile, mkdtemp
from time import sleep
from typing import Iterable, List, Optional
from urllib.parse import quote

from speech_translate.log_helpers import logger
from speech_translate.utils.translate.selenium_runtime import SeleniumTranslatorConfig


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
        self._active_user_data_dir: Optional[Path] = None
        self._temp_user_data_dir: Optional[Path] = None
        self._line_sep_token = "。"

    @staticmethod
    def _is_connection_lost_error(exc: Exception) -> bool:
        message = str(exc or "").lower()
        signals = (
            "maxretryerror",
            "newconnectionerror",
            "failed to establish a new connection",
            "connection refused",
            "actively refused",
            "winerror 10061",
            "invalid session id",
            "session deleted because of page crash",
            "disconnected: not connected to devtools",
            "chrome not reachable",
        )
        return any(token in message for token in signals)

    @staticmethod
    def _is_timeout_error(exc: Exception) -> bool:
        message = str(exc or "").lower()
        return "timeout" in message or "timed out" in message

    def _reset_driver(self) -> None:
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass
            finally:
                self._driver = None
        self._page_template_loaded = False
        self._template_lang_hint = ""

        # When fallback temp profile is used, remove it after browser closes to avoid stale lock/state.
        if self._temp_user_data_dir is not None:
            try:
                rmtree(self._temp_user_data_dir, ignore_errors=True)
            except Exception:
                pass
            self._temp_user_data_dir = None
        self._active_user_data_dir = None

    @staticmethod
    def _is_devtools_port_error(exc: Exception) -> bool:
        message = str(exc or "").lower()
        return (
            "devtoolsactiveport" in message
            or "session not created" in message and "chrome failed to start" in message
        )

    def _build_options(self, options_cls, user_data_dir: Path):
        options = options_cls()
        if self.config.headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1280,900")
        options.add_argument("--remote-debugging-port=0")

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

        user_data_dir.mkdir(parents=True, exist_ok=True)
        options.add_argument(f"--user-data-dir={user_data_dir}")
        options.add_argument("--profile-directory=Default")
        return options

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
        keywords.extend(["Speech Translate Rev", "Speech Translate", "语音翻译"])

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
            try:
                _ = self._driver.current_url
                return self._driver
            except Exception:
                self._reset_driver()

        try:
            webdriver = import_module("selenium.webdriver")
            chrome_options_mod = import_module("selenium.webdriver.chrome.options")
            Options = getattr(chrome_options_mod, "Options")
        except Exception as exc:
            raise RuntimeError("Selenium is not installed. Please install `selenium` first.") from exc

        primary_user_data_dir = (
            Path(self.config.chrome_user_data_dir)
            if self.config.chrome_user_data_dir
            else self._default_chrome_user_data_dir()
        )

        try:
            options = self._build_options(Options, primary_user_data_dir)
            # Selenium Manager (Selenium 4.6+) can resolve chromedriver automatically.
            self._driver = webdriver.Chrome(options=options)
            self._active_user_data_dir = primary_user_data_dir
        except Exception as exc:
            if not self._is_devtools_port_error(exc):
                raise

            # Chrome may still be tearing down previous process/profile lock right after auto-close.
            sleep(0.6)
            fallback_user_data_dir = Path(mkdtemp(prefix="st_chrome_profile_"))
            self._temp_user_data_dir = fallback_user_data_dir
            logger.warning(
                f"Chrome failed to start with persistent profile; retrying with temp profile: {fallback_user_data_dir}"
            )
            options = self._build_options(Options, fallback_user_data_dir)
            self._driver = webdriver.Chrome(options=options)
            self._active_user_data_dir = fallback_user_data_dir

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
        self._reset_driver()

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

    def _page_has_payload_root(self) -> bool:
        driver = self._ensure_driver()
        try:
            return bool(
                driver.execute_script(
                    """
                    const payload = document.getElementById('payload');
                    return Boolean(payload);
                    """
                )
            )
        except Exception:
            return False

    def _ensure_page_template_loaded(self, source_lang: Optional[str] = None) -> None:
        lang_hint = self._normalize_template_lang(source_lang)
        should_reload = (not self._page_template_loaded) or (self._template_lang_hint != lang_hint)
        if not should_reload and not self._page_has_payload_root():
            should_reload = True
            try:
                current_url = str(self._ensure_driver().current_url)
            except Exception:
                current_url = ""
            logger.warning(
                f"Selenium page template marker was stale; payload root missing. Reloading template. "
                f"url={current_url or 'unknown'} lang_hint={lang_hint or 'auto'}"
            )
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
        segments: List[str] = []
        n = len(payload_lines)
        for idx, line in enumerate(payload_lines):
            suffix = self._line_sep_token if idx < n - 1 else ""
            if idx == n - 1:
                # 最后一行用<p>
                segments.append(f'<p data-st-line="{idx}">{escape(line + suffix)}</p>')
            else:
                segments.append(f'<span data-st-line="{idx}">{escape(line + suffix)}</span>')
                segments.append('<br data-st-br="1" />')
        payload_html = "".join(segments)
        payload_text = driver.execute_script(
            """
            const payload = document.getElementById('payload');
            if (!payload) return null;
            payload.innerHTML = arguments[0];
            return String(payload.innerText || payload.textContent || '')
                .replaceAll(String.fromCharCode(13), '')
                .trim();
            """,
            payload_html,
        )
        if payload_text is None:
            try:
                current_url = str(driver.current_url)
            except Exception:
                current_url = ""
            raise RuntimeError(
                "Template payload root not found before text injection; "
                f"current_url={current_url or 'unknown'}"
            )
        return self._normalize_page_text(str(payload_text))

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

    @staticmethod
    def _join_non_empty_lines(lines: Iterable[str]) -> str:
        return "\n".join(str(line).strip() for line in lines if str(line).strip()).strip()

    def _read_payload_lines(self) -> List[str]:
        driver = self._ensure_driver()
        lines = driver.execute_script(
            r"""
            const payload = document.getElementById('payload');
            if (!payload) return [];

            const readText = (node) => String(node?.innerText || node?.textContent || '')
                .replaceAll(String.fromCharCode(13), '')
                .replaceAll(String.fromCharCode(10), ' ')
                .trim();
            // 先取所有 span[data-st-line] 和 p[data-st-line]，保证顺序
            const tagged = Array.from(payload.querySelectorAll('span[data-st-line],p[data-st-line]'));
            if (tagged.length > 0) {
                return tagged.map(readText).filter(Boolean);
            }

            // Fallback: treat each non-separator span as one line.
            const fallback = Array.from(payload.querySelectorAll('span:not([data-st-sep])'));
            return fallback
                .map(readText)
                .filter(Boolean);
            """,
        )
        if not isinstance(lines, list):
            return []
        return [str(line).strip() for line in lines if str(line).strip()]

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
        last_error: Optional[Exception] = None
        for attempt in range(2):
            try:
                self._ensure_page_template_loaded(lang_hint)
                baseline = self._normalize_page_text(self._set_payload_text(raw_lines))
                observed_text = self._normalize_page_text(self._wait_translation_event(baseline, wait_timeout_sec))
                translated_lines = self._read_payload_lines()
                if not any(line.strip() for line in translated_lines):
                    # Fallback if page translation changed DOM unexpectedly.
                    translated_text = self._read_payload_text()
                    translated_lines = [
                        part.strip() for part in translated_text.split(self._line_sep_token) if part.strip()
                    ]
                candidate_text = self._normalize_page_text(
                    self._join_non_empty_lines(translated_lines) or observed_text
                )
                if not candidate_text or candidate_text == baseline:
                    logger.info(
                        "Page-translate result stayed unchanged after timeout; returning original text for next injection cycle."
                    )
                    return raw_lines
                if translated_lines:
                    return translated_lines
            except Exception as exc:
                last_error = exc
                if attempt == 0 and self._is_connection_lost_error(exc):
                    self._reset_driver()
                    continue
                if self._is_timeout_error(exc):
                    logger.info(
                        "Page-translate timed out while waiting for mutation; returning original text for next injection cycle."
                    )
                    return raw_lines
                raise

        if last_error is not None and self._is_connection_lost_error(last_error):
            raise RuntimeError(
                "Selenium browser connection lost during translation. "
                "The browser was restarted once but did not recover. Please try again."
            ) from last_error

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
