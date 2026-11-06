"""Presentation must preserve routing IDs and a single reading order."""
import io
import os
import re
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tools.launcher.interface import show_menu


class MenuTests(unittest.TestCase):
    def test_numeric_order_and_ids_at_each_width(self):
        actions = {"25": ("Музика", None), "2": ("Перезапустити", None),
                   "12": ("Налаштування", None), "1": ("Запустити", None)}
        for width in (40, 80, 120):
            with self.subTest(width=width):
                output = io.StringIO()
                with patch("tools.launcher.interface.shutil.get_terminal_size",
                           return_value=os.terminal_size((width, 24))), redirect_stdout(output):
                    show_menu(actions, "running")
                text = output.getvalue()
                self.assertEqual(re.findall(r"^\s*\[(\d+)\]", text, re.MULTILINE),
                                 ["0", "1", "2", "12", "25"])
                self.assertIn("ПРАЦЮЄ", text)
                self.assertNotIn("\x1b", text)
                self.assertTrue(all(len(line) <= width for line in text.splitlines()))
