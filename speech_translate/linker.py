from ast import literal_eval
from copy import deepcopy
from platform import system
import re
from shlex import quote
from threading import Lock, Thread
from typing import Any, List, Literal, Optional, Union

# 本地依赖 (请确保这些导入路径在你的项目中仍然有效)
from speech_translate.utils.helper import generate_color, str_separator_to_html, wrap_result
from speech_translate.utils.types import ToInsert
from ._path import dir_debug, dir_export, dir_log, dir_temp, dir_user, p_app_icon, p_app_settings
from .utils.setting import SettingJson

if system() == "Windows":
    from multiprocessing import Queue
    import pyaudiowpatch as pyaudio  # type: ignore # pylint: disable=import-error
else:
    import pyaudio  # type: ignore # pylint: disable=import-error
    # to get qsize on platform other than windows
    from .utils.custom.queue import MyQueue as Queue

# ------------------ #
sj: SettingJson = SettingJson(p_app_settings, [dir_user, dir_temp, dir_log, dir_export, dir_debug], p_app_icon)


class BridgeClass:
    """
    Class containing all references needed to avoid circular import.
    Acts as the central state manager and data bridge for the Web UI.
    """
    def __init__(self):
        self.cuda: str = ""
        self.running_after_id: str = ""
        self.bg_color: str = ""
        self.fg_color: str = ""
        self.has_ffmpeg = False

        # file processing states
        self.file_processing: bool = False
        self.transcribing_file: bool = False
        self.translating_file: bool = False

        # record states
        self.rec_tc_thread: Optional[Thread] = None
        self.rec_tl_thread: Optional[Thread] = None
        self.recording: bool = False

        # model download states
        self.dl_thread: Optional[Thread] = None
        self.cancel_dl: bool = False

        # web ui bridge (由启动Web服务的入口注入)
        self.web_bridge = None

        # stream / transcribe variables
        self.stream: Optional[pyaudio.Stream] = None
        self.data_queue = Queue()
        self.current_rec_status: str = ""
        self.auto_detected_lang: str = "~"
        self.tc_lock: Optional[Lock] = None
        
        # Core data storage for realtime text
        self.tc_sentences: List = []
        self.tl_sentences: List = []

        # file process counters
        self.file_tced_counter: int = 0
        self.file_tled_counter: int = 0
        self.mod_file_counter: int = 0

    def enable_rec(self):
        self.recording = True

    def disable_rec(self):
        self.recording = False

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

    def insert_to_mw(self, text: str, mode: Literal["tc", "tl"], separator: str):
        """直接将增量文本通过 Web Bridge 推送给前端"""
        if self.web_bridge is not None:
            target = "main_transcribed" if mode == "tc" else "main_translated"
            self.web_bridge.append_live_text(target, text, separator)
            # 如果前端还有悬浮窗/分离窗口的需求，可以保留这两个目标推送
            detached_target = "detached_transcribed" if mode == "tc" else "detached_translated"
            self.web_bridge.append_live_text(detached_target, text, separator)

    def update_result_display(
        self, total_len: int, res_with_conf: List[ToInsert], mode: Literal["mw_tc", "ex_tc", "mw_tl", "ex_tl"]
    ):
        """
        根据信心值计算颜色，生成带样式的 HTML 字符串，并通过 Web Bridge 发送给前端渲染。
        """
        copied_res = deepcopy(res_with_conf)

        # 长度限制处理：如果超过了设定的最大字符数，从头部裁剪旧文本
        if sj.cache.get(f"tb_{mode}_limit_max") and total_len > sj.cache.get(f"tb_{mode}_max", 0):
            over_for = total_len - sj.cache.get(f"tb_{mode}_max")  # type: ignore
            index = 0

            while over_for > 0 and index < len(copied_res):
                temp = copied_res[index]["text"]
                delete_for = len(temp) if over_for > len(temp) else over_for
                over_for -= delete_for
                temp = temp[delete_for:]
                copied_res[index]["text"] = temp
                index += 1

        # 换行处理
        if sj.cache.get(f"tb_{mode}_limit_max_per_line"):
            copied_res = wrap_result(copied_res, sj.cache.get(f"tb_{mode}_max_per_line", 0))

        # 组装带颜色的 <span> 标签
        to_insert = ""
        for res in copied_res:
            temp = res["text"]
            if sj.cache.get(f"tb_{mode}_use_conf_color", False):
                color = res["color"]
            else:
                color = sj.cache.get(f"tb_{mode}_font_color", None)

            if color is None:
                color = self.fg_color or "#000000"

            to_insert += f"""<span style="color: {color}">{temp}</span>"""

        # 外层容器样式
        insert = f"""<div style='font-family: {sj.cache.get(f"tb_{mode}_font")}; text-align: left;
                    font-size: {sj.cache.get(f"tb_{mode}_font_size")}px; background-color: transparent;
                    font-weight: {"bold" if sj.cache.get(f"tb_{mode}_font_bold") else "normal"};'>
                        {to_insert}
                    </div>"""

        # 通过 WebSocket 推送完整 HTML 到前端对应的 ID 容器
        if self.web_bridge is not None:
            bridge_target = {
                "mw_tc": "main_transcribed_html",
                "mw_tl": "main_translated_html",
                "ex_tc": "detached_transcribed_html",
                "ex_tl": "detached_translated_html",
            }.get(mode)
            
            if bridge_target is not None:
                self.web_bridge.update_live_html(bridge_target, insert)

    def map_result_lists(self, source_list, store_list: List[ToInsert], separator: str):
        """
        遍历 Whisper 结果，根据信心值(Confidence)映射颜色配置，返回总字符长度。
        """
        total_len = 0
        low_color = sj.cache["gradient_low_conf"]
        high_color = sj.cache["gradient_high_conf"]

        for sentence in source_list:
            if isinstance(sentence, str):
                sentence = sentence.strip() + separator
                total_len += len(sentence)
                store_list.append({"text": sentence, "color": None, "is_last": None})
                
            elif sj.cache["colorize_per_segment"]:
                for segment in sentence.segments:
                    temp = segment.text.lstrip() if segment.id == 0 else segment.text
                    confidence_total_word = sum(word.probability for word in segment.words)
                    word_len = len(segment.words) if len(segment.words) != 0 else 1
                    confidence = confidence_total_word / word_len

                    store_list.append({
                        "text": temp,
                        "color": generate_color(confidence, low_color, high_color),
                        "is_last": None
                    })
                    total_len += len(temp)
                
                # 为该句子的最后一个片段加上分隔符
                if store_list:
                    store_list[-1]["text"] += separator
                    
            elif sj.cache["colorize_per_word"]:
                for segment in sentence.segments:
                    for word in segment.words:
                        temp = word.word.lstrip() if word.id == 0 else word.word
                        store_list.append({
                            "text": temp,
                            "color": generate_color(word.probability, low_color, high_color),
                            "is_last": None
                        })
                        total_len += len(temp)
                if store_list:
                    store_list[-1]["text"] += separator
                    
            else:
                temp = sentence.text.strip() + separator
                total_len += len(sentence)
                store_list.append({"text": temp, "color": None, "is_last": None})

        return total_len

    def swap_textbox(self):
        """如果前端需要交换原文和译文的显示内容，可以通过此方法对调数据并重新推送"""
        separator = str_separator_to_html(literal_eval(quote(sj.cache["separate_with"])))
        self.tc_sentences, self.tl_sentences = self.tl_sentences, self.tc_sentences
        self.update_tc(None, separator)
        self.update_tl(None, separator)

    def update_tc(self, new_res, separator: str):
        """刷新转录文本 (Transcription)"""
        res_with_conf: List[ToInsert] = []
        total_len = self.map_result_lists(self.tc_sentences, res_with_conf, separator)
        if new_res is not None:
            total_len += self.map_result_lists([new_res], res_with_conf, separator)
            
        self.update_result_display(total_len, res_with_conf, "mw_tc")
        self.update_result_display(total_len, res_with_conf, "ex_tc")

    def update_tl(self, new_res, separator: str):
        """刷新翻译文本 (Translation)"""
        res_with_conf: List[ToInsert] = []
        total_len = self.map_result_lists(self.tl_sentences, res_with_conf, separator)
        if new_res is not None:
            total_len += self.map_result_lists([new_res], res_with_conf, separator)
            
        self.update_result_display(total_len, res_with_conf, "mw_tl")
        self.update_result_display(total_len, res_with_conf, "ex_tl")

    def clear_mw_tc(self):
        if self.web_bridge is not None:
            self.web_bridge.clear_live("main_transcribed")

    def clear_mw_tl(self):
        if self.web_bridge is not None:
            self.web_bridge.clear_live("main_translated")

    def clear_ex_tc(self):
        if self.web_bridge is not None:
            self.web_bridge.clear_live("detached_transcribed")

    def clear_ex_tl(self):
        if self.web_bridge is not None:
            self.web_bridge.clear_live("detached_translated")

    def clear_all(self):
        """一键清空历史数据并通知前端清屏"""
        self.tc_sentences = []
        self.tl_sentences = []
        self.clear_mw_tc()
        self.clear_mw_tl()
        self.clear_ex_tc()
        self.clear_ex_tl()


# ------------------ #
bc = BridgeClass()