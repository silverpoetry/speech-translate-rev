from typing import Optional

# Default initial prompts per whisper language code (two-letter codes).
# These are conservative instructions to prefer punctuation and plain-text output.
DEFAULT_INITIAL_PROMPTS = {
    "en": "Transcribe the following audio in English. Use proper punctuation. Do not include timestamps, speaker labels, or extra commentary. Output plain text only.",
    "zh": "你好，欢迎！请问今天有什么安排？",
    "ja": "次の音声を日本語で文字起こししてください。句読点を付け、タイムスタンプや話者名、余計な注釈は付けず、テキストのみを出力してください。",
    "ko": "다음 오디오를 한국어로 전사하세요. 문장부호를 사용하고, 타임스탬프나 화자명, 불필요한 설명은 포함하지 마세요. 텍스트만 출력하세요。",
    "es": "Transcribe the following audio in Spanish. Use punctuation and output plain text only without timestamps or speaker labels.",
    "fr": "Transcribe the following audio in French. Use punctuation and output plain text only without timestamps or speaker labels.",
    "de": "Transcribe the following audio in German. Use punctuation and output plain text only without timestamps or speaker labels.",
    "pt": "Transcribe the following audio in Portuguese. Use punctuation and output plain text only without timestamps or speaker labels.",
    "id": "Transcribe the following audio in Indonesian. Use punctuation and output plain text only without timestamps or speaker labels.",
}


def get_initial_prompt(lang_code: Optional[str]) -> Optional[str]:
    """Return an initial prompt for the given whisper language code.

    lang_code: whisper language code like 'en', 'zh', 'ja', etc.
    Returns None when no default prompt is available.
    """
    if not lang_code:
        return None
    lc = str(lang_code).lower()
    # direct match
    if lc in DEFAULT_INITIAL_PROMPTS:
        return DEFAULT_INITIAL_PROMPTS[lc]
    # try prefix (e.g., 'zh-cn' -> 'zh')
    if "-" in lc:
        base = lc.split("-", 1)[0]
        if base in DEFAULT_INITIAL_PROMPTS:
            return DEFAULT_INITIAL_PROMPTS[base]
    return None


def pick_initial_prompt(
    lang_code: Optional[str],
    enabled: bool = False,
    user_map: Optional[dict] = None,
    global_prompt: Optional[str] = None,
) -> Optional[str]:
    """Choose the effective initial prompt considering user settings and defaults.

    Priority when enabled=True:
    1. user_map exact match for lang_code (or base prefix like 'zh' for 'zh-CN')
    2. global_prompt (if provided)
    3. built-in default from DEFAULT_INITIAL_PROMPTS via get_initial_prompt

    If enabled=False, returns None (prompts disabled globally).
    """
    if not enabled:
        return None

    user_map = user_map or {}
    if not lang_code:
        # No language information: prefer explicit global_prompt then nothing
        return str(global_prompt) if global_prompt else None

    lc = str(lang_code).lower()

    # Exact user map match
    if lc in user_map and user_map[lc]:
        return str(user_map[lc])

    # Prefix match (e.g., 'zh-cn' -> 'zh')
    if "-" in lc:
        base = lc.split("-", 1)[0]
        if base in user_map and user_map[base]:
            return str(user_map[base])

    # Fallback to explicit global prompt
    if global_prompt:
        return str(global_prompt)

    # Finally fall back to built-in defaults
    return get_initial_prompt(lang_code)
