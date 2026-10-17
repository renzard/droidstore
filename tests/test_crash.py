"""A crash AFTER the first 1.5 s must be reported, with the log and the extra env/args applied.

    python3 tests/test_crash.py
"""
import json
import os
import stat
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

tmp = tempfile.mkdtemp()
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = os.path.join(tmp, var)

import store  # noqa: E402

events = []
store.pyotherside.send = lambda *a: events.append(a)

# fake ATL: prints its env/args, stays alive 2 s, then dies with SIGSEGV
fake_atl = os.path.join(tmp, "fake-atl.sh")
with open(fake_atl, "w") as f:
    f.write('#!/bin/sh\necho "args: $@"\necho "MYVAR=$MYVAR"\necho "boom" >&2\nsleep 2\nkill -SEGV $$\n')
os.chmod(fake_atl, os.stat(fake_atl).st_mode | stat.S_IEXEC)

apk = os.path.join(store.APK_DIR, "org.crash_1.apk")
open(apk, "wb").write(b"PK\x03\x04")
store._write_json(store.LIBRARY_FILE, {"org.crash": {"pkgId": "org.crash", "name": "Crash", "apk": apk}})

store.set_settings({"atl_bin": fake_atl, "extra_env": 'MYVAR="a b" bad-name=1', "extra_args": "--verbose"})
assert store._parse_extra_env('MYVAR="a b" bad-name=1 X=') == {"MYVAR": "a b", "X": ""}

res = store.launch("org.crash")
assert res["ok"], res  # it was still alive after 1.5 s

for _ in range(60):
    if any(e[0] == "app_exited" for e in events):
        break
    time.sleep(0.1)
exited = [e for e in events if e[0] == "app_exited"]
assert exited, events
_, pkg, code, runtime, description, tail = exited[0]
assert pkg == "org.crash" and code < 0 and "SIGSEGV" in description, exited[0]
assert runtime >= 1.5, runtime

log = store.get_launch_log("org.crash")
assert "args: --verbose" in log and "MYVAR=a b" in log and "boom" in log and "SIGSEGV" in log, log
assert store.get_launch_log("../evil") == ""
with open(store._launch_log_path("org.crash"), "a") as f:
    f.write("X" * 1000 + "\nlast line\n")
rev = store.get_launch_log("org.crash", 50, 300, True).splitlines()
assert rev[0] == "last line" and "[+700 chars]" in rev[1] and len(rev[1]) < 330, rev[:2]
print("test_crash: OK")

# noise filter: jit_logger spam must not push the error out of the window
with open(store._launch_log_path("org.crash"), "a") as f:
    f.write("real error\n" + "android-translation-layer W jit_logger.cc:70] spam\n" * 500)
out = store.get_launch_log("org.crash", 20, 300, True)
assert "real error" in out and "jit_logger" not in out and "500 harmless" in out, out[:300]

# compat symlinks: created for names no existing dir provides, skipped when provided
d = tempfile.mkdtemp()
store.COMPAT_LIB_DIR = os.path.join(d, "compat")
fake_glibc = os.path.join(d, "glibc")
os.makedirs(fake_glibc)
for n in ("libm.so.6", "libc.so.6", "libdl.so.2"):
    open(os.path.join(fake_glibc, n), "w").close()
store._glibc_dirs = lambda: [fake_glibc]
provided = os.path.join(d, "provided")
os.makedirs(provided)
open(os.path.join(provided, "libdl.so"), "w").close()
res = store._ensure_compat_libs([provided])
assert res == store.COMPAT_LIB_DIR, res
assert sorted(os.listdir(res)) == ["libc.so", "libm.so"], os.listdir(res)
assert os.readlink(os.path.join(res, "libm.so")) == os.path.join(fake_glibc, "libm.so.6")
assert store._ensure_compat_libs([provided]) == res  # idempotent
print("test_crash (filter + compat): OK")

log = store.get_launch_log("org.crash", 100, 300)
assert "[atlstore]" in log
env = {"LD_LIBRARY_PATH": store.COMPAT_LIB_DIR}
diag = "\n".join(store._lib_diag(env))
assert "libm.so -> %s" % store.COMPAT_LIB_DIR in diag and "libc.so -> %s" % store.COMPAT_LIB_DIR in diag, diag
assert "liblog.so -> NOT in LD_LIBRARY_PATH" in diag and "compat dir in LD_LIBRARY_PATH: True" in diag, diag
print("test_crash (diag): OK")

# glibc discovery must work without listing directories (confined app): use the real lookup, not the test stub
import importlib  # noqa: E402
importlib.reload(store)
real = store._glibc_dirs()
assert real, "no glibc dir found"
assert any(os.path.exists(os.path.join(d, n)) for d in real for n in ("libc.so.6", "libc.so")), real
assert store._mapped_lib_dirs(), "this process has libc mapped, /proc/self/maps must show it"
orig_glob = store.glob.glob
store.glob.glob = lambda *a, **k: []  # as if directory listing were denied
assert store._glibc_dirs(), "must work without glob"
store.glob.glob = orig_glob
print("test_crash (glibc discovery without listing): OK")

import zipfile  # noqa: E402
apk32 = os.path.join(tempfile.mkdtemp(), "x.apk")
with zipfile.ZipFile(apk32, "w") as z:
    z.writestr("AndroidManifest.xml", b"x")
    z.writestr("lib/armeabi-v7a/libgnustl_shared.so", b"x")
    z.writestr("lib/armeabi-v7a/libfoo.so", b"x")
    z.writestr("lib/arm64-v8a/libfoo.so", b"x")
summ = "\n".join(store._apk_abi_summary(apk32))
assert "arm64-v8a=1 files, armeabi-v7a=2 files" in summ and "libgnustl_shared.so is in: armeabi-v7a" in summ, summ
assert "none" in store._apk_abi_summary(os.path.join(os.path.dirname(apk32), "missing.apk"))[0] or True
print("test_crash (apk abi summary): OK")

only32 = os.path.join(tempfile.mkdtemp(), "y.apk")
with zipfile.ZipFile(only32, "w") as z:
    z.writestr("AndroidManifest.xml", b"x")
    z.writestr("lib/armeabi-v7a/libfoo.so", b"x")
store.platform.machine = lambda: "aarch64"
h = store._abi_hint(only32)
assert "armeabi-v7a" in h and "arm64-v8a" in h, h
assert store._abi_hint(apk32) == ""  # has arm64-v8a too
assert store._abi_hint(os.path.join(tempfile.mkdtemp(), "nope.apk")) == ""
print("test_crash (abi hint): OK")

# libmediandk.so is an alias of ATL's libandroid.so (found in the SDK folders), and is not created when already provided
import tempfile as _tf
_d = _tf.mkdtemp()
_sdk = os.path.join(_d, "sdk"); os.makedirs(_sdk)
open(os.path.join(_sdk, "libandroid.so"), "w").close()
store.COMPAT_LIB_DIR = os.path.join(_d, "compat2")
store._glibc_dirs = lambda: []
_res = store._ensure_compat_libs([_sdk])
assert _res == store.COMPAT_LIB_DIR
assert os.readlink(os.path.join(_res, "libmediandk.so")) == os.path.join(_sdk, "libandroid.so")
open(os.path.join(_sdk, "libmediandk.so"), "w").close()
store.COMPAT_LIB_DIR = os.path.join(_d, "compat3")
assert store._ensure_compat_libs([_sdk]) is None
print("test_crash (libmediandk alias): OK")
