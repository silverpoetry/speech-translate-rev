import json
from dataclasses import dataclass
from datetime import datetime
from os import makedirs, path
from threading import Thread
from time import gmtime, sleep, strftime, time
from typing import Callable, Dict, List, Literal, Mapping
import os

import stable_whisper
from torch import cuda
from whisper.tokenizer import TO_LANGUAGE_CODE

from speech_translate._logging import logger
from speech_translate._path import dir_alignment, dir_export, dir_refinement, dir_translate
from speech_translate.linker import bc, sj
from speech_translate.utils.translate.language import get_whisper_lang_name, get_whisper_lang_similar

from ..helper import filename_only, get_proxies, kill_thread, start_file
from ..translate.translator import translate
from ..whisper.helper import get_hallucination_filter, get_task_format, model_values, to_language_name
from ..whisper.load import get_model, get_model_args, get_tc_args
from ..whisper.result import remove_segments_by_str, split_res
from ..whisper.save import save_output_stable_ts

# =========================================================================
# GLOBAL STATE & DECOUPLED UI SYNC
# =========================================================================

status_tc: Dict[int, str] = {}
status_tl: Dict[int, str] = {}
status_mod: Dict[int, str] = {}
ACTIVE_STATUSES = {"Waiting", "Transcribing please wait...", "Translating please wait...", "Processing", "Re-transcribing..."}

# 全局任务类型标记，帮助组合状态
GLOBAL_IS_TC = False
GLOBAL_IS_TL = False
GLOBAL_IS_MOD = False

StatusMap = Dict[int, str]


@dataclass
class WorkerFailure:
    failed: bool = False
    error: Exception | None = None

    def capture(self, exc: Exception) -> None:
        self.failed = True
        self.error = exc

    def raise_if_failed(self) -> None:
        if self.failed:
            raise self.error or RuntimeError("Unknown worker failure")


def _build_combined_status(
    index: int,
    *,
    is_tc: bool,
    is_tl: bool,
    is_mod: bool,
    tc_status: Mapping[int, str],
    tl_status: Mapping[int, str],
    mod_status: Mapping[int, str],
) -> str:
    parts: list[str] = []
    if is_tc:
        current = tc_status.get(index, "Waiting")
        if current and current != "Waiting":
            parts.append(current)
    if is_tl:
        current = tl_status.get(index, "Waiting")
        if current and current != "Waiting":
            parts.append(current)
    if is_mod:
        current = mod_status.get(index, "Waiting")
        if current and current != "Waiting":
            parts.append(current)
    return ", ".join(parts) if parts else "Waiting"


def _is_file_status_completed(
    index: int,
    combined_status: str,
    *,
    is_tc: bool,
    is_tl: bool,
    is_mod: bool,
    tc_status: Mapping[int, str],
    tl_status: Mapping[int, str],
    mod_status: Mapping[int, str],
) -> bool:
    lower_status = combined_status.lower()
    if "fail" in lower_status or "error" in lower_status or "parse error" in lower_status:
        return True
    if is_tc and is_tl:
        return "transcribed" in tc_status.get(index, "").lower() and "translated" in tl_status.get(index, "").lower()
    if is_tc:
        return "transcribed" in tc_status.get(index, "").lower()
    if is_tl:
        return "translated" in tl_status.get(index, "").lower()
    if is_mod:
        mod_value = mod_status.get(index, "").lower()
        return "refined" in mod_value or "aligned" in mod_value or "translated" in mod_value
    return False

def _sync_ui(index: int):
    """自动判断文件是否处理完成，并把最纯粹的状态文字抛给 webview_app 去渲染"""
    if not bc.web_bridge: return
    combined_status = _build_combined_status(
        index,
        is_tc=GLOBAL_IS_TC,
        is_tl=GLOBAL_IS_TL,
        is_mod=GLOBAL_IS_MOD,
        tc_status=status_tc,
        tl_status=status_tl,
        mod_status=status_mod,
    )
    is_completed = _is_file_status_completed(
        index,
        combined_status,
        is_tc=GLOBAL_IS_TC,
        is_tl=GLOBAL_IS_TL,
        is_mod=GLOBAL_IS_MOD,
        tc_status=status_tc,
        tl_status=status_tl,
        mod_status=status_mod,
    )

    # 抛给前端桥接器去处理 UI 细节
    bc.web_bridge.sync_file_status(index, combined_status, is_completed)

