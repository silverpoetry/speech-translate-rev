from __future__ import annotations

import argparse
from typing import Literal, Optional, Union

from speech_translate.log_helpers import logger
from speech_translate.runtime_deps import (
    get_faster_whisper_transcription_options_type,
    get_stable_whisper_utils,
    get_torch,
    get_whisper_decoding_options_type,
)


str2val = {"true": True, "false": False, "1": True, "0": False}


def _get_torch_api():
    return get_torch()


def _get_stable_whisper_utils():
    return get_stable_whisper_utils()


def _get_decoding_options_type():
    return get_whisper_decoding_options_type()


def _get_faster_whisper_transcription_options_type():
    return get_faster_whisper_transcription_options_type()


def optional_int(string):
    return None if string == "None" else int(string)


def optional_float(string):
    return None if string == "None" else float(string)


def str2bool(string: str) -> bool:
    string = string.lower()
    if string in str2val:
        return str2val[string]
    raise ValueError(f"Expected one of {set(str2val.keys())}, got {string}")


class ArgumentParserWithErrors(argparse.ArgumentParser):
    """
    An ArgumentParser that raises ValueError on error
    so we can see the error message by catching it
    """

    def error(self, message):
        raise ValueError(message)


def parse_args_stable_ts(
    arguments: str, mode: Union[Literal["load", "transcribe", "align", "refine", "save"], str], method=None, **kwargs
):
    """Parse arguments to be passed onto stable ts with each mode in mind."""

    parser = ArgumentParserWithErrors(
        description="Example Argument Parser", formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    args = {}
    torch = _get_torch_api()
    isolate_useful_options, str_to_valid_type = _get_stable_whisper_utils()

    def update_options_with_args(arg_key: str, options: Optional[dict] = None, pop: bool = False):
        extra_options = args.pop(arg_key) if pop else args.get(arg_key)
        if not extra_options:
            return
        extra_options = [kv.split("=", maxsplit=1) for kv in extra_options]
        missing_val = [kv[0] for kv in extra_options if len(kv) == 1]
        if missing_val:
            raise ValueError(f"Following expected values for the following custom options: {missing_val}")
        extra_options = dict(
            (k.replace('"', "").replace("'", ""), str_to_valid_type(v.replace('"', "").replace("'", "")))
            for k, v in extra_options
        )
        if options is None:
            return extra_options
        options.update(extra_options)

    try:
        # ruff: noqa: E501
        # yapf: disable
        parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu",
                            help="device to use for PyTorch inference")
        parser.add_argument("--cpu_preload", type=str2bool, default=True,
                            help="load model into CPU memory first then move model to specified device; "
                                "this reduces GPU memory usage when loading model.")

        parser.add_argument("--dynamic_quantization", "-dq", action="store_true",
                            help="whether to apply Dynamic Quantization to model "
                                "to reduced memory usage (~half less) and increase inference speed "
                                "at cost of slight decrease in accuracy; Only for CPU; "
                                "NOTE: overhead might make inference slower for models smaller than 'large'")

        parser.add_argument("--prepend_punctuations", "-pp", type=str, default="\"'“¿([{-",
                            help="Punctuations to prepend to next word")
        parser.add_argument("--append_punctuations", "-ap", type=str, default="\"'.。,，!！?？:：”)]}、",
                            help="Punctuations to append to previous word")

        parser.add_argument("--gap_padding", type=str, default=" ...",
                            help="padding prepend to each segments for word timing alignment; "
                                "used to reduce the probability of model predicting timestamps "
                                "earlier than the first utterance")

        parser.add_argument("--word_timestamps", type=str2bool, default=True,
                            help="extract word-level timestamps using the cross-attention pattern and dynamic time warping, "
                                "and include the timestamps for each word in each segment; "
                                "disabling this will prevent segments from splitting/merging properly.")

        parser.add_argument("--regroup", type=str, default="True",
                            help="whether to regroup all words into segments with more natural boundaries; "
                                "specify string for customizing the regrouping algorithm "
                                "ignored if [word_timestamps]=False.")

        parser.add_argument("--ts_num", type=int, default=0,
                            help="number of extra inferences to perform to find the mean timestamps")
        parser.add_argument("--ts_noise", type=float, default=0.1,
                            help="percentage of noise to add to audio_features to perform inferences for [ts_num]")

        parser.add_argument("--suppress_silence", type=str2bool, default=True,
                            help="whether to suppress timestamp where audio is silent at segment-level "
                                "and word-level if [suppress_word_ts]=True")
        parser.add_argument("--suppress_word_ts", type=str2bool, default=True,
                            help="whether to suppress timestamps where audio is silent at word-level; "
                                "ignored if [suppress_silence]=False")

        parser.add_argument("--suppress_ts_tokens", type=str2bool, default=False,
                            help="whether to use silence mask to suppress silent timestamp tokens during inference; "
                                "increases word accuracy in some cases, but tends reduce 'verbatimness' of the transcript "
                                "ignored if [suppress_silence]=False")

        parser.add_argument("--q_levels", type=int, default=20,
                            help="quantization levels for generating timestamp suppression mask; "
                                "acts as a threshold to marking sound as silent; "
                                "fewer levels will increase the threshold of volume at which to mark a sound as silent")

        parser.add_argument("--k_size", type=int, default=5,
                            help="Kernel size for average pooling waveform to generate suppression mask; "
                                "recommend 5 or 3; higher sizes will reduce detection of silence")

        parser.add_argument("--time_scale", type=float,
                            help="factor for scaling audio duration for inference; "
                                "greater than 1.0 'slows down' the audio; "
                                "less than 1.0 'speeds up' the audio; "
                                "1.0 is no scaling")

        parser.add_argument("--vad", type=str2bool, default=False,
                            help="whether to use Silero VAD to generate timestamp suppression mask; "
                                "Silero VAD requires PyTorch 1.12.0+; "
                                "Official repo: https://github.com/snakers4/silero-vad")
        parser.add_argument("--vad_threshold", type=float, default=0.35,
                            help="threshold for detecting speech with Silero VAD. (Default: 0.35); "
                                "low threshold reduces false positives for silence detection")
        parser.add_argument("--vad_onnx", type=str2bool, default=False,
                            help="whether to use ONNX for Silero VAD")

        parser.add_argument("--min_word_dur", type=float, default=0.1,
                            help="only allow suppressing timestamps that result in word durations greater than this value")

        parser.add_argument("--demucs", type=str2bool, default=False,
                            help="whether to reprocess the audio track with Demucs to isolate vocals/remove noise; "
                                "Demucs official repo: https://github.com/facebookresearch/demucs")
        parser.add_argument("--demucs_output", action="extend", nargs="+", type=str,
                            help="path(s) to save the vocals isolated by Demucs as WAV file(s); "
                                "ignored if [demucs]=False")
        parser.add_argument("--only_voice_freq", "-ovf", action="store_true",
                            help="whether to only use sound between 200 - 5000 Hz, where majority of human speech are.")

        parser.add_argument("--strip", type=str2bool, default=True,
                            help="whether to remove spaces before and after text on each segment for output")

        parser.add_argument("--tag", type=str, action="extend", nargs="+",
                            help="a pair tags used to change the properties a word at its predicted time "
                                "SRT Default: '<font color=\"#00ff00\">', '</font>' "
                                "VTT Default: '<u>', '</u>' "
                                "ASS Default: '{\\1c&HFF00&}', '{\\r}'")

        parser.add_argument("--reverse_text", type=str2bool, default=False,
                            help="whether to reverse the order of words for each segment of text output")

        parser.add_argument("--font", type=str, default="Arial",
                            help="word font for ASS output(s)")
        parser.add_argument("--font_size", type=int, default=48,
                            help="word font size for ASS output(s)")
        parser.add_argument("--karaoke", type=str2bool, default=False,
                            help="whether to use progressive filling highlights for karaoke effect (only for ASS outputs)")

        parser.add_argument("--length_penalty", type=float, default=None,
                            help="optional token length penalty coefficient (alpha) "
                                "as in https://arxiv.org/abs/1609.08144, uses simple length normalization by default")

        parser.add_argument("--compression_ratio_threshold", type=optional_float, default=2.4,
                            help="if the gzip compression ratio is higher than this value, treat the decoding as failed")
        parser.add_argument("--logprob_threshold", type=optional_float, default=-1.0,
                            help="if the average log probability is lower than this value, treat the decoding as failed")
        parser.add_argument("--no_speech_threshold", type=optional_float, default=0.6,
                            help="if the probability of the <|nospeech|> token is higher than this value AND the decoding "
                                "has failed due to `logprob_threshold`, consider the segment as silence")
        parser.add_argument("--threads", type=optional_int, default=0,
                            help="number of threads used by torch for CPU inference; "
                                "supercedes MKL_NUM_THREADS/OMP_NUM_THREADS")

        parser.add_argument("--mel_first", action="store_true",
                            help="process entire audio track into log-Mel spectrogram first instead in chunks")

        parser.add_argument("--demucs_option", "-do", action="extend", nargs="+", type=str,
                            help="Extra option(s) to use for demucs; Replace True/False with 1/0; "
                                "E.g. --demucs_option \"shifts=3\" --demucs_options \"overlap=0.5\"")

        parser.add_argument("--refine_option", "-ro", action="extend", nargs="+", type=str,
                            help="Extra option(s) to use for refining timestamps; Replace True/False with 1/0; "
                                "E.g. --refine_option \"steps=sese\" --refine_options \"rel_prob_decrease=0.05\"")
        parser.add_argument("--model_option", "-mo", action="extend", nargs="+", type=str,
                            help="Extra option(s) to use for loading model; Replace True/False with 1/0; "
                                "E.g. --model_option \"download_root=./downloads\"")
        parser.add_argument("--transcribe_option", "-to", action="extend", nargs="+", type=str,
                            help="Extra option(s) to use for transcribing/alignment; Replace True/False with 1/0; "
                                "E.g. --transcribe_option \"ignore_compatibility=1\"")
        parser.add_argument("--save_option", "-so", action="extend", nargs="+", type=str,
                            help="Extra option(s) to use for text outputs; Replace True/False with 1/0; "
                                "E.g. --save_option \"highlight_color=ffffff\"")
        # yapf: enable

        args = parser.parse_args(arguments.split()).__dict__
        threads = args.pop("threads")

        args["demucs_options"] = update_options_with_args("demucs_option", pop=True)
        if dq := args.pop("dynamic_quantization", False):
            args["device"] = "cpu"
            args["dq"] = dq
        if args["reverse_text"]:
            args["reverse_text"] = (args.get("prepend_punctuations"), args.get("append_punctuations"))

        regroup = args.pop("regroup")
        if regroup:
            try:
                args["regroup"] = str2bool(regroup)
            except ValueError:
                pass

        if tag := args.get("tag"):
            assert tag == ["-1"] or len(tag) == 2, f"[tag] must be a pair of str but got {tag}"

        if mode == "load":
            temp = args["model_option"]

            args = isolate_useful_options(args, method)
            args["model_option"] = temp

            update_options_with_args("model_option", args)
            args.pop("model_option")
        elif mode == "transcribe":
            temp = args["transcribe_option"]

            args = isolate_useful_options(args, method)
            args["transcribe_option"] = temp
            update_options_with_args("transcribe_option", args)
            args.pop("transcribe_option")

            if "faster_whisper" in str(method):
                transcription_options = _get_faster_whisper_transcription_options_type()

                if kwargs["best_of"] is None:
                    kwargs["best_of"] = 1
                if kwargs["beam_size"] is None:
                    kwargs["beam_size"] = 1
                if kwargs["patience"] is None:
                    kwargs["patience"] = 1
                args.update(isolate_useful_options(kwargs, transcription_options))
            else:
                args.update(isolate_useful_options(kwargs, _get_decoding_options_type()))

            args["threads"] = threads

        elif mode == "align":
            temp = args["transcribe_option"]

            args = isolate_useful_options(args, method)
            args["transcribe_option"] = temp

            update_options_with_args("transcribe_option", args)
            args.pop("transcribe_option")
            args.update(isolate_useful_options(args, _get_decoding_options_type()))
            args["threads"] = threads

        elif mode == "refine":
            temp = args["refine_option"]

            args = isolate_useful_options(args, method)
            args["refine_option"] = temp

            update_options_with_args("refine_option", args)
            args.pop("refine_option")
            args["threads"] = threads

        elif mode == "save":
            temp = args["save_option"]
            args["filepath"] = kwargs.get("save_path")
            args["path"] = kwargs.get("save_path")
            args["word_level"] = kwargs.get("word_level")
            args["segment_level"] = kwargs.get("segment_level")

            args = isolate_useful_options(args, method)
            args["save_option"] = temp

            update_options_with_args("save_option", args)
            args.pop("save_option")

        args.pop("download_root", None)
        args["success"] = True

        if kwargs.pop("show_parsed", True):
            logger.debug(f"Mode {mode} args get: {args}")
    except ValueError as exc:
        logger.exception(exc)
        args["success"] = False
        args["msg"] = str(exc)
    except Exception as exc:
        logger.exception(exc)
        args["success"] = False
        args["msg"] = str(exc)

    return args
