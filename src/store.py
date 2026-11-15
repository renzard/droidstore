"""DroidStore backend (runs inside pyotherside).

Downloads APKs, verifies sha256, keeps them in a library and opens them with the
Android Translation Layer (ATL).
"""
import glob
import gzip
import hashlib
import io
import json
import os
import platform
import re
import shlex
import shutil
import signal
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
APP = (os.environ.get("APP_ID") or "").split("_")[0] or "droidstore"


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
# Optional pinned hashes {"atl-sdk-arm64.tar.zst": "<sha256>"}: this is what makes the SDK download authentic,
# the ".sha256" file from the same server only proves the file arrived intact.
SDK_SHA256 = {}
# Only for development: allow an SDK without any sha256 (by default the download is refused)
SDK_ALLOW_UNVERIFIED = os.environ.get("ATL_SDK_ALLOW_UNVERIFIED") == "1"
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
PORTS_BASE = os.environ.get("ATL_PORTS_BASE", "https://ports.ubuntu.com/ubuntu-ports")

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
    # Diagnostics: extra environment variables for ATL, e.g. "G_MESSAGES_DEBUG=all FOO=bar"
    "extra_env": "",
    # Diagnostics: extra arguments placed between the ATL command and the APK
    "extra_args": "",
    # "1" = every installed app gets its own entry in the Lomiri app drawer (needs droidstore-drawer.apparmor or the
    # unconfined profile; the OpenStore build has no write_path, so it is off by default)
    "launcher_shortcuts": "0",
}

PKG_RE = re.compile(r"^[A-Za-z0-9_.]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_lock = threading.Lock()
_PROCS = {}  # pkg -> subprocess.Popen of the running ATL (used by src/launcher.py to wait for the app)
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
    request = urllib.request.Request(url, headers={"User-Agent": "droidstore/0.1"})
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

        expected = (SDK_SHA256.get(name) or "").lower() or None
        if not expected:
            try:
                req = urllib.request.Request(SDK_BASE + "/" + name + ".sha256", headers={"User-Agent": "droidstore/0.1"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    expected = resp.read().decode().split()[0].lower()
                if not SHA256_RE.match(expected):
                    raise ValueError("invalid sha256 file")
            except Exception as exc:  # noqa: BLE001
                if not SDK_ALLOW_UNVERIFIED:
                    raise ValueError("Could not get the sha256 of the SDK (%s): download refused" % exc)
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
        "updateAvailable": app["id"] in _update_map(),
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


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_index_integrity(repo_url, index_path):
    """Compares index-v2.json with the sha256 the repo publishes in entry.json.

    This catches truncated/mixed/corrupted downloads. It does NOT prove who published the index
    (that needs the signature of entry.jar). Repos without entry.json are skipped. Returns True if checked.
    """
    try:
        entry = json.loads(_http_get(repo_url.rstrip("/") + "/entry.json", timeout=30).decode("utf-8"))
        expected = str((entry.get("index") or {}).get("sha256") or "").lower()
    except Exception:  # noqa: BLE001  (no entry.json / not JSON: nothing to compare)
        return False
    if not SHA256_RE.match(expected):
        return False
    if _file_sha256(index_path) != expected:
        raise ValueError("index-v2.json does not match the sha256 in entry.json: catalog rejected")
    return True


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
            try:
                _check_index_integrity(settings["repo_url"], tmp)
                index = _read_json(tmp, None)
            finally:
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


def _list_item(app, verified, library, updates):
    """The flat dict the UI lists show for one catalog app."""
    return {
        "pkgId": app["id"],
        "name": app["name"],
        "summary": app.get("summary", ""),
        "icon": app.get("icon", ""),
        "version": app.get("version", ""),
        "size": app.get("size", 0),
        "native": bool(app.get("native")),
        "verified": app["id"] in verified,
        "installed": app["id"] in library,
        "updateAvailable": app["id"] in updates,
        "newVersion": app.get("version", "") if app["id"] in updates else "",
        "busy": app["id"] in _active,
        "progress": _active.get(app["id"], 0.0),
    }


_meta_cache = {"stamp": None, "data": {}}


def _meta():
    """{pkgId: (added, lastUpdated, [categories])} read from details.db; cached until the catalog is rebuilt.

    Read from the database (not from the flat catalog) so Discover and the categories work with a catalog that
    was downloaded by an older version of the app, without a refresh.
    """
    try:
        stamp = os.stat(DETAILS_DB).st_mtime
    except OSError:
        return {}
    with _lock:
        if _meta_cache["stamp"] == stamp:
            return _meta_cache["data"]
    data = {}
    try:
        db = sqlite3.connect(DETAILS_DB)
        try:
            for pkg_id, raw in db.execute("SELECT id, data FROM details"):
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                data[pkg_id] = (d.get("added") or 0, d.get("lastUpdated") or 0, list(d.get("categories") or []))
        finally:
            db.close()
    except sqlite3.Error:
        return {}
    with _lock:
        _meta_cache["stamp"] = stamp
        _meta_cache["data"] = data
    return data


def search(query="", pure_only=False, limit=80, offset=0, category="", sort=""):
    """One page of results (sorted); the UI asks for the next page with offset=<apps already shown>."""
    q = (query or "").strip().lower()
    verified = set(_read_json(COMPAT_FILE, {}).get("verified", []))
    library = _read_json(LIBRARY_FILE, {})
    updates = _update_map()
    meta = _meta() if (category or sort) else {}
    results = []
    for app in _load_catalog():
        if pure_only and app.get("native"):
            continue
        if category and category not in (meta.get(app["id"], (0, 0, []))[2]):
            continue
        if q and q not in app["name"].lower() and q not in app.get("summary", "").lower() and q not in app["id"].lower():
            continue
        results.append(_list_item(app, verified, library, updates))
    if sort in ("new", "updated"):  # newest first (Discover's "see all")
        idx = 0 if sort == "new" else 1
        results.sort(key=lambda a: -meta.get(a["pkgId"], (0, 0, []))[idx])
    else:
        results.sort(key=lambda a: (not a["verified"], a["native"], a["name"].lower()))
    return results[offset:offset + limit]


def categories():
    """[{name, count}] of the categories that have apps, biggest first."""
    meta = _meta()
    counts = {}
    for app in _load_catalog():
        for cat in meta.get(app["id"], (0, 0, []))[2]:
            counts[cat] = counts.get(cat, 0) + 1
    return [{"name": n, "count": c} for n, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))]


def discover(limit=12):
    """Rows for the Discover tab: newest apps, recently updated apps, apps verified to work with ATL, categories."""
    verified = set(_read_json(COMPAT_FILE, {}).get("verified", []))
    library = _read_json(LIBRARY_FILE, {})
    updates = _update_map()
    meta = _meta()
    catalog = _load_catalog()

    def top(key_index, count):
        ranked = sorted((a for a in catalog if meta.get(a["id"], (0, 0, []))[key_index]),
                        key=lambda a: meta[a["id"]][key_index], reverse=True)
        return [_list_item(a, verified, library, updates) for a in ranked[:count]]

    ok = [a for a in catalog if a["id"] in verified]
    ok.sort(key=lambda a: a["name"].lower())
    return {
        "new": top(0, limit),
        "updated": top(1, limit),
        "verified": [_list_item(a, verified, library, updates) for a in ok[:limit]],
        "categories": categories(),
        "total": len(catalog),
    }


# ---------- search history ----------

HISTORY_FILE = os.path.join(CONFIG_DIR, "search_history.json")
HISTORY_MAX = 10


def get_search_history():
    items = _read_json(HISTORY_FILE, [])
    return [i for i in items if isinstance(i, str)][:HISTORY_MAX]


def add_search_history(query):
    q = (query or "").strip()
    if len(q) < 2:
        return get_search_history()
    items = [i for i in get_search_history() if i.lower() != q.lower()]
    items.insert(0, q[:60])
    items = items[:HISTORY_MAX]
    _write_json(HISTORY_FILE, items)
    return items


def clear_search_history():
    _write_json(HISTORY_FILE, [])
    return []


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
            "versionCode": app.get("versionCode", 0),
            "apk": dest,
        }
        _write_json(LIBRARY_FILE, library)
        _safe_add_shortcut(pkg_id)
        pyotherside.send("installed", pkg_id)
    except Exception as exc:  # noqa: BLE001
        pyotherside.send("failed", pkg_id, str(exc))
    finally:
        _active.pop(pkg_id, None)


def _installed_version_code(entry):
    """versionCode of an installed app. Older library entries have none: it is in the file name (<pkg>_<code>.apk)."""
    code = entry.get("versionCode")
    if isinstance(code, int) and code > 0:
        return code
    match = re.search(r"_(\d+)\.apk$", entry.get("apk", ""))
    return int(match.group(1)) if match else 0


def _update_map():
    """{pkgId: catalog app} for the installed apps whose catalog version is newer (own APKs are never updated)."""
    catalog = {a["id"]: a for a in _load_catalog()}
    result = {}
    for pkg_id, entry in _read_json(LIBRARY_FILE, {}).items():
        app = catalog.get(pkg_id)
        if entry.get("local") or app is None:
            continue
        current = _installed_version_code(entry)
        if current and app.get("versionCode", 0) > current:  # unknown current version: never a false alarm
            result[pkg_id] = app
    return result


def updates_available():
    """Installed apps with a newer version in the catalog."""
    library = _read_json(LIBRARY_FILE, {})
    result = [
        {
            "pkgId": pkg_id,
            "name": app["name"],
            "installedVersion": library[pkg_id].get("version", ""),
            "newVersion": app.get("version", ""),
        }
        for pkg_id, app in _update_map().items()
    ]
    return sorted(result, key=lambda a: a["name"].lower())


def update_all():
    """Starts the update of every app that has one (progress/installed/failed events as for install). Returns how many."""
    pending = list(_update_map())
    for pkg_id in pending:
        install(pkg_id)
    return len(pending)


def list_installed():
    updates = _update_map()
    apps = sorted(_read_json(LIBRARY_FILE, {}).values(), key=lambda a: a["name"].lower())
    return [dict(a, icon=a.get("icon", ""), version=a.get("version", ""), local=bool(a.get("local")),
                 installed=True, busy=a["pkgId"] in _active, progress=_active.get(a["pkgId"], 0.0),
                 updateAvailable=a["pkgId"] in updates,
                 newVersion=updates[a["pkgId"]].get("version", "") if a["pkgId"] in updates else "")
            for a in apps]


def remove(pkg_id):
    library = _read_json(LIBRARY_FILE, {})
    entry = library.pop(pkg_id, None)
    if entry and os.path.exists(entry.get("apk", "")):
        os.remove(entry["apk"])
    _write_json(LIBRARY_FILE, library)
    remove_shortcut(pkg_id)
    return entry is not None


# ---------- launcher entries (app drawer) ----------
# Lomiri lists every .desktop file in ~/.local/share/applications in the app drawer, like a normal app. Each entry
# runs src/launcher.py, which starts ATL with the same environment as the "Open" button of the store.
# The confined sideload build may write only the files droidstore-drawer.apparmor names (write_path), so everything here uses
# SHORTCUT_GLOB-shaped names and never needs to list or read the folder.

SHORTCUT_PREFIX = "droidstore-app-"
SHORTCUT_GLOB = SHORTCUT_PREFIX + "*.desktop*"  # keep in sync with write_path in droidstore-drawer.apparmor (tests check it)
ICON_DIR = os.path.join(DATA_DIR, "icons")
SHORTCUTS_FILE = os.path.join(DATA_DIR, "shortcuts.json")  # packages that have an entry (no folder listing needed)
_ICON_NET_PAUSE = 300  # seconds without trying the network for icons after a failure (offline)
_icon_net_retry_at = 0.0
_sync_busy = False


