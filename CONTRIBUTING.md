# Contributing

Thanks for helping improve Speech Translate Rev.

## Development Setup

```powershell
git clone --recurse-submodules https://github.com/silverpoetry/speech-translate-rev.git
cd speech-translate-rev
python -m venv .venv314
.\.venv314\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

Rev is currently validated with Python 3.14.

Run the app:

```powershell
python Run.py
```

Run checks:

```powershell
node --check speech_translate/web/app.js
.\.venv314\Scripts\python.exe -m py_compile Run.py speech_translate/__main__.py speech_translate/webview_app.py speech_translate/web_bridge_api.py
.\.venv314\Scripts\python.exe -m build
.\.venv314\Scripts\python.exe -m unittest discover -s test -p app_tray_test.py
.\.venv314\Scripts\python.exe -m unittest discover -s test -p app_startup_controller_test.py
.\.venv314\Scripts\python.exe -m unittest discover -s test -p web_ui_preview_sync_test.py
.\.venv314\Scripts\python.exe -m unittest discover -s test -p web_settings_contract_test.py
.\.venv314\Scripts\python.exe -m unittest discover -s test -p runtime_registry_test.py
```

## Pull Requests

- Keep changes focused and explain the user-visible behavior.
- Add or update tests for controller, runtime, WebView bridge, and settings behavior when relevant.
- Preserve the existing `speech_translate` Python import package name unless a dedicated migration is planned.
- Do not commit user state, logs, cache folders, virtual environments, temporary screenshots, or local shortcuts.
- Respect the original MIT attribution and third-party license files.

## UI Changes

For Web UI work, compare against `speech_translate/web/ui-preview.html` and the design guidance in `docs/ui-design-guide.md`. Prefer consistent components and shared CSS over local one-off styles.
