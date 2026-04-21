import sys
from huggingface_hub import snapshot_download
import tqdm.auto
from unittest.mock import patch

class CustomTqdm(tqdm.auto.tqdm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._prev_n = 0
        
    def update(self, n=1):
        super().update(n)
        print(f"Desc: {self.desc}, Total: {self.total}, Current: {self.n}")

with patch('tqdm.auto.tqdm', CustomTqdm):
    # or huggingface_hub.utils._progress.tqdm
    # or huggingface_hub.file_download.tqdm
    try:
        with patch('huggingface_hub.utils.tqdm', CustomTqdm):
            snapshot_download(repo_id="Systran/faster-whisper-tiny", local_files_only=False, resume_download=True)
    except:
        pass
