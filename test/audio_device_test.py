from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.audio.device import AudioDeviceSettings, get_device_details


class FakePyAudioDeviceBackend:
    def get_device_info_by_host_api_device_index(self, device_index: int, host_index: int):
        self.last_lookup = (device_index, host_index)
        return {
            "index": 9,
            "name": "Primary Mic",
            "defaultSampleRate": 48000,
            "maxInputChannels": 2,
            "isLoopbackDevice": True,
        }


class AudioDeviceTests(unittest.TestCase):
    def test_get_device_details_accepts_explicit_settings_adapter(self) -> None:
        settings = AudioDeviceSettings(
            cache={
                "mic": "[ID: 3,4] | Primary Mic",
                "chunk_size_mic": 960,
                "auto_sample_rate_mic": True,
                "sample_rate_mic": 16000,
                "auto_channels_mic": False,
                "channels_mic": "mono",
            }
        )
        backend = FakePyAudioDeviceBackend()

        success, detail = get_device_details("mic", settings, backend, debug=False)

        self.assertTrue(success)
        self.assertEqual(backend.last_lookup, (3, 4))
        self.assertEqual(detail["sample_rate"], 48000)
        self.assertEqual(detail["chunk_size"], 960)
        self.assertEqual(detail["num_of_channels"], 1)


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
