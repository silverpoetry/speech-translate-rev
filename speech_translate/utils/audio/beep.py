import os

from sounddevice import play
from soundfile import read

from speech_translate._path import dir_assets
from speech_translate.log_helpers import logger


def beep():
    beep_path = os.path.join(dir_assets, "beep.mp3")
    try:
        data, fs = read(beep_path)
        play(data, fs, blocking=False)
    except Exception as e:
        logger.exception(e)
