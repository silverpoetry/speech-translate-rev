import huggingface_hub

api = huggingface_hub.HfApi()
repo_info = api.repo_info(repo_id="Systran/faster-whisper-tiny", repo_type="model")

allow_patterns = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
filtered = list(huggingface_hub.utils.filter_repo_objects(
    items=[f.rfilename for f in repo_info.siblings],
    allow_patterns=allow_patterns,
    ignore_patterns=None
))

total_size = sum(f.size for f in repo_info.siblings if f.rfilename in filtered and f.size is not None)
print("Files:", filtered)
print("Total size:", total_size)
