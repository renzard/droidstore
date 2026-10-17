"""Tests adding your own APK (import_apk) without a device.

    python3 tests/test_import.py
"""
import os
import struct
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

tmp = tempfile.mkdtemp()
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = os.path.join(tmp, var)
os.environ["HOME"] = os.path.join(tmp, "home")
os.makedirs(os.path.join(tmp, "home", "Downloads"))

import store  # noqa: E402


def build_axml(package, version):
    """Builds a minimal binary AndroidManifest.xml (UTF-16 string pool, one <manifest> element)."""
    strings = ["package", "versionName", "manifest", package, version]
    blobs = b""
    offsets = []
    for text in strings:
        offsets.append(len(blobs))
        blobs += struct.pack("<H", len(text)) + text.encode("utf-16-le") + b"\x00\x00"
    while len(blobs) % 4:
        blobs += b"\x00"
    strings_start = 28 + 4 * len(strings)
    pool = struct.pack("<HHIIIIII", 0x0001, 28, strings_start + len(blobs), len(strings), 0, 0, strings_start, 0)
    pool += struct.pack("<%dI" % len(offsets), *offsets) + blobs

    attrs = b""
    for name_idx, value_idx in ((0, 3), (1, 4)):
        attrs += struct.pack("<iiiHBBI", -1, name_idx, value_idx, 8, 0, 3, value_idx)
    element = struct.pack("<HHIii", 0x0102, 16, 36 + len(attrs), 1, -1)
    element += struct.pack("<iiHHHHHH", -1, 2, 20, 20, 2, 0, 0, 0) + attrs

    body = pool + element
    return struct.pack("<HHI", 0x0003, 8, 8 + len(body)) + body


def make_apk(path, package="org.example.hello", version="1.2.3", extra=()):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("AndroidManifest.xml", build_axml(package, version))
        z.writestr("classes.dex", b"dex\n035\x00")
        for name in extra:
            z.writestr(name, b"\x7fELF")


# 1. normal APK: package and version are read from the manifest
apk = os.path.join(tmp, "home", "Downloads", "My_Hello-App.apk")
make_apk(apk)
assert store._read_axml(build_axml("a.b.c", "9")) == ("a.b.c", "9")
result = store.import_apk(apk)
assert result["ok"], result
assert result["pkgId"] == "org.example.hello", result
assert result["name"] == "My Hello App", result
assert result["warning"] == "", result

installed = store.list_installed()
assert len(installed) == 1
entry = installed[0]
assert entry["pkgId"] == "org.example.hello" and entry["version"] == "1.2.3" and entry["local"] is True
assert os.path.isfile(entry["apk"]) and entry["apk"].startswith(store.APK_DIR)

# 2. re-importing the same package replaces it, does not duplicate
assert store.import_apk(apk)["ok"]
assert len(store.list_installed()) == 1

# 3. finding APKs in the folders
found = store.find_apks()
assert [f["name"] for f in found] == ["My_Hello-App.apk"], found

# 4. native code only for another architecture -> warning
foreign = "x86_64" if "arm64-v8a" in store.device_abis() else "arm64-v8a"
nat = os.path.join(tmp, "native.apk")
make_apk(nat, package="org.example.native", extra=["lib/%s/libfoo.so" % foreign])
res = store.import_apk(nat)
assert res["ok"] and "native" in res["warning"], res

# 5. broken manifest -> name from the file
bad = os.path.join(tmp, "weird name.apk")
with zipfile.ZipFile(bad, "w") as z:
    z.writestr("AndroidManifest.xml", b"not binary xml")
res = store.import_apk(bad)
assert res["ok"] and res["pkgId"] == "local.weird_name", res

# 6. not an APK
txt = os.path.join(tmp, "x.apk")
with open(txt, "w") as f:
    f.write("hello")
assert not store.import_apk(txt)["ok"]
nomanifest = os.path.join(tmp, "y.apk")
with zipfile.ZipFile(nomanifest, "w") as z:
    z.writestr("foo.txt", "x")
assert not store.import_apk(nomanifest)["ok"]
assert not store.import_apk(os.path.join(tmp, "nope.apk"))["ok"]

# 7. removal also deletes the copy
apk_copy = store.list_installed()[0]["apk"]
for item in store.list_installed():
    assert store.remove(item["pkgId"])
assert store.list_installed() == []
assert not os.path.exists(apk_copy)

print("test_import: OK")