def _applications_dir():
    base = os.environ.get("DROIDSTORE_APPLICATIONS_DIR")
    if base:
        return base
    return os.path.join(os.path.expanduser("~"), ".local", "share", "applications")


def _shortcut_path(pkg_id):
    return os.path.join(_applications_dir(), "%s%s.desktop" % (SHORTCUT_PREFIX, pkg_id))


def _installed_app_root():
    """Stable path of the installed click (the 'current' link survives updates), else the folder we run from."""
    link = os.path.join("/opt/click.ubuntu.com", APP, "current")
    if os.path.isdir(link) and os.path.exists(os.path.join(link, "src", "launcher.py")):
        return link
    return APP_ROOT


def _read_shortcut_state():
    data = _read_json(SHORTCUTS_FILE, [])
    return {p for p in data if isinstance(p, str) and PKG_RE.match(p)} if isinstance(data, list) else set()


def _write_shortcut_state(packages):
    try:
        _write_json(SHORTCUTS_FILE, sorted(packages))
    except OSError:
        pass


def _find_apk_icon(apk_path):
    """Best-effort PNG icon from inside the APK (largest ic_launcher*.png). Returns bytes or None."""
    try:
        with zipfile.ZipFile(apk_path) as apk:
            best = None
            for info in apk.infolist():
                base = os.path.basename(info.filename).lower()
                if info.filename.startswith("res/") and base.endswith(".png") and (
                        base.startswith("ic_launcher") or base in ("icon.png", "app_icon.png")):
                    if "foreground" in base or "background" in base:
                        continue
                    if best is None or info.file_size > best.file_size:
                        best = info
            return apk.read(best) if best else None
    except (OSError, zipfile.BadZipFile, KeyError):
        return None


def _icon_for(pkg_id, entry):
    """Local icon file for the launcher entry: the catalog icon, else one taken from the APK, else the store logo."""
    global _icon_net_retry_at
    fallback = os.path.join(_installed_app_root(), "assets", "logo.svg")
    try:
        os.makedirs(ICON_DIR, exist_ok=True)
    except OSError:
        return fallback
    target = os.path.join(ICON_DIR, "%s.png" % pkg_id)
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return target
    data = None
    url = entry.get("icon") or ""
    if url.startswith(("http://", "https://")) and time.time() >= _icon_net_retry_at:
        try:
            data = _http_get(url, timeout=10)
        except Exception:  # noqa: BLE001  (offline: do not wait for every app, the APK icon is tried next)
            _icon_net_retry_at = time.time() + _ICON_NET_PAUSE
            data = None
    if not data and entry.get("apk"):
        data = _find_apk_icon(entry["apk"])
    if not data:
        return fallback
    try:
        with open(target, "wb") as f:
            f.write(data)
    except OSError:
        return fallback
    return target


def _desktop_value(text):
    return re.sub(r"[\r\n]+", " ", str(text)).strip()


def _exec_quote(arg):
    """Quoting rules of the .desktop Exec key: double quotes around the argument, \\ " ` $ escaped."""
    return '"%s"' % re.sub(r'(["`$\\])', r"\\\1", arg)


def shortcuts_enabled():
    return get_settings().get("launcher_shortcuts", "0") not in ("0", "", "false", "no")


def _write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def add_shortcut(pkg_id, force=False):
    """Creates (or refreshes) the app drawer entry of one installed app. Returns {"ok", "error"}."""
    if not PKG_RE.match(pkg_id or ""):
        return {"ok": False, "error": "Invalid package name"}
    if not force and not shortcuts_enabled():
        return {"ok": False, "error": "Launcher entries are turned off in Settings"}
    entry = _read_json(LIBRARY_FILE, {}).get(pkg_id)
    if not entry:
        return {"ok": False, "error": "The app is not installed"}
    launcher = os.path.join(_installed_app_root(), "src", "launcher.py")
    text = "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=%s" % _desktop_value(entry.get("name") or pkg_id),
        "Comment=%s" % (_desktop_value(entry.get("summary") or "") or "Android app (DroidStore)"),
        "Exec=python3 %s %s %s" % (_exec_quote(launcher), _exec_quote(pkg_id), _exec_quote(APP)),
        "Icon=%s" % _icon_for(pkg_id, entry),
        "Terminal=false",
        "Categories=Android;",
        "X-Ubuntu-Touch=true",
        "X-Lomiri-Touch=true",
        "X-DroidStore-Package=%s" % pkg_id,
        "",
    ])
    path = _shortcut_path(pkg_id)
    tmp = path + ".part"
    try:
        try:
            with open(path, "r", encoding="utf-8") as f:
                unchanged = f.read() == text
        except OSError:
            unchanged = False
        if not unchanged:  # do not touch identical files: Lomiri reloads the app list on every change
            try:
                _write_text(tmp, text)
            except FileNotFoundError:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                _write_text(tmp, text)
            os.replace(tmp, path)
    except OSError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return {"ok": False, "error": "Cannot write to %s (%s). The OpenStore build is not allowed to add app drawer entries. "
                                      "Sideload build: use droidstore-drawer.apparmor (or droidstore-unconfined.apparmor)."
                                      % (os.path.dirname(path), exc.strerror or exc)}
    _write_shortcut_state(_read_shortcut_state() | {pkg_id})
    return {"ok": True, "error": ""}


def _safe_add_shortcut(pkg_id):
    """add_shortcut for the install/import paths: a problem with the app drawer must never break an install."""
    try:
        return add_shortcut(pkg_id)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def remove_shortcut(pkg_id):
    """Removes the entry and its icon. Returns True if there was something to remove."""
    removed = False
    for path in (_shortcut_path(pkg_id), os.path.join(ICON_DIR, "%s.png" % pkg_id)):
        try:
            os.remove(path)
            removed = True
        except OSError:
            pass
    state = _read_shortcut_state()
    if pkg_id in state:
        _write_shortcut_state(state - {pkg_id})
        removed = True
    return removed


def sync_shortcuts():
    """Makes the app drawer match the library: adds missing entries, drops the ones of deleted apps.
    Returns {"ok", "created", "removed", "error"}."""
    library = _read_json(LIBRARY_FILE, {})
    result = {"ok": True, "created": 0, "removed": 0, "error": ""}
    wanted = set(library) if shortcuts_enabled() else set()
    known = _read_shortcut_state()
    try:  # entries from before the state file existed; a confined build may not be allowed to list the folder
        for name in os.listdir(_applications_dir()):
            if name.startswith(SHORTCUT_PREFIX) and name.endswith(".desktop"):
                known.add(name[len(SHORTCUT_PREFIX):-len(".desktop")])
    except OSError:
        pass
    for pkg in sorted(known - wanted):
        if remove_shortcut(pkg):
            result["removed"] += 1
    for pkg in sorted(wanted):
        outcome = add_shortcut(pkg, force=True)
        if outcome["ok"]:
            result["created"] += 1
        else:
            result["ok"], result["error"] = False, outcome["error"]
            break  # same folder for every app: one failure explains all
    return result


def sync_shortcuts_async():
    """sync_shortcuts in a background thread (it may download icons; pyotherside runs calls one after another, so a
    slow sync would hold up the whole UI). Result: event "shortcuts_synced" (ok, created, removed, error)."""
    global _sync_busy
    with _lock:
        if _sync_busy:
            return False
        _sync_busy = True

    def work():
        global _sync_busy
        try:
            result = sync_shortcuts()
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "created": 0, "removed": 0, "error": str(exc)}
        finally:
            _sync_busy = False
        pyotherside.send("shortcuts_synced", result["ok"], result["created"], result["removed"], result["error"])

    threading.Thread(target=work, daemon=True).start()
    return True


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
    info = _read_axml_full(data)
    return info.get("package", ""), info.get("versionName", "")


def _read_axml_full(data):
    """Reads package, versionName and versionCode (as strings) from the binary AndroidManifest.xml."""
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
                if raw == -1 and dtype in (0x10, 0x11):  # integer attribute (versionCode)
                    found[strings[aname]] = str(value)
                    continue
                idx = raw if raw != -1 else (value if dtype == 3 else -1)
                if 0 <= idx < len(strings):
                    found[strings[aname]] = strings[idx]
            return found
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
                manifest = _read_axml_full(apk.read("AndroidManifest.xml"))
            except (ValueError, struct.error, IndexError):
                manifest = {}
            pkg_id, version = manifest.get("package", ""), manifest.get("versionName", "")
            version_code = int(manifest["versionCode"]) if str(manifest.get("versionCode", "")).isdigit() else 0
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
        "versionCode": version_code,
        "apk": dest,
        "local": True,
    }
    _write_json(LIBRARY_FILE, library)
    _safe_add_shortcut(pkg_id)
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


ATL_XDG_DIR = os.path.join(DATA_DIR, "atl-xdg")
# Folder ATL is started in. The store itself starts in its read-only package folder and a child process inherits that
# current directory, so anything that writes a RELATIVE path fails there with "Permission denied". The case that
# showed up: ATL's Context.getCacheDir() returned null (it tried /tmp/atl_cache, which a confined app may not write),
# Chaquopy turned that into the text 'None' and Python tried to create a folder called None in the current directory.
ATL_CWD = os.path.join(ATL_XDG_DIR, "cwd")

# Android native libraries list bionic names as dependencies (libm.so, libc.so, libdl.so). Facebook's SoLoader,
# which Instagram uses, resolves those by looking for a FILE with that name in LD_LIBRARY_PATH. glibc only ships
# libm.so.6 etc., so the lookup fails ("couldn't find DSO to load: libm.so"). These symlinks give it something to find.
COMPAT_LIB_DIR = os.path.join(DATA_DIR, "compat-lib")
COMPAT_LIBS = {"libm.so": "libm.so.6", "libc.so": "libc.so.6", "libdl.so": "libdl.so.2",
               # zlib: Android apps ask for libz.so, Ubuntu only ships libz.so.1 (the shim logs "library 'libz.so' not found")
               "libz.so": "libz.so.1"}
# Android's NDK media API (AMediaCodec, AMediaFormat...) lives in libmediandk.so, ATL implements it inside libandroid.so.
# Native libraries of apps such as Spotify list libmediandk.so as a dependency, so give it an alias to ATL's libandroid.so.
COMPAT_ALIASES = {"libmediandk.so": "libandroid.so"}


def _mapped_lib_dirs():
    """Folders of the libc/libm this very process has loaded (from /proc/self/maps). Needs no directory listing."""
    found = []
    try:
        with open("/proc/self/maps") as f:
            for line in f:
                parts = line.split(None, 5)
                if len(parts) < 6:
                    continue
                path = parts[5].strip()
                if os.path.basename(path).startswith(("libc.so", "libc-", "libm.so", "libm-")):
                    d = os.path.dirname(path)
                    if d not in found:
                        found.append(d)
    except OSError:
        pass
    return found


def _glibc_dirs():
    """Where glibc lives. A confined app may not be allowed to list /lib, so well-known paths are tested one by one
    (stat) and the folders this process has already loaded libc from are added; glob is only a last resort."""
    triplets = {platform.machine() + "-linux-gnu", "aarch64-linux-gnu"}
    candidates = []
    for t in sorted(triplets):
        candidates += ["/lib/" + t, "/usr/lib/" + t]
    candidates += ["/lib64", "/usr/lib64", "/lib", "/usr/lib"]
    candidates += _mapped_lib_dirs()
    for pattern in ("/lib/*-linux-gnu", "/usr/lib/*-linux-gnu"):
        candidates += sorted(glob.glob(pattern))
    found = []
    for d in candidates:
        if d not in found and os.path.isdir(d):
            found.append(d)
    return found


