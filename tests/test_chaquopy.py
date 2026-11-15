"""The Chaquopy compat patch makes ctypes survive dlopen(NULL) inside the APK's stdlib asset, and changes nothing else.

    python3 tests/test_chaquopy.py
"""
import io
import marshal
import os
import sys
import tempfile
import types
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
tmp = tempfile.mkdtemp()
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = os.path.join(tmp, var)
import store  # noqa: E402

# a stand-in for ctypes/__init__: imports dlopen from _ctypes and calls it with None, like the real one
CTYPES = ("from _ctypes import dlopen as _dlopen\nclass PyDLL:\n    def __init__(self, name):\n"
          "        self._handle = _dlopen(name, 0)\npythonapi = PyDLL(None)\nOTHER = 42\n")


def fake__ctypes():
    mod = types.ModuleType("_ctypes")

    def dlopen(name, mode=0):
        if name is None:
            raise OSError("Cannot load library: ")
        return 1234
    mod.dlopen = dlopen
    return mod


def make_apk(path, as_pyc):
    imy = io.BytesIO()
    with zipfile.ZipFile(imy, "w", zipfile.ZIP_STORED) as z:
        if as_pyc:
            z.writestr("ctypes/__init__.pyc", b"\0" * 16 + marshal.dumps(compile(CTYPES, "stdlib/ctypes/__init__.py", "exec")))
        else:
            z.writestr("ctypes/__init__.py", CTYPES)
        z.writestr("os.py", "# os")
    with zipfile.ZipFile(path, "w") as apk:
        apk.writestr("AndroidManifest.xml", b"manifest")
        apk.writestr("classes.dex", b"dex" * 100, zipfile.ZIP_DEFLATED)
        apk.writestr("lib/arm64-v8a/libchaquopy_java.so", b"so")
        apk.writestr("assets/chaquopy/stdlib-common.imy", imy.getvalue(), zipfile.ZIP_STORED)


def patched_ctypes_names(apk):
    with zipfile.ZipFile(apk) as z:
        assert z.read("classes.dex") == b"dex" * 100 and z.getinfo("classes.dex").compress_type == zipfile.ZIP_DEFLATED
        assert z.getinfo("assets/chaquopy/stdlib-common.imy").compress_type == zipfile.ZIP_STORED
        with zipfile.ZipFile(io.BytesIO(z.read("assets/chaquopy/stdlib-common.imy"))) as imy:
            assert imy.read("os.py") == b"# os"
            return imy.namelist(), imy.read("ctypes/__init__.py").decode()


for as_pyc in (True, False):
    label = "pyc" if as_pyc else "source"
    # unpatched: the stand-in fails like ATL does
    sys.modules["_ctypes"] = fake__ctypes()
    try:
        exec(compile(CTYPES, "x", "exec"), {})
        raise SystemExit("the unpatched stand-in should fail")
    except OSError:
        pass

    apk = os.path.join(tmp, label + ".apk")
    make_apk(apk, as_pyc)
    msg = store._patch_chaquopy_apk(apk)
    assert "patched ctypes in stdlib-common.imy" in msg, msg
    names, src = patched_ctypes_names(apk)
    assert "ctypes/__init__.pyc" not in names and "ctypes/__init__.py" in names, names

    sys.modules["_ctypes"] = fake__ctypes()
    ns = {"__name__": "ctypes"}
    exec(compile(src, "ctypes/__init__.py", "exec"), ns)   # must import now
    assert ns["pythonapi"]._handle == 0 and ns["OTHER"] == 42, label
    assert ns["_dsc"].dlopen("libm.so", 0) == 1234          # real libraries are untouched
    assert "already patched" in store._patch_chaquopy_apk(apk)   # idempotent

none = os.path.join(tmp, "none.apk")
with zipfile.ZipFile(none, "w") as z:
    z.writestr("assets/chaquopy/x.imy", b"not a zip")
assert "nothing patched" in store._patch_chaquopy_apk(none)
print("test_chaquopy: OK")

# an old, unpatched copy that Chaquopy extracted into the app's data folder must be removed
apk = os.path.join(tmp, "cache.apk")
make_apk(apk, True)
base = store._app_data_dir(apk)
old_imy = os.path.join(base, "files", "chaquopy", "stdlib-common.imy")
os.makedirs(os.path.dirname(old_imy))
with zipfile.ZipFile(old_imy, "w") as z:
    z.writestr("ctypes/__init__.pyc", b"x")
keep = os.path.join(base, "files", "settings.xml")
open(keep, "w").write("keep")
notes = store._clear_stale_chaquopy_cache(apk)
assert any("removed old unpatched copy" in n for n in notes), notes
assert not os.path.exists(os.path.dirname(old_imy)) and os.path.exists(keep)
assert "no old copy" in store._clear_stale_chaquopy_cache(apk)[0]
# a copy extracted from the PATCHED apk is left alone
store._patch_chaquopy_apk(apk)
with zipfile.ZipFile(apk) as z:
    good = z.read("assets/chaquopy/stdlib-common.imy")
os.makedirs(os.path.dirname(old_imy))
open(old_imy, "wb").write(good)
assert "no old copy" in store._clear_stale_chaquopy_cache(apk)[0] and os.path.exists(old_imy)
print("test_chaquopy (stale cache): OK")
