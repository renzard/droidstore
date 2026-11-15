"""App drawer entries (.desktop files) for installed apps.

    python3 tests/test_shortcuts.py
"""
import os
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

tmp = tempfile.mkdtemp()
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = os.path.join(tmp, var)
os.environ["APP_ID"] = "droidstore.example_droidstore_0.1.0"
apps_dir = os.path.join(tmp, "applications")
os.environ["DROIDSTORE_APPLICATIONS_DIR"] = apps_dir

import store  # noqa: E402

store.pyotherside = type("P", (), {"send": staticmethod(lambda *a: None)})
assert store.get_settings()["launcher_shortcuts"] == "0"  # off by default: the OpenStore build may not write entries
store.set_settings({"launcher_shortcuts": "1"})

# an APK with an icon inside
apk = os.path.join(store.APK_DIR, "org.example.app_1.apk")
with zipfile.ZipFile(apk, "w") as z:
    z.writestr("AndroidManifest.xml", b"x")
    z.writestr("res/mipmap-hdpi/ic_launcher.png", b"\x89PNG-small")
    z.writestr("res/mipmap-xxhdpi/ic_launcher.png", b"\x89PNG-bigger-one")
    z.writestr("res/mipmap-xxhdpi/ic_launcher_foreground.png", b"\x89PNG-should-be-ignored-because-foreground")
store._write_json(store.LIBRARY_FILE, {"org.example.app": {
    "pkgId": "org.example.app", "name": "Example\nApp", "summary": "", "icon": "", "version": "1",
    "versionCode": 1, "apk": apk}})

assert store.add_shortcut("org.example.app")["ok"]
path = os.path.join(apps_dir, "droidstore-app-org.example.app.desktop")
text = open(path, encoding="utf-8").read()
assert "Name=Example App\n" in text, text  # no newline injection through the name
assert "X-Ubuntu-Touch=true" in text and "Terminal=false" in text
exec_line = [l for l in text.splitlines() if l.startswith("Exec=")][0]
assert exec_line.startswith("Exec=python3 \""), exec_line  # no env wrapper, every argument quoted whole
assert exec_line.endswith('launcher.py" "org.example.app" "droidstore.example"'), exec_line
icon = [l for l in text.splitlines() if l.startswith("Icon=")][0][5:]
assert open(icon, "rb").read() == b"\x89PNG-bigger-one", icon  # largest non-foreground icon of the APK

# an identical entry is not rewritten (Lomiri reloads the app list on every change)
before = os.stat(path).st_mtime_ns
assert store.add_shortcut("org.example.app")["ok"]
assert os.stat(path).st_mtime_ns == before
assert store._read_shortcut_state() == {"org.example.app"}

# stale entry of an app that is not in the library is removed by the sync, the installed one is kept
stale = os.path.join(apps_dir, "droidstore-app-gone.app.desktop")
open(stale, "w").write("[Desktop Entry]\n")
other = os.path.join(apps_dir, "someone-else.desktop")
open(other, "w").write("[Desktop Entry]\n")
result = store.sync_shortcuts()
assert result == {"ok": True, "created": 1, "removed": 1, "error": ""}, result
assert not os.path.exists(stale) and os.path.exists(other) and os.path.exists(path)

# the confined build may not list the folder: the state file alone is enough to clean up
store.add_shortcut("org.example.app")
store._write_shortcut_state({"org.example.app", "gone.too"})
open(os.path.join(apps_dir, "droidstore-app-gone.too.desktop"), "w").write("[Desktop Entry]\n")
real_listdir = os.listdir
os.listdir = lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "Permission denied"))
try:
    result = store.sync_shortcuts()
finally:
    os.listdir = real_listdir
assert result["ok"] and result["removed"] == 1 and result["created"] == 1, result
assert not os.path.exists(os.path.join(apps_dir, "droidstore-app-gone.too.desktop"))

# turned off in the settings: nothing is created and the sync clears them
store.set_settings({"launcher_shortcuts": "0"})
assert not store.add_shortcut("org.example.app")["ok"]
assert store.sync_shortcuts()["removed"] == 1 and not os.path.exists(path)
store.set_settings({"launcher_shortcuts": "1"})

# deleting the app removes its entry
store.add_shortcut("org.example.app")
store.remove("org.example.app")
assert not os.path.exists(path)

# a folder we cannot write to gives a readable error, not a crash
os.environ["DROIDSTORE_APPLICATIONS_DIR"] = "/proc/nope/applications"
store._write_json(store.LIBRARY_FILE, {"a.b": {"pkgId": "a.b", "name": "A", "apk": apk}})
outcome = store.add_shortcut("a.b")
assert not outcome["ok"] and "droidstore-drawer.apparmor" in outcome["error"], outcome

# an unwritable entry file (what the confined build did before the write_path rule): readable error, no leftover .part
locked = os.path.join(tmp, "locked")
os.makedirs(locked)
os.environ["DROIDSTORE_APPLICATIONS_DIR"] = locked
os.chmod(locked, 0o500)
try:
    if os.access(locked, os.W_OK):
        print("(running as root: the read-only folder check is skipped)")
    else:
        outcome = store.add_shortcut("a.b")
        assert not outcome["ok"] and "Permission denied" in outcome["error"], outcome
        assert os.listdir(locked) == [], os.listdir(locked)
finally:
    os.chmod(locked, 0o700)

# AppArmor denying the rename (simulated, also works as root): readable error and no leftover .part
os.environ["DROIDSTORE_APPLICATIONS_DIR"] = os.path.join(tmp, "denied")
os.makedirs(os.path.join(tmp, "denied"))
real_replace = os.replace
os.replace = lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "Permission denied"))
try:
    outcome = store.add_shortcut("a.b")
finally:
    os.replace = real_replace
assert not outcome["ok"] and "Permission denied" in outcome["error"], outcome
assert os.listdir(os.path.join(tmp, "denied")) == [], os.listdir(os.path.join(tmp, "denied"))

# the background sync reports through an event
events = []
store.pyotherside = type("P", (), {"send": staticmethod(lambda *a: events.append(a))})
os.environ["DROIDSTORE_APPLICATIONS_DIR"] = apps_dir
assert store.sync_shortcuts_async() is True
for _ in range(100):
    if events:
        break
    import time
    time.sleep(0.05)
assert events and events[0][0] == "shortcuts_synced" and events[0][1] is True, events

print("test_shortcuts: OK")
