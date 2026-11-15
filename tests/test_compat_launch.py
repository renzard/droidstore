"""The store adds the compat dex (compat/atl-compat.jar) to the APK only when it is needed.

Needed = the installed ATL lacks android.app.TaskStackBuilder (or android.net.VpnService) AND the APK uses it
(DuckDuckGo at start-up). Each class has its own jar and its own marker, so an APK that was patched for one of them
before the other was added is still patched for the other.

    python3 tests/test_compat_launch.py
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

import store  # noqa: E402

store.pyotherside = type("P", (), {"send": staticmethod(lambda *a: None)})

DESC = b"Landroid/app/TaskStackBuilder;"
VPN_DESC = b"Landroid/net/VpnService;"
AF_DESC = b"Landroid/service/autofill/AutofillService;"


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def make_sdk(name, with_class, with_vpn=None):
    with_vpn = with_class if with_vpn is None else with_vpn
    usr = os.path.join(tmp, name, "usr")
    os.makedirs(os.path.join(usr, "bin"))
    exe = os.path.join(usr, "bin", store.ATL_NAME)
    open(exe, "w").write("#!/bin/sh\n")
    os.chmod(exe, 0o755)
    jar_dir = os.path.join(usr, "share", "android_translation_layer")
    os.makedirs(jar_dir)
    make_zip(os.path.join(jar_dir, "api-impl.jar"),
             {"classes.dex": b"dex\n035\0 Landroid/view/View; " + (DESC if with_class else b"")
                                                + (VPN_DESC if with_vpn else b"") + (AF_DESC if with_class else b"")})
    return usr


apk_with = make_zip(os.path.join(tmp, "ddg.apk"), {"classes.dex": b"xx Lcom/duckduckgo/A; " + DESC, "classes2.dex": b"yy",
                                                    "AndroidManifest.xml": b"m"})
apk_with_in_2 = make_zip(os.path.join(tmp, "ddg2.apk"), {"classes.dex": b"xx", "classes2.dex": b"zz " + DESC})
apk_without = make_zip(os.path.join(tmp, "plain.apk"), {"classes.dex": b"xx Lcom/example/A;", "res/raw/note.txt": DESC})
not_a_zip = os.path.join(tmp, "broken.apk")
open(not_a_zip, "wb").write(b"not a zip")

# ---- an ATL WITHOUT the class (the downloaded SDK): added to the app that uses it, to no other app
old_usr = make_sdk("old", with_class=False)
store.sdk_root = lambda: old_usr
store._API_IMPL_JAR_CACHE.clear()
assert store._atl_has_class("android.app.TaskStackBuilder") is False

JAR_DEX = zipfile.ZipFile(store.ATL_COMPAT_JAR).read("classes.dex")


def names(path):
    return zipfile.ZipFile(path).namelist()


before = open(apk_with, "rb").read()
notes = store._embed_compat_dex(apk_with)
assert notes and "TaskStackBuilder" in notes[0] and "classes3.dex" in notes[0], notes
with zipfile.ZipFile(apk_with) as z:
    assert z.namelist() == ["classes.dex", "classes2.dex", "AndroidManifest.xml", "classes3.dex", store.ATL_COMPAT_MARKER], z.namelist()
    assert z.read("classes3.dex") == JAR_DEX, "the exact dex of the jar, as the next classesN.dex"
    assert z.getinfo("classes3.dex").compress_type == zipfile.ZIP_STORED
    assert z.read("classes.dex") == b"xx Lcom/duckduckgo/A; " + DESC and z.read("AndroidManifest.xml") == b"m", "the rest is untouched"
assert not os.path.exists(apk_with + ".part")
again = store._embed_compat_dex(apk_with)
assert again and "already" in again[0], again
assert names(apk_with).count("classes3.dex") == 1 and "classes4.dex" not in names(apk_with), "patched once only"

notes = store._embed_compat_dex(apk_with_in_2)
assert notes and "classes3.dex" in notes[0], "the class is found in classes2.dex too: %s" % notes
untouched = open(apk_without, "rb").read()
assert store._embed_compat_dex(apk_without) == [] and open(apk_without, "rb").read() == untouched, \
    "an app that does not use the class is left alone (res/raw does not count)"
assert store._embed_compat_dex(not_a_zip) == [] and open(not_a_zip, "rb").read() == b"not a zip"
assert store._embed_compat_dex(os.path.join(tmp, "missing.apk")) == []
no_dex = make_zip(os.path.join(tmp, "nodex.apk"), {"AndroidManifest.xml": b"m", "res/x": DESC})
assert store._embed_compat_dex(no_dex) == []
print("test_compat_launch (ATL without the class: dex added to the APK only where needed, once): OK")

# ---- android.net.VpnService (DuckDuckGo: NoClassDefFoundError: Failed resolution of: Landroid/net/VpnService;)
assert store._atl_has_class("android.net.VpnService") is False
VPN_DEX = [d for _n, d in store._jar_dex_files(store.ATL_COMPAT_VPN_JAR)]
assert len(VPN_DEX) == 2, "VpnService + VpnService$Builder"
apk_vpn = make_zip(os.path.join(tmp, "ddg_vpn.apk"), {"classes.dex": b"xx " + VPN_DESC, "classes2.dex": b"yy",
                                                       "AndroidManifest.xml": b"m"})
notes = store._embed_compat_dex(apk_vpn)
assert len(notes) == 1 and "android.net.VpnService" in notes[0] and "TaskStackBuilder" not in notes[0], notes
assert "classes3.dex" in notes[0] and "classes4.dex" in notes[0], notes
with zipfile.ZipFile(apk_vpn) as z:
    assert z.namelist() == ["classes.dex", "classes2.dex", "AndroidManifest.xml", "classes3.dex", "classes4.dex",
                            store.ATL_COMPAT_VPN_MARKER], z.namelist()
    assert z.read("classes3.dex") == VPN_DEX[0] and z.read("classes4.dex") == VPN_DEX[1], "contiguous, in jar order"
    assert z.getinfo("classes3.dex").compress_type == zipfile.ZIP_STORED
again = store._embed_compat_dex(apk_vpn)
assert again and "already" in again[0] and names(apk_vpn).count("classes3.dex") == 1 and "classes5.dex" not in names(apk_vpn)

# an APK that was patched for TaskStackBuilder by the previous store version still gets the VpnService classes
apk_old_patched = make_zip(os.path.join(tmp, "ddg_old.apk"), {"classes.dex": b"xx " + DESC + VPN_DESC, "classes2.dex": b"yy",
                                                               "AndroidManifest.xml": b"m"})
only_tsb = store.ATL_COMPAT_SETS
store.ATL_COMPAT_SETS = only_tsb[:1]  # what the previous version did
assert "TaskStackBuilder" in store._embed_compat_dex(apk_old_patched)[0]
store.ATL_COMPAT_SETS = only_tsb
notes = store._embed_compat_dex(apk_old_patched)
assert len(notes) == 2 and "already added" in notes[0] and "android.net.VpnService" in notes[1], notes
assert names(apk_old_patched) == ["classes.dex", "classes2.dex", "AndroidManifest.xml", "classes3.dex",
                                  store.ATL_COMPAT_MARKER, "classes4.dex", "classes5.dex", store.ATL_COMPAT_VPN_MARKER], \
    names(apk_old_patched)

# an app that uses both, on a fresh APK: both jars, one after the other, no gap in the numbering
apk_both = make_zip(os.path.join(tmp, "ddg_both.apk"), {"classes.dex": b"xx " + DESC, "classes2.dex": b"zz " + VPN_DESC})
notes = store._embed_compat_dex(apk_both)
assert len(notes) == 2 and "TaskStackBuilder" in notes[0] and "VpnService" in notes[1], notes
assert names(apk_both)[2:] == ["classes3.dex", store.ATL_COMPAT_MARKER, "classes4.dex", "classes5.dex",
                               store.ATL_COMPAT_VPN_MARKER], names(apk_both)

# an app that does not use VpnService does not get it (the TaskStackBuilder-only APK from above)
assert "classes4.dex" not in names(apk_with) and store.ATL_COMPAT_VPN_MARKER not in names(apk_with)
# an ATL that lacks only one of them: only that one is added
half_usr = make_sdk("half", with_class=True, with_vpn=False)
store.sdk_root = lambda: half_usr
store._API_IMPL_JAR_CACHE.clear()
apk_half = make_zip(os.path.join(tmp, "ddg_half.apk"), {"classes.dex": b"xx " + DESC + VPN_DESC})
notes = store._embed_compat_dex(apk_half)
assert len(notes) == 1 and "VpnService" in notes[0] and "TaskStackBuilder" not in notes[0], notes
store.sdk_root = lambda: old_usr
store._API_IMPL_JAR_CACHE.clear()
print("test_compat_launch (VpnService: own jar and marker, two dex files, old patched APKs get it too): OK")

# ---- android.service.autofill.AutofillService (DuckDuckGo, the next one after VpnService)
assert store._atl_has_class("android.service.autofill.AutofillService") is False
AF_DEX = [d for _n, d in store._jar_dex_files(store.ATL_COMPAT_AUTOFILL_JAR)]
assert len(AF_DEX) == 1
# the APK that was already patched for TaskStackBuilder AND VpnService by the previous store version: it gets the autofill one
apk_ddg = make_zip(os.path.join(tmp, "ddg_all.apk"), {"classes.dex": b"xx " + DESC + VPN_DESC + AF_DESC, "classes2.dex": b"yy",
                                                       "AndroidManifest.xml": b"m"})
all_sets = store.ATL_COMPAT_SETS
store.ATL_COMPAT_SETS = all_sets[:2]
assert len(store._embed_compat_dex(apk_ddg)) == 2
store.ATL_COMPAT_SETS = all_sets
notes = store._embed_compat_dex(apk_ddg)
assert len(notes) == 3 and "already added" in notes[0] and "already added" in notes[1], notes
assert "android.service.autofill.AutofillService" in notes[2] and "classes6.dex" in notes[2], notes
assert names(apk_ddg) == ["classes.dex", "classes2.dex", "AndroidManifest.xml", "classes3.dex", store.ATL_COMPAT_MARKER,
                          "classes4.dex", "classes5.dex", store.ATL_COMPAT_VPN_MARKER, "classes6.dex",
                          store.ATL_COMPAT_AUTOFILL_MARKER], names(apk_ddg)
assert zipfile.ZipFile(apk_ddg).read("classes6.dex") == AF_DEX[0]
print("test_compat_launch (AutofillService added on top of an APK patched by an older store): OK")

# ---- an ATL WITH the class (built from ./atl): never touched
apk_new = make_zip(os.path.join(tmp, "ddg_new.apk"), {"classes.dex": b"xx " + DESC})
new_usr = make_sdk("new", with_class=True)
store.sdk_root = lambda: new_usr
store._API_IMPL_JAR_CACHE.clear()
assert all(store._atl_has_class(c) is True for c in store.ATL_ADDED_CLASSES)
apk_new = make_zip(os.path.join(tmp, "ddg_new.apk"), {"classes.dex": b"xx " + DESC + VPN_DESC + AF_DESC})
raw = open(apk_new, "rb").read()
assert store._embed_compat_dex(apk_new) == [] and open(apk_new, "rb").read() == raw
print("test_compat_launch (ATL with the class: nothing changed): OK")

# ---- cannot tell (no ATL / no api-impl.jar): nothing is changed
store.sdk_root = lambda: None
store._API_IMPL_JAR_CACHE.clear()
assert store._embed_compat_dex(apk_new) == [] and open(apk_new, "rb").read() == raw
print("test_compat_launch (ATL unknown: nothing changed): OK")

# ---- launch() patches the APK before ATL starts, keeps the command line as it was, and the log says so
store.sdk_root = lambda: old_usr
store._API_IMPL_JAR_CACHE.clear()
apk_launch = make_zip(os.path.join(tmp, "ddg_launch.apk"), {"classes.dex": b"xx " + DESC, "AndroidManifest.xml": b"m"})
seen = {}


class FakeProc:
    returncode = 0

    def poll(self):
        return 0

    def wait(self):
        return 0


def fake_popen(cmd, **kw):
    seen["cmd"] = cmd
    seen["classes2_at_start"] = "classes2.dex" in names(apk_launch)
    return FakeProc()


store.subprocess.Popen = fake_popen
store._ensure_libs = lambda cmd: None
store._launch_env = lambda cmd: {}
store._launch_cwd = lambda: None
import json  # noqa: E402

json.dump({"com.duckduckgo.mobile.android": {"apk": apk_launch}}, open(store.LIBRARY_FILE, "w"))
store.get_settings = lambda: {"atl_bin": "/x/android-translation-layer", "extra_args": "", "extra_env": ""}
result = store.launch("com.duckduckgo.mobile.android")
assert seen["cmd"] == ["/x/android-translation-layer", apk_launch], seen["cmd"]
assert seen["classes2_at_start"], "the APK already has the class when ATL starts"
log = open(store._launch_log_path("com.duckduckgo.mobile.android")).read()
assert "ATL compat: this ATL lacks android.app.TaskStackBuilder" in log and "classes2.dex" in log, log
print("test_compat_launch (launch: APK patched before ATL starts, command line unchanged, the log says so): OK")