def _ensure_compat_libs(existing_dirs):
    """Creates the bionic-name symlinks that are not already provided by one of existing_dirs.
    Returns the folder to add to LD_LIBRARY_PATH, or None if nothing is needed."""
    needed = {}
    for name, target in COMPAT_LIBS.items():
        if any(os.path.exists(os.path.join(d, name)) for d in existing_dirs):
            continue
        for d in _glibc_dirs():
            if os.path.exists(os.path.join(d, target)):
                needed[name] = os.path.join(d, target)
                break
    for name, target in COMPAT_ALIASES.items():
        if any(os.path.exists(os.path.join(d, name)) for d in existing_dirs):
            continue
        for d in existing_dirs:
            if os.path.exists(os.path.join(d, target)):
                needed[name] = os.path.join(d, target)
                break
    if not needed:
        return None
    try:
        os.makedirs(COMPAT_LIB_DIR, exist_ok=True)
        for name, target in needed.items():
            link = os.path.join(COMPAT_LIB_DIR, name)
            if os.path.islink(link) and os.readlink(link) == target:
                continue
            if os.path.lexists(link):
                os.remove(link)
            os.symlink(target, link)
    except OSError:
        return None
    return COMPAT_LIB_DIR


# ---------- sound ----------
# ATL plays sound in two ways, and both load their parts with dlopen, so ldd (and tools/bundle_closure.py) never see them:
#   * AudioTrack, AudioRecord and OpenSL ES open the ALSA device "default" (atl/src/api-impl-jni/audio/audio.h).
#     libasound looks for its configuration and its plugins in paths that are compiled into it: the PHONE's
#     /usr/share/alsa and /usr/lib/<triplet>/alsa-lib. Ubuntu Touch's sound server is PulseAudio, so "default" has to be
#     sent there with the ALSA "pulse" plugin. tools/build_atl_inner.sh copies that plugin to usr/lib/alsa-lib and
#     _write_alsa_conf() writes the ALSA configuration that uses it (ALSA_CONFIG_PATH).
#   * MediaPlayer and SoundPool play through GTK's GtkMediaFile: GTK loads its media backend (libmedia-gstreamer.so)
#     from GTK_PATH and GStreamer then loads its plugins from GST_PLUGIN_SYSTEM_PATH_1_0.
# An SDK that does not contain these parts (e.g. the prebuilt atl-touch one) is started exactly as before.
ALSA_CONF = os.path.join(ATL_XDG_DIR, "asound.conf")
ALSA_PCM_PULSE = "libasound_module_pcm_pulse.so"
ALSA_CTL_PULSE = "libasound_module_ctl_pulse.so"
GTK_MEDIA_BACKEND = "libmedia-gstreamer.so"
GST_SINK_PLUGIN = "libgstpulseaudio.so"


def _alsa_conf_text(alsa_dir):
    """ALSA configuration that sends the "default" PCM (playback and capture) and the mixer to PulseAudio, using the
    plugin files in alsa_dir. Self-contained on purpose: it replaces the phone's /usr/share/alsa/alsa.conf."""
    lines = [
        "# written by DroidStore every time an app starts: ALSA's default device goes to PulseAudio",
        'pcm_type.pulse { lib "%s" }' % os.path.join(alsa_dir, ALSA_PCM_PULSE),
        "pcm.!default { type pulse }",
        "pcm.pulse { type pulse }",
    ]
    if os.path.exists(os.path.join(alsa_dir, ALSA_CTL_PULSE)):
        lines += [
            'ctl_type.pulse { lib "%s" }' % os.path.join(alsa_dir, ALSA_CTL_PULSE),
            "ctl.!default { type pulse }",
            "ctl.pulse { type pulse }",
        ]
    return "\n".join(lines) + "\n"


def _write_alsa_conf(alsa_dir):
    """Writes ALSA_CONF (only if its text changed). Returns its path, or None if it cannot be written or if alsa_dir
    contains a character that would break the config syntax."""
    if any(c in alsa_dir for c in '"\\\n\r'):
        return None
    text = _alsa_conf_text(alsa_dir)
    try:
        try:
            with open(ALSA_CONF, "r", encoding="utf-8") as f:
                if f.read() == text:
                    return ALSA_CONF
        except OSError:
            pass
        os.makedirs(os.path.dirname(ALSA_CONF), exist_ok=True)
        tmp = ALSA_CONF + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, ALSA_CONF)
    except OSError:
        return None
    return ALSA_CONF


def _plugin_files(folder):
    """Names of the GStreamer plugins (libgst*.so) in folder."""
    try:
        return sorted(n for n in os.listdir(folder) if n.startswith("libgst") and n.endswith(".so"))
    except OSError:
        return []


def _audio_env(usr, env):
    """Environment variables that make ATL find the sound parts that ship in usr/ (see the comment above). env is
    the environment the app will otherwise get; only variables that have to change are returned."""
    result = {}
    alsa_dir = os.path.join(usr, "lib", "alsa-lib")
    if os.path.exists(os.path.join(alsa_dir, ALSA_PCM_PULSE)):
        conf = _write_alsa_conf(alsa_dir)
        if conf:
            result["ALSA_CONFIG_PATH"] = conf
    gtk_dir = os.path.join(usr, "lib", "gtk-4.0")
    if os.path.exists(os.path.join(gtk_dir, "4.0.0", "media", GTK_MEDIA_BACKEND)):
        result["GTK_PATH"] = ":".join([gtk_dir] + [p for p in (env.get("GTK_PATH") or "").split(":") if p])
    gst_dir = os.path.join(usr, "lib", "gstreamer-1.0")
    if _plugin_files(gst_dir):
        result["GST_PLUGIN_SYSTEM_PATH_1_0"] = gst_dir  # only our plugins: the phone's may belong to another GStreamer
        # scan the plugins inside this process (the result is cached in XDG_CACHE_HOME): no helper program that the
        # confinement could refuse to start
        result["GST_REGISTRY_FORK"] = "no"
        # errors and warnings only (level 2): GTK reports just "GStreamer error: Resource not found."; GStreamer's own
        # line says which file or plugin was missing. A GST_DEBUG that the user set in Settings (extra_env) wins.
        if not env.get("GST_DEBUG"):
            result["GST_DEBUG"] = "2"
    return result


def _audio_diag(env):
    """Lines for the top of the launch log: which sound parts ATL gets. Read these first when an app is silent."""
    lines = []
    conf = env.get("ALSA_CONFIG_PATH")
    if conf:
        lines.append("sound/ALSA (AudioTrack, OpenSL ES): default -> PulseAudio plugin, config %s" % conf)
    else:
        lines.append("sound/ALSA (AudioTrack, OpenSL ES): NOT configured, usr/lib/alsa-lib has no pulse plugin: ATL uses "
                     "whatever the phone's libasound finds. Rebuild ATL with the current tools/ (tools/build_atl.sh)")
    runtime = env.get("XDG_RUNTIME_DIR") or ""
    socket_path = os.path.join(runtime, "pulse", "native") if runtime else ""
    if env.get("PULSE_SERVER"):
        lines.append("sound/PulseAudio: PULSE_SERVER=%s" % env["PULSE_SERVER"])
    elif socket_path and os.path.exists(socket_path):
        lines.append("sound/PulseAudio: server socket found (%s)" % socket_path)
    else:
        lines.append("sound/PulseAudio: server socket NOT found (%s). Is PulseAudio running, and is \"audio\" in the "
                     "policy groups of droidstore.apparmor?" % (socket_path or "XDG_RUNTIME_DIR is not set"))
    gtk_path = env.get("GTK_PATH") or ""
    backend = next((os.path.join(p, "4.0.0", "media", GTK_MEDIA_BACKEND) for p in gtk_path.split(":")
                    if p and os.path.exists(os.path.join(p, "4.0.0", "media", GTK_MEDIA_BACKEND))), None)
    if backend:
        lines.append("sound/GTK media backend (MediaPlayer, SoundPool): %s" % backend)
    else:
        lines.append("sound/GTK media backend (MediaPlayer, SoundPool): NOT found, GTK_PATH=%s. Without it MediaPlayer "
                     "and SoundPool are silent. Rebuild ATL with the current tools/" % (gtk_path or "(not set)"))
    gst_dir = env.get("GST_PLUGIN_SYSTEM_PATH_1_0")
    plugins = _plugin_files(gst_dir) if gst_dir else []
    if plugins:
        lines.append("sound/GStreamer: %d plugins in %s, PulseAudio output plugin: %s" % (
            len(plugins), gst_dir, "yes" if GST_SINK_PLUGIN in plugins else "NO (libgstpulseaudio.so is missing)"))
    else:
        lines.append("sound/GStreamer: no plugins in usr/lib/gstreamer-1.0 (the phone's own GStreamer is used, if it has "
                     "one). Rebuild ATL with the current tools/")
    return lines


PATCH_MARKER = b"nativeShowSoftInputEditable"  # a symbol that only the keyboard patch in ./atl adds


def _file_contains(path, needle):
    import mmap
    try:
        if os.path.getsize(path) == 0:
            return False
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            return mm.find(needle) != -1
    except (OSError, ValueError):
        return False


def _keyboard_diag(env, cmd=None):
    """Lines for the top of the launch log about the on-screen keyboard (Lomiri's maliit). Read these first when the
    keyboard does not show up or types wrongly in an app."""
    lines = ["keyboard: GTK_IM_MODULE=%s QT_IM_MODULE=%s GTK_PATH=%s" % (
        env.get("GTK_IM_MODULE") or "(not set)", env.get("QT_IM_MODULE") or "(not set)", env.get("GTK_PATH") or "(not set)")]
    runtime = env.get("XDG_RUNTIME_DIR") or ""
    display = env.get("WAYLAND_DISPLAY") or ""
    sock = display if os.path.isabs(display) else os.path.join(runtime, display) if runtime and display else ""
    lines.append("keyboard: WAYLAND_DISPLAY=%s (socket %s) GDK_BACKEND=%s" % (
        display or "(not set)", "found" if sock and os.path.exists(sock) else "NOT found", env.get("GDK_BACKEND") or "(not set)"))
    # Which ATL runs: the one inside the click (usr/), the downloaded SDK, or a custom command
    exe = None
    if cmd:
        exe = cmd[0] if os.path.isabs(cmd[0]) else shutil.which(cmd[0])
    usr = sdk_root()
    if exe:
        if os.path.abspath(exe).startswith(os.path.join(APP_ROOT, "usr") + os.sep):
            origin = "the ATL inside the click (built from ./atl by tools/build_atl.sh, or fetch_sdk.sh)"
        elif usr and os.path.abspath(exe).startswith(SDK_DIR + os.sep):
            origin = "the downloaded atl-touch SDK (NOT built from ./atl: changes in ./atl have no effect on it)"
        else:
            origin = "a custom ATL command"
        lines.append("keyboard: ATL = %s -> %s" % (exe, origin))
    # Does this ATL contain the keyboard patch, and who links libmaliit?
    if usr:
        candidates = []
        if exe and os.path.exists(exe):
            candidates.append(exe)
        libdir = os.path.join(usr, "lib")
        try:
            for name in sorted(os.listdir(libdir)):
                path = os.path.join(libdir, name)
                if ".so" in name and os.path.isfile(path) and os.path.getsize(path) < 120 * 1024 * 1024:
                    candidates.append(path)
        except OSError:
            pass
        patched = [os.path.basename(c) for c in candidates if _file_contains(c, PATCH_MARKER)]
        lines.append("keyboard: EditText keyboard patch inside this ATL: %s" % (
            ("yes (%s)" % ", ".join(patched[:2])) if patched else "NO (rebuild ATL with tools/build_atl.sh, or this is the downloaded SDK)"))
        users = [os.path.basename(c) for c in candidates
                 if any("maliit" in n for n in _elf_needed(c))]
        lines.append("keyboard: libraries that need libmaliit: %s" % (", ".join(users[:5]) if users else "none found"))
    modules = []
    import glob
    for pattern in ((os.path.join(usr, "lib", "gtk-4.0", "4.0.0", "immodules", "*.so"),) if usr else ()) + (
            "/usr/lib/*/gtk-4.0/4.0.0/immodules/*.so", "/usr/lib/*/gtk-3.0/3.0.0/immodules/*.so"):
        modules += glob.glob(pattern)
    shown = sorted(set("%s (%s)" % (os.path.basename(m), "gtk4" if "gtk-4.0" in m else "gtk3") for m in modules))
    lines.append("keyboard: GTK input method modules found: %s" % (", ".join(shown) if shown else "none (GTK4 then uses only its built-in Wayland text-input)"))
    dirs = [d for d in (env.get("LD_LIBRARY_PATH") or "").split(":") if d]
    found = [os.path.join(d, n) for d in dirs if os.path.isdir(d)
             for n in sorted(os.listdir(d)) if n.startswith("libmaliit-glib.so")]
    lines.append("keyboard: libmaliit-glib in LD_LIBRARY_PATH: %s" % (found[0] if found else "no"))
    return lines


