"""Sounds in an app's res/raw are put where the ATL of the atl-touch SDK looks for them.

    python3 tests/test_rawaudio.py
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

import store  # noqa: E402

apk = os.path.join(tmp, "gr.learn.programming_local.apk")
with zipfile.ZipFile(apk, "w") as z:
    z.writestr("AndroidManifest.xml", b"x")
    z.writestr("res/raw/sound_click.ogg", b"OggS-click")
    z.writestr("res/raw/sound_true.mp3", b"ID3-true")
    z.writestr("res/raw/notes.txt", b"not audio")
    z.writestr("res/xy/ab.ogg", b"OggS-shortened")      # a name the build shortened: cannot be mapped
    z.writestr("res/raw/.hidden.ogg", b"nope")

dest = store._app_data_dir(apk)
assert dest.endswith(os.path.join("data", "android_translation_layer", "gr.learn.programming_local.apk_")), dest

notes = store._prepare_raw_audio(apk)
assert os.path.isfile(os.path.join(dest, "sound_click")) and open(os.path.join(dest, "sound_click"), "rb").read() == b"OggS-click"
assert open(os.path.join(dest, "sound_true"), "rb").read() == b"ID3-true"
assert not os.path.exists(os.path.join(dest, "notes")) and not os.path.exists(os.path.join(dest, "ab"))
assert not any(n.endswith(".part") for n in os.listdir(dest)), os.listdir(dest)
text = "\n".join(notes)
assert "audio resources in the APK: 4" in text and "2 copied" in text and "sound_click" in text, text
assert "left alone" in text, text

# a second start copies nothing, and a file of the same name that is not ours is not replaced
notes = store._prepare_raw_audio(apk)
assert "0 copied" in "\n".join(notes) and "2 already there" in "\n".join(notes), notes
with open(os.path.join(dest, "sound_true"), "wb") as f:
    f.write(b"something else entirely")
store._prepare_raw_audio(apk)
assert open(os.path.join(dest, "sound_true"), "rb").read() == b"something else entirely"

# no sound files / not a zip: only a note, no exception
empty = os.path.join(tmp, "empty.apk")
with zipfile.ZipFile(empty, "w") as z:
    z.writestr("classes.dex", b"x")
assert "no sound files" in store._prepare_raw_audio(empty)[0]
bad = os.path.join(tmp, "bad.apk")
open(bad, "wb").write(b"not a zip")
assert "cannot be read" in store._prepare_raw_audio(bad)[0]
print("test_rawaudio: OK")
