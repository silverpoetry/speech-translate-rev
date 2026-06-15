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
        self.load_calls = []
        self.load_faster_calls = []

        fake_api = type(
            "FakeStableWhisperApi",
            (),
            {
                "load_model": self._fake_load_model,
                "load_faster_whisper": self._fake_load_faster,
            },
        )()
        whisper_load._get_stable_whisper_api = lambda: fake_api

    def tearDown(self) -> None:
        whisper_load._MODEL_CACHE.clear()
        whisper_load._MODEL_CACHE.update(self.previous_model_cache)
        whisper_load._MODEL_BUNDLE_CACHE.clear()
        whisper_load._MODEL_BUNDLE_CACHE.update(self.previous_bundle_cache)
        whisper_load._get_stable_whisper_api = self.previous_get_stable_whisper_api

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


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
