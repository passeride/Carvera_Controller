"""Opt-in FreeCAD CAM inbox for Carvera Community Controller.

Consumes JSON requests created by FreeCAD_SendToCommunity.FCMacro and reuses
the Controller's uploadLocalFile/select_file. Never starts a CNC program,
probes, jogs, or exposes any network service.
"""
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from kivy.clock import Clock

log = logging.getLogger(__name__)
DEFAULT_INBOX = Path.home() / "Documents" / "Carvera-FreeCAD-Inbox"
MAX_BYTES = 256 * 1024 * 1024
UPLOAD_TIMEOUT_SECONDS = 600


def safe_file(request, inbox):
    """Validate filename, location, size, and checksum before upload."""
    name = request.get("filename")
    if (not isinstance(name, str) or not name or name in (".", "..")
            or name != os.path.basename(name) or "/" in name or "\\" in name
            or not name.lower().endswith(".nc")):
        raise ValueError("Invalid .nc filename")
    base = Path(inbox).resolve()
    path = base / name
    if path.is_symlink() or path.resolve().parent != base or not path.is_file():
        raise ValueError("G-code file missing, symlinked, or outside inbox")
    size = path.stat().st_size
    if not (0 < size <= MAX_BYTES) or size != request.get("bytes"):
        raise ValueError("G-code file size mismatch")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != request.get("sha256"):
        raise ValueError("SHA-256 mismatch")
    return path


def finalize(request_path, status, message, remote=""):
    if status not in ("done", "failed"):
        raise ValueError("Invalid status")
    request_path = Path(request_path)
    if not request_path.name.endswith(".processing.json"):
        raise ValueError("Expected claimed request")
    marker = request_path.with_name(
        request_path.name.replace(".processing.json", "." + status + ".json"))
    temp = marker.with_name(marker.name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump({"status": status, "message": str(message), "remote": str(remote),
                   "timestamp": time.time()}, stream, ensure_ascii=False, indent=2)
    os.replace(str(temp), str(marker))
    request_path.unlink(missing_ok=True)
    log.info("FreeCAD inbox %s: %s", status, marker)


class InboxBridge:
    def __init__(self, app, inbox=None):
        self.app = app
        self.inbox = Path(inbox or DEFAULT_INBOX).expanduser()
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.active = None
        self.started = 0.0
        self.original_dir = None
        self.select = True

    def tick(self, _dt):
        root = getattr(self.app, "root", None)
        if root is None:
            return
        if self.active is not None:
            if time.monotonic() - self.started > UPLOAD_TIMEOUT_SECONDS:
                self._complete("failed",
                               "Upload callback timed out. Check CNC file list before retrying.")
            return
        if getattr(self.app, "state", None) != "Idle" or getattr(self.app, "playing", False):
            return
        if getattr(root, "uploading", False) or getattr(root, "downloading", False):
            return
        controller = getattr(root, "controller", None)
        if controller is None or getattr(controller, "stream", None) is None:
            return
        popup = getattr(root, "file_popup", None)
        view = getattr(popup, "remote_rv", None)
        if view is None or getattr(popup, "firmware_mode", False):
            return
        for pending in sorted(self.inbox.glob("*.request.json")):
            active = pending.with_name(
                pending.name.replace(".request.json", ".processing.json"))
            try:
                data = json.loads(pending.read_text(encoding="utf-8"))
                if data.get("schema") != 1:
                    raise ValueError("Unsupported inbox schema")
                path = safe_file(data, self.inbox)
            except Exception as exc:
                os.replace(str(pending), str(active))
                finalize(active, "failed", exc)
                continue
            os.replace(str(pending), str(active))
            self.active = active
            self.started = time.monotonic()
            self.original_dir = view.curr_dir
            self.select = bool(data.get("select", True))
            try:
                # Existing Controller upload selects destination from remote_rv.
                view.curr_dir = view.base_dir  # normally /sd/gcodes
                root.uploadLocalFile(str(path), self._uploaded)
            except Exception as exc:
                self._complete("failed", exc)
            return

    def _uploaded(self, remote_path, cached_path):
        # The upload worker may call this off the Kivy UI thread.
        def finish(_dt):
            if self.active is None:
                return
            try:
                if self.select:
                    self.app.root.select_file(remote_path, cached_path)
                self._complete("done", "Upload succeeded"
                               + ("; file selected" if self.select else ""), remote_path)
            except Exception as exc:
                self._complete("failed",
                               "Uploaded, but selection failed: %s" % exc, remote_path)
        Clock.schedule_once(finish, 0)

    def _complete(self, status, message, remote=""):
        current = self.active
        if current is None:
            return
        try:
            root = getattr(self.app, "root", None)
            if root is not None and self.original_dir is not None:
                view = root.file_popup.remote_rv
                if view.curr_dir == view.base_dir:
                    view.curr_dir = self.original_dir
            finalize(current, status, message, remote)
        finally:
            self.active = None
            self.original_dir = None


def install(app, inbox=None):
    """Enable polling once. Caller must check FREECAD_BRIDGE_ENABLED."""
    previous = getattr(app, "_freecad_inbox_bridge", None)
    if previous is not None:
        return previous
    bridge = InboxBridge(app, inbox)
    app._freecad_inbox_bridge = bridge
    Clock.schedule_interval(bridge.tick, 1.0)
    log.info("FreeCAD bridge enabled: %s", bridge.inbox)
    return bridge
