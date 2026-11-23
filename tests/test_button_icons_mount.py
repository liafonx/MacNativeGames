"""The game mounts the button icons the launcher makes (app/launcher.m sets TOTK_ROMFS_BUTTON_ICONS; runtime/romfs.c
loads that folder like a mod). A merge that took another romfs.c once dropped the loader: the launcher still made the
icons, and the game showed its own."""
import re
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / 'games/totk/runtime'


class ButtonIconsMount(unittest.TestCase):
    def test_launcher_sets_the_folder(self):
        self.assertIn('TOTK_ROMFS_BUTTON_ICONS', (RUNTIME.parent / 'app/launcher.m').read_text())

    def test_romfs_loads_the_folder(self):
        src = (RUNTIME / 'romfs.c').read_text()
        self.assertIn('getenv("TOTK_ROMFS_BUTTON_ICONS")', src)
        self.assertRegex(src, r'static const char \*overlay_taken\(')
        self.assertRegex(src, r'else overlay_load\(icons,')


if __name__ == '__main__':
    unittest.main()
