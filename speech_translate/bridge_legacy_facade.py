from __future__ import annotations

from threading import Lock, Thread
from typing import List, Literal, Optional

from speech_translate.utils.types import ToInsert


class BridgeVisualLegacyMixin:
    @property
    def cuda(self) -> str:
        return self.visual.cuda

    @cuda.setter
    def cuda(self, value: str) -> None:
        self.visual.cuda = value

    @property
    def running_after_id(self) -> str:
        return self.visual.running_after_id

    @running_after_id.setter
    def running_after_id(self, value: str) -> None:
        self.visual.running_after_id = value

    @property
    def bg_color(self) -> str:
        return self.visual.bg_color

    @bg_color.setter
    def bg_color(self, value: str) -> None:
        self.visual.bg_color = value

    @property
    def fg_color(self) -> str:
        return self.visual.fg_color

    @fg_color.setter
    def fg_color(self, value: str) -> None:
        self.visual.fg_color = value

    @property
    def has_ffmpeg(self) -> bool:
        return self.visual.has_ffmpeg

    @has_ffmpeg.setter
    def has_ffmpeg(self, value: bool) -> None:
        self.visual.has_ffmpeg = bool(value)

    @property
    def web_bridge(self):
        return self.visual.web_bridge

    @web_bridge.setter
    def web_bridge(self, value) -> None:
        self.visual.web_bridge = value


class BridgeFileLegacyMixin:
    @property
    def file_processing(self) -> bool:
        return self.file_runtime.file_processing

    @file_processing.setter
    def file_processing(self, value: bool) -> None:
        self.file_runtime.file_processing = bool(value)

    @property
    def transcribing_file(self) -> bool:
        return self.file_runtime.transcribing_file

    @transcribing_file.setter
    def transcribing_file(self, value: bool) -> None:
        self.file_runtime.transcribing_file = bool(value)

    @property
    def translating_file(self) -> bool:
        return self.file_runtime.translating_file

    @translating_file.setter
    def translating_file(self, value: bool) -> None:
        self.file_runtime.translating_file = bool(value)

    @property
    def file_tced_counter(self) -> int:
        return self.file_runtime.file_tced_counter

    @file_tced_counter.setter
    def file_tced_counter(self, value: int) -> None:
        self.file_runtime.file_tced_counter = int(value)

    @property
    def file_tled_counter(self) -> int:
        return self.file_runtime.file_tled_counter

    @file_tled_counter.setter
    def file_tled_counter(self, value: int) -> None:
        self.file_runtime.file_tled_counter = int(value)

    @property
    def mod_file_counter(self) -> int:
        return self.file_runtime.mod_file_counter

    @mod_file_counter.setter
    def mod_file_counter(self, value: int) -> None:
        self.file_runtime.mod_file_counter = int(value)

    def enable_file_process(self):
        self.file_processing = True

    def disable_file_process(self):
        self.file_processing = False

    def enable_file_tc(self):
        self.transcribing_file = True

    def disable_file_tc(self):
        self.transcribing_file = False

    def enable_file_tl(self):
        self.translating_file = True

    def disable_file_tl(self):
        self.translating_file = False


class BridgeDownloadLegacyMixin:
    @property
    def dl_thread(self) -> Optional[Thread]:
        return self.download.dl_thread

    @dl_thread.setter
    def dl_thread(self, value: Optional[Thread]) -> None:
        self.download.dl_thread = value

    @property
    def cancel_dl(self) -> bool:
        return self.download.cancel_dl

    @cancel_dl.setter
    def cancel_dl(self, value: bool) -> None:
        self.download.cancel_dl = bool(value)


