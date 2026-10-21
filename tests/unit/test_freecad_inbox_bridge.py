"""Offline FreeCAD bridge regression tests. No real CNC and no Kivy dependency."""
import hashlib
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class FakeClock:
    @staticmethod
    def schedule_once(callback, delay):
        callback(delay)

    @staticmethod
    def schedule_interval(callback, interval):
        return None


kivy = types.ModuleType("kivy")
clock = types.ModuleType("kivy.clock")
clock.Clock = FakeClock
module_path = Path(__file__).resolve().parents[2] / "carveracontroller" / "freecad_inbox_bridge.py"
with patch.dict(sys.modules, {"kivy": kivy, "kivy.clock": clock}):
    spec = importlib.util.spec_from_file_location("testable_freecad_inbox", module_path)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)


class View:
    base_dir = "/sd/gcodes"

    def __init__(self):
        self.curr_dir = "/sd/gcodes/previous"


class Root:
    def __init__(self):
        self.uploading = False
        self.downloading = False
        self.controller = types.SimpleNamespace(stream=object())
        self.file_popup = types.SimpleNamespace(remote_rv=View(), firmware_mode=False)
        self.uploaded = []
        self.selected = []

    def uploadLocalFile(self, filename, callback):
        self.uploaded.append((filename, self.file_popup.remote_rv.curr_dir))
        self.callback = callback
        self.uploading = True

    def select_file(self, remote, cached):
        self.selected.append(remote)


class App:
    def __init__(self):
        self.root = Root()
        self.state = "Idle"
        self.playing = False


def queue(folder, **changes):
    data = b"T60 M6\nG1 X1"
    (folder / "test.nc").write_bytes(data)
    request = dict(schema=1, filename="test.nc", bytes=len(data),
                   sha256=hashlib.sha256(data).hexdigest(), select=True)
    request.update(changes)
    pending = folder / "abc.request.json"
    pending.write_text(json.dumps(request))
    return pending


class FreeCADInboxTest(unittest.TestCase):
    def test_success_select_and_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            queue(folder)
            app = App()
            worker = bridge.InboxBridge(app, folder)
            worker.tick(0)
            self.assertEqual(len(app.root.uploaded), 1)
            self.assertEqual(app.root.uploaded[0][1], "/sd/gcodes")
            app.root.callback("/sd/gcodes/test.nc", "/tmp/test.nc")
            self.assertEqual(app.root.selected, ["/sd/gcodes/test.nc"])
            self.assertTrue(list(folder.glob("*.done.json")))
            self.assertEqual(app.root.file_popup.remote_rv.curr_dir, "/sd/gcodes/previous")

    def test_idle_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            req = queue(folder)
            app = App()
            app.state = "Run"
            bridge.InboxBridge(app, folder).tick(0)
            self.assertTrue(req.exists())
            self.assertFalse(app.root.uploaded)

    def test_other_upload_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            req = queue(folder)
            app = App()
            app.root.uploading = True
            bridge.InboxBridge(app, folder).tick(0)
            self.assertTrue(req.exists())

    def test_altered_checksum(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            queue(folder)
            (folder / "test.nc").write_text("altered")
            app = App()
            bridge.InboxBridge(app, folder).tick(0)
            self.assertTrue(list(folder.glob("*.failed.json")))
            self.assertFalse(app.root.uploaded)

    def test_path_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                bridge.safe_file({"filename": "../bad.nc"}, temp)

    def test_wrong_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            queue(folder, schema=2)
            bridge.InboxBridge(App(), folder).tick(0)
            self.assertTrue(list(folder.glob("*.failed.json")))

    def test_skip_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            queue(folder, select=False)
            app = App()
            worker = bridge.InboxBridge(app, folder)
            worker.tick(0)
            app.root.callback("/sd/gcodes/test.nc", "/tmp/test.nc")
            self.assertEqual(app.root.selected, [])
            self.assertTrue(list(folder.glob("*.done.json")))

    def test_firmware_ui_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            req = queue(folder)
            app = App()
            app.root.file_popup.firmware_mode = True
            bridge.InboxBridge(app, folder).tick(0)
            self.assertTrue(req.exists())

    def test_timeout_is_not_success(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            queue(folder)
            app = App()
            worker = bridge.InboxBridge(app, folder)
            worker.tick(0)
            worker.started -= bridge.UPLOAD_TIMEOUT_SECONDS + 1
            worker.tick(0)
            self.assertTrue(list(folder.glob("*.failed.json")))
            self.assertFalse(list(folder.glob("*.done.json")))

    def test_missing_connection(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            req = queue(folder)
            app = App()
            app.root.controller.stream = None
            bridge.InboxBridge(app, folder).tick(0)
            self.assertTrue(req.exists())


if __name__ == "__main__":
    unittest.main()
