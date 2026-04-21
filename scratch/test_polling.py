import os
import huggingface_hub
from huggingface_hub.file_download import repo_folder_name
import time
from threading import Thread

def measure_hf_progress(repo_id, cache_dir):
    api = huggingface_hub.HfApi()
    repo_info = api.repo_info(repo_id=repo_id, repo_type="model", files_metadata=True)
    
    allow_patterns = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
    filtered = list(huggingface_hub.utils.filter_repo_objects(
        items=[f.rfilename for f in repo_info.siblings],
        allow_patterns=allow_patterns,
        ignore_patterns=None
    ))
    total_size = sum(f.size for f in repo_info.siblings if f.rfilename in filtered and f.size is not None)
    
    storage_folder = os.path.join(cache_dir, repo_folder_name(repo_id=repo_id, repo_type="model"))
    
    def get_current_size():
        size = 0
        if not os.path.exists(storage_folder):
            return 0
        for root, dirs, files in os.walk(storage_folder):
            # Don't double count snapshots (they are symlinks to blobs)
            if 'snapshots' in root.split(os.sep) or 'refs' in root.split(os.sep):
                continue
            for f in files:
                filepath = os.path.join(root, f)
                if not os.path.islink(filepath):
                    size += os.path.getsize(filepath)
        return size

    print("Total size expected:", total_size)
    finished = False
    
    def dl():
        nonlocal finished
        huggingface_hub.snapshot_download(repo_id, cache_dir=cache_dir, allow_patterns=allow_patterns, local_files_only=False, resume_download=True)
        finished = True
        
    Thread(target=dl).start()
    
    while not finished:
        print(f"Current size: {get_current_size()} / {total_size}")
        time.sleep(0.5)

measure_hf_progress("Systran/faster-whisper-tiny", "D:\\test_cache")
