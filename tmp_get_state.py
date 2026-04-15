import importlib, sys
importlib.invalidate_caches()
from speech_translate.webview_app import WebBridge
wb = WebBridge()
state = wb.get_state()
print('enable_initial_prompt in get_state:', state['settings'].get('enable_initial_prompt'))
print('initial_prompts_map in get_state:', state['settings'].get('initial_prompts_map'))
