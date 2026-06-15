from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.whisper import load as whisper_load
from speech_translate.utils.whisper import save as whisper_save
from speech_translate.utils.whisper import stable_args


class WhisperStableArgsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_get_torch_api = stable_args._get_torch_api
        self.previous_get_stable_whisper_utils = stable_args._get_stable_whisper_utils
        self.previous_get_decoding_options_type = stable_args._get_decoding_options_type
        self.previous_get_faster_options_type = stable_args._get_faster_whisper_transcription_options_type

        stable_args._get_torch_api = lambda: type(
            "FakeTorchApi",
            (),
            {"cuda": type("FakeCuda", (), {"is_available": staticmethod(lambda: False)})()},
        )()
        stable_args._get_stable_whisper_utils = lambda: (
            lambda values, _method: dict(values),
            lambda value: value,
        )
        stable_args._get_decoding_options_type = lambda: object()
        stable_args._get_faster_whisper_transcription_options_type = lambda: object()

    def tearDown(self) -> None:
        stable_args._get_torch_api = self.previous_get_torch_api
        stable_args._get_stable_whisper_utils = self.previous_get_stable_whisper_utils
        stable_args._get_decoding_options_type = self.previous_get_decoding_options_type
        stable_args._get_faster_whisper_transcription_options_type = self.previous_get_faster_options_type

    def test_parse_args_save_mode_keeps_export_inputs_and_strips_download_root(self) -> None:
        args = stable_args.parse_args_stable_ts(
            "--model_option download_root=./cache --save_option highlight_color=ffffff",
            "save",
            method=object(),
            save_path="D:\\exports\\sample",
            segment_level=True,
            word_level=False,
            show_parsed=False,
        )

        self.assertTrue(args["success"])
        self.assertEqual(args["filepath"], "D:\\exports\\sample")
        self.assertEqual(args["path"], "D:\\exports\\sample")
        self.assertTrue(args["segment_level"])
        self.assertFalse(args["word_level"])
        self.assertEqual(args["highlight_color"], "ffffff")
        self.assertNotIn("download_root", args)

    def test_parse_args_transcribe_mode_sets_faster_whisper_defaults(self) -> None:
        args = stable_args.parse_args_stable_ts(
            "",
            "transcribe",
            method="faster_whisper_runner",
            best_of=None,
            beam_size=None,
            patience=None,
            show_parsed=False,
        )

        self.assertTrue(args["success"])
        self.assertEqual(args["best_of"], 1)
        self.assertEqual(args["beam_size"], 1)
        self.assertEqual(args["patience"], 1)
        self.assertIn("threads", args)

    def test_save_module_uses_stable_args_parser_directly(self) -> None:
        self.assertIs(whisper_save.parse_args_stable_ts, stable_args.parse_args_stable_ts)

    def test_load_module_keeps_parser_api_via_forwarding_wrapper(self) -> None:
        previous_impl = whisper_load._parse_args_stable_ts
        try:
            whisper_load._parse_args_stable_ts = lambda *args, **kwargs: {"args": args, "kwargs": kwargs}
            result = whisper_load.parse_args_stable_ts("x", "save", object(), save_path="out")
        finally:
            whisper_load._parse_args_stable_ts = previous_impl

        self.assertEqual(result["args"][0], "x")
        self.assertEqual(result["args"][1], "save")
        self.assertEqual(len(result["args"]), 3)
        self.assertEqual(result["kwargs"], {"save_path": "out"})


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