def _launch_env(cmd):
    env = dict(os.environ)
    # A confined app may only write inside its own folders; ATL stores each app's data under
    # $XDG_DATA_HOME/android_translation_layer, so point it (and its cache/config) into our writable folder.
    for var, sub in (("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"), ("XDG_CONFIG_HOME", "config")):
        path = os.path.join(ATL_XDG_DIR, sub)
        try:
            os.makedirs(path, exist_ok=True)
            env[var] = path
        except OSError:
            pass
    usr = sdk_root()
    dirs = []
    if usr and os.path.abspath(cmd[0]).startswith(usr + os.sep):
        dirs = _bundled_ld_path(usr)
        env.update(_audio_env(usr, env))  # ALSA -> PulseAudio, GTK media backend, GStreamer plugins (see "sound" above)
        if not env.get("GST_DEBUG"):
            # also for an ATL without its own GStreamer plugins (the downloaded SDK): GTK only reports "GStreamer error:
            # Resource not found.", GStreamer's own line (level 2 = errors and warnings) says what was missing
            env["GST_DEBUG"] = "2"
    if os.path.isdir(EXTRA_LIB_DIR):
        dirs.append(EXTRA_LIB_DIR)
    compat = _ensure_compat_libs(dirs + [d for d in env.get("LD_LIBRARY_PATH", "").split(":") if d])
    if compat:
        dirs.append(compat)
    if dirs:
        if env.get("LD_LIBRARY_PATH"):
            dirs.append(env["LD_LIBRARY_PATH"])
        env["LD_LIBRARY_PATH"] = ":".join(dirs)
    return env


def _launch_cwd():
    """A writable folder to start ATL in (see ATL_CWD), or None to keep the current directory."""
    try:
        os.makedirs(ATL_CWD, exist_ok=True)
    except OSError:
        return None
    return ATL_CWD if os.access(ATL_CWD, os.W_OK | os.X_OK) else None


def _missing_libs(cmd):
    """Libraries the loader cannot find for ATL (via ldd). Returns an empty list if ldd is unavailable."""
    exe = cmd[0] if os.path.isabs(cmd[0]) else shutil.which(cmd[0])
    if not exe or not os.path.exists(exe):
        return []
    try:
        out = subprocess.run(["ldd", exe], env=_launch_env(cmd), capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    found = [m.group(1) for m in re.finditer(r"^\s*(\S+) => not found", out, re.M)]
    if found:
        return found
    # ldd is missing or printed nothing useful on the phone: fall back to the pure-Python ELF check
    return _missing_libs_py(exe)


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
    request = urllib.request.Request(url, headers={"User-Agent": "droidstore/0.1"})
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
                resp = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "droidstore/0.1"}), timeout=60)
            except OSError:
                continue
            with resp, gzip.GzipFile(fileobj=resp) as gz:
                name = version = filename = sha = None
                for raw in io.TextIOWrapper(gz, encoding="utf-8", errors="replace"):
                    line = raw.rstrip("\n")
                    if line.startswith("Package: "):
                        name = line[9:]
                    elif line.startswith("Version: "):
                        version = line[9:]
                    elif line.startswith("Filename: "):
                        filename = line[10:]
                    elif line.startswith("SHA256: "):
                        sha = line[8:].strip().lower()
                    elif not line:
                        # without a SHA256 the package cannot be verified, so it is not offered
                        if (name and version and filename and sha
                                and (name.startswith("lib") or name in LIB_NAME_OVERRIDES.values())):
                            key = _version_key(version)
                            if name not in index or index[name][0] < key:
                                index[name] = (key, filename, sha)
                        name = version = filename = sha = None
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


def _find_deb(soname, index_holder):
    """Returns (URL, sha256) of the .deb (from the Ubuntu noble index, so the version matches the system)."""
    if index_holder.get("index") is None:
        index_holder["index"] = _load_index()
    for package in _candidate_packages(soname):
        if package in index_holder["index"]:
            _key, filename, sha = index_holder["index"][package]
            return "%s/%s" % (PORTS_BASE.rstrip("/"), filename), sha
    raise ValueError("don't know which Ubuntu package contains it")


def _find_deb_url(soname, index_holder):
    return _find_deb(soname, index_holder)[0]


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
    url, expected_sha = _find_deb(soname, index_holder if index_holder is not None else {})
    data = _http_get(url)
    if hashlib.sha256(data).hexdigest() != expected_sha:
        raise ValueError("the .deb does not match the sha256 in the Ubuntu index, rejected")
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


ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parse_extra_env(text):
    """'A=1 B="x y"' -> {'A': '1', 'B': 'x y'}. Invalid entries are ignored."""
    result = {}
    try:
        parts = shlex.split(text or "")
    except ValueError:
        return result
    for part in parts:
        name, sep, value = part.partition("=")
        if sep and ENV_NAME_RE.match(name):
            result[name] = value
    return result


def _lib_diag(env):
    """One line per bionic base library: where the loader could find it, and the compat symlinks."""
    dirs = [d for d in env.get("LD_LIBRARY_PATH", "").split(":") if d]
    lines = []
    for name in ("libm.so", "libc.so", "libdl.so", "libz.so", "liblog.so"):
        where = [d for d in dirs if os.path.lexists(os.path.join(d, name))]
        lines.append("%s -> %s" % (name, ", ".join(where) if where else "NOT in LD_LIBRARY_PATH"))
    compat = []
    try:
        for n in sorted(os.listdir(COMPAT_LIB_DIR)):
            link = os.path.join(COMPAT_LIB_DIR, n)
            compat.append("%s => %s%s" % (n, os.readlink(link) if os.path.islink(link) else "?",
                                           "" if os.path.exists(link) else " (BROKEN)"))
    except OSError:
        compat.append("(no compat-lib folder)")
    lines.append("compat-lib: " + "; ".join(compat))
    lines.append("compat dir in LD_LIBRARY_PATH: %s" % (COMPAT_LIB_DIR in dirs))
    lines.append("glibc dirs: %s" % ", ".join(_glibc_dirs()))
    return lines


def _apk_abi_summary(apk_path):
    """Which CPU architectures the APK ships native libraries for, and where its libgnustl_shared.so is."""
    try:
        with zipfile.ZipFile(apk_path) as apk:
            names = apk.namelist()
    except (OSError, zipfile.BadZipFile) as exc:
        return ["APK native libs: cannot read (%s)" % exc]
    counts = {}
    for n in names:
        parts = n.split("/")
        if len(parts) >= 3 and parts[0] == "lib" and n.endswith(".so"):
            counts[parts[1]] = counts.get(parts[1], 0) + 1
    if not counts:
        return ["APK native libs: none (pure Java/Kotlin)"]
    gnustl = sorted(n.split("/")[1] for n in names if n.endswith("/libgnustl_shared.so") and n.startswith("lib/"))
    return ["APK native libs: " + ", ".join("%s=%d files" % (a, c) for a, c in sorted(counts.items())),
            "device runs: %s (a 64-bit process cannot load 32-bit armeabi-v7a/armeabi/x86 libraries)" % ", ".join(device_abis()),
            "libgnustl_shared.so is in: %s" % (", ".join(gnustl) or "-")]


def _abi_hint(apk_path):
    """Plain-language reason when the APK ships native code, but none for this device's CPU architecture."""
    try:
        with zipfile.ZipFile(apk_path) as apk:
            abis = sorted({n.split("/")[1] for n in apk.namelist()
                           if n.startswith("lib/") and n.endswith(".so") and n.count("/") >= 2})
    except (OSError, zipfile.BadZipFile):
        return ""
    if abis and not any(a in abis for a in device_abis()):
        return ("This APK only has native code for %s, but this device needs %s. "
                "Get the %s build of the app." % (
                    ", ".join(abis), ", ".join(device_abis()), " / ".join(device_abis())))
    return ""


# ---------- raw audio resources (MediaPlayer.create / SoundPool.load of an app's res/raw files) ----------
# The ATL of the downloaded atl-touch SDK looks for a sound that an app plays from its resources (R.raw.sound_click)
# at  <app data>/<resource name>  (no folder, no extension) and does not always extract it from the APK first. GStreamer
# then says "No such file .../gr.learn.programming_local.apk_/sound_click" and the app only gets
# "SoundPool: GStreamer error: Resource not found." / "IOException: MediaPlayer: prepare failed". Before an app starts,
# the store puts those files there itself (copies of the APK's own res/raw entries; nothing is changed in the APK).
_AUDIO_EXTS = (".ogg", ".oga", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".opus", ".mid", ".midi", ".amr", ".3gp")
_AUDIO_MAX_BYTES = 20 * 1024 * 1024
_AUDIO_MAX_FILES = 300


def _app_data_dir(apk_path):
    """Where ATL keeps the files of one app: $XDG_DATA_HOME/android_translation_layer/<apk file name>_"""
    return os.path.join(ATL_XDG_DIR, "data", "android_translation_layer", os.path.basename(apk_path) + "_")


