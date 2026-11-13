import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.launcher import diagnostics


class ReportTests(unittest.TestCase):
    def test_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "absent.db"
            self.assertEqual(diagnostics.sqlite_check(path)["code"], "database_missing")
            self.assertFalse(path.exists())

    def test_export_replaces_report_atomically_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory(prefix="Звіт з пробілами ") as directory:
            root = Path(directory)
            diagnostics.export({"report_version": 1}, root)
            path = diagnostics.export({"report_version": 2}, root)
            self.assertEqual(json.loads(path.read_text()), {"report_version": 2})
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_failed_export_preserves_previous_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = diagnostics.export({"previous": True}, root)
            with patch.object(diagnostics.os, "replace", side_effect=OSError("private-path")):
                with self.assertRaises(OSError):
                    diagnostics.export({"new": True}, root)
            self.assertEqual(json.loads(path.read_text()), {"previous": True})
            self.assertEqual(list(path.parent.iterdir()), [path])
