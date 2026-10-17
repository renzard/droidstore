"""ATL Store backend (runs inside pyotherside).

Downloads APKs, verifies sha256, keeps them in a library and opens them with the
Android Translation Layer (ATL).
"""
import gzip
import hashlib
import io
import json
import os
import platform
import re
import shlex
import shutil
import sqlite3
import struct
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile

from catalog import parse_index_v2

try:
    import pyotherside
except ImportError:  # running outside the app (tests / tools)

    class _Stub:
        @staticmethod
        def send(*args):
            print("[event]", *args)

    pyotherside = _Stub()

# A confined click app may only write to ~/.config|.cache|.local/share/<package name>/,
# so the folder name must follow the package name (APP_ID is "<package>_<app>_<version>").
APP = (os.environ.get("APP_ID") or "").split("_")[0] or "atlstore"


def _app_dir(env_name, default):
    base = os.environ.get(env_name) or os.path.expanduser(default)
    path = os.path.join(base, APP)
    os.makedirs(path, exist_ok=True)
    return path


CONFIG_DIR = _app_dir("XDG_CONFIG_HOME", "~/.config")
DATA_DIR = _app_dir("XDG_DATA_HOME", "~/.local/share")
CACHE_DIR = _app_dir("XDG_CACHE_HOME", "~/.cache")
APK_DIR = os.path.join(DATA_DIR, "apks")
os.makedirs(APK_DIR, exist_ok=True)

# App folder (where the click package is installed, read-only)
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATL_NAME = "android-translation-layer"
# The ATL SDK is downloaded here by the app (writable). If usr/ exists inside the click package, that one is used instead.
SDK_DIR = os.path.join(DATA_DIR, "atl-sdk")
SDK_TAG = os.environ.get("ATL_SDK_TAG", "sdk-3d93da2")
SDK_BASE = os.environ.get("ATL_SDK_BASE", "https://github.com/NotKit/atl-touch/releases/download/%s" % SDK_TAG)

# System libraries missing from the phone, downloaded as .deb (soname -> Ubuntu package)
EXTRA_LIB_DIR = os.path.join(DATA_DIR, "extra-lib")
# Changes whenever the version selection method changes, so libraries fetched the old (wrong) way get deleted
EXTRA_LIB_SCHEME = "2-noble-index"
# sonames that cannot be derived from the library name
LIB_NAME_OVERRIDES = {"libOpenCL.so.1": "ocl-icd-libopencl1"}
INDEX_SUITES = ("noble", "noble-updates")
INDEX_COMPONENTS = ("main", "universe")
PORTS_BASE = os.environ.get("ATL_PORTS_BASE", "http://ports.ubuntu.com/ubuntu-ports")

# The zstd decoder (C extension for arm64) ships inside the click package, in vendor/
_VENDOR = os.path.join(APP_ROOT, "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.append(_VENDOR)

# Bundled mode: the ATL SDK (and any extra system libraries) ship inside the click package.
# Nothing executable is downloaded at runtime, which is what a confined (AppArmor) app requires.
BUNDLED = os.path.isdir(os.path.join(APP_ROOT, "usr"))


def sdk_root():
    """Returns the usr/ folder that contains ATL, or None if it is not there yet."""
    for base in (os.path.join(APP_ROOT, "usr"), os.path.join(SDK_DIR, "usr")):
        if os.access(os.path.join(base, "bin", ATL_NAME), os.X_OK):
            return base
    return None


SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
LIBRARY_FILE = os.path.join(DATA_DIR, "library.json")
CATALOG_FILE = os.path.join(CACHE_DIR, "catalog.json")
DETAILS_DB = os.path.join(CACHE_DIR, "details.db")
COMPAT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "compat.json")

DEFAULT_SETTINGS = {
    # How ATL is invoked on the device. May include arguments, e.g. "/opt/atl/bin/android-translation-layer"
    # Empty = automatic (the ATL SDK downloaded by the app)
    "atl_bin": "",
    "repo_url": "https://f-droid.org/repo",
    # If set, a prebuilt small catalog.json is downloaded instead of the whole F-Droid index
    "catalog_url": "",
}

PKG_RE = re.compile(r"^[A-Za-z0-9_.]+$")

_lock = threading.Lock()
_catalog = None
_active = {}  # pkg -> download progress (0..1)


# ---------- helpers ----------

