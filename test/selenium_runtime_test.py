from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.utils.translate.selenium_runtime import (
    SeleniumTranslatorManager,
    build_selenium_translator_config,
    resolve_selenium_compact_level,
)


class FakeTranslator:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class SeleniumRuntimeTests(unittest.TestCase):
    def test_resolve_selenium_compact_level_clamps_invalid_values(self) -> None:
        self.assertEqual(resolve_selenium_compact_level("bad"), 2)
        self.assertEqual(resolve_selenium_compact_level(-1), 0)
        self.assertEqual(resolve_selenium_compact_level(99), 3)

    def test_build_selenium_translator_config_uses_profile_and_defaults(self) -> None:
        config = build_selenium_translator_config(
            {
                "selenium_compact_level": 1,
                "selenium_z_order_mode": "bottom",
                "selenium_chrome_user_data_dir": "  ",
            }
        )

        self.assertEqual(config.engine_width, 360)
        self.assertEqual(config.engine_height, 210)
        self.assertAlmostEqual(config.engine_content_opacity, 0.92)
        self.assertEqual(config.win_z_order_mode, "bottom")
        self.assertIsNone(config.chrome_user_data_dir)

    def test_selenium_translator_manager_caches_singleton_instance(self) -> None:
        created = []

        def factory(config):
            created.append(config)
            return FakeTranslator()

        manager = SeleniumTranslatorManager(
            settings_snapshot_provider=lambda: {
                "selenium_compact_level": 3,
                "selenium_z_order_mode": "behind-main",
            },
            translator_factory=factory,
        )

        first = manager.get()
        second = manager.get()

        self.assertIs(first, second)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].engine_width, 280)

    def test_selenium_translator_manager_shutdown_closes_and_rebuilds_instance(self) -> None:
        created = []

        def factory(config):
            translator = FakeTranslator()
            created.append((config, translator))
            return translator

        manager = SeleniumTranslatorManager(
            settings_snapshot_provider=lambda: {
                "selenium_compact_level": 0,
                "selenium_z_order_mode": "bottom",
                "selenium_chrome_user_data_dir": "D:/profile",
            },
            translator_factory=factory,
        )

        first = manager.get()
        manager.shutdown()
        second = manager.get()

        self.assertEqual(first.closed, 1)
        self.assertIsNot(first, second)
        self.assertEqual(len(created), 2)
        self.assertEqual(created[0][0].chrome_user_data_dir, "D:/profile")


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