def _update_status(status_map: StatusMap, index: int, msg: str):
    """修改状态并触发 UI 同步（带防崩溃保护）"""
    status_map[index] = msg
    # 🛡️ 修复点：绝对的防弹保护，永远不能让 UI 更新报错杀死转录主线程
    try:
        _sync_ui(index)
    except Exception as e:
        logger.error(f"UI Sync Error suppressed: {e}")

def _save_metadata(filepath: str, meta_data: dict):
    try:
        makedirs(path.dirname(filepath), exist_ok=True)
        if path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                existing = json.load(f)
                existing.update(meta_data)
                meta_data = existing
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(meta_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.warning(f"Failed to save metadata: {e}")

def _generate_save_name(template: str, file_name: str, lang_src: str, lang_tgt: str, tc_model: str, tl_engine: str, action: str = "") -> str:
    res = template.replace("{file}", file_name).replace("{lang-source}", lang_src).replace("{lang-target}", lang_tgt)\
                  .replace("{transcribe-with}", tc_model).replace("{translate-with}", tl_engine)
    if action:
        for fmt, value in get_task_format(action, action, f"{action} with {tc_model or tl_engine}", f"{action} from {lang_src} to {lang_tgt}", both=True).items():
            res = res.replace(fmt, value)
    return res


def _build_base_export_name(template: str, file_name: str, lang_src: str, lang_tgt: str, tc_model: str, tl_engine: str) -> str:
    return (
        template.replace("{file}", file_name)
        .replace("{lang-source}", lang_src)
        .replace("{lang-target}", lang_tgt)
        .replace("{transcribe-with}", tc_model)
        .replace("{translate-with}", tl_engine)
    )


def _build_metadata_name(base_name: str) -> str:
    meta_name = base_name
    for fmt, val in get_task_format("metadata", "metadata", "metadata", "metadata", both=True).items():
        meta_name = meta_name.replace(fmt, val)
    return meta_name


def _apply_task_format(base_name: str, format_dict: Mapping[str, str]) -> str:
    save_name = base_name
    for fmt, val in format_dict.items():
        save_name = save_name.replace(fmt, val)
    return save_name

def _monitor_thread(thread: Thread, check_cancel: Callable[[], bool]) -> None:
    while thread.is_alive():
        if not check_cancel():
            kill_thread(thread)
            raise Exception("Cancelled")
        sleep(0.1)

# =========================================================================
# ATOMIC EXECUTORS
# =========================================================================

def run_whisper(func, audio: str | None, task: str, fail_status: WorkerFailure, **kwargs) -> None:
    try:
        result = func(audio, task=task, **kwargs)
        bc.data_queue.put(result)
    except Exception as e:
        fail_status.capture(e)
        if "The system cannot find the file specified" in str(e) and not bc.has_ffmpeg:
            fail_status.error = Exception("FFmpeg not found in system path. Please install FFmpeg.")

def run_translate_api(
    query: stable_whisper.WhisperResult,
    engine: str,
    lang_source: str,
    lang_target: str,
    fail_status: WorkerFailure,
    **kwargs,
) -> None:
    try:
        segment_texts = [segment.text for segment in query.segments]
        query.language = lang_target 
        _success, result = translate(engine, segment_texts, lang_source, lang_target, get_proxies(sj.cache["http_proxy"], sj.cache["https_proxy"]), sj.cache["debug_translate"], **kwargs)

        for segment in query.segments:
            if not result: return
            if isinstance(result, str): raise Exception(result)

            translated_text = " " + str(result.pop(0))
            temp_words = translated_text.split()
            segment_words = [w for w in getattr(segment, "words", []) if hasattr(w, "word")]
            
            if len(temp_words) == len(segment_words):
                for w in segment_words: w.word = " " + temp_words.pop(0)
            elif not segment_words:
                setattr(segment, "_default_text", translated_text)
            else:
                if len(temp_words) > len(segment_words):
                    for idx, word in enumerate(temp_words):
                        target_idx = min(idx, len(segment_words) - 1)
                        if idx < len(segment_words): segment_words[target_idx].word = " " + word
                        else: segment_words[target_idx].word += f" {word}"
                else:
                    last_end = segment_words[-1].end
                    for idx, word in enumerate(temp_words): segment_words[idx].word = " " + word
                    segment.words = segment_words[:len(temp_words)]
                    segment.words[-1].end = last_end
    except Exception as e:
        fail_status.capture(e)

# =========================================================================
# FILE PROCESSORS
# =========================================================================

def _cancellable_tc(file_path, lang_source, lang_target, model_name, tc_func, tl_func, auto, is_tc, is_tl, engine, base_name, meta_path, index, filters, **kwargs):
    start = time()
    try:
        _update_status(status_tc, index, "Transcribing please wait...")
        fail_status = WorkerFailure()
        
        format_dict = get_task_format("transcribed", f"transcribed {lang_source}", f"transcribed with {model_name}", f"transcribed {lang_source} with {model_name}")
        format_dict.update(get_task_format("tc", f"tc {lang_source}", f"tc with {model_name}", f"tc {lang_source} with {model_name}", short_only=True))
        tc_save_name = _apply_task_format(base_name, format_dict)

        thread = Thread(target=run_whisper, args=[tc_func, file_path, "transcribe", fail_status], kwargs=kwargs, daemon=True)
        thread.start()
        _monitor_thread(thread, lambda: bc.transcribing_file)

        fail_status.raise_if_failed()

        result: stable_whisper.WhisperResult = bc.data_queue.get()
        if sj.cache["filter_file_import"]:
            try: result = remove_segments_by_str(result, filters.get(get_whisper_lang_name(result.language) if auto else get_whisper_lang_similar(lang_source), []), sj.cache["filter_file_import_case_sensitive"], sj.cache["filter_file_import_strip"], sj.cache["filter_file_import_ignore_punctuations"], sj.cache["filter_file_import_exact_match"], sj.cache["filter_file_import_similarity"])
            except Exception: pass

        if sj.cache["remove_repetition_file_import"]: result = result.remove_repetition(sj.cache["remove_repetition_amount"])

        if is_tc:
            if result.text.strip():
                bc.file_tced_counter += 1
                export_dir = dir_export if sj.cache["dir_export"] == "auto" else sj.cache["dir_export"]
                save_output_stable_ts(split_res(stable_whisper.WhisperResult(result.to_dict()), sj.cache), path.join(export_dir, tc_save_name), sj.cache["export_to"], sj, source_media_path=file_path)
            else:
                _update_status(status_tc, index, "TC Fail! Got empty text")

        _update_status(status_tc, index, "Transcribed")
        _save_metadata(meta_path, {"transcribe_time": time() - start, "transcribe_success": True})

        if is_tl:
            tl_query = file_path if engine in model_values else result
            Thread(target=_cancellable_tl, args=[tl_query, lang_source, lang_target, tl_func, engine, base_name, meta_path, index, file_path, filters], kwargs=kwargs, daemon=True).start()
            
    except Exception as e:
        _update_status(status_tc, index, "Failed to transcribe")
        if is_tl: _update_status(status_tl, index, "Skipped (TC Failed)")
        if str(e) != "Cancelled": logger.error(f"TC Error: {e}")

def _cancellable_tl(query, lang_source, lang_target, tl_func, engine, base_name, meta_path, index, media_path, filters, **kwargs):
    start = time()
    try:
        _update_status(status_tl, index, "Translating please wait...")
        export_dir = dir_export if sj.cache["dir_export"] == "auto" else sj.cache["dir_export"]
        fail_status = WorkerFailure()

        format_dict = get_task_format("translated", f"translated {lang_source} to {lang_target}", f"translated with {engine}", f"translated {lang_source} to {lang_target} with {engine}")
        format_dict.update(get_task_format("tl", f"tl {lang_source} to {lang_target}", f"tl with {engine}", f"tl {lang_source} to {lang_target} with {engine}", short_only=True))
        tl_save_name = _apply_task_format(base_name, format_dict)

        if engine in model_values:
            thread = Thread(target=run_whisper, args=[tl_func, query, "translate", fail_status], kwargs=kwargs, daemon=True)
            thread.start()
            _monitor_thread(thread, lambda: bc.translating_file)
            fail_status.raise_if_failed()

            result = bc.data_queue.get()
            if sj.cache["filter_file_import"]:
                try: result = remove_segments_by_str(result, filters.get("english", []), sj.cache["filter_file_import_case_sensitive"], sj.cache["filter_file_import_strip"], sj.cache["filter_file_import_ignore_punctuations"], sj.cache["filter_file_import_exact_match"], sj.cache["filter_file_import_similarity"])
                except Exception: pass
            if sj.cache["remove_repetition_file_import"]: result = result.remove_repetition(sj.cache["remove_repetition_amount"])
        else:
            if not getattr(query, "text", "").strip(): return _update_status(status_tl, index, "TL Fail! Empty text")
            api_kwargs = {"libre_link": sj.cache["libre_link"], "libre_api_key": sj.cache["libre_api_key"]} if engine == "LibreTranslate" else {}
            thread = Thread(target=run_translate_api, args=[query, engine, lang_source, lang_target, fail_status], kwargs=api_kwargs, daemon=True)
            thread.start()
            _monitor_thread(thread, lambda: bc.translating_file)
            fail_status.raise_if_failed()
            result = query

        if not getattr(result, "text", "").strip(): return _update_status(status_tl, index, "TL Fail! Empty text")

        bc.file_tled_counter += 1
        save_output_stable_ts(split_res(result, sj.cache), path.join(export_dir, tl_save_name), sj.cache["export_to"], sj, source_media_path=media_path)
        _update_status(status_tl, index, "Translated")
        _save_metadata(meta_path, {"translate_time": time() - start, "translate_success": True})

    except Exception as e:
        _update_status(status_tl, index, "Failed to translate")
        if str(e) != "Cancelled": logger.error(f"TL Error: {e}")

# =========================================================================
# PUBLIC BATCH APIS
# =========================================================================

def process_file(data_files: List[str], model_name_tc: str, lang_source: str, lang_target: str, is_tc: bool, is_tl: bool, engine: str) -> None:
    try:
        global status_tc, status_tl, GLOBAL_IS_TC, GLOBAL_IS_TL, GLOBAL_IS_MOD
        status_tc, status_tl = {}, {}
        GLOBAL_IS_TC, GLOBAL_IS_TL, GLOBAL_IS_MOD = is_tc, is_tl, False
        bc.file_tced_counter = bc.file_tled_counter = 0
        
        tl_engine_whisper = engine in model_values
        export_dir = dir_export if sj.cache["dir_export"] == "auto" else sj.cache["dir_export"]
        export_fmt = sj.cache["export_format"]
        slice_s, slice_e = int(sj.cache["file_slice_start"]) if sj.cache["file_slice_start"] else None, int(sj.cache["file_slice_end"]) if sj.cache["file_slice_end"] else None
        
        _, _, stable_tc, stable_tl, to_args = get_model(is_tc, is_tl, tl_engine_whisper, model_name_tc, engine, sj.cache, **get_model_args(sj.cache))
        whisper_args = get_tc_args(to_args, sj.cache)
        whisper_args["language"] = TO_LANGUAGE_CODE[get_whisper_lang_similar(lang_source)] if lang_source != "auto detect" else None
        whisper_args["verbose"] = None
        filters = get_hallucination_filter('file', sj.cache["path_filter_file_import"]) if sj.cache["filter_file_import"] else {}

        taskname = "Transcribe & Translate" if is_tc and is_tl else "Transcribe" if is_tc else "Translate"
        t_start = time()

        bc.enable_file_tc()
        bc.enable_file_tl()

        if bc.web_bridge:
            bc.web_bridge.init_file_batch(f"Task: {taskname} with {model_name_tc}", data_files)

        def is_still_active():
            for i in range(len(data_files)):
                if is_tc and status_tc.get(i, 'Waiting') in ACTIVE_STATUSES: return True
                if is_tl and status_tl.get(i, 'Waiting') in ACTIVE_STATUSES: return True
            return False

        for i, file in enumerate(data_files):
            if not bc.file_processing: break
            logger.info(f"Loop entered for file: {file}")
            file_name = filename_only(file)[slice_s:slice_e]
            base_name = _build_base_export_name(
                datetime.now().strftime(export_fmt),
                file_name,
                lang_source,
                lang_target,
                model_name_tc,
                engine,
            )
            meta_name = _build_metadata_name(base_name)
            meta_path = path.join(export_dir, meta_name + ".json")

            _save_metadata(meta_path, {
                "meta_written_at": str(datetime.now()), "task": taskname, "filename": file_name,
                "transcribe": is_tc, "translate": is_tl, "model": model_name_tc, "engine": engine
            })

            if is_tl and not is_tc and tl_engine_whisper:
                Thread(target=_cancellable_tl, args=[file, lang_source, lang_target, stable_tl, engine, base_name, meta_path, i, file, filters], kwargs=whisper_args, daemon=True).start()
            else:
                tc_thread = Thread(target=_cancellable_tc, args=[file, lang_source, lang_target, model_name_tc, stable_tc, stable_tl, lang_source == "auto detect", is_tc, is_tl, engine, base_name, meta_path, i, filters], kwargs=whisper_args, daemon=True)
                tc_thread.start()
                tc_thread.join()

        while bc.file_processing and is_still_active():
            sleep(0.5)

        logger.info(f"Process FILE completed in {time() - t_start:.2f}s")
        if (bc.file_tced_counter > 0 or bc.file_tled_counter > 0) and sj.cache["auto_open_dir_export"]:
            start_file(export_dir)

    except Exception as e:
        logger.error(f"Process FILE error: {e}")
    finally:
        bc.disable_file_process()
        bc.disable_file_tc()
        bc.disable_file_tl()
        cuda.empty_cache()


def mod_result(data_files: List, model_name_tc: str, mode: Literal["refinement", "alignment"]):
    try:
        global status_mod, GLOBAL_IS_TC, GLOBAL_IS_TL, GLOBAL_IS_MOD
        status_mod = {}
        GLOBAL_IS_TC, GLOBAL_IS_TL, GLOBAL_IS_MOD = False, False, True
        bc.mod_file_counter = 0
        action = "Refinement" if mode == "refinement" else "Alignment"
        
        export_dir = dir_refinement if mode == "refinement" else dir_alignment
        if sj.cache["dir_export"] != "auto": export_dir = sj.cache["dir_export"] + f"/@{action.lower()}"
        slice_s, slice_e = int(sj.cache["file_slice_start"]) if sj.cache["file_slice_start"] else None, int(sj.cache["file_slice_end"]) if sj.cache["file_slice_end"] else None

        model = stable_whisper.load_model(model_name_tc, **get_model_args(sj.cache))
        mod_func = model.refine if mode == "refinement" else model.align 
        mod_args = get_tc_args(mod_func, sj.cache, mode="refine" if mode == "refinement" else "align")

        t_start = time()

        if bc.web_bridge:
            bc.web_bridge.init_file_batch(f"Task {mode} with {model_name_tc}", [f[0] for f in data_files])

        def is_still_active():
            return any(status_mod.get(i, 'Waiting') in ACTIVE_STATUSES for i in range(len(data_files)))

        for i, file_data in enumerate(data_files):
            if not bc.file_processing: break

            audio_path, mod_path = file_data[0], file_data[1]
            file_name = filename_only(audio_path)[slice_s:slice_e]
            base_name = _build_base_export_name(
                datetime.now().strftime(sj.cache["export_format"]),
                file_name,
                "",
                "",
                model_name_tc,
                "",
            )
            meta_name = _build_metadata_name(base_name)
            meta_path = path.join(export_dir, meta_name + ".json")

            task_short = {"refinement": "rf", "alignment": "al"}
            format_dict = get_task_format(action, action, f"{action} with {model_name_tc}", f"{action} with {model_name_tc}")
            format_dict.update(get_task_format(task_short[mode], task_short[mode], f"{task_short[mode]} with {model_name_tc}", f"{task_short[mode]} with {model_name_tc}", short_only=True))
            save_name = _apply_task_format(base_name, format_dict)

            try:
                mod_src = stable_whisper.WhisperResult(mod_path) if mod_path.endswith(".json") else open(mod_path, "r", encoding="utf-8").read()
            except Exception:
                _update_status(status_mod, i, "Parse Error")
                continue

            if mode == "alignment" and len(file_data) > 2 and len(file_data[2]) > 3:
                mod_args["language"] = TO_LANGUAGE_CODE.get(get_whisper_lang_similar(file_data[2]), "auto")

            def _run_mod():
                try:
                    _update_status(status_mod, i, f"Processing {mode}")
                    res = mod_func(audio_path, mod_src, **mod_args)
                    bc.data_queue.put(res)
                except Exception as e:
                    if "'NoneType'" in str(e) and mode == "refinement":
                        try:
                            _update_status(status_mod, i, "Re-transcribing...")
                            res = model.transcribe(audio_path, **get_tc_args(model.transcribe, sj.cache))
                            res = mod_func(audio_path, res, **mod_args)
                            bc.data_queue.put(res)
                        except Exception as ee:
                            raise Exception(f"Re-transcribe failed: {ee}")
                    else: raise e

            fail_status = WorkerFailure()
            thread = Thread(target=lambda: run_whisper(_run_mod, None, mode, fail_status), daemon=True)
            thread.start()
            _monitor_thread(thread, lambda: bc.file_processing)

            if fail_status.failed:
                _update_status(status_mod, i, "Failed")
                continue

            result = split_res(bc.data_queue.get(), sj.cache)
            if not result.language: result.language = mod_args.get("language", "auto")

            save_output_stable_ts(result, path.join(export_dir, save_name), sj.cache["export_to"], sj)
            bc.mod_file_counter += 1
            _update_status(status_mod, i, action)
            _save_metadata(meta_path, {"meta_written_at": str(datetime.now()), "task": f"Mod Result ({mode})", "time": time() - t_start})

        while bc.file_processing and is_still_active():
            sleep(0.5)

        logger.info(f"Process MOD completed in {time() - t_start:.2f}s")
        if bc.mod_file_counter > 0 and sj.cache.get(f"auto_open_dir_{mode}", True):
            start_file(export_dir)

    except Exception as e:
        logger.error(f"Process MOD error: {e}")
    finally:
        bc.disable_file_process()
        cuda.empty_cache()


def translate_result(data_files: List, engine: str, lang_target: str):
    try:
        global status_mod, GLOBAL_IS_TC, GLOBAL_IS_TL, GLOBAL_IS_MOD
        status_mod = {}
        GLOBAL_IS_TC, GLOBAL_IS_TL, GLOBAL_IS_MOD = False, False, True
        bc.mod_file_counter = 0
        export_dir = dir_translate if sj.cache["dir_export"] == "auto" else sj.cache["dir_export"] + "/@translated"
        slice_s, slice_e = int(sj.cache["file_slice_start"]) if sj.cache["file_slice_start"] else None, int(sj.cache["file_slice_end"]) if sj.cache["file_slice_end"] else None
        
        t_start = time()

        if bc.web_bridge:
            bc.web_bridge.init_file_batch(f"Task Translate with {engine}", data_files)

        def is_still_active():
            return any(status_mod.get(i, 'Waiting') in ACTIVE_STATUSES for i in range(len(data_files)))

        api_kwargs = {"libre_link": sj.cache["libre_link"], "libre_api_key": sj.cache["libre_api_key"]} if engine == "LibreTranslate" else {}

        for i, file_path in enumerate(data_files):
            if not bc.file_processing: break

            try: result = stable_whisper.WhisperResult(file_path)
            except Exception:
                _update_status(status_mod, i, "Parse Error")
                continue

            lang_src = to_language_name(result.language) or "auto"
            file_name = filename_only(file_path)[slice_s:slice_e]
            base_name = _build_base_export_name(
                datetime.now().strftime(sj.cache["export_format"]),
                file_name,
                lang_src,
                lang_target,
                "",
                engine,
            )
            meta_name = _build_metadata_name(base_name)
            meta_path = path.join(export_dir, meta_name + ".json")

            format_dict = get_task_format("translated result", f"translated result from {lang_src} to {lang_target}", f"translated result with {engine}", f"translated result from {lang_src} to {lang_target} with {engine}")
            format_dict.update(get_task_format("tl res", f"tl res from {lang_src} to {lang_target}", f"tl res with {engine}", f"tl res from {lang_src} to {lang_target} with {engine}", short_only=True))
            save_name = _apply_task_format(base_name, format_dict)

            _update_status(status_mod, i, "Translating please wait...")
            fail_status = WorkerFailure()
            
            thread = Thread(target=run_translate_api, args=[result, engine, lang_src, lang_target, fail_status], kwargs=api_kwargs, daemon=True)
            thread.start()
            _monitor_thread(thread, lambda: bc.file_processing)

            if fail_status.failed:
                _update_status(status_mod, i, "Failed")
                continue

            bc.mod_file_counter += 1
            save_output_stable_ts(split_res(result, sj.cache), path.join(export_dir, save_name), sj.cache["export_to"], sj, source_media_path=file_path)
            _update_status(status_mod, i, "Translated")
            _save_metadata(meta_path, {"meta_written_at": str(datetime.now()), "task": "Translate JSON", "time": time() - t_start})

        while bc.file_processing and is_still_active():
            sleep(0.5)

        logger.info(f"Process TL JSON completed in {time() - t_start:.2f}s")
        if bc.mod_file_counter > 0 and sj.cache["auto_open_dir_translate"]:
            start_file(export_dir)

    except Exception as e:
        logger.error(f"Process TL JSON error: {e}")
    finally:
        bc.disable_file_process()