def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def device_abis():
    machine = platform.machine().lower()
    if machine in ("aarch64", "arm64"):
        return ["arm64-v8a"]
    if machine.startswith("arm"):
        return ["armeabi-v7a", "armeabi"]
    if machine in ("x86_64", "amd64"):
        return ["x86_64", "x86"]
    return []


def _download(url, dest, on_progress=None, expected_sha256=None):
    request = urllib.request.Request(url, headers={"User-Agent": "atl-store/0.1"})
    part = dest + ".part"
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=30) as resp, open(part, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if on_progress and total:
                    on_progress(done / total)
        if expected_sha256 and digest.hexdigest().lower() != expected_sha256.lower():
            raise ValueError("The file's sha256 does not match, download rejected")
        os.replace(part, dest)
    finally:
        if os.path.exists(part):
            os.remove(part)


def _load_catalog():
    global _catalog
    with _lock:
        if _catalog is None:
            _catalog = _read_json(CATALOG_FILE, [])
        return _catalog


# ---------- settings ----------

def _resolve_atl_bin(value):
    value = (value or "").strip()
    root = sdk_root()
    if root and value in ("", ATL_NAME):
        return os.path.join(root, "bin", ATL_NAME)
    return value or ATL_NAME


def get_settings():
    settings = dict(DEFAULT_SETTINGS)
    saved = _read_json(SETTINGS_FILE, {})
    for key in DEFAULT_SETTINGS:
        if isinstance(saved.get(key), str):
            settings[key] = saved[key]
    settings["atl_bin"] = _resolve_atl_bin(settings["atl_bin"])
    return settings


def set_settings(new_values):
    settings = get_settings()
    for key in DEFAULT_SETTINGS:
        if isinstance(new_values.get(key), str):
            settings[key] = new_values[key].strip() or DEFAULT_SETTINGS[key]
    auto = _resolve_atl_bin("")
    if settings["atl_bin"] == auto:
        settings["atl_bin"] = ""  # keep it automatic, so no stale path is left behind
    _write_json(SETTINGS_FILE, settings)
    return get_settings()


def device_info():
    return {"machine": platform.machine(), "abis": device_abis(), "data_dir": DATA_DIR}


# ---------- ATL SDK (automatic download) ----------

_sdk_busy = False


def sdk_status():
    root = sdk_root()
    return {"installed": root is not None, "busy": _sdk_busy, "path": root or "", "tag": SDK_TAG}


def install_sdk():
    """Downloads and unpacks the ATL SDK. Sends events: sdk_progress, sdk_ready, sdk_error."""
    global _sdk_busy
    if BUNDLED:
        pyotherside.send("sdk_error", "ATL is missing from this build (usr/bin/%s not found). "
                         "Run ./tools/fetch_sdk.sh before clickable build; it cannot be downloaded at runtime." % ATL_NAME)
        return
    with _lock:
        if _sdk_busy:
            return
        _sdk_busy = True
    threading.Thread(target=_install_sdk_worker, daemon=True).start()


def _arch_name():
    return "arm64" if platform.machine().lower() in ("aarch64", "arm64") else platform.machine().lower()


def _open_zstd(path):
    """Returns (stream, cleanup). The bundled decoder first, then the zstd program if present."""
    try:
        import zstandard

        handle = open(path, "rb")
        reader = zstandard.ZstdDecompressor().stream_reader(handle)
        return reader, handle, reader.close
    except ImportError:
        pass
    exe = shutil.which("zstd")
    if exe:
        proc = subprocess.Popen([exe, "-dc", path], stdout=subprocess.PIPE)
        return proc.stdout, None, proc.wait
    raise RuntimeError("No zstd decompressor found (neither in the click package nor on the system)")


def _extract_sdk(archive, dest, progress):
    total = os.path.getsize(archive)
    stream, handle, cleanup = _open_zstd(archive)
    last = -1.0
    try:
        with tarfile.open(fileobj=stream, mode="r|") as tar:
            for member in tar:
                try:
                    tar.extract(member, dest, filter="tar")
                except TypeError:  # older Python without filter
                    tar.extract(member, dest)
                if handle is not None:
                    fraction = min(handle.tell() / total, 1.0)
                    if fraction - last >= 0.01:
                        last = fraction
                        progress("extract", fraction)
    finally:
        cleanup()
        if handle is not None:
            handle.close()


def _install_sdk_worker():
    global _sdk_busy
    work = os.path.join(DATA_DIR, "sdk-work")
    try:
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work)
        name = "atl-sdk-%s.tar.zst" % _arch_name()
        archive = os.path.join(work, name)

        def progress(phase, fraction):
            pyotherside.send("sdk_progress", phase, fraction)

        expected = None
        try:
            req = urllib.request.Request(SDK_BASE + "/" + name + ".sha256", headers={"User-Agent": "atl-store/0.1"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                expected = resp.read().decode().split()[0]
        except Exception:  # noqa: BLE001  (without .sha256 we continue without verification)
            expected = None

        progress("download", 0.0)
        _download(SDK_BASE + "/" + name, archive, lambda f: progress("download", f), expected)

        out = os.path.join(work, "x")
        os.makedirs(out)
        progress("extract", 0.0)
        _extract_sdk(archive, out, progress)
        os.remove(archive)

        binary = os.path.join(out, "usr", "bin", ATL_NAME)
        if not os.path.exists(binary):
            raise ValueError("The SDK does not contain usr/bin/%s" % ATL_NAME)
        os.chmod(binary, os.stat(binary).st_mode | 0o111)

        shutil.rmtree(SDK_DIR, ignore_errors=True)
        os.makedirs(SDK_DIR)
        os.replace(os.path.join(out, "usr"), os.path.join(SDK_DIR, "usr"))
        pyotherside.send("sdk_ready")
    except Exception as exc:  # noqa: BLE001
        pyotherside.send("sdk_error", str(exc))
    finally:
        shutil.rmtree(work, ignore_errors=True)
        _sdk_busy = False


# ---------- catalog ----------

def has_catalog():
    # without details.db (older app version) the catalog is rebuilt so that details exist
    return len(_load_catalog()) > 0 and os.path.exists(DETAILS_DB)


def _split_details(catalog):
    """Moves the heavy data (description, screenshots...) out of the list into sqlite, so search stays fast."""
    rows = []
    for app in catalog:
        detail = app.pop("detail", None)
        if detail is not None:
            rows.append((app["id"], json.dumps(detail)))
    tmp = DETAILS_DB + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    try:
        db.execute("CREATE TABLE details (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        db.executemany("INSERT OR REPLACE INTO details VALUES (?, ?)", rows)
        db.commit()
    finally:
        db.close()
    os.replace(tmp, DETAILS_DB)
    return catalog


ANTI_FEATURES = {
    "Ads": "Advertising",
    "Tracking": "Tracks user activity",
    "NonFreeNet": "Promotes non-free network services",
    "NonFreeAdd": "Promotes non-free apps",
    "NonFreeDep": "Depends on non-free apps",
    "NonFreeAssets": "Non-free content",
    "UpstreamNonFree": "Non-free upstream source code",
    "KnownVuln": "Known security vulnerabilities",
    "DisabledAlgorithm": "Insecure signing algorithm",
    "NoSourceSince": "Source code is no longer available",
}


def get_details(pkg_id):
    """Everything an app's details page needs."""
    app = next((a for a in _load_catalog() if a["id"] == pkg_id), None)
    if app is None:
        entry = _read_json(LIBRARY_FILE, {}).get(pkg_id)  # installed app that is missing from the catalog
        if not entry:
            return {}
        app = dict(entry, id=pkg_id)
    detail = {}
    try:
        db = sqlite3.connect(DETAILS_DB)
        try:
            row = db.execute("SELECT data FROM details WHERE id = ?", (pkg_id,)).fetchone()
        finally:
            db.close()
        if row:
            detail = json.loads(row[0])
    except (sqlite3.Error, ValueError):
        detail = {}
    detail["antiFeatures"] = [ANTI_FEATURES.get(k, k) for k in detail.get("antiFeatures", [])]
    verified = set(_read_json(COMPAT_FILE, {}).get("verified", []))
    result = {
        "pkgId": app["id"],
        "name": app["name"],
        "summary": app.get("summary", ""),
        "icon": app.get("icon", ""),
        "version": app.get("version", ""),
        "size": app.get("size", 0),
        "native": bool(app.get("native")),
        "minSdk": app.get("minSdk", 0),
        "verified": app["id"] in verified,
        "installed": app["id"] in _read_json(LIBRARY_FILE, {}),
        "busy": app["id"] in _active,
        "progress": _active.get(app["id"], 0.0),
        "hasDetail": bool(detail.get("description") or detail.get("screenshots") or detail.get("license")),
    }
    result.update(
        {
            "description": detail.get("description", ""),
            "whatsNew": detail.get("whatsNew", ""),
            "categories": detail.get("categories", []),
            "license": detail.get("license", ""),
            "author": detail.get("author", ""),
            "webSite": detail.get("webSite", ""),
            "sourceCode": detail.get("sourceCode", ""),
            "issueTracker": detail.get("issueTracker", ""),
            "added": detail.get("added", 0),
            "lastUpdated": detail.get("lastUpdated", 0),
            "screenshots": detail.get("screenshots", []),
            "permissions": detail.get("permissions", []),
            "antiFeatures": detail["antiFeatures"],
            "targetSdk": detail.get("targetSdk", 0),
        }
    )
    return result


def update_catalog():
    threading.Thread(target=_update_catalog, daemon=True).start()


def _update_catalog():
    global _catalog
    try:
        settings = get_settings()

        def progress(fraction):
            pyotherside.send("catalog_progress", fraction)

        if settings["catalog_url"]:
            tmp = os.path.join(CACHE_DIR, "catalog_remote.json")
            _download(settings["catalog_url"], tmp, progress)
            catalog = _read_json(tmp, None)
            os.remove(tmp)
            if not isinstance(catalog, list):
                raise ValueError("Invalid catalog")
        else:
            tmp = os.path.join(CACHE_DIR, "index-v2.json")
            _download(settings["repo_url"].rstrip("/") + "/index-v2.json", tmp, progress)
            index = _read_json(tmp, None)
            os.remove(tmp)
            if not isinstance(index, dict):
                raise ValueError("Invalid index")
            catalog = parse_index_v2(index, device_abis(), settings["repo_url"])
            del index
        catalog = _split_details(catalog)
        _write_json(CATALOG_FILE, catalog)
        with _lock:
            _catalog = catalog
        pyotherside.send("catalog_ready", len(catalog))
    except Exception as exc:  # noqa: BLE001
        pyotherside.send("catalog_error", str(exc))


def search(query="", pure_only=False, limit=80):
    q = (query or "").strip().lower()
    verified = set(_read_json(COMPAT_FILE, {}).get("verified", []))
    library = _read_json(LIBRARY_FILE, {})
    results = []
    for app in _load_catalog():
        if pure_only and app.get("native"):
            continue
        if q and q not in app["name"].lower() and q not in app.get("summary", "").lower() and q not in app["id"].lower():
            continue
        results.append(
            {
                "pkgId": app["id"],
                "name": app["name"],
                "summary": app.get("summary", ""),
                "icon": app.get("icon", ""),
                "version": app.get("version", ""),
                "size": app.get("size", 0),
                "native": bool(app.get("native")),
                "verified": app["id"] in verified,
                "installed": app["id"] in library,
                "busy": app["id"] in _active,
                "progress": _active.get(app["id"], 0.0),
            }
        )
    results.sort(key=lambda a: (not a["verified"], a["native"], a["name"].lower()))
    return results[:limit]


# ---------- install / launch ----------

def install(pkg_id):
    if not PKG_RE.match(pkg_id or ""):
        pyotherside.send("failed", pkg_id, "Invalid package name")
        return
    app = next((a for a in _load_catalog() if a["id"] == pkg_id), None)
    if app is None:
        pyotherside.send("failed", pkg_id, "The app is not in the catalog")
        return
    with _lock:
        if pkg_id in _active:
            return
        _active[pkg_id] = 0.0
    threading.Thread(target=_install_worker, args=(app,), daemon=True).start()


def _install_worker(app):
    pkg_id = app["id"]
    try:
        dest = os.path.join(APK_DIR, "%s_%s.apk" % (pkg_id, app["versionCode"]))

        def on_progress(fraction):
            _active[pkg_id] = fraction
            pyotherside.send("progress", pkg_id, fraction)

        pyotherside.send("progress", pkg_id, 0.0)
        if not os.path.exists(dest):
            _download(app["apk"], dest, on_progress, app["sha256"])

        library = _read_json(LIBRARY_FILE, {})
        previous = library.get(pkg_id)
        if previous and previous.get("apk") != dest and os.path.exists(previous.get("apk", "")):
            os.remove(previous["apk"])
        library[pkg_id] = {
            "pkgId": pkg_id,
            "name": app["name"],
            "summary": app.get("summary", ""),
            "icon": app.get("icon", ""),
            "version": app.get("version", ""),
            "apk": dest,
        }
        _write_json(LIBRARY_FILE, library)
        pyotherside.send("installed", pkg_id)
    except Exception as exc:  # noqa: BLE001
        pyotherside.send("failed", pkg_id, str(exc))
    finally:
        _active.pop(pkg_id, None)


def list_installed():
    apps = sorted(_read_json(LIBRARY_FILE, {}).values(), key=lambda a: a["name"].lower())
    return [dict(a, icon=a.get("icon", ""), version=a.get("version", ""), local=bool(a.get("local")),
                 installed=True, busy=False, progress=0.0) for a in apps]


def remove(pkg_id):
    library = _read_json(LIBRARY_FILE, {})
    entry = library.pop(pkg_id, None)
    if entry and os.path.exists(entry.get("apk", "")):
        os.remove(entry["apk"])
    _write_json(LIBRARY_FILE, library)
    return entry is not None


# ---------- your own APKs (sideload) ----------

def _pool_string(data, off, utf8):
    if utf8:
        first = data[off]
        off += 2 if first & 0x80 else 1  # length in characters (we skip it)
        n = data[off]
        if n & 0x80:
            n = ((n & 0x7F) << 8) | data[off + 1]
            off += 2
        else:
            off += 1
        return data[off:off + n].decode("utf-8", "replace")
    n = struct.unpack_from("<H", data, off)[0]
    off += 2
    if n & 0x8000:
        n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, off)[0]
        off += 2
    return data[off:off + n * 2].decode("utf-16-le", "replace")


def _read_axml(data):
    """Reads (package, versionName) from an APK's binary AndroidManifest.xml."""
    if len(data) < 8 or struct.unpack_from("<H", data, 0)[0] != 0x0003:
        raise ValueError("Invalid AndroidManifest.xml")
    pos = struct.unpack_from("<H", data, 2)[0]
    strings = []
    while pos + 8 <= len(data):
        ctype, _hsize, size = struct.unpack_from("<HHI", data, pos)
        if size < 8:
            break
        if ctype == 0x0001:
            count, _styles, flags, str_start, _ss = struct.unpack_from("<IIIII", data, pos + 8)
            offsets = struct.unpack_from("<%dI" % count, data, pos + 28)
            base = pos + str_start
            strings = [_pool_string(data, base + o, bool(flags & 0x100)) for o in offsets]
        elif ctype == 0x0102:  # first start element = <manifest>
            _ns, _name, attr_start, attr_size, attr_count = struct.unpack_from("<iiHHH", data, pos + 16)
            found = {}
            for i in range(attr_count):
                at = pos + 16 + attr_start + i * attr_size
                _ans, aname, raw, _sz, _res, dtype, value = struct.unpack_from("<iiiHBBI", data, at)
                if not 0 <= aname < len(strings):
                    continue
                idx = raw if raw != -1 else (value if dtype == 3 else -1)
                if 0 <= idx < len(strings):
                    found[strings[aname]] = strings[idx]
            return found.get("package", ""), found.get("versionName", "")
        pos += size
    raise ValueError("AndroidManifest.xml has no manifest element")


def find_apks():
    """APK files found in the usual folders (Downloads, Documents, home folder)."""
    home = os.path.expanduser("~")
    found = []
    for folder in (os.path.join(home, "Downloads"), os.path.join(home, "Documents"), home):
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            path = os.path.join(folder, name)
            if name.lower().endswith(".apk") and os.path.isfile(path):
                found.append({"path": path, "name": name, "size": os.path.getsize(path), "mtime": os.path.getmtime(path)})
    found.sort(key=lambda a: a["mtime"], reverse=True)
    return found


def import_apk(path):
    """Adds your own APK to the library so it can be opened with ATL."""
    path = os.path.expanduser((path or "").strip())
    if not os.path.isfile(path):
        return {"ok": False, "error": "File not found: %s" % path}
    if not zipfile.is_zipfile(path):
        return {"ok": False, "error": "The file is not an APK (not a zip)"}
    try:
        with zipfile.ZipFile(path) as apk:
            names = apk.namelist()
            if "AndroidManifest.xml" not in names:
                return {"ok": False, "error": "The file is not an APK (AndroidManifest.xml is missing)"}
            try:
                pkg_id, version = _read_axml(apk.read("AndroidManifest.xml"))
            except (ValueError, struct.error, IndexError):
                pkg_id, version = "", ""
    except zipfile.BadZipFile:
        return {"ok": False, "error": "Corrupted APK"}

    stem = os.path.splitext(os.path.basename(path))[0]
    if not PKG_RE.match(pkg_id or ""):
        pkg_id = "local." + (re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_") or "app")
    name = re.sub(r"[_\-]+", " ", stem).strip() or pkg_id

    abis = sorted({n.split("/")[1] for n in names if n.startswith("lib/") and n.endswith(".so") and n.count("/") >= 2})
    warning = ""
    if abis and not any(a in abis for a in device_abis()):
        warning = "It has native code only for %s, but the device is %s. It will most likely not run." % (
            ", ".join(abis), platform.machine())
    elif abis:
        warning = "It has native code, so it is more likely not to work in ATL."

    dest = os.path.join(APK_DIR, "%s_local.apk" % pkg_id)
    try:
        if os.path.abspath(path) != os.path.abspath(dest):
            tmp = dest + ".part"
            shutil.copyfile(path, tmp)
            os.replace(tmp, dest)
    except OSError as exc:
        return {"ok": False, "error": "Copy failed: %s" % exc}

    library = _read_json(LIBRARY_FILE, {})
    previous = library.get(pkg_id)
    if previous and previous.get("apk") != dest and os.path.exists(previous.get("apk", "")):
        os.remove(previous["apk"])
    library[pkg_id] = {
        "pkgId": pkg_id,
        "name": name,
        "summary": "Your own APK",
        "icon": "",
        "version": version,
        "apk": dest,
        "local": True,
    }
    _write_json(LIBRARY_FILE, library)
    return {"ok": True, "pkgId": pkg_id, "name": name, "warning": warning, "error": ""}


def _bundled_ld_path(usr):
    """All folders under usr/lib that contain .so files (the SDK does not declare them anywhere)."""
    root = os.path.join(usr, "lib")
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath[len(root):].count(os.sep)
        if depth >= 5:
            dirnames[:] = []
        if any(".so" in name for name in filenames):
            found.append(dirpath)
    return sorted(found, key=lambda d: (d.count(os.sep), d))


def _launch_env(cmd):
    env = dict(os.environ)
    usr = sdk_root()
    dirs = []
    if usr and os.path.abspath(cmd[0]).startswith(usr + os.sep):
        dirs = _bundled_ld_path(usr)
    if os.path.isdir(EXTRA_LIB_DIR):
        dirs.append(EXTRA_LIB_DIR)
    if dirs:
        if env.get("LD_LIBRARY_PATH"):
            dirs.append(env["LD_LIBRARY_PATH"])
        env["LD_LIBRARY_PATH"] = ":".join(dirs)
    return env


def _missing_libs(cmd):
    """Libraries the loader cannot find for ATL (via ldd). Returns an empty list if ldd is unavailable."""
    exe = cmd[0] if os.path.isabs(cmd[0]) else shutil.which(cmd[0])
    if not exe or not os.path.exists(exe):
        return []
    try:
        out = subprocess.run(["ldd", exe], env=_launch_env(cmd), capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [m.group(1) for m in re.finditer(r"^\s*(\S+) => not found", out, re.M)]


# ---------- dependency check without ldd (works in a confined app) ----------

_IGNORED_LIBS = ("ld-linux", "linux-vdso", "linux-gate")


def _elf_needed(path):
    """Returns the DT_NEEDED sonames of an ELF64 little-endian file ([] for anything else)."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
            if len(head) < 64 or head[:4] != b"\x7fELF" or head[4] != 2 or head[5] != 1:
                return []
            phoff = struct.unpack_from("<Q", head, 0x20)[0]
            phentsize, phnum = struct.unpack_from("<HH", head, 0x36)
            loads, dyn = [], None
            for i in range(phnum):
                f.seek(phoff + i * phentsize)
                ptype, _flags, offset, vaddr, _paddr, filesz, _memsz, _align = struct.unpack("<IIQQQQQQ", f.read(56))
                if ptype == 1:
                    loads.append((vaddr, offset, filesz))
                elif ptype == 2:
                    dyn = (offset, filesz)
            if dyn is None:
                return []
            f.seek(dyn[0])
            raw = f.read(dyn[1])
            needed, strtab = [], None
            for pos in range(0, len(raw) - 15, 16):
                tag, val = struct.unpack_from("<qQ", raw, pos)
                if tag == 0:
                    break
                if tag == 1:
                    needed.append(val)
                elif tag == 5:
                    strtab = val
            if strtab is None:
                return []
            base = next((o + strtab - v for v, o, s in loads if v <= strtab < v + s), None)
            if base is None:
                return []
            names = []
            for off in needed:
                f.seek(base + off)
                names.append(f.read(256).split(b"\0")[0].decode("utf-8", "replace"))
            return names
    except (OSError, struct.error):
        return []


def _missing_libs_py(exe, search_dirs=None):
    """Sonames that the executable (and, transitively, the libraries it finds) cannot resolve."""
    if search_dirs is None:
        env = _launch_env([exe]).get("LD_LIBRARY_PATH", "")
        multiarch = "%s-linux-gnu" % platform.machine()
        search_dirs = [d for d in env.split(":") if d] + [
            "/lib/" + multiarch, "/usr/lib/" + multiarch, "/lib", "/usr/lib"]
    missing, seen, queue = [], set(), [exe]
    while queue:
        current = queue.pop()
        for soname in _elf_needed(current):
            if soname in seen or soname.startswith(_IGNORED_LIBS):
                continue
            seen.add(soname)
            found = next((os.path.join(d, soname) for d in search_dirs if os.path.exists(os.path.join(d, soname))), None)
            if found:
                queue.append(found)
            else:
                missing.append(soname)
    return sorted(missing)


def _deb_data_tar(deb_bytes):
    """Reads a .deb (ar archive) and returns a tarfile of data.tar.*"""
    if deb_bytes[:8] != b"!<arch>\n":
        raise ValueError("Invalid .deb")
    pos = 8
    while pos + 60 <= len(deb_bytes):
        name = deb_bytes[pos:pos + 16].decode().strip().rstrip("/")
        size = int(deb_bytes[pos + 48:pos + 58].decode().strip())
        body = deb_bytes[pos + 60:pos + 60 + size]
        pos += 60 + size + (size % 2)
        if name.startswith("data.tar"):
            if name.endswith(".zst"):
                import zstandard

                return tarfile.open(fileobj=zstandard.ZstdDecompressor().stream_reader(io.BytesIO(body)), mode="r|")
            return tarfile.open(fileobj=io.BytesIO(body), mode="r:*")
    raise ValueError("The .deb has no data.tar")


def _deb_arch():
    return "arm64" if platform.machine().lower() in ("aarch64", "arm64") else "amd64"


def _http_get(url, timeout=60):
    request = urllib.request.Request(url, headers={"User-Agent": "atl-store/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read()


def _version_key(version):
    return [(1, int(t), "") if t.isdigit() else (0, 0, t) for t in re.findall(r"\d+|[^\d]+", version)]


def _load_index():
    """Downloads the Ubuntu Packages index (libraries only) -> {package: (version, file)}. Large, so only done when needed."""
    index = {}
    base = PORTS_BASE.rstrip("/")
    for suite in INDEX_SUITES:
        for component in INDEX_COMPONENTS:
            url = "%s/dists/%s/%s/binary-%s/Packages.gz" % (base, suite, component, _deb_arch())
            try:
                resp = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "atl-store/0.1"}), timeout=60)
            except OSError:
                continue
            with resp, gzip.GzipFile(fileobj=resp) as gz:
                name = version = filename = None
                for raw in io.TextIOWrapper(gz, encoding="utf-8", errors="replace"):
                    line = raw.rstrip("\n")
                    if line.startswith("Package: "):
                        name = line[9:]
                    elif line.startswith("Version: "):
                        version = line[9:]
                    elif line.startswith("Filename: "):
                        filename = line[10:]
                    elif not line:
                        if name and version and filename and (name.startswith("lib") or name in LIB_NAME_OVERRIDES.values()):
                            key = _version_key(version)
                            if name not in index or index[name][0] < key:
                                index[name] = (key, filename)
                        name = version = filename = None
    return index


def _candidate_packages(soname):
    if soname in LIB_NAME_OVERRIDES:
        return [LIB_NAME_OVERRIDES[soname]]
    match = re.match(r"^(.*)\.so\.(\d+)", soname)
    if not match:
        return []
    base, major = match.groups()
    names = []
    for b in (base, base.lower()):
        for sep in ("", "-"):
            for suffix in ("", "t64"):
                name = b + sep + major + suffix
                if name not in names:
                    names.append(name)
    return names


def _find_deb_url(soname, index_holder):
    """Returns the .deb URL (from the Ubuntu noble index, so the version matches the system)."""
    if index_holder.get("index") is None:
        index_holder["index"] = _load_index()
    for package in _candidate_packages(soname):
        if package in index_holder["index"]:
            return "%s/%s" % (PORTS_BASE.rstrip("/"), index_holder["index"][package][1])
    raise ValueError("don't know which Ubuntu package contains it")


def _reset_stale_extra_lib():
    marker = os.path.join(EXTRA_LIB_DIR, ".scheme")
    if not os.path.isdir(EXTRA_LIB_DIR):
        return
    try:
        with open(marker) as f:
            if f.read().strip() == EXTRA_LIB_SCHEME:
                return
    except OSError:
        pass
    shutil.rmtree(EXTRA_LIB_DIR, ignore_errors=True)


def _fetch_system_lib(soname, index_holder=None):
    """Downloads the .deb from Ubuntu and extracts only the libraries matching the soname."""
    data = _http_get(_find_deb_url(soname, index_holder if index_holder is not None else {}))
    prefix = soname.split(".so")[0] + ".so"
    os.makedirs(EXTRA_LIB_DIR, exist_ok=True)
    with open(os.path.join(EXTRA_LIB_DIR, ".scheme"), "w") as f:
        f.write(EXTRA_LIB_SCHEME)
    with _deb_data_tar(data) as tar:
        for member in tar:
            base = os.path.basename(member.name)
            if not base.startswith(prefix):
                continue
            dest = os.path.join(EXTRA_LIB_DIR, base)
            if os.path.lexists(dest):
                os.remove(dest)
            if member.issym():
                os.symlink(os.path.basename(member.linkname), dest)
            elif member.isfile():
                with open(dest, "wb") as out:
                    shutil.copyfileobj(tar.extractfile(member), out)
                os.chmod(dest, 0o755)
    if not os.path.exists(os.path.join(EXTRA_LIB_DIR, soname)):
        raise ValueError("the package does not contain %s" % soname)


def _ensure_libs(cmd, max_rounds=8):
    """Automatically downloads whatever is missing from the system (in rounds, since each library may need more).
    Returns an error message or None."""
    if BUNDLED:  # confined build: never download code; missing libraries are reported after a failed start
        return None
    _reset_stale_extra_lib()
    index_holder = {}
    tried = set()
    failed = {}
    for _ in range(max_rounds):
        missing = _missing_libs(cmd)
        todo = [m for m in missing if m not in tried]
        if not todo:
            break
        for soname in todo:
            tried.add(soname)
            try:
                _fetch_system_lib(soname, index_holder)
            except Exception as exc:  # noqa: BLE001
                failed[soname] = str(exc)
    missing = _missing_libs(cmd)
    if missing:
        details = "; ".join("%s (%s)" % (m, failed.get(m, "not downloaded")) for m in missing)
        return "Missing system libraries: " + details
    return None


def launch(pkg_id):
    entry = _read_json(LIBRARY_FILE, {}).get(pkg_id)
    if not entry or not os.path.exists(entry["apk"]):
        return {"ok": False, "error": "The APK was not found, download it again"}
    try:
        cmd = shlex.split(get_settings()["atl_bin"]) + [entry["apk"]]
    except ValueError as exc:
        return {"ok": False, "error": "Invalid ATL command: %s" % exc}

    if cmd[0] == ATL_NAME and not shutil.which(ATL_NAME):
        return {"ok": False, "needs_sdk": True, "error": "ATL is not installed"}

    problem = _ensure_libs(cmd)
    if problem:
        return {"ok": False, "error": problem}

    log_path = os.path.join(CACHE_DIR, "launch_%s.log" % pkg_id)
    try:
        with open(log_path, "w") as log:
            proc = subprocess.Popen(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env=_launch_env(cmd),
            )
    except FileNotFoundError:
        return {"ok": False, "error": "ATL not found (%s). Change it in Settings." % cmd[0]}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    time.sleep(1.5)  # if it exits right away, show the error
    if proc.poll() is not None and proc.returncode != 0:
        try:
            with open(log_path, "r", errors="replace") as f:
                tail = "".join(f.readlines()[-6:]).strip()
        except OSError:
            tail = ""
        if EXTRA_LIB_DIR in tail:
            shutil.rmtree(EXTRA_LIB_DIR, ignore_errors=True)  # probably an incompatible library: it will be downloaded again on the next attempt
        hint = ""
        if BUNDLED:
            missing = _missing_libs_py(cmd[0])
            if missing:
                hint = "\n\nMissing libraries: %s\nBundle them: python3 tools/fetch_libs.py %s" % (
                    ", ".join(missing), " ".join(missing))
        return {"ok": False, "error": "ATL exited (code %s)\n%s%s" % (proc.returncode, tail, hint)}
    return {"ok": True, "error": ""}
