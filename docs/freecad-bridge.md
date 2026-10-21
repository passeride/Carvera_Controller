# FreeCAD → Z1 inbox bridge

An optional bridge to [passeride/FreeCadMakeraBridge](https://github.com/passeride/FreeCadMakeraBridge).

Launch this source-built controller with:

~~~powershell
$env:FREECAD_BRIDGE_ENABLED = "1"
# Optional inbox override:
# $env:FREECAD_BRIDGE_INBOX = "C:\Users\you\Documents\Carvera-FreeCAD-Inbox"
poetry run python -m carveracontroller
~~~

Without FREECAD_BRIDGE_ENABLED, this fork starts as before. The local inbox defaults to
%USERPROFILE%\Documents\Carvera-FreeCAD-Inbox. The standard prebuilt upstream executable
does not contain this functionality.

When the Z1 is connected and Idle, the bridge verifies an incoming .nc file's size and SHA-256,
uploads with Community Controller's own uploadLocalFile, and optionally selects the file.
It **never starts machining**, jogs the machine, or changes work coordinates.
A done.json status reports positive upload callback, while failed.json denotes failure or
timeout. A failed timeout does not prove the remote file is absent: check the Z1 file list
before retrying. Requests left in processing.json after a crash are not retried.

Run offline tests:
~~~
python -m unittest tests.unit.test_freecad_inbox_bridge -v
~~~
