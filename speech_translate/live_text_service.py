from __future__ import annotations

from ast import literal_eval
from copy import deepcopy
from dataclasses import dataclass
from shlex import quote
from typing import Literal, Mapping, Protocol

from speech_translate.utils.helper import generate_color, str_separator_to_html, wrap_result
from speech_translate.utils.types import ToInsert

LiveTextMode = Literal["mw_tc", "ex_tc", "mw_tl", "ex_tl"]
LiveTextStream = Literal["tc", "tl"]


class LiveTextBridge(Protocol):
    def append_live_text(self, target: str, text: str, separator: str = "") -> None:
        ...

    def update_live_html(self, target: str, html: str) -> None:
        ...

    def clear_live(self, prefix: str = "") -> None:
        ...


class LiveTextSettingsStore(Protocol):
    cache: Mapping[str, object]


@dataclass(frozen=True)
class LiveTextRenderer:
    settings: LiveTextSettingsStore

    def _cache(self) -> Mapping[str, object]:
        return self.settings.cache

    def separator_html(self) -> str:
        return str_separator_to_html(literal_eval(quote(str(self._cache()["separate_with"]))))

    def append_incremental_text(
        self,
        bridge: LiveTextBridge | None,
        *,
        text: str,
        mode: LiveTextStream,
        separator: str,
    ) -> None:
        if bridge is None:
            return

        target = "main_transcribed" if mode == "tc" else "main_translated"
        bridge.append_live_text(target, text, separator)
        detached_target = "detached_transcribed" if mode == "tc" else "detached_translated"
        bridge.append_live_text(detached_target, text, separator)

    def map_result_lists(self, source_list, store_list: list[ToInsert], separator: str) -> int:
        total_len = 0
        cache = self._cache()
        low_color = str(cache["gradient_low_conf"])
        high_color = str(cache["gradient_high_conf"])

        for sentence in source_list:
            if isinstance(sentence, str):
                sentence = sentence.strip() + separator
                total_len += len(sentence)
                store_list.append({"text": sentence, "color": None, "is_last": None})
                continue

            if bool(cache["colorize_per_segment"]):
                for segment in sentence.segments:
                    temp = segment.text.lstrip() if segment.id == 0 else segment.text
                    confidence_total_word = sum(word.probability for word in segment.words)
                    word_len = len(segment.words) if len(segment.words) != 0 else 1
                    confidence = confidence_total_word / word_len
                    store_list.append(
                        {
                            "text": temp,
                            "color": generate_color(confidence, low_color, high_color),
                            "is_last": None,
                        }
                    )
                    total_len += len(temp)

                if store_list:
                    store_list[-1]["text"] += separator
                continue

            if bool(cache["colorize_per_word"]):
                for segment in sentence.segments:
                    for word in segment.words:
                        temp = word.word.lstrip() if word.id == 0 else word.word
                        store_list.append(
                            {
                                "text": temp,
                                "color": generate_color(word.probability, low_color, high_color),
                                "is_last": None,
                            }
                        )
                        total_len += len(temp)
                if store_list:
                    store_list[-1]["text"] += separator
                continue

            temp = sentence.text.strip() + separator
            total_len += len(sentence)
            store_list.append({"text": temp, "color": None, "is_last": None})

        return total_len

    def _render_result_html(
        self,
        *,
        total_len: int,
        result_items: list[ToInsert],
        mode: LiveTextMode,
        fg_color: str,
    ) -> str:
        cache = self._cache()
        copied_res = deepcopy(result_items)

        if cache.get(f"tb_{mode}_limit_max") and total_len > cache.get(f"tb_{mode}_max", 0):
            over_for = total_len - cache.get(f"tb_{mode}_max", 0)
            index = 0
            while over_for > 0 and index < len(copied_res):
                temp = copied_res[index]["text"]
                delete_for = len(temp) if over_for > len(temp) else over_for
                over_for -= delete_for
                copied_res[index]["text"] = temp[delete_for:]
                index += 1

        if cache.get(f"tb_{mode}_limit_max_per_line"):
            copied_res = wrap_result(copied_res, int(cache.get(f"tb_{mode}_max_per_line", 0)))

        to_insert = ""
        for result in copied_res:
            temp = result["text"]
            color = result["color"] if cache.get(f"tb_{mode}_use_conf_color", False) else cache.get(f"tb_{mode}_font_color")
            if color is None:
                color = fg_color or "#000000"
            to_insert += f"""<span style="color: {color}">{temp}</span>"""

        return f"""<div style='font-family: {cache.get(f"tb_{mode}_font")}; text-align: left;
                    font-size: {cache.get(f"tb_{mode}_font_size")}px; background-color: transparent;
                    font-weight: {"bold" if cache.get(f"tb_{mode}_font_bold") else "normal"};'>
                        {to_insert}
                    </div>"""

    def update_result_display(
        self,
        bridge: LiveTextBridge | None,
        *,
        total_len: int,
        result_items: list[ToInsert],
        mode: LiveTextMode,
        fg_color: str,
    ) -> None:
        if bridge is None:
            return

        bridge_target = {
            "mw_tc": "main_transcribed_html",
            "mw_tl": "main_translated_html",
            "ex_tc": "detached_transcribed_html",
            "ex_tl": "detached_translated_html",
        }.get(mode)
        if bridge_target is None:
            return

        bridge.update_live_html(
            bridge_target,
            self._render_result_html(
                total_len=total_len,
                result_items=result_items,
                mode=mode,
                fg_color=fg_color,
            ),
        )

    def update_stream(
        self,
        bridge: LiveTextBridge | None,
        *,
        mode: LiveTextStream,
        sentences: list[object],
        new_result: object | None,
        separator: str,
        fg_color: str,
    ) -> None:
        result_items: list[ToInsert] = []
        total_len = self.map_result_lists(sentences, result_items, separator)
        if new_result is not None:
            total_len += self.map_result_lists([new_result], result_items, separator)

        if mode == "tc":
            self.update_result_display(
                bridge,
                total_len=total_len,
                result_items=result_items,
                mode="mw_tc",
                fg_color=fg_color,
            )
            self.update_result_display(
                bridge,
                total_len=total_len,
                result_items=result_items,
                mode="ex_tc",
                fg_color=fg_color,
            )
            return

        self.update_result_display(
            bridge,
            total_len=total_len,
            result_items=result_items,
            mode="mw_tl",
            fg_color=fg_color,
        )
        self.update_result_display(
            bridge,
            total_len=total_len,
            result_items=result_items,
            mode="ex_tl",
            fg_color=fg_color,
        )

    def clear_target(self, bridge: LiveTextBridge | None, prefix: str) -> None:
        if bridge is not None:
            bridge.clear_live(prefix)
