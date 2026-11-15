"""The launch log lists the android.* classes the APK refers to that this ATL's api-impl.jar does not define.

Real dex bytes (written by tools/make_compat_dex.py) are used, and the expected list is worked out independently with a
regular expression over the raw bytes.

    python3 tests/test_atl_scan.py
"""
import os
import re
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

tmp = tempfile.mkdtemp()
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = os.path.join(tmp, var)
os.environ["APP_ID"] = "droidstore.example_droidstore_0.1.0"

import store  # noqa: E402
import make_compat_dex as gen  # noqa: E402

store.pyotherside = type("P", (), {"send": staticmethod(lambda *a: None)})

tsb_dex = gen.build_dex(gen.TSB_SPEC)
vpn_dex = gen.build_dex(gen.VPN_SPEC)
autofill_dex = gen.build_dex(gen.AUTOFILL_SPEC)


def android_names(dex):
    return {m.decode()[1:-1] for m in re.findall(rb"Landroid/[A-Za-z0-9_/$]+;", dex)}


def write_zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    return path


# the parser: what a dex refers to and what it defines
refs, defined = store._dex_android_types(vpn_dex)
assert refs == android_names(vpn_dex) and defined == {"android/net/VpnService"}, (refs, defined)
refs, defined = store._dex_android_types(tsb_dex)
assert refs == android_names(tsb_dex) and defined == {"android/app/TaskStackBuilder"}
assert store._dex_android_types(b"not a dex") == (set(), set())
assert store._dex_android_types(vpn_dex[:0x80]) == (set(), set()), "a truncated dex does not raise"

# an ATL that has the TaskStackBuilder only; an app that brings the VpnService dex and refers to the AutofillService
usr = os.path.join(tmp, "usr")
jar_dir = os.path.join(usr, "share", "android_translation_layer")
os.makedirs(jar_dir)
write_zip(os.path.join(jar_dir, "api-impl.jar"), {"classes.dex": tsb_dex, "android/os/Bundle.class": b"x"})
store.sdk_root = lambda: usr
apk = write_zip(os.path.join(tmp, "app.apk"), {"classes.dex": autofill_dex, "classes2.dex": vpn_dex, "res/x": b"Landroid/zzz;"})

lines = store._atl_missing_scan(apk)
assert len(lines) == 1 and lines[0].startswith("ATL scan: the app refers to "), lines
want = (android_names(autofill_dex) | android_names(vpn_dex)) - {"android/service/autofill/AutofillService", "android/net/VpnService",
                                                                   "android/app/TaskStackBuilder", "android/os/Bundle"}
assert "%d android.* classes" % len(want) in lines[0], (len(want), lines[0])
listed = set(lines[0].split("): ", 1)[1].split(", "))
assert listed == {w.replace("/", ".") for w in want}, listed
assert "android.service.autofill.AutofillService" not in lines[0], "the app defines it itself"
assert "android.app.Service" in lines[0] and "android.zzz" not in lines[0], "only dex files are read"
assert store._atl_missing_scan(apk) is lines or store._atl_missing_scan(apk) == lines, "cached"
print("test_atl_scan (candidates for the next crash are listed): OK")

# nothing missing: a positive line
full = write_zip(os.path.join(tmp, "full.apk"), {"classes.dex": tsb_dex})
os.remove(os.path.join(jar_dir, "api-impl.jar"))
write_zip(os.path.join(jar_dir, "api-impl.jar"),
          {"classes.dex": tsb_dex, "classes2.dex": b"dex\n035\0" + b"\0" * 200,
           **{n + ".class": b"x" for n in android_names(tsb_dex)}})
store._ATL_SCAN_CACHE.clear()
lines = store._atl_missing_scan(full)
assert len(lines) == 1 and "every android.* class" in lines[0], lines

# the list is cut at `limit`
store._ATL_SCAN_CACHE.clear()
write_zip(os.path.join(jar_dir, "api-impl.jar"), {"classes.dex": gen.build_dex(gen.TSB_SPEC)})
lines = store._atl_missing_scan(apk, limit=2)
assert "... and " in lines[0] and "more" in lines[0], lines

# no ATL / no jar / not a zip: no claim
store.sdk_root = lambda: None
assert store._atl_missing_scan(apk) == []
store.sdk_root = lambda: usr
store._ATL_SCAN_CACHE.clear()
assert store._atl_missing_scan(os.path.join(tmp, "missing.apk")) == []
broken = os.path.join(tmp, "broken.apk")
open(broken, "wb").write(b"nope")
assert store._atl_missing_scan(broken) == []
print("test_atl_scan (nothing missing, limit, no ATL, broken files): OK")
