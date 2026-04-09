from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List

import webview

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
  sys.path.insert(0, str(PROJECT_ROOT))

from speech_translate.utils.translate.selenium_web_translator import (
    SeleniumTranslatorConfig,
    SeleniumWebTranslator,
)


HTML = """
<!doctype html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Selenium 翻译实验窗口</title>
  <style>
    body { font-family: Segoe UI, sans-serif; margin: 14px; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    textarea { width: 100%; min-height: 320px; }
    .toolbar { margin: 8px 0 12px; display: flex; gap: 8px; align-items: center; }
    input { padding: 4px 8px; }
    button { padding: 7px 12px; }
    pre { white-space: pre-wrap; background: #f5f5f5; padding: 8px; border-radius: 6px; }
  </style>
</head>
<body>
  <h3>Selenium 翻译实验窗口</h3>
  <div class=\"toolbar\">
    <label>源语言 <input id=\"source\" value=\"auto\" /></label>
    <label>目标语言 <input id=\"target\" value=\"zh-CN\" /></label>
    <button id=\"run\">站点翻译模式</button>
    <button id=\"run-page\">页面翻译模式</button>
  </div>
  <div class=\"row\">
    <div>
      <div>输入（每行一条）</div>
      <textarea id=\"input\">hello world\nthis is a selenium translation probe</textarea>
    </div>
    <div>
      <div>输出</div>
      <textarea id=\"output\"></textarea>
    </div>
  </div>
  <pre id=\"log\"></pre>

  <script>
    const runBtn = document.getElementById('run');
    const runPageBtn = document.getElementById('run-page');
    const input = document.getElementById('input');
    const output = document.getElementById('output');
    const source = document.getElementById('source');
    const target = document.getElementById('target');
    const log = document.getElementById('log');

    runBtn.addEventListener('click', async () => {
      runBtn.disabled = true;
      log.textContent = '翻译中...';
      try {
        const result = await window.pywebview.api.translate_lines(
          input.value,
          source.value,
          target.value
        );
        output.value = result.text || '';
        log.textContent = JSON.stringify(result, null, 2);
      } catch (e) {
        log.textContent = '错误: ' + (e.message || String(e));
      } finally {
        runBtn.disabled = false;
      }
    });

    runPageBtn.addEventListener('click', async () => {
      runPageBtn.disabled = true;
      log.textContent = '页面翻译模式中...若浏览器弹出翻译气泡，请选择翻译为中文';
      try {
        const result = await window.pywebview.api.translate_lines_page_mode(
          input.value,
          target.value
        );
        output.value = result.text || '';
        log.textContent = JSON.stringify(result, null, 2);
      } catch (e) {
        log.textContent = '错误: ' + (e.message || String(e));
      } finally {
        runPageBtn.disabled = false;
      }
    });
  </script>
</body>
</html>
"""


class Api:
    def __init__(self):
        self.translator = SeleniumWebTranslator(
      SeleniumTranslatorConfig(
        source_lang="auto",
        target_lang="zh-CN",
        headless=False,
        engine_compact_mode=True,
        engine_width=420,
        engine_height=240,
        engine_margin_right=16,
        engine_margin_top=56,
      )
        )

    def translate_lines(self, block_text: str, source_lang: str, target_lang: str):
        lines: List[str] = block_text.splitlines()
        translated = self.translator.translate_lines(lines, source_lang=source_lang, target_lang=target_lang)
        return {
            "ok": True,
            "count": len(lines),
            "text": "\n".join(translated),
        }

    def translate_lines_page_mode(self, block_text: str, target_lang: str):
      lines: List[str] = block_text.splitlines()
      translated = self.translator.translate_lines_via_page_translate(lines, target_lang=target_lang)
      return {
        "ok": True,
        "mode": "page_translate",
        "count": len(lines),
        "text": "\n".join(translated),
      }


if __name__ == "__main__":
    api = Api()
    window = webview.create_window("Selenium 翻译实验", html=HTML, js_api=api, width=1100, height=760)
    try:
        webview.start(debug=True)
    finally:
        api.translator.close()
