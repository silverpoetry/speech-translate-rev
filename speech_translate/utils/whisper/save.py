import csv
import json
import os
import shutil
import subprocess
import tempfile
from typing import List, Mapping, Optional, Protocol, Union

from speech_translate.log_helpers import logger
from speech_translate.utils.types import StableTsResultDict

from .stable_args import parse_args_stable_ts


class WhisperSegmentLike(Protocol):
    text: str
    start: float
    end: float


class WhisperResultLike(Protocol):
    segments: list[WhisperSegmentLike]

    def to_dict(self) -> StableTsResultDict:
        ...


class WhisperSaveSettings(Protocol):
    cache: Mapping[str, object]


def _is_whisper_result_like(value: object) -> bool:
    return hasattr(value, "segments") and hasattr(value, "to_dict")


def write_csv(
    transcript: Union[WhisperResultLike, StableTsResultDict],
    file,
    sep=",",
    text_first=True,
    format_timestamps=None,
    header=False
):
    writer = csv.writer(file, delimiter=sep)
    if format_timestamps is None:
        format_timestamps = lambda x: x  # pylint: disable=unnecessary-lambda-assignment
    if header is True:
        header = ["text", "start", "end"] if text_first else ["start", "end", "text"]
    if header:
        writer.writerow(header)
    if text_first:
        if _is_whisper_result_like(transcript):
            writer.writerows(
                [
                    [segment.text.strip(),
                     format_timestamps(segment.start),
                     format_timestamps(segment.end)] for segment in transcript.segments
                ]
            )
        else:
            writer.writerows(
                [
                    [segment["text"].strip(),
                     format_timestamps(segment['start']),
                     format_timestamps(segment['end'])] for segment in transcript['segments']
                ]
            )
    else:
        if _is_whisper_result_like(transcript):
            writer.writerows(
                [
                    [format_timestamps(segment.start),
                     format_timestamps(segment.end),
                     segment.text.strip()] for segment in transcript.segments
                ]
            )
        else:
            writer.writerows(
                [
                    [format_timestamps(segment['start']),
                     format_timestamps(segment['end']), segment["text"].strip()] for segment in transcript['segments']
                ]
            )


def fname_dupe_check(filename: str, extension: str):
    # check if file already exists
    if os.path.exists(filename + extension):
        # add (2) to the filename, but if that already exists, add (3) and so on
        i = 2
        while os.path.exists(filename + f" ({i})"):
            i += 1

        filename += f" ({i})"

    return filename


def _next_available_path(base_path: str, extension: str) -> str:
    candidate = f"{base_path}.{extension}"
    if not os.path.exists(candidate):
        return candidate

    idx = 2
    while True:
        candidate = f"{base_path} ({idx}).{extension}"
        if not os.path.exists(candidate):
            return candidate
        idx += 1


def _save_temp_srt(result: Union[WhisperResultLike, StableTsResultDict], settings: WhisperSaveSettings) -> str:
    temp_dir = tempfile.mkdtemp(prefix="st_subtitle_")
    temp_base = os.path.join(temp_dir, "subtitle")
    save_method = getattr(result, "to_srt_vtt")
    kwargs_to_pass = {
        "save_path": temp_base,
        "segment_level": settings.cache["segment_level"],
        "word_level": settings.cache["word_level"],
    }
    args = parse_args_stable_ts(settings.cache["whisper_args"], "save", save_method, **kwargs_to_pass)
    args.pop("success", None)
    save_method(**args)
    return temp_base + ".srt"


def _source_has_video(media_path: str) -> bool:
    video_ext = {
        ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v", ".ts", ".m2ts", ".mpg", ".mpeg"
    }
    return os.path.splitext(media_path)[1].lower() in video_ext


