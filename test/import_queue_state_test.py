from __future__ import annotations

import os
import sys
import unittest

to_add = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(to_add)

from speech_translate.import_queue_state import ImportQueueStateStore, QueueItem


class ImportQueueStateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = ImportQueueStateStore()

    def test_add_files_deduplicates_paths(self) -> None:
        added = self.store.add_files(["a.wav", "a.wav", "b.wav"])

        self.assertEqual(added, 2)
        self.assertEqual([item["path"] for item in self.store.file_import_queue], ["a.wav", "b.wav"])

    def test_get_display_queue_merges_processing_state(self) -> None:
        self.store.file_import_queue = [{"path": "a.wav", "name": "a.wav", "status": "Waiting", "is_completed": False}]
        self.store.processing_queue = [{"path": "a.wav", "name": "a.wav", "status": "Transcribing", "is_completed": False}]

        queue = self.store.get_display_queue()

        self.assertEqual(queue[0]["status"], "Transcribing")

    def test_extract_files_to_process_skips_completed_entries(self) -> None:
        self.store.file_import_queue = [
            {"path": "a.wav", "name": "a.wav", "status": "Done", "is_completed": True},
            QueueItem(path="b.wav", name="b.wav"),
            "c.wav",
        ]

        self.assertEqual(self.store.extract_files_to_process(), ["b.wav", "c.wav"])

    def test_remove_by_index_prefers_processing_queue_mapping(self) -> None:
        self.store.file_import_queue = [
            {"path": "a.wav", "name": "a.wav", "status": "Waiting", "is_completed": False},
            {"path": "b.wav", "name": "b.wav", "status": "Waiting", "is_completed": False},
        ]
        self.store.processing_queue = [
            {"path": "b.wav", "name": "b.wav", "status": "Working", "is_completed": False},
        ]

        removed = self.store.remove_by_index(0)

        self.assertEqual(removed["path"], "b.wav")
        self.assertEqual([item["path"] for item in self.store.file_import_queue], ["a.wav"])

    def test_finalize_processing_queue_persists_processing_status(self) -> None:
        self.store.file_import_queue = [{"path": "a.wav", "name": "a.wav", "status": "Waiting", "is_completed": False}]
        self.store.processing_queue = [{"path": "a.wav", "name": "a.wav", "status": "Done", "is_completed": True}]

        self.store.finalize_processing_queue()

        self.assertEqual(self.store.file_import_queue[0]["status"], "Done")
        self.assertTrue(self.store.file_import_queue[0]["is_completed"])
        self.assertEqual(self.store.processing_queue, [])

    def test_cancel_processing_marks_active_items(self) -> None:
        self.store.processing_queue = [{"path": "a.wav", "name": "a.wav", "status": "Working", "is_completed": False}]

        result = self.store.cancel_processing()

        self.assertTrue(result)
        self.assertEqual(self.store.processing_queue[0]["status"], "Cancelled")


if __name__ == "__main__":
    unittest.main()

sys.path.remove(to_add)
