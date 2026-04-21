from huggingface_hub.utils import tqdm
from huggingface_hub import snapshot_download
import sys

class CustomTqdm(tqdm):
    def update(self, n=1):
        super().update(n)
        if self.total:
            print(f"Progress: {self.n} / {self.total} ({(self.n / self.total) * 100:.2f}%)")

from unittest.mock import patch

with patch('huggingface_hub.utils._progress.tqdm', CustomTqdm):
    # or huggingface_hub.file_download.tqdm
    # or huggingface_hub.utils.tqdm
    snapshot_download(repo_id="Systran/faster-whisper-tiny", local_files_only=False)