def _export_fast_mp4_with_subtitle(
    result: Union[WhisperResultLike, StableTsResultDict],
    outname: str,
    media_path: Optional[str],
    settings: WhisperSaveSettings,
) -> None:
    if not media_path or not os.path.exists(media_path):
        logger.warning("Skip MP4 export: source media path is missing or does not exist")
        return

    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        logger.warning("Skip MP4 export: ffmpeg not found in PATH")
        return

    temp_dir = None
    try:
        srt_path = _save_temp_srt(result, settings)
        temp_dir = os.path.dirname(srt_path)
        output_mp4 = _next_available_path(outname, "mp4")

        if _source_has_video(media_path):
            # Fastest path for videos: copy A/V streams and only mux subtitle track.
            cmd = [
                ffmpeg_path,
                "-y",
                "-i",
                media_path,
                "-i",
                srt_path,
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-map",
                "1:0",
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                "-c:s",
                "mov_text",
                output_mp4,
            ]
        else:
            # Audio input: generate a lightweight black video and mux subtitles.
            cmd = [
                ffmpeg_path,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=960x540:r=24",
                "-i",
                media_path,
                "-i",
                srt_path,
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-map",
                "2:0",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-tune",
                "stillimage",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-c:s",
                "mov_text",
                "-shortest",
                output_mp4,
            ]

        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            logger.error("Failed to export MP4 with subtitle")
            if proc.stderr:
                logger.error(proc.stderr.strip().splitlines()[-1])
            return

        logger.info(f"Saved MP4 subtitle video: {output_mp4}")
    except Exception as e:
        logger.exception(e)
        logger.error("Failed to export MP4 with subtitle")
    finally:
        if temp_dir and os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


def save_output_stable_ts(
    result: Union[WhisperResultLike, StableTsResultDict], outname, output_formats: List, settings: WhisperSaveSettings,
    source_media_path: Optional[str] = None
):
    output_formats_methods = {
        "srt": "to_srt_vtt",
        "ass": "to_ass",
        "json": "save_as_json",
        "vtt": "to_srt_vtt",
        "tsv": "to_tsv",
        "txt": "to_txt",
    }

    # make sure the output dir is exist
    os.makedirs(os.path.dirname(outname), exist_ok=True)

    for f_format in output_formats:
        if f_format == "mp4":
            logger.debug("Saving to mp4")
            _export_fast_mp4_with_subtitle(result, outname, source_media_path, settings)
            continue

        outname = fname_dupe_check(outname, f_format)
        logger.debug(f"Saving to {f_format}")

        # Save CSV
        if f_format == "csv":
            with open(outname + ".csv", "w", encoding="utf-8") as f_csv:
                write_csv(result, file=f_csv)

        # Save JSON
        elif f_format == "json":
            with open(fname_dupe_check(outname, f_format) + ".json", "w", encoding="utf-8") as f_json:
                res = result.to_dict() if _is_whisper_result_like(result) else result
                json.dump(res, f_json, indent=2, allow_nan=True, ensure_ascii=False)

        # Save other formats (SRT, ASS, VTT, TSV)
        else:
            save_method = getattr(result, output_formats_methods[f_format])
            kwargs_to_pass = {
                "save_path": outname,
                "segment_level": settings.cache["segment_level"],
                "word_level": settings.cache["word_level"]
            }
            if f_format == "vtt":
                kwargs_to_pass["vtt"] = True

            if f_format == "tsv":
                # must keep only segment or word level
                # prioritize word level
                logger.debug("Format is TSV so we only keep 1 type of export level")
                if kwargs_to_pass["word_level"]:
                    logger.debug("Prioritizing word level format")
                    kwargs_to_pass["segment_level"] = False
                if kwargs_to_pass["segment_level"]:
                    logger.debug("Using segment level format")
                    kwargs_to_pass["word_level"] = False

                if not kwargs_to_pass["word_level"] and not kwargs_to_pass["segment_level"]:
                    logger.warning("Somehow both word level and segment level is False ??, setting segment level to True")
                    kwargs_to_pass["word_level"] = True

            args = parse_args_stable_ts(settings.cache["whisper_args"], "save", save_method, **kwargs_to_pass)
            args.pop('success')  # no need to check, because it probably have been checked before since this is the last step
            save_method(**args)  # run the method