def _prepare_raw_audio(apk_path):
    """Copies the audio files of the APK's res/raw folder to <app data>/<name without extension>, unless a file of that
    name is already there. Returns the lines for the log (what the APK has, what was copied)."""
    try:
        z = zipfile.ZipFile(apk_path)
    except (OSError, zipfile.BadZipFile) as exc:
        return ["audio resources: the APK cannot be read (%s)" % exc]
    notes = []
    with z:
        audio = [i for i in z.infolist()
                 if not i.is_dir() and i.filename.lower().endswith(_AUDIO_EXTS)][:_AUDIO_MAX_FILES]
        if not audio:
            return ["audio resources: the APK has no sound files"]
        notes.append("audio resources in the APK: %d (%s%s)" % (
            len(audio), ", ".join(i.filename for i in audio[:6]), ", ..." if len(audio) > 6 else ""))
        dest = _app_data_dir(apk_path)
        copied, present, skipped = [], 0, 0
        for info in audio:
            if not info.filename.startswith("res/raw") or info.file_size > _AUDIO_MAX_BYTES:
                skipped += 1
                continue
            name = os.path.splitext(os.path.basename(info.filename))[0]
            if not name or name.startswith("."):
                skipped += 1
                continue
            target = os.path.join(dest, name)
            try:
                if os.path.isfile(target) and os.path.getsize(target) == info.file_size:
                    present += 1
                    continue
                if os.path.exists(target):  # something else with that name: not ours to replace
                    skipped += 1
                    continue
                os.makedirs(dest, exist_ok=True)
                tmp = target + ".part"
                with z.open(info) as src, open(tmp, "wb") as out:
                    shutil.copyfileobj(src, out)
                os.replace(tmp, target)
                copied.append(name)
            except (OSError, zipfile.BadZipFile) as exc:
                notes.append("audio resources: could not copy %s (%s)" % (info.filename, exc))
        if copied or present:
            notes.append("audio resources: %d copied to the app's data folder (%s), %d already there" % (
                len(copied), ", ".join(copied[:6]) + (", ..." if len(copied) > 6 else ""), present))
        if skipped:
            notes.append("audio resources: %d sound file(s) not under res/raw were left alone (a name that was "
                         "shortened by the app's build cannot be mapped back)" % skipped)
    return notes


