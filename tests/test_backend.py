"""Runs the backend end-to-end with a fake F-Droid repo (localhost) and a fake ATL.

    python3 tests/test_backend.py
"""
import hashlib
import http.server
import json
import os
import stat
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

tmp = tempfile.mkdtemp()
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = os.path.join(tmp, var)

# ---- fake repo ----
repo_dir = os.path.join(tmp, "repo")
os.makedirs(repo_dir)
apk_bytes = b"PK\x03\x04 fake apk"
apk_sha = hashlib.sha256(apk_bytes).hexdigest()
for name in ("org.pure_2.apk", "org.native_1.apk"):
    with open(os.path.join(repo_dir, name), "wb") as f:
        f.write(apk_bytes)


def version(file_name, code, native):
    manifest = {"versionName": "v%d" % code, "versionCode": code, "usesSdk": {"minSdkVersion": 21}}
    if native:
        manifest["nativecode"] = native
    return {"file": {"name": "/" + file_name, "sha256": apk_sha, "size": len(apk_bytes)}, "manifest": manifest}


index = {
    "packages": {
        "org.pure": {
            "metadata": {"name": {"en-US": "Pure App"}, "summary": {"en-US": "pure java"}},
            "versions": {"a": version("org.pure_2.apk", 2, None), "b": version("org.pure_2.apk", 1, None)},
        },
        "org.native": {
            "metadata": {"name": {"en-US": "Native App"}},
            "versions": {"a": version("org.native_1.apk", 1, ["x86_64", "arm64-v8a"])},
        },
        "org.wrongabi": {
            "metadata": {"name": {"en-US": "Wrong ABI"}},
            "versions": {"a": version("org.native_1.apk", 1, ["mips"])},
        },
    }
}
with open(os.path.join(repo_dir, "index-v2.json"), "w") as f:
    json.dump(index, f)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=repo_dir, **kw)

    def log_message(self, *a):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
threading.Thread(target=server.serve_forever, daemon=True).start()
repo_url = "http://127.0.0.1:%d" % server.server_port

# ---- fake ATL ----
fake_atl = os.path.join(tmp, "fake-atl.sh")
with open(fake_atl, "w") as f:
    f.write('#!/bin/sh\necho "launched $1" > "%s/launched.txt"\nsleep 3\n' % tmp)
os.chmod(fake_atl, os.stat(fake_atl).st_mode | stat.S_IEXEC)

import store  # noqa: E402

events = []
store.pyotherside.send = lambda *a: events.append(a)

# settings
store.set_settings({"repo_url": repo_url, "atl_bin": fake_atl})
assert store.get_settings()["repo_url"] == repo_url

# catalog (with this machine's ABI)
store._update_catalog()
assert any(e[0] == "catalog_ready" for e in events), events
ids = {a["pkgId"] for a in store.search("")}
assert "org.pure" in ids and "org.wrongabi" not in ids, ids
assert [a["pkgId"] for a in store.search("pure")] == ["org.pure"]
assert all(not a["native"] for a in store.search("", pure_only=True))

# picks the newest version
assert store.search("pure")[0]["version"] == "v2"

# installation
store.install("org.pure")
for _ in range(50):
    if any(e[0] == "installed" for e in events):
        break
    time.sleep(0.1)
assert ("installed", "org.pure") in events, events
assert store.list_installed()[0]["pkgId"] == "org.pure"
assert store.search("pure")[0]["installed"] is True

# invalid name / unknown package
store.install("../evil")
store.install("nope.pkg")
assert sum(1 for e in events if e[0] == "failed") == 2

# launch
res = store.launch("org.pure")
assert res["ok"], res
time.sleep(0.3)
assert "launched" in open(os.path.join(tmp, "launched.txt")).read()

# missing ATL
store.set_settings({"atl_bin": "/nonexistent/atl"})
assert not store.launch("org.pure")["ok"]

# wrong sha256 → rejected
index["packages"]["org.pure"]["versions"]["a"]["file"]["sha256"] = "0" * 64
with open(os.path.join(repo_dir, "index-v2.json"), "w") as f:
    json.dump(index, f)
store.remove("org.pure")
events.clear()
store._update_catalog()
store.install("org.pure")
for _ in range(50):
    if any(e[0] == "failed" for e in events):
        break
    time.sleep(0.1)
assert any(e[0] == "failed" and "sha256" in e[2] for e in events), events
assert not os.listdir(store.APK_DIR), os.listdir(store.APK_DIR)

# removal
index["packages"]["org.pure"]["versions"]["a"]["file"]["sha256"] = apk_sha
with open(os.path.join(repo_dir, "index-v2.json"), "w") as f:
    json.dump(index, f)
store._update_catalog()
store.install("org.pure")
time.sleep(1)
assert store.remove("org.pure") and not store.list_installed()

# bundled SDK: LD_LIBRARY_PATH from usr/lib
sdk_lib = os.path.join(store.APP_ROOT, "usr", "lib")
created = not os.path.exists(os.path.join(store.APP_ROOT, "usr"))
os.makedirs(os.path.join(sdk_lib, "art"), exist_ok=True)
open(os.path.join(sdk_lib, "libfoo.so"), "w").close()
open(os.path.join(sdk_lib, "art", "libart.so"), "w").close()
deep = os.path.join(sdk_lib, "java", "dex", "android_translation_layer", "natives")
os.makedirs(deep, exist_ok=True)
open(os.path.join(deep, "libtranslation_layer_main.so"), "w").close()
os.makedirs(os.path.join(store.APP_ROOT, "usr", "bin"), exist_ok=True)
fake_bin = os.path.join(store.APP_ROOT, "usr", "bin", "android-translation-layer")
open(fake_bin, "w").close()
os.chmod(fake_bin, 0o755)
try:
    env = store._launch_env([os.path.join(store.APP_ROOT, "usr", "bin", "android-translation-layer")])
    assert env["LD_LIBRARY_PATH"].split(":")[:2] == [sdk_lib, os.path.join(sdk_lib, "art")], env["LD_LIBRARY_PATH"]
    assert deep in env["LD_LIBRARY_PATH"].split(":"), env["LD_LIBRARY_PATH"]
finally:
    if created:
        import shutil
        shutil.rmtree(os.path.join(store.APP_ROOT, "usr"))

print("ALL TESTS PASSED")
