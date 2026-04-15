import importlib, sys, os
importlib.invalidate_caches()
from speech_translate import linker
print('before enable:', linker.sj.cache.get('enable_initial_prompt'))
linker.sj.save_key('enable_initial_prompt', True)
print('after enable:', linker.sj.cache.get('enable_initial_prompt'))
linker.sj.save_key('initial_prompts_map', {'en':'__test_prompt__'})
print('after map:', linker.sj.cache.get('initial_prompts_map'))
print('setting file exists:', os.path.exists(linker.sj.setting_path))
with open(linker.sj.setting_path, 'r', encoding='utf-8') as f:
    content = f.read()
print('setting snippet:', content[-400:])
