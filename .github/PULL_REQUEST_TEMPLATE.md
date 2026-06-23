## Summary

- 

## Verification

- [ ] `node --check speech_translate/web/app.js`
- [ ] `.\.venv314\Scripts\python.exe -m py_compile Run.py speech_translate/__main__.py speech_translate/webview_app.py speech_translate/web_bridge_api.py`
- [ ] `.\.venv314\Scripts\python.exe -m unittest discover -s test -p app_tray_test.py`
- [ ] `.\.venv314\Scripts\python.exe -m unittest discover -s test -p app_startup_controller_test.py`
- [ ] `.\.venv314\Scripts\python.exe -m unittest discover -s test -p web_ui_preview_sync_test.py`
- [ ] `.\.venv314\Scripts\python.exe -m unittest discover -s test -p web_settings_contract_test.py`
- [ ] `.\.venv314\Scripts\python.exe -m unittest discover -s test -p runtime_registry_test.py`

## Notes

Mention UI screenshots, migration concerns, release notes, or follow-up work.
