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
        actions = {"21": ("Музика", None), "2": ("Перезапустити", None),
                   "12": ("Налаштування", None), "1": ("Запустити", None)}
        for width in (40, 80, 120):
            with self.subTest(width=width):
                output = io.StringIO()
                with patch("tools.launcher.interface.shutil.get_terminal_size",
                           return_value=os.terminal_size((width, 24))), redirect_stdout(output):
                    show_menu(actions, "running")
                text = output.getvalue()
                self.assertEqual(re.findall(r"^\s*\[(\d+)\]", text, re.MULTILINE),
                                 ["0", "1", "2", "12", "21"])
                self.assertIn("ПРАЦЮЄ", text)
                self.assertNotIn("\x1b", text)
                self.assertTrue(all(len(line) <= width for line in text.splitlines()))


    def test_installed_branch_sha_is_visible_without_widening_terminal(self):
        actions = {"1": ("Запустити", None), "6": ("Оновити з GitHub", None)}
        for width in (40, 80, 100):
            with self.subTest(width=width):
                output = io.StringIO()
                with patch("tools.launcher.interface.shutil.get_terminal_size",
                           return_value=os.terminal_size((width, 24))), redirect_stdout(output):
                    show_menu(actions, "running", "improvement/probot-readiness · a1b2c3d4e5")
                rendered = output.getvalue()
                self.assertIn("Версія:", rendered)
                self.assertIn("improvement/probot-readiness", rendered)
                self.assertIn("a1b2c3d4e5", rendered)
                self.assertTrue(all(len(line) <= width for line in rendered.splitlines()))
