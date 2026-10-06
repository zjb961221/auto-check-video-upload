import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import tkinter as tk
from tkinter import ttk
from ui_theme import read_preferences, save_preferences, PALETTES, ZOOMS
import app


class PreferenceTests(unittest.TestCase):
    def test_only_supported_values_are_restored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'appearance.json'
            self.assertEqual(read_preferences(path), {})
            for content in ('bad json', '[]', '{"zoom":true}', '{"zoom":999,"theme":"unknown"}'):
                path.write_text(content)
                self.assertEqual(read_preferences(path), {})
            save_preferences(path, {'zoom':130,'theme':'清爽浅色'})
            self.assertEqual(read_preferences(path), {'zoom':130,'theme':'清爽浅色'})


@unittest.skipUnless(os.name=='nt' or os.environ.get('DISPLAY'), 'Requires a desktop')
class ResponsiveWindowTests(unittest.TestCase):
    def test_resize_zoom_theme_preserve_inputs_and_keep_navigation_reachable(self):
        with tempfile.TemporaryDirectory() as folder, patch('app.SETTINGS',Path(folder)/'connection.json'), patch('app.configure_logging',return_value=MagicMock()):
            window=app.App()
            try:
                window.geometry('900x650+0+0')
                window.update()
                panel=window.workflow
                self.assertTrue(panel.step_picker.winfo_ismapped())
                self.assertFalse(panel.sidebar.winfo_ismapped())
                for var in panel.checks:
                    var.set(True)
                panel.note_changed();panel.next()
                window.vars['host'].set('unchanged-host')
                snapshot=panel.run.states[:]
                for zoom in ZOOMS:
                    window.design.set_zoom(zoom)
                    window.update()
                    self.assertEqual(window.vars['host'].get(),'unchanged-host')
                    self.assertEqual(panel.run.states,snapshot)
                    for widget in (panel.previous,panel.next_button):
                        self.assertTrue(widget.winfo_ismapped())
                        self.assertGreater(widget.winfo_width(),20)
                        self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(),window.winfo_rootx()+window.winfo_width()+2)
                        self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),window.winfo_rooty()+window.winfo_height()+2)
                for _ in range(2):
                    window.design.toggle_theme();window.update()
                    self.assertEqual(panel.output.cget('background'),window.design.colors['panel'])
                    self.assertEqual(window.vars['host'].get(),'unchanged-host')
                window.design.set_zoom(100)
                window.geometry('1280x850+0+0');window.update()
                self.assertTrue(panel.sidebar.winfo_ismapped())
                self.assertFalse(panel.step_picker.winfo_ismapped())
                window.design.toggle_fullscreen();window.update()
                window.design.exit_fullscreen();window.update()
                self.assertFalse(window.design.fullscreen)
                prefs=read_preferences(Path(folder)/'appearance.json')
                self.assertEqual(prefs['zoom'],100)
                self.assertEqual(prefs['theme'],window.design.theme)
            finally:
                window.destroy()

    def test_advanced_dialogs_have_scrollable_fallback(self):
        with tempfile.TemporaryDirectory() as folder, patch('app.SETTINGS',Path(folder)/'connection.json'), patch('app.configure_logging',return_value=MagicMock()):
            root=app.App()
            try:
                root.design.set_zoom(150)
                from api_ui import ApiWindow
                from updates_ui import UpdateWindow
                from ui_theme import ScrollFrame
                api=ApiWindow(root,Path(__file__).parents[1]/'api_requests.json',Path(folder)/'api.json',MagicMock())
                api.geometry('850x550');root.update()
                viewport=next(x for x in api.winfo_children() if isinstance(x,ScrollFrame))
                self.assertGreater(viewport.content.winfo_height(),viewport.canvas.winfo_height())
                api.destroy()
                update=UpdateWindow(root,dict(host='localhost',port='3306',database='fixture',user='fixture'),Path(__file__).parents[1]/'updates.example.json',MagicMock())
                update.geometry('850x550');root.update()
                viewport=next(x for x in update.winfo_children() if isinstance(x,ScrollFrame))
                self.assertGreater(viewport.content.winfo_height(),viewport.canvas.winfo_height())
                update.destroy()
            finally:
                root.destroy()
