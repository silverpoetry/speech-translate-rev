from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.whisper import load as whisper_load


class FakeModel:
    def __init__(self, name: str) -> None:
        self.name = name

    def transcribe(self, *args, **kwargs):
        return ("transcribe", self.name, args, kwargs)

    def transcribe_stable(self, *args, **kwargs):
        return ("transcribe_stable", self.name, args, kwargs)


class WhisperLoadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_model_cache = dict(whisper_load._MODEL_CACHE)
        self.previous_bundle_cache = dict(whisper_load._MODEL_BUNDLE_CACHE)
        whisper_load._MODEL_CACHE.clear()
        whisper_load._MODEL_BUNDLE_CACHE.clear()

        self.previous_get_stable_whisper_api = whisper_load._get_stable_whisper_api
        self.previous_get_torch_api = whisper_load._get_torch_api
        self.load_calls = []
        self.load_faster_calls = []
        self.thread_calls = []

        fake_api = type(
            "FakeStableWhisperApi",
            (),
            {
                "load_model": self._fake_load_model,
                "load_faster_whisper": self._fake_load_faster,
            },
        )()
        whisper_load._get_stable_whisper_api = lambda: fake_api
        whisper_load._get_torch_api = lambda: type(
            "FakeTorchApi",
            (),
            {
                "set_num_threads": lambda _self, threads: self.thread_calls.append(threads),
                "cuda": type("FakeCuda", (), {"is_available": staticmethod(lambda: False)})(),
            },
        )()

    def tearDown(self) -> None:
        whisper_load._MODEL_CACHE.clear()
        whisper_load._MODEL_CACHE.update(self.previous_model_cache)
        whisper_load._MODEL_BUNDLE_CACHE.clear()
        whisper_load._MODEL_BUNDLE_CACHE.update(self.previous_bundle_cache)
        whisper_load._get_stable_whisper_api = self.previous_get_stable_whisper_api
        whisper_load._get_torch_api = self.previous_get_torch_api

    def _fake_load_model(self, model_name: str, **model_args):
        self.load_calls.append((model_name, dict(model_args)))
        return FakeModel(model_name)

    def _fake_load_faster(self, model_name: str, **model_args):
        self.load_faster_calls.append((model_name, dict(model_args)))
        return FakeModel(model_name)

    def test_load_model_cached_reuses_whisper_backend_instance(self) -> None:
        first = whisper_load._load_model_cached("small", False, device="cpu")
        second = whisper_load._load_model_cached("small", False, device="cpu")
        self.assertIs(first, second)
        self.assertEqual(self.load_calls, [("small", {"device": "cpu"})])

    def test_load_model_cached_forces_local_files_only_for_faster_whisper_when_available(self) -> None:
        previous_cached = whisper_load._is_faster_whisper_model_cached
        try:
            whisper_load._is_faster_whisper_model_cached = lambda *_args, **_kwargs: True
            model = whisper_load._load_model_cached("tiny", True, download_root="D:\\models")
        finally:
            whisper_load._is_faster_whisper_model_cached = previous_cached

        self.assertEqual(model.name, "tiny")
        self.assertEqual(self.load_faster_calls[0][1]["local_files_only"], True)

    def test_load_model_cached_does_not_force_local_files_only_when_faster_whisper_missing(self) -> None:
        previous_cached = whisper_load._is_faster_whisper_model_cached
        try:
            whisper_load._is_faster_whisper_model_cached = lambda *_args, **_kwargs: False
            whisper_load._load_model_cached("tiny", True, download_root="D:\\models")
        finally:
            whisper_load._is_faster_whisper_model_cached = previous_cached

        self.assertNotIn("local_files_only", self.load_faster_calls[0][1])

    def test_build_whisper_load_runtime_collects_runtime_dependencies(self) -> None:
        runtime = whisper_load._build_whisper_load_runtime()

        self.assertTrue(hasattr(runtime.stable_whisper_api, "load_model"))
        self.assertTrue(hasattr(runtime.stable_whisper_api, "load_faster_whisper"))
        self.assertTrue(hasattr(runtime.torch_api, "set_num_threads"))
        self.assertIsNotNone(runtime.faster_whisper_model_type)

    def test_load_model_variant_selects_backend_specific_runner(self) -> None:
        whisper_model, whisper_runner = whisper_load._load_model_variant("small", False, device="cpu")
        faster_model, faster_runner = whisper_load._load_model_variant("base", True, device="cpu")

        self.assertEqual(whisper_runner(), ("transcribe", "small", (), {}))
        self.assertEqual(faster_runner(), ("transcribe_stable", "base", (), {}))
        self.assertEqual(whisper_model.name, "small")
        self.assertEqual(faster_model.name, "base")

    def test_get_model_reuses_cached_bundle_for_same_request(self) -> None:
        setting_cache = {"use_faster_whisper": False}

        first = whisper_load.get_model(
            True,
            True,
            True,
            "small",
            "small",
            setting_cache,
            device="cpu",
        )
        second = whisper_load.get_model(
            True,
            True,
            True,
            "small",
            "small",
            setting_cache,
            device="cpu",
        )

        self.assertIs(first, second)
        self.assertEqual(self.load_calls, [("small", {"device": "cpu"})])
        self.assertTrue(
            whisper_load.is_model_bundle_cached(
                True,
                True,
                True,
                "small",
                "small",
                setting_cache,
                device="cpu",
            )
        )

    def test_build_model_load_plan_shares_model_when_transcribe_and_translate_match(self) -> None:
        plan = whisper_load._build_model_load_plan(
            transcribe=True,
            translate=True,
            tl_engine_whisper=True,
            model_name_tc="small",
            engine="small",
        )

        self.assertEqual(plan.tc_model_name, "small")
        self.assertIsNone(plan.tl_model_name)
        self.assertTrue(plan.reuse_tc_for_tl)

    def test_build_model_load_plan_keeps_transcribe_model_for_translate_only_external_engine(self) -> None:
        plan = whisper_load._build_model_load_plan(
            transcribe=False,
            translate=True,
            tl_engine_whisper=False,
            model_name_tc="small",
            engine="Google Translate",
        )

        self.assertEqual(plan.tc_model_name, "small")
        self.assertIsNone(plan.tl_model_name)
        self.assertFalse(plan.reuse_tc_for_tl)

    def test_get_tc_args_sets_torch_threads_via_runtime_api(self) -> None:
        previous_parser = whisper_load.parse_args_stable_ts
        try:
            whisper_load.parse_args_stable_ts = lambda *_args, **_kwargs: {"success": True, "threads": 4, "device": "cpu"}
            setting_cache = {
                "temperature": "0.0",
                "suppress_tokens": "",
                "whisper_args": "",
                "best_of": None,
                "beam_size": None,
                "patience": None,
                "compression_ratio_threshold": 2.4,
                "logprob_threshold": -1.0,
                "no_speech_threshold": 0.6,
                "suppress_blank": True,
                "initial_prompt": None,
                "prefix": None,
                "condition_on_previous_text": True,
                "max_initial_timestamp": None,
                "fp16": True,
            }

            result = whisper_load.get_tc_args("fake_process", setting_cache)
        finally:
            whisper_load.parse_args_stable_ts = previous_parser

        self.assertEqual(result, {"device": "cpu"})
        self.assertEqual(self.thread_calls, [4])

    def test_get_model_args_falls_back_to_default_download_root_and_auto_device(self) -> None:
        previous_parser = whisper_load.parse_args_stable_ts
        previous_default_download_root = whisper_load.get_default_download_root
        try:
            whisper_load.parse_args_stable_ts = lambda *_args, **_kwargs: {"success": True}
            whisper_load.get_default_download_root = lambda: "D:\\default-models"
            setting_cache = {
                "use_faster_whisper": False,
                "whisper_args": "",
                "dir_model": "auto",
                "model_device_preference": "invalid-value",
            }

            result = whisper_load.get_model_args(setting_cache)
        finally:
            whisper_load.parse_args_stable_ts = previous_parser
            whisper_load.get_default_download_root = previous_default_download_root

        self.assertEqual(result["download_root"], "D:\\default-models")
        self.assertEqual(result["device"], "cpu")

    def test_get_model_args_preserves_explicit_cuda_preference_when_available(self) -> None:
        previous_parser = whisper_load.parse_args_stable_ts
        previous_runtime_builder = whisper_load._build_whisper_load_runtime
        try:
            whisper_load.parse_args_stable_ts = lambda *_args, **_kwargs: {"success": True}
            whisper_load._build_whisper_load_runtime = lambda: whisper_load.WhisperLoadRuntime(
                stable_whisper_api=type("StableApi", (), {"load_model": lambda *_args, **_kwargs: None})(),
                torch_api=type("TorchApi", (), {"cuda": type("Cuda", (), {"is_available": staticmethod(lambda: True)})()})(),
                faster_whisper_model_type=object(),
            )
            setting_cache = {
                "use_faster_whisper": False,
                "whisper_args": "",
                "dir_model": "D:\\models",
                "model_device_preference": "cuda",
            }

            result = whisper_load.get_model_args(setting_cache)
        finally:
            whisper_load.parse_args_stable_ts = previous_parser
            whisper_load._build_whisper_load_runtime = previous_runtime_builder

        self.assertEqual(result["download_root"], "D:\\models")
        self.assertEqual(result["device"], "cuda")


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