class BridgeRecordingLegacyMixin:
    @property
    def rec_tc_thread(self) -> Optional[Thread]:
        return self.recording_runtime.rec_tc_thread

    @rec_tc_thread.setter
    def rec_tc_thread(self, value: Optional[Thread]) -> None:
        self.recording_runtime.rec_tc_thread = value

    @property
    def rec_tl_thread(self) -> Optional[Thread]:
        return self.recording_runtime.rec_tl_thread

    @rec_tl_thread.setter
    def rec_tl_thread(self, value: Optional[Thread]) -> None:
        self.recording_runtime.rec_tl_thread = value

    @property
    def recording(self) -> bool:
        return self.recording_runtime.recording

    @recording.setter
    def recording(self, value: bool) -> None:
        self.recording_runtime.recording = bool(value)

    @property
    def stream(self) -> Optional[object]:
        return self.recording_runtime.stream

    @stream.setter
    def stream(self, value: Optional[object]) -> None:
        self.recording_runtime.stream = value

    @property
    def data_queue(self):
        return self.recording_runtime.data_queue

    @data_queue.setter
    def data_queue(self, value) -> None:
        self.recording_runtime.data_queue = value

    @property
    def current_rec_status(self) -> str:
        return self.recording_runtime.current_rec_status

    @current_rec_status.setter
    def current_rec_status(self, value: str) -> None:
        self.recording_runtime.current_rec_status = value

    @property
    def tc_lock(self) -> Optional[Lock]:
        return self.recording_runtime.tc_lock

    @tc_lock.setter
    def tc_lock(self, value: Optional[Lock]) -> None:
        self.recording_runtime.tc_lock = value

    def enable_rec(self):
        self.recording = True

    def disable_rec(self):
        self.recording = False


class BridgeLiveTextLegacyMixin:
    @property
    def auto_detected_lang(self) -> str:
        return self.live_text.auto_detected_lang

    @auto_detected_lang.setter
    def auto_detected_lang(self, value: str) -> None:
        self.live_text.auto_detected_lang = value

    @property
    def tc_sentences(self) -> List:
        return self.live_text.tc_sentences

    @tc_sentences.setter
    def tc_sentences(self, value: List) -> None:
        self.live_text.tc_sentences = list(value)

    @property
    def tl_sentences(self) -> List:
        return self.live_text.tl_sentences

    @tl_sentences.setter
    def tl_sentences(self, value: List) -> None:
        self.live_text.tl_sentences = list(value)

    def insert_to_mw(self, text: str, mode: Literal["tc", "tl"], separator: str):
        self.live_text_renderer.append_incremental_text(self.web_bridge, text=text, mode=mode, separator=separator)

    def update_result_display(
        self, total_len: int, res_with_conf: List[ToInsert], mode: Literal["mw_tc", "ex_tc", "mw_tl", "ex_tl"]
    ):
        self.live_text_renderer.update_result_display(
            self.web_bridge,
            total_len=total_len,
            result_items=res_with_conf,
            mode=mode,
            fg_color=self.fg_color,
        )

    def map_result_lists(self, source_list, store_list: List[ToInsert], separator: str):
        return self.live_text_renderer.map_result_lists(source_list, store_list, separator)

    def swap_textbox(self):
        separator = self.live_text_renderer.separator_html()
        self.tc_sentences, self.tl_sentences = self.tl_sentences, self.tc_sentences
        self.update_tc(None, separator)
        self.update_tl(None, separator)

    def update_tc(self, new_res, separator: str):
        self.live_text_renderer.update_stream(
            self.web_bridge,
            mode="tc",
            sentences=self.tc_sentences,
            new_result=new_res,
            separator=separator,
            fg_color=self.fg_color,
        )

    def update_tl(self, new_res, separator: str):
        self.live_text_renderer.update_stream(
            self.web_bridge,
            mode="tl",
            sentences=self.tl_sentences,
            new_result=new_res,
            separator=separator,
            fg_color=self.fg_color,
        )

    def clear_mw_tc(self):
        self.live_text_renderer.clear_target(self.web_bridge, "main_transcribed")

    def clear_mw_tl(self):
        self.live_text_renderer.clear_target(self.web_bridge, "main_translated")

    def clear_ex_tc(self):
        self.live_text_renderer.clear_target(self.web_bridge, "detached_transcribed")

    def clear_ex_tl(self):
        self.live_text_renderer.clear_target(self.web_bridge, "detached_translated")

    def clear_all(self):
        self.tc_sentences = []
        self.tl_sentences = []
        self.clear_mw_tc()
        self.clear_mw_tl()
        self.clear_ex_tc()
        self.clear_ex_tl()


class BridgeLegacyFacade(
    BridgeVisualLegacyMixin,
    BridgeFileLegacyMixin,
    BridgeDownloadLegacyMixin,
    BridgeRecordingLegacyMixin,
    BridgeLiveTextLegacyMixin,
):
    pass


__all__ = [
    "BridgeVisualLegacyMixin",
    "BridgeFileLegacyMixin",
    "BridgeDownloadLegacyMixin",
    "BridgeRecordingLegacyMixin",
    "BridgeLiveTextLegacyMixin",
    "BridgeLegacyFacade",
]