def _uses_chaquopy(apk_path):
    """True if the APK embeds Python through Chaquopy (it ships libchaquopy_java.so or assets/chaquopy/)."""
    try:
        with zipfile.ZipFile(apk_path) as apk:
            return any(n.startswith("assets/chaquopy/") or n.endswith("/libchaquopy_java.so") for n in apk.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


# ---------- Chaquopy (apps with embedded Python) ----------
# ATL's bionic shim cannot dlopen(NULL), and Python's ctypes does exactly that on import
# (pythonapi = PyDLL(None)), so every Chaquopy app died in Application.onCreate with
# "OSError: Cannot load library:". The fix is made in the store's COPY of the APK (the app's own source is untouched):
# ctypes/__init__ gets a small prelude that makes _ctypes.dlopen(None) fall back to the global-scope handle 0
# (RTLD_DEFAULT) instead of raising. Chaquopy ships the standard library as .pyc only, so then the original .pyc is
# embedded in a new ctypes/__init__.py that runs it after the prelude; if the source exists, the prelude is put in front.
_CTYPES_PY = "ctypes/__init__.py"
_CTYPES_PYC = "ctypes/__init__.pyc"
_PYDLL_MARK = "droidstore: ATL cannot dlopen(NULL)"
_CTYPES_PRELUDE = (
    "# " + _PYDLL_MARK + ": dlopen(NULL) falls back to the global scope (RTLD_DEFAULT = 0)\n"
    "import _ctypes as _dsc\n"
    "_ds_real_dlopen = _dsc.dlopen\n"
    "def _ds_dlopen(name, *args):\n"
    "    if name is None:\n"
    "        try:\n"
    "            return _ds_real_dlopen(None, *args)\n"
    "        except OSError:\n"
    "            return 0\n"
    "    return _ds_real_dlopen(name, *args)\n"
    "_dsc.dlopen = _ds_dlopen\n"
)


def _ctypes_from_source(text):
    """New ctypes/__init__.py text (prelude in front of the original source), or None when already patched."""
    if _PYDLL_MARK in text:
        return None
    return _CTYPES_PRELUDE + text


def _ctypes_from_pyc(pyc):
    """New ctypes/__init__.py source that applies the prelude and then runs the original compiled module."""
    return (_CTYPES_PRELUDE + "import marshal as _ds_marshal\n_ds_pyc = %r\n"
            "exec(_ds_marshal.loads(_ds_pyc[16:]), globals())\n") % bytes(pyc)


def _copy_info(info, name=None):
    new_info = zipfile.ZipInfo(name or info.filename, info.date_time)
    new_info.compress_type = info.compress_type
    new_info.external_attr = info.external_attr
    new_info.create_system = info.create_system
    return new_info


def _rewrite_zip(data, replacements, removals=()):
    """Copy of a zip (bytes): members in replacements get new bytes (new names are added at the end), members in
    removals are dropped, every other member keeps its bytes and compression."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        done = set()
        for info in src.infolist():
            if info.filename in removals:
                continue
            payload = replacements.get(info.filename)
            if payload is None:
                payload = src.read(info.filename)
            else:
                done.add(info.filename)
            dst.writestr(_copy_info(info), payload)
        for name, payload in replacements.items():
            if name not in done:
                dst.writestr(zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0)), payload)
    return out.getvalue()


def _app_data_dir(apk_path):
    """Where ATL keeps the data of this APK (ATL: $XDG_DATA_HOME/android_translation_layer/<apk file name>_)."""
    return os.path.join(ATL_XDG_DIR, "data", "android_translation_layer", os.path.basename(apk_path) + "_")


def _clear_stale_chaquopy_cache(apk_path):
    """Chaquopy copies its Python files out of the APK into the app's data folder on the first start and only copies
    them again when the APK's own manifest says they changed. After the store patches ctypes the manifest is the
    same, so the old, unpatched copy would keep being used. Removes such an old copy (the whole 'chaquopy' folder
    that holds it, Chaquopy makes it again). Returns the list of things removed or found, for the log."""
    base = _app_data_dir(apk_path)
    notes = []
    if not os.path.isdir(base):
        return ["no app data folder yet"]
    stale = set()
    for dirpath, dirnames, filenames in os.walk(base):
        if dirpath[len(base):].count(os.sep) >= 6:
            dirnames[:] = []
        for name in filenames:
            path = os.path.join(dirpath, name)
            old = False
            try:
                if name.endswith(".imy"):
                    with zipfile.ZipFile(path) as imy:
                        names = imy.namelist()
                        if _CTYPES_PYC in names:
                            old = True
                        elif _CTYPES_PY in names and _PYDLL_MARK not in imy.read(_CTYPES_PY).decode("utf-8", "replace"):
                            old = True
                elif path.endswith(("/" + _CTYPES_PYC, "/" + _CTYPES_PY)):
                    if path.endswith(_CTYPES_PYC):
                        old = True
                    else:
                        with open(path, "r", errors="replace") as f:
                            old = _PYDLL_MARK not in f.read()
            except (OSError, zipfile.BadZipFile):
                continue
            if old:
                # remove the 'chaquopy' folder that holds it, or else just the file
                target = path
                parts = path[len(base):].split(os.sep)
                if "chaquopy" in parts:
                    target = os.path.join(base, *parts[:parts.index("chaquopy") + 1])
                stale.add(target)
    for target in sorted(stale):
        try:
            if os.path.isdir(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
            notes.append("removed old unpatched copy: " + target[len(base):])
        except OSError as exc:
            notes.append("could not remove %s (%s)" % (target[len(base):], exc))
    return notes or ["no old copy of the Python files found in the app data"]


def _patch_chaquopy_apk(apk_path):
    """Applies the ctypes fix to a Chaquopy APK. Returns a one-line status for the launch log."""
    try:
        with zipfile.ZipFile(apk_path) as apk:
            imys = [i for i in apk.infolist() if i.filename.startswith("assets/chaquopy/") and i.filename.endswith(".imy")]
            changes = {}
            already = False
            for info in imys:
                data = apk.read(info.filename)
                try:
                    with zipfile.ZipFile(io.BytesIO(data)) as imy:
                        names = imy.namelist()
                        if _CTYPES_PY in names:
                            new_text = _ctypes_from_source(imy.read(_CTYPES_PY).decode("utf-8"))
                            if new_text is None:
                                already = True
                                continue
                            changes[info.filename] = _rewrite_zip(data, {_CTYPES_PY: new_text.encode("utf-8")},
                                                                  (_CTYPES_PYC,))
                        elif _CTYPES_PYC in names:
                            new_text = _ctypes_from_pyc(imy.read(_CTYPES_PYC))
                            changes[info.filename] = _rewrite_zip(data, {_CTYPES_PY: new_text.encode("utf-8")},
                                                                  (_CTYPES_PYC,))
                except zipfile.BadZipFile:
                    continue
            if not changes:
                if already:
                    return "Chaquopy compat: already patched"
                return "Chaquopy compat: no ctypes found in the app's Python assets, nothing patched"
            tmp = apk_path + ".part"
            with zipfile.ZipFile(tmp, "w") as dst:
                for info in apk.infolist():
                    dst.writestr(_copy_info(info), changes[info.filename] if info.filename in changes
                                 else apk.read(info.filename))
        os.replace(tmp, apk_path)
        return "Chaquopy compat: patched ctypes in %s" % ", ".join(sorted(os.path.basename(n) for n in changes))
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError) as exc:
        try:
            os.remove(apk_path + ".part")
        except OSError:
            pass
        return "Chaquopy compat: could not patch (%s)" % exc


def _launch_log_path(pkg_id):
    return os.path.join(CACHE_DIR, "launch_%s.log" % pkg_id)


def _tail(path, lines):
    try:
        with open(path, "r", errors="replace") as f:
            return "".join(f.readlines()[-lines:]).strip()
    except OSError:
        return ""


def _describe_exit(code):
    if code is None:
        return "still running"
    if code < 0:
        try:
            return "killed by signal %s (%s)" % (-code, signal.Signals(-code).name)
        except ValueError:
            return "killed by signal %s" % -code
    return "code %s" % code


LOG_NOISE = ("jit_logger.cc", "hidden_api.cc", "I/SystemProperties")

# Lines with these words are the ones that explain a crash: never cut them short
_IMPORTANT_RE = re.compile(r"Exception|Error|FATAL|dlopen|cannot open shared|undefined symbol|System\.exit")
_THREAD_HEADER_RE = re.compile(r'^"[^"]+" (?:daemon )?prio=\d+ tid=\d+ (\w+)')
_DUMP_DETAIL = ("  | ", "  kernel:", "  native:", "  at ", "  - ")

# (kind, pattern, what it means). The first line of the log that matches any of them is taken as the cause,
# because everything after it (thread dumps, System.exit) is only the consequence.
_CAUSES = (
    ("chaquopy", re.compile(r"com\.chaquo\.python\.PyException|OSError: Cannot load library"),
     "This app embeds Python (Chaquopy) and Python failed to start inside ATL. The store patches ctypes in its "
     "copy of the APK before launching (see the 'Chaquopy compat' line at the top of the log). If that line says "
     "it could not patch, or the error stays the same, send the whole log: the problem is then in ATL itself."),
    ("native", re.compile(r"UnsatisfiedLinkError|dlopen failed|cannot open shared object file|undefined symbol"
                          r"|library [\"'][^\"']+[\"'] not found|error while loading shared libraries"),
     "A native library could not be loaded (a missing library or symbol). If it is a system library, bundle it "
     "(tools/fetch_libs.py). If it is one of the .so files inside the APK, the app's native code does not work "
     "with ATL and the store cannot fix that."),
    ("gms", re.compile(r"(?:Exception|Error).*(?:google[./]android[./]gms|GoogleApiAvailability|google[./]firebase)"
                       r"|(?:google[./]android[./]gms|GoogleApiAvailability).*(?:Exception|Error)"),
     "The app needs Google Play Services, which ATL does not provide. Apps that depend on it usually do not work."),
    ("api", re.compile(r"NoSuchMethodError|NoSuchFieldError|AbstractMethodError|NoClassDefFoundError"
                       r"|ClassNotFoundException|UnsupportedOperationException"),
     "The app uses an Android class or method that ATL does not implement (yet). A newer ATL SDK may help; "
     "otherwise report this exact line to the ATL developers."),
    ("security", re.compile(r"SecurityException"),
     "The app was denied something. Check the AppArmor policy groups of the store."),
    ("exception", re.compile(r"FATAL EXCEPTION|Exception in thread|^\s*Caused by:"
                             r"|(?<![\w.$])(?:[a-z][\w]*\.)+[A-Z]\w*(?:Exception|Error)\b"),
     "The app crashed with a Java exception. The first line is the cause; the lines after it show where."),
)
# Python tried to create a folder called 'None': ATL's Context.getCacheDir() returned null (see ATL_CWD)
_NONE_DIR_RE = re.compile(r"(?:PermissionError|FileNotFoundError|OSError)\b[^\n]*['\"]None['\"]")
_NONE_DIR_ADVICE = ("ATL could not create the app's cache folder (Context.getCacheDir() returned null: ATL tries "
                    "/tmp/atl_cache first, and a confined app may not write there), so Python tried to create a folder "
                    "literally named 'None' in its current directory. This is not an Android permission: the app asks "
                    "for none. Current ATL falls back to the app's data folder, and the store starts ATL in a writable "
                    "folder (see 'working folder' at the top of the log). If you still see this, the ATL inside the "
                    "package was built before that fix: rebuild it (./tools/build_atl.sh).")
_MISSING_LIB_RE = re.compile(r"shim bionic linker: library ['\"]([^'\"]+)['\"] not found")
# What ATL, ALSA, the ALSA pulse plugin and GStreamer print when sound cannot be opened. An app can keep running
# silently after these lines, so they are never taken as THE cause of a crash: _cause_text only adds a note.
_SOUND_RE = re.compile(r"Can't open \"[^\"]*\" PCM device|Unknown PCM|snd_pcm_\w+: Assertion"
                       r"|ALSA lib .*(?:cannot find card|cannot access file|Cannot open shared library)"
                       r"|PulseAudio: Unable to connect|no element \"(?:playbin|pulsesink|autoaudiosink)\"")
_EXIT_RE = re.compile(r"System\.exit called, status: (-?\d+)")
# The store's own last line of a launch log: "[droidstore] ended after 24.1s: code 0"
_ENDED_RE = re.compile(r"^\[droidstore\] ended after [\d.]+s: code (-?\d+)")
# What marks the exception that killed the app: Android's "FATAL EXCEPTION: main", the JVM's "Exception in thread" and
# ATL's own handler, which prints "hacky_uncaught_exception_handler: Thread: <name>, Exception:" and then the exception
# on the next line (this one was missing, so a harmless warning earlier in the log was reported as the cause).
_FATAL_RE = re.compile(r"FATAL EXCEPTION|Exception in thread|hacky_uncaught_exception_handler\b.*Exception")
# A line the app logged at verbose/debug/info/WARNING level: "W/Tag(pid): ..." as ATL prints it, or ART's
# "<tag> W 11-15 17:02:05 pid tid file.cc:1] ...". An exception printed at that level was caught and logged by the app
# (SQLite's "Unable to grab file lock" is one), it did not end the app. They are only used as the cause when nothing
# at error level (or without a level) explains the crash.
_BENIGN_LEVEL_RE = re.compile(r"^(?:[VDIW]/|\S+ [VDIW] \d\d-\d\d [\d:.]+ )")
# The class that is missing, from ART's "Failed resolution of: Landroid/app/TaskStackBuilder;", from
# "NoClassDefFoundError: android.app.X" / "ClassNotFoundException: android.app.X" or from "Didn't find class "android.x.Y""
_MISSING_CLASS_RES = (
    re.compile(r"Failed resolution of: L([\w/$]+);"),
    re.compile(r"(?:NoClassDefFoundError|ClassNotFoundException): ([A-Za-z_][\w.$]*)"),
    re.compile(r"Didn't find class \"([\w.$]+)\""),
)


def _missing_android_class(lines):
    """The android.* class that an app needed and ATL does not have (from the first line that names one), or None.
    Only the Android framework counts: that is the part ATL implements itself (atl/src/api-impl)."""
    for l in lines:
        for regex in _MISSING_CLASS_RES:
            m = regex.search(l)
            if m:
                name = m.group(1).replace("/", ".")
                if name.startswith("android."):
                    return name
    return None


# A line that belongs to a Java stack trace: "  at x.y.Z(File.kt:1)", "... 11 more", "Caused by: ..." or "Suppressed: ...".
# ATL's uncaught-exception handler prints "Exception:" and then the trace, sometimes WITHOUT the first line that names
# the exception (the DuckDuckGo log had an empty line there), so the name only shows up in a later "Caused by:".
def _is_trace_line(l):
    t = l.strip()
    return not t or t.startswith(("at ", "... ", "Caused by:", "Suppressed:"))


# "Suppressed: kotlinx.coroutines.internal.DiagnosticCoroutineContextException: [StandaloneCoroutine{Cancelling}@..]" is
# debugging information that kotlinx adds to every coroutine crash, never the reason for it.
def _is_suppressed_line(l):
    return l.lstrip().startswith("Suppressed:")


# The first stack frame that belongs to the app (not the JDK, Android, Kotlin or coroutines): where the app was.
_FRAMEWORK_FRAME_RE = re.compile(r"^\s*at (?:java|javax|android|androidx|dalvik|libcore|sun|kotlin|kotlinx|com\.android)\.")


def _app_frame(lines, i):
    """The first app frame of the stack trace that contains lines[i] (before or after it), or None."""
    lo = hi = i
    while lo > 0 and _is_trace_line(lines[lo - 1]):
        lo -= 1
    while hi + 1 < len(lines) and _is_trace_line(lines[hi + 1]):
        hi += 1
    for l in lines[lo:hi + 1]:
        if l.strip().startswith("at ") and not _FRAMEWORK_FRAME_RE.match(l):
            return l.strip()
    return None


# Classes that the atl/ source of this project adds to ATL (atl/src/api-impl/...). An ATL binary built before they were
# added does not have them, and the app then dies with ClassNotFoundException however new the store is.
ATL_ADDED_CLASSES = ("android.app.TaskStackBuilder", "android.net.VpnService", "android.service.autofill.AutofillService")
_API_IMPL_JAR_CACHE = {}


def _api_impl_jar(usr=None):
    usr = usr or sdk_root()
    if not usr:
        return None
    for rel in ("share/android_translation_layer/api-impl.jar", "lib/android_translation_layer/api-impl.jar",
                "share/atl/api-impl.jar", "lib/atl/api-impl.jar"):
        path = os.path.join(usr, rel)
        if os.path.isfile(path):
            return path
    for root, _dirs, files in os.walk(usr):  # a different install layout: look for it
        if "api-impl.jar" in files:
            return os.path.join(root, "api-impl.jar")
    return None


def _atl_has_class(name, usr=None):
    """True/False: does the ATL that is installed (its api-impl.jar, which holds the Android classes ATL implements)
    contain this class? None if it cannot be told (no ATL, no jar, unreadable)."""
    jar = _api_impl_jar(usr)
    if not jar:
        return None
    try:
        stamp = (jar, os.path.getmtime(jar), os.path.getsize(jar))
    except OSError:
        return None
    entry = _API_IMPL_JAR_CACHE.get(stamp)
    if entry is None:
        entry = set()
        try:
            with zipfile.ZipFile(jar) as z:
                names = z.namelist()
                wanted = [c.replace(".", "/") for c in ATL_ADDED_CLASSES]
                for c, w in zip(ATL_ADDED_CLASSES, wanted):
                    if w + ".class" in names:
                        entry.add(c)
                for n in names:
                    if n.endswith(".dex"):  # the dex file lists every class as its descriptor: Landroid/app/X;
                        data = z.read(n)
                        entry.update(c for c, w in zip(ATL_ADDED_CLASSES, wanted) if ("L%s;" % w).encode() in data)
        except (OSError, zipfile.BadZipFile, KeyError):
            return None
        _API_IMPL_JAR_CACHE.clear()
        _API_IMPL_JAR_CACHE[stamp] = entry
    if name in ATL_ADDED_CLASSES:
        return name in entry
    # not one of ours: look it up directly (a rare, one-off check)
    try:
        with zipfile.ZipFile(jar) as z:
            needle = ("L%s;" % name.replace(".", "/")).encode()
            return any(needle in z.read(n) for n in z.namelist() if n.endswith(".dex"))
    except (OSError, zipfile.BadZipFile, KeyError):
        return None


# Fallback for an ATL that was built before ATL_ADDED_CLASSES existed (for example the downloaded atl-touch SDK, which is
# what runs when the click was built without usr/): compat/atl-compat.jar (tools/make_compat_dex.py) holds a small
# android.app.TaskStackBuilder. It is added to the APK as one more dex file (classes<N+1>.dex): whatever ATL version runs,
# the app's own class loader reads every classesN.dex of its APK, while an extra file on ATL's command line was ignored by
# the downloaded SDK (the log showed the jar on the command line and the class still missing). Done only for an APK that
# uses the class AND only when this ATL really lacks it, so an app that works today and an ATL that has the class are
# never touched. The APK is rewritten the same way the Chaquopy and raw-audio fixes do it; a re-downloaded APK is patched again.
ATL_COMPAT_JAR = os.path.join(APP_ROOT, "compat", "atl-compat.jar")
ATL_COMPAT_MARKER = "META-INF/droidstore-compat.txt"
ATL_COMPAT_VPN_JAR = os.path.join(APP_ROOT, "compat", "atl-compat-vpn.jar")
ATL_COMPAT_VPN_MARKER = "META-INF/droidstore-compat-vpn.txt"
ATL_COMPAT_AUTOFILL_JAR = os.path.join(APP_ROOT, "compat", "atl-compat-autofill.jar")
ATL_COMPAT_AUTOFILL_MARKER = "META-INF/droidstore-compat-autofill.txt"
# One entry per jar: the class that decides ("does THIS ATL lack it and does the APK use it?") and the marker that says
# "already added". A jar holds one dex per class (classes.dex, classes2.dex, ...); all of them are added together.
ATL_COMPAT_SETS = (
    {"jar": ATL_COMPAT_JAR, "marker": ATL_COMPAT_MARKER, "classes": ("android.app.TaskStackBuilder",)},
    # DuckDuckGo: "NoClassDefFoundError: Failed resolution of: Landroid/net/VpnService;" (+ its inner class Builder)
    {"jar": ATL_COMPAT_VPN_JAR, "marker": ATL_COMPAT_VPN_MARKER, "classes": ("android.net.VpnService",)},
    # DuckDuckGo: "NoClassDefFoundError: Failed resolution of: Landroid/service/autofill/AutofillService;"
    {"jar": ATL_COMPAT_AUTOFILL_JAR, "marker": ATL_COMPAT_AUTOFILL_MARKER,
     "classes": ("android.service.autofill.AutofillService",)},
)
ATL_COMPAT_CLASSES = tuple(c for cs in ATL_COMPAT_SETS for c in cs["classes"])
_APK_CLASS_CACHE = {}


def _apk_uses_classes(apk_path, classes):
    """The subset of `classes` (dotted names) that the dex files of this APK refer to. Empty set if it cannot be read."""
    try:
        st = os.stat(apk_path)
    except OSError:
        return set()
    key = (apk_path, st.st_mtime, st.st_size, tuple(classes))
    if key in _APK_CLASS_CACHE:
        return _APK_CLASS_CACHE[key]
    found = set()
    needles = {c: ("L%s;" % c.replace(".", "/")).encode() for c in classes}
    try:
        with zipfile.ZipFile(apk_path) as apk:
            for name in apk.namelist():
                if not re.match(r"^classes\d*\.dex$", name):
                    continue
                data = apk.read(name)
                found.update(c for c, needle in needles.items() if needle in data)
                del data
                if len(found) == len(needles):
                    break
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError):
        return set()
    _APK_CLASS_CACHE.clear()
    _APK_CLASS_CACHE[key] = found
    return found


def _jar_dex_files(jar_path):
    """[(entry name, bytes)] of the classesN.dex files of a compat jar, in order."""
    with zipfile.ZipFile(jar_path) as jar:
        names = sorted((n for n in jar.namelist() if re.match(r"^classes\d*\.dex$", n)),
                       key=lambda n: int(re.sub(r"\D", "", n) or 1))
        return [(n, jar.read(n)) for n in names]


def _embed_one_compat_set(apk_path, cset):
    """Adds the dex files of ONE compat jar to the APK when this ATL lacks its class and the app uses it."""
    if not os.path.isfile(cset["jar"]):
        return []
    # only the classes that THIS ATL lacks (False; None = cannot tell, then nothing is changed)
    lacking = [c for c in cset["classes"] if _atl_has_class(c) is False]
    if not lacking:
        return []
    used = sorted(_apk_uses_classes(apk_path, lacking))
    if not used:
        return []
    tmp = apk_path + ".part"
    try:
        dex_files = _jar_dex_files(cset["jar"])
        if not dex_files:
            return []
        with zipfile.ZipFile(apk_path) as apk:
            names = set(apk.namelist())
            if cset["marker"] in names:
                return ["ATL compat: %s already added to this APK (this ATL lacks it)" % ", ".join(used)]
            n = 1
            while ("classes.dex" if n == 1 else "classes%d.dex" % n) in names:
                n += 1
            if n == 1:
                return ["ATL compat: this APK has no classes.dex, nothing added"]
            new_names = ["classes%d.dex" % (n + i) for i in range(len(dex_files))]  # contiguous: ART stops at the first gap
            with zipfile.ZipFile(tmp, "w") as dst:
                for info in apk.infolist():
                    dst.writestr(_copy_info(info), apk.read(info.filename))
                for new_name, (_entry, dex) in zip(new_names, dex_files):
                    info = zipfile.ZipInfo(new_name, (2020, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_STORED
                    dst.writestr(info, dex)
                dst.writestr(zipfile.ZipInfo(cset["marker"], (2020, 1, 1, 0, 0, 0)),
                             ("DroidStore added %s (%s) because this ATL lacks it.\n"
                              % (", ".join(new_names), ", ".join(used))).encode())
        os.replace(tmp, apk_path)
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError) as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return ["ATL compat: could not add the classes to the APK (%s)" % exc]
    return ["ATL compat: this ATL lacks %s, which the app uses: added them to the APK as %s (the real fix is an ATL built "
            "from ./atl, see README)" % (", ".join(used), ", ".join(new_names))]


def _embed_compat_dex(apk_path):
    """Adds the compat classes to the APK when this ATL lacks them and the app uses them. Lines for the launch log."""
    notes = []
    for cset in ATL_COMPAT_SETS:
        notes += _embed_one_compat_set(apk_path, cset)
    return notes


def _dex_android_types(data):
    """(referenced, defined): the android.* classes (as 'android/x/Y') that one dex file refers to and that it defines.
    Reads only the type table and the class list. Both empty if it is not a dex file."""
    try:
        if len(data) < 0x70 or data[:4] != b"dex\n":
            return set(), set()
        u32 = lambda off: struct.unpack_from("<I", data, off)[0]  # noqa: E731
        str_off, n_type, type_off, n_cls, cls_off = u32(0x3C), u32(0x40), u32(0x44), u32(0x60), u32(0x64)
        strs = struct.unpack_from("<%dI" % n_type, data, type_off)

        def descriptor(type_idx):
            off = u32(str_off + 4 * strs[type_idx])
            while data[off] & 0x80:  # skip the uleb128 length
                off += 1
            off += 1
            return data[off:data.index(b"\0", off)]

        def android(raw):
            raw = raw.lstrip(b"[")  # an array of android classes refers to the class too
            return raw[1:-1].decode("ascii", "replace") if raw.startswith(b"Landroid/") and raw.endswith(b";") else None

        refs = {n for n in (android(descriptor(i)) for i in range(n_type)) if n}
        defined = set()
        for i in range(n_cls):
            n = android(descriptor(u32(cls_off + 32 * i)))
            if n:
                defined.add(n)
        return refs, defined
    except (struct.error, ValueError, IndexError):
        return set(), set()


_ATL_SCAN_CACHE = {}


def _atl_missing_scan(apk_path, limit=100):
    """One log line: the android.* classes the APK refers to that this ATL's api-impl.jar does not define (and the APK does
    not bring itself). Most are never used at run time; the first one that is used ends the app with a
    NoClassDefFoundError, so this is the list of candidates for the next crash. [] if it cannot be told."""
    try:
        jar = _api_impl_jar()
        if not jar:
            return []
        st, jst = os.stat(apk_path), os.stat(jar)
        key = (apk_path, st.st_mtime, st.st_size, jar, jst.st_mtime, jst.st_size)
        if key in _ATL_SCAN_CACHE:
            return _ATL_SCAN_CACHE[key]
        atl_defined = set()
        with zipfile.ZipFile(jar) as z:
            for n in z.namelist():
                if n.startswith("android/") and n.endswith(".class"):
                    atl_defined.add(n[:-len(".class")])
                elif n.endswith(".dex"):
                    atl_defined |= _dex_android_types(z.read(n))[1]
        refs, apk_defined = set(), set()
        with zipfile.ZipFile(apk_path) as apk:
            for n in apk.namelist():
                if re.match(r"^classes\d*\.dex$", n):
                    r, d = _dex_android_types(apk.read(n))
                    refs |= r
                    apk_defined |= d
        if not atl_defined:  # no usable class list in that jar: do not claim anything
            return []
        missing = sorted(refs - atl_defined - apk_defined)
        if missing:
            shown = ", ".join(m.replace("/", ".") for m in missing[:limit])
            more = " ... and %d more" % (len(missing) - limit) if len(missing) > limit else ""
            lines = ["ATL scan: the app refers to %d android.* classes that this ATL's api-impl.jar does not have (most are "
                     "never used; the first one that is used ends the app): %s%s" % (len(missing), shown, more)]
        else:
            lines = ["ATL scan: every android.* class the app refers to is in this ATL's api-impl.jar (or in the app)"]
        _ATL_SCAN_CACHE.clear()
        _ATL_SCAN_CACHE[key] = lines
        return lines
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError, ValueError):
        return []


def _atl_build_diag():
    """Lines for the top of the launch log: is the installed ATL new enough for the classes this project added?"""
    out = []
    for cls in ATL_ADDED_CLASSES:
        has = _atl_has_class(cls)
        if has is None:
            continue
        out.append("ATL build: %s %s in api-impl.jar%s" % (
            cls, "present" if has else "MISSING",
            "" if has else " (this ATL was built before the class was added: apps that need it will crash; "
                           "install a click built from this source, see README)"))
    return out


def _collapse_thread_dumps(lines):
    """ART prints a dump of every thread when an app aborts: hundreds of native frames that push the real error
    out of view. Keeps only the Java frames of the threads that were running (Runnable/Native), drops the rest.
    Returns (lines, number of threads, number of lines removed)."""
    out = []
    threads = removed = 0
    in_thread = keep = False
    for line in lines:
        m = _THREAD_HEADER_RE.match(line)
        if m:
            if threads == 0:
                out.append(None)  # placeholder for the note, filled below
            threads += 1
            in_thread = True
            keep = m.group(1) in ("Runnable", "Native")
            if keep:
                out.append(line)
            else:
                removed += 1
            continue
        if in_thread and line.startswith(_DUMP_DETAIL):
            if keep and line.startswith(("  at ", "  - ")):
                out.append(line)
            else:
                removed += 1
            continue
        in_thread = False
        out.append(line)
    if threads:
        i = out.index(None)
        out[i] = "(thread dump collapsed: %d threads, %d lines hidden; only Runnable/Native threads are kept)" % (
            threads, removed)
    return out, threads, removed


_LOG_PREFIX_RE = re.compile(r"^\S+ [VDIWEF] \d\d-\d\d [\d:.]+ \d+ \d+ [^\]]*\] ?")
_TRACEBACK_END_RE = re.compile(r"^\s*(?:\*\*\*|Fatal signal|\(Throwable|\[droidstore\])")


def _python_traceback_tail(lines, start, limit=30):
    """The lines of a Python traceback that follow lines[start] (ART splits it over many log lines, each with the
    logcat prefix). Stops where the abort report begins. The last line is the Python error itself."""
    out = []
    for l in lines[start + 1:start + 1 + limit * 3]:
        plain = _LOG_PREFIX_RE.sub("", l).rstrip()
        if _TRACEBACK_END_RE.match(plain):
            break
        if plain and (not out or out[-1] != plain):
            out.append(plain)
        if len(out) >= limit:
            break
    return out


def _analyze_log(raw):
    """Finds the cause of a crash in the whole log. Returns None if the log has nothing, else a dict with
    kind, line (the cause), context (the lines after it), advice and exit_status (System.exit status or None)."""
    lines, _, _ = _collapse_thread_dumps(raw)
    lines = [l for l in lines if not any(n in l for n in LOG_NOISE)]
    exit_status = None
    for l in lines:
        m = _EXIT_RE.search(l)
        if m:
            exit_status = int(m.group(1))
            break
    # a caught exception early in the log is not the cause: if the app says which one was fatal, start there.
    # The marker line itself ("FATAL EXCEPTION: main") only says "something crashed", the line after it says what.
    fatal = next((i for i, l in enumerate(lines) if _FATAL_RE.search(l)), None)
    # The app ended normally (exit code 0, closed by the user or the compositor, no fatal exception, no System.exit
    # with an error): whatever exception it logged on the way was caught by the app (androidx probes classes by
    # reflection and logs "E/WindowInsetsCompat ... ClassNotFoundException: android.view.View$AttachInfo" and goes on).
    # Showing one of those as "Cause" made a normal close look like a crash.
    ended = next((int(m.group(1)) for l in reversed(raw) for m in [_ENDED_RE.match(l)] if m), None)
    if ended == 0 and fatal is None and exit_status in (None, 0):
        return None
    starts = [0] if fatal is None else [fatal + 1, fatal]
    for start in starts:
        # First only the lines that are not warnings/info: an exception that the app caught and logged as a warning
        # (for example SQLite's lock file in a folder ATL could not write to) is not what ended it. If nothing else
        # explains the crash, the warnings are looked at too.
        for skip_benign in (True, False):
            for i in range(start, len(lines)):
                l = lines[i]
                if l.startswith("[droidstore]") or l.strip().startswith("at ") or _is_suppressed_line(l):
                    continue
                if skip_benign and _BENIGN_LEVEL_RE.match(l):
                    continue
                for kind, regex, advice in _CAUSES:
                    if regex.search(l):
                        if kind == "exception":
                            # a generic exception: the root cause is the LAST "Caused by:" of the same trace, and if
                            # that one is more specific (a missing class, a native library...) it is the real reason
                            j = i
                            while j + 1 < len(lines) and _is_trace_line(lines[j + 1]):
                                j += 1
                                if lines[j].strip().startswith("Caused by:"):
                                    for k2, r2, a2 in _CAUSES:
                                        if k2 != "exception" and r2.search(lines[j]):
                                            i, l, kind, advice = j, lines[j], k2, a2
                                            break
                        ctx = [c for c in lines[i + 1:i + 7] if c.strip() and not c.startswith("[droidstore]")]
                        if kind in ("api", "exception"):
                            ctx = [c for c in ctx if _is_trace_line(c)]
                        if kind == "chaquopy":
                            ctx = _python_traceback_tail(lines, i)
                            if _NONE_DIR_RE.search(l) or any(_NONE_DIR_RE.search(c) for c in ctx):
                                advice = _NONE_DIR_ADVICE
                        return {"kind": kind, "line": l.strip(), "context": ctx, "advice": advice,
                                "exit_status": exit_status,
                                "frame": _app_frame(lines, i) if kind in ("api", "exception") else None}
    if exit_status is not None:
        return {"kind": "exit", "line": "System.exit(%d) called by the app" % exit_status, "context": [],
                "advice": "No exception appears in the log: the app closed itself. Apps usually do this when a startup "
                          "check fails (Play Services, integrity or root check, native initialisation). Look at "
                          "the lines just before the thread dump.",
                "exit_status": exit_status}
    return None


def _cause_text(raw, width=300):
    cause = _analyze_log(raw)
    if not cause:
        return ""
    out = ["Cause (%s): %s" % (cause["kind"], cause["line"][:width])]
    out += ["    " + c.strip()[:width] for c in cause["context"][:(30 if cause["kind"] == "chaquopy" else 4)]]
    out.append("What it means: " + cause["advice"])
    if cause.get("frame"):
        out.append("Where: " + cause["frame"][:width])
    if cause["kind"] == "api":
        missing_class = _missing_android_class([cause["line"]] + cause["context"])
        if missing_class:
            has = _atl_has_class(missing_class)
            if missing_class in ATL_ADDED_CLASSES and has is False:
                out.append("Missing Android class: %s. This project's ATL source has it, but the ATL inside THIS "
                           "package was built before it was added (its api-impl.jar does not contain it). Install a "
                           "click that was built after that change: the CI build of main, or ./tools/build_all.sh. "
                           "A plain `clickable build` reuses the old usr/ folder." % missing_class)
            elif has:
                out.append("Missing Android class: %s. It IS in this ATL's api-impl.jar, so the app failed to load it "
                           "for another reason: send the whole log." % missing_class)
            else:
                out.append("Missing Android class: %s. ATL has no implementation of it: add atl/src/api-impl/%s.java, "
                           "list it in atl/src/api-impl/meson.build and rebuild ATL (./tools/build_atl.sh; the CI does "
                           "it when atl/ changes)." % (missing_class, missing_class.split("$")[0].replace(".", "/")))
    for l in raw:
        if l.startswith(("[droidstore] Chaquopy compat:", "[droidstore] Chaquopy cache:")):
            out.append(l[len("[droidstore] "):])
    missing = []
    for l in raw:
        for m in _MISSING_LIB_RE.finditer(l):
            if m.group(1) not in missing:
                missing.append(m.group(1))
    if missing:
        out.append("Libraries the bionic shim could not find: " + ", ".join(missing[:8]))
    for l in raw:
        if _SOUND_RE.search(l):
            out.append("Sound: ATL could not open the sound device (%s). See the 'sound/...' lines at the top of the "
                       "log." % l.strip()[:200])
            break
    if cause["exit_status"] is not None and cause["kind"] != "exit":
        out.append("The app then ended with System.exit(%d)." % cause["exit_status"])
    return "\n".join(out)


def _read_log_lines(pkg_id):
    if not PKG_RE.match(pkg_id or ""):
        return []
    try:
        with open(_launch_log_path(pkg_id), "r", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


def get_launch_summary(pkg_id):
    """Short explanation of why the last launch of this app ended, searched in the WHOLE log (empty if unknown)."""
    return _cause_text(_read_log_lines(pkg_id))


def get_launch_log(pkg_id, lines=200, max_width=300, newest_first=False, hide_noise=True):
    """Last lines of the log of the most recent launch of this app.

    Harmless ART noise (hundreds of jit_logger lines) and thread dumps are dropped first so they cannot push the
    real error out of view, lines longer than max_width are cut (except the ones that explain an error), and with
    newest_first the end of the log comes first."""
    raw = _read_log_lines(pkg_id)
    if not raw:
        return ""
    if hide_noise:
        raw = _collapse_thread_dumps(raw)[0]
    hidden = 0
    kept = []
    for line in raw:
        if hide_noise and any(n in line for n in LOG_NOISE):
            hidden += 1
            continue
        if max_width and len(line) > max_width and not line.startswith("[droidstore]"):
            limit = max(max_width, 2000) if _IMPORTANT_RE.search(line) else max_width
            if len(line) > limit:
                line = line[:limit] + " \u2026[+%d chars]" % (len(line) - limit)
        kept.append(line)
    kept = kept[-max(1, min(int(lines), 2000)):]
    if newest_first:
        kept.reverse()
    if hidden:
        note = "(%d harmless ART/system lines hidden)" % hidden
        kept = ([note] if not newest_first else []) + kept + ([note] if newest_first else [])
    return "\n".join(kept)


def _watch_app(proc, pkg_id, log_path, started, apk_path=""):
    """Waits for the app to end and tells the UI how, even if that happens long after the launch."""
    code = proc.wait()
    runtime = time.time() - started
    try:
        with open(log_path, "a") as log:
            log.write("\n[droidstore] ended after %.1fs: %s\n" % (runtime, _describe_exit(code)))
    except OSError:
        pass
    description = _describe_exit(code)
    cause = None
    if code:
        try:
            with open(log_path, "r", errors="replace") as f:
                raw_log = f.read().splitlines()
            cause = _analyze_log(raw_log)
            if cause:
                with open(log_path, "a") as log:
                    log.write("[droidstore] cause (%s): %s\n[droidstore] %s\n" % (
                        cause["kind"], cause["line"][:300], cause["advice"]))
                description += ". Cause: " + cause["line"][:120]
        except OSError:
            pass
    hint = _abi_hint(apk_path) if code else ""
    if hint:
        description += ". " + hint
        try:
            with open(log_path, "a") as log:
                log.write("[droidstore] %s\n" % hint)
        except OSError:
            pass
    pyotherside.send("app_exited", pkg_id, code, round(runtime, 1), description, _tail(log_path, 8))


def _retry_worth_it(log_path):
    """True if a start that ended at once looks like a passing problem, not a missing library or a wrong architecture."""
    try:
        text = _tail(log_path, 40).lower()
    except OSError:
        return True
    for marker in ("cannot open shared object", "error while loading shared libraries", "not found",
                   "exec format error", "no such file"):
        if marker in text:
            return False
    return True


def launch(pkg_id):
    entry = _read_json(LIBRARY_FILE, {}).get(pkg_id)
    if not entry or not os.path.exists(entry["apk"]):
        return {"ok": False, "error": "The APK was not found, download it again"}
    settings = get_settings()
    try:
        cmd = shlex.split(settings["atl_bin"]) + shlex.split(settings["extra_args"]) + [entry["apk"]]
    except ValueError as exc:
        return {"ok": False, "error": "Invalid ATL command or arguments: %s" % exc}
    compat_notes = _embed_compat_dex(entry["apk"])

    if cmd[0] == ATL_NAME and not shutil.which(ATL_NAME):
        return {"ok": False, "needs_sdk": True, "error": "ATL is not installed"}

    problem = _ensure_libs(cmd)
    if problem:
        return {"ok": False, "error": problem}

    env = _launch_env(cmd)
    extra_env = _parse_extra_env(settings["extra_env"])
    env.update(extra_env)
    cwd = _launch_cwd()

    # Already running? A second ATL for the same APK would fight the first one over its data folder
    # (sqlite locks, Chaquopy cache) and one of the two then dies, which looks like "the app sometimes does not open".
    running = _PROCS.get(pkg_id)
    if running is not None and running.poll() is None:
        return {"ok": True, "error": "", "already_running": True}

    log_path = _launch_log_path(pkg_id)
    started = time.time()
    proc = None
    for attempt in (1, 2):
        try:
            with open(log_path, "w" if attempt == 1 else "a") as log:
                if attempt == 1:
                    log.write("[droidstore] %s\n[droidstore] extra env: %s\n[droidstore] LD_LIBRARY_PATH=%s\n" % (
                        " ".join(shlex.quote(c) for c in cmd), extra_env or "-", env.get("LD_LIBRARY_PATH", "")))
                    log.write("[droidstore] working folder: %s\n" % (cwd or "(unchanged: %s)" % os.getcwd()))
                    if _uses_chaquopy(entry["apk"]):
                        log.write("[droidstore] this app embeds Python (Chaquopy)\n[droidstore] %s\n" % _patch_chaquopy_apk(entry["apk"]))
                        for note in _clear_stale_chaquopy_cache(entry["apk"]):
                            log.write("[droidstore] Chaquopy cache: %s\n" % note)
                    for note in _prepare_raw_audio(entry["apk"]):
                        log.write("[droidstore] %s\n" % note)
                    for diag in _apk_abi_summary(entry["apk"]) + _lib_diag(env) + _audio_diag(env) + _keyboard_diag(env, cmd) + _atl_build_diag() + compat_notes + _atl_missing_scan(entry["apk"]):
                        log.write("[droidstore] %s\n" % diag)
                else:
                    log.write("\n[droidstore] the app ended right after starting, trying once more\n")
                log.write("\n")
                log.flush()
                proc = subprocess.Popen(
                    cmd,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    env=env,
                    cwd=cwd,
                )
        except FileNotFoundError:
            return {"ok": False, "error": "ATL not found (%s). Change it in Settings." % cmd[0]}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

        # Wait for the start-up window. Poll instead of one fixed sleep, so a quick failure is noticed at once.
        deadline = time.time() + 1.6
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        if proc.poll() is None:
            break  # still running: it opened
        if attempt == 1 and proc.returncode != 0 and _retry_worth_it(log_path):
            time.sleep(1.0)  # start-up races (display, GL, a lock of the previous run) often clear by themselves
            continue
        break

    if proc.poll() is not None and proc.returncode != 0:  # ended with an error inside the start-up window
        tail = _tail(log_path, 6)
        if EXTRA_LIB_DIR in tail:
            shutil.rmtree(EXTRA_LIB_DIR, ignore_errors=True)  # probably an incompatible library: it will be downloaded again on the next attempt
        hint = ""
        if BUNDLED:
            missing = _missing_libs_py(cmd[0])
            if missing:
                hint = "\n\nMissing libraries: %s\nBundle them: python3 tools/fetch_libs.py %s" % (
                    ", ".join(missing), " ".join(missing))
        abi_hint = _abi_hint(entry["apk"])
        if abi_hint:
            hint = "\n\n" + abi_hint + hint
        cause = _cause_text(_read_log_lines(pkg_id))
        if cause:
            hint = "\n\n" + cause + hint
        return {"ok": False, "error": "ATL exited (%s)\n%s%s" % (_describe_exit(proc.returncode), tail, hint)}

    # Still running (or finished cleanly): keep watching, so a crash later on is not lost
    _PROCS[pkg_id] = proc
    threading.Thread(target=_watch_app, args=(proc, pkg_id, log_path, started, entry["apk"]), daemon=True).start()
    return {"ok": True, "error": ""}
