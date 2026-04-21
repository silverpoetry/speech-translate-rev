import sys
from huggingface_hub import snapshot_download
import tqdm.auto

class DummyBridge:
    def update_task_message(self, msg):
        pass
    def update_task_progress(self, percent):
        sys.stdout.write(f"\rProgress: {percent:.2f}%")
        sys.stdout.flush()

def get_custom_tqdm_class(bridge, model_name: str):
    class CustomTqdm(tqdm.auto.tqdm):
        _global_total = 0
        _global_n = 0
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if self.total is not None:
                CustomTqdm._global_total += self.total
                
        def update(self, n=1):
            super().update(n)
            CustomTqdm._global_n += n
            if bridge:
                percent = (CustomTqdm._global_n / CustomTqdm._global_total * 100) if CustomTqdm._global_total > 0 else 0
                bridge.update_task_progress(percent)
                
    return CustomTqdm

bridge = DummyBridge()
tcls = get_custom_tqdm_class(bridge, "tiny")
snapshot_download(repo_id="Systran/faster-whisper-tiny", local_files_only=False, resume_download=True, tqdm_class=tcls)
