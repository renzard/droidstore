"""Bundled (confined) mode must never download code at runtime.

    python3 tests/test_bundled.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

tmp = tempfile.mkdtemp()
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = os.path.join(tmp, var)
os.environ["APP_ID"] = "droidstore.example_droidstore_0.1.0"

import store  # noqa: E402

assert store.APP == "droidstore.example", store.APP  # writable dir follows the package name
assert os.path.basename(store.DATA_DIR) == "droidstore.example"

events = []
store.pyotherside = type("P", (), {"send": staticmethod(lambda *a: events.append(a))})
store.BUNDLED = True
store._fetch_system_lib = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not download in bundled mode"))
assert store._ensure_libs(["/x/atl"]) is None  # never downloads, never blocks the launch

# ELF dependency check (no ldd): any ELF64 binary of this machine works as a sample
exe = "/bin/ls"
needed = store._elf_needed(exe)
assert "libc.so.6" in needed, needed
assert store._elf_needed(__file__) == []  # not an ELF
assert "libc.so.6" in store._missing_libs_py(exe, search_dirs=[])
libdirs = ["/lib/%s-linux-gnu" % store.platform.machine(), "/usr/lib/%s-linux-gnu" % store.platform.machine(), "/lib", "/usr/lib"]
assert store._missing_libs_py(exe, search_dirs=libdirs) == [], store._missing_libs_py(exe, search_dirs=libdirs)

store.install_sdk()
assert events and events[-1][0] == "sdk_error", events

print("test_bundled: OK")

# launch failure in bundled mode: the error is shown (no crash in the diagnosis path)
fake = os.path.join(tmp, "fake-atl")
with open(fake, "w") as f:
    f.write("#!/bin/sh\necho 'error while loading shared libraries: libx.so.1' >&2\nexit 127\n")
os.chmod(fake, 0o755)
apk = os.path.join(store.APK_DIR, "a.apk")
open(apk, "wb").write(b"x")
store._write_json(store.LIBRARY_FILE, {"org.a": {"pkgId": "org.a", "name": "A", "apk": apk}})
store.set_settings({"atl_bin": fake})
res = store.launch("org.a")
assert not res["ok"] and "ATL exited (code 127)" in res["error"] and "libx.so.1" in res["error"], res
print("test_bundled launch error: OK")
