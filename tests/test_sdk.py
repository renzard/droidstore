"""Tests the automatic ATL SDK download with a fake SDK on localhost.

    python3 tests/test_sdk.py        (needs zstandard or zstd on the PC)
"""
import hashlib
import http.server
import io
import os
import sys
import tarfile
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

tmp = tempfile.mkdtemp()
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = os.path.join(tmp, var)

srv = os.path.join(tmp, "srv")
os.makedirs(srv)

# fake SDK: usr/bin/android-translation-layer + usr/lib/x/libfoo.so + symlink
tar_path = os.path.join(srv, "atl-sdk-%s.tar" % ("arm64" if os.uname().machine == "aarch64" else os.uname().machine))
with tarfile.open(tar_path, "w") as tar:
    def add(name, data, mode=0o644):
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mode = mode
        tar.addfile(info, io.BytesIO(data))
    add("usr/bin/android-translation-layer", b"#!/bin/sh\necho ok\n", 0o755)
    add("usr/lib/x/libfoo.so.1", b"x" * 100000)
    link = tarfile.TarInfo("usr/lib/x/libfoo.so")
    link.type = tarfile.SYMTYPE
    link.linkname = "libfoo.so.1"
    tar.addfile(link)
sys.path.append(os.path.join(ROOT, "vendor"))
import zstandard  # noqa: E402

with open(tar_path, "rb") as src, open(tar_path + ".zst", "wb") as dst:
    zstandard.ZstdCompressor().copy_stream(src, dst)
archive = tar_path + ".zst"
with open(archive, "rb") as f:
    sha = hashlib.sha256(f.read()).hexdigest()
with open(archive + ".sha256", "w") as f:
    f.write("%s  %s\n" % (sha, os.path.basename(archive)))


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=srv, **kw)

    def log_message(self, *a):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["ATL_SDK_BASE"] = "http://127.0.0.1:%d" % server.server_port

import store  # noqa: E402

events = []
store.pyotherside.send = lambda *a: events.append(a)

assert not store.sdk_status()["installed"]
assert store.get_settings()["atl_bin"] == "android-translation-layer"

store.install_sdk()
for _ in range(100):
    if any(e[0] in ("sdk_ready", "sdk_error") for e in events):
        break
    time.sleep(0.1)
assert ("sdk_ready",) in events, events

status = store.sdk_status()
assert status["installed"] and not status["busy"], status
atl = store.get_settings()["atl_bin"]
assert atl.endswith("usr/bin/android-translation-layer") and os.access(atl, os.X_OK), atl
assert os.path.islink(os.path.join(store.SDK_DIR, "usr/lib/x/libfoo.so"))
assert store._bundled_ld_path(status["path"]) == [os.path.join(status["path"], "lib/x")]
assert any(e[0] == "sdk_progress" and e[1] == "extract" for e in events)
assert not os.path.exists(os.path.join(store.DATA_DIR, "sdk-work"))
print("test_sdk: OK")
