# DroidStore

An Android app store for Ubuntu Touch. It downloads APKs (from F-Droid or from your own catalog),
verifies their sha256 and opens them through the Android Translation Layer (ATL).

## One project: store + ATL

This repository contains both the store and the Android Translation Layer itself:

    atl/                     ATL source (from upstream android_translation_layer, GPL-3.0) - patch it here
    tools/build_atl.sh       builds atl/ + its dependencies in an arm64 Ubuntu 24.04 container -> usr/
    tools/build_atl_inner.sh the steps that run inside the container (wolfSSL, bionic_translation, art_standalone, ATL)
    tools/Dockerfile.atl     the build environment

`usr/` is where the store already looks first (bundled mode), so a build from source needs no download at runtime:

    ./tools/build_all.sh            # = build_atl.sh + clickable build; needs Docker, first build is slow (qemu on x86)

The result is self-contained: `build_atl.sh` also bundles every shared library ATL needs that the phone lacks
(`tools/bundle_closure.py` -> `usr/lib/extra`), and the store never downloads anything when `usr/` is in the package.
To keep the package small, list once the libraries the phone already has (one-time, on the phone) and rebuild:

    ldconfig -p | awk '/=>/ {print $1}' | sort -u > tools/device-libs.txt

Without that file everything except the C runtime and the graphics stack (EGL/GL/Wayland/Vulkan, which must come
from the phone) is bundled. Not covered: files that GTK/WebKit load at runtime (pixbuf loaders, GSettings schemas,
GStreamer plugins) are not found by ldd; if an app misses them, the log will say so.

`tools/fetch_sdk.sh` (prebuilt SDK from atl-touch) still works as a shortcut when you don't want to compile ATL.
The dependency versions are taken from upstream branches; pin them with WOLFSSL_REF, BIONIC_REF, ART_REF.
The exact versions used end up in `usr/share/atl-build-info.txt`.


Licenses: the store is AGPL-3.0 (`LICENSE`), ATL is GPL-3.0 (`atl/LICENSE.txt`).

## What it does

- Search an app catalog (F-Droid index-v2), filtered to the device architecture
- "Only without native code" filter (pure Java/Kotlin, more likely to work in ATL)
- App page (tap an app): screenshots, description, what's new, categories, license, permissions, warnings, links
- Download with progress and sha256 verification
- Library of installed apps: Open / Delete
- Add your own APK (+ button at the top): tap "Choose from an app" and the system asks which app
  (File Manager etc.) to pick the APK from; or pick it from the Downloads/Documents list, or type its path.
  The package and version are read from the manifest, the APK is copied to the library and opened with ATL
  just like catalog apps. It warns you if the APK has native code that doesn't fit the device.
- ✔ marker for apps confirmed to work (`compat.json` file)
- Settings: ATL command, repository, optional URL of a prebuilt `catalog.json`

## Layout

    manifest.json, droidstore.apparmor, droidstore.desktop   click packaging
    qml/Main.qml, qml/AppDetailsPage.qml, qml/SettingsPage.qml, qml/ImportPage.qml, qml/PickerPage.qml  UI (Lomiri QML)
    src/store.py                                         backend (pyotherside)
    src/catalog.py                                       converts the F-Droid index into a small catalog
    tools/build_catalog.py                               builds catalog.json on a PC/server
    tests/test_backend.py                                offline tests of the backend
    compat.json                                          list of confirmed apps

## What has been tested and what hasn't

- The backend (catalog, download, sha256, library, launch, APK import) is tested offline: `python3 tests/test_backend.py`
- The QML and the click package have been tested on a device.
- Unknowns that depend on atl-touch (see the repo's `ci/README.md`):
  - the `LD_LIBRARY_PATH` the store builds is a guess; I haven't seen the atl-touch launcher
  - the SDK is built for Ubuntu Touch 24.04-2.x
  - whether launching an APK from an external process opens a proper window in Lomiri

## Limitations

- OpenStore build (default): the app is **confined** (AppArmor policy groups in `droidstore.apparmor`) and ships ATL inside the click,
  so nothing executable is downloaded at runtime. It has been tested on a device, and ATL runs under confinement; if you hit
  a problem on your setup, look for `apparmor="DENIED"` lines in the syslog and add the matching policy group.
- Sideload build: point `hooks.droidstore.apparmor` in `manifest.json` at `droidstore-unconfined.apparmor`. This one can download the SDK
  and libraries at runtime (see below) but OpenStore rejects `unconfined`.
- The whole F-Droid index is big and parsing it needs memory. For production use, build a small catalog
  on a PC/server and put its URL in Settings:
  `python3 tools/build_catalog.py --abi arm64-v8a -o catalog.json`
- The F-Droid index signature is not verified (only each APK's sha256 against the index).
- `name` in `manifest.json` (`droidstore.renz21`) and `applicationName` in `qml/Main.qml`
  must be changed and must match.

## Build the OpenStore (confined) version

    ./tools/fetch_sdk.sh                         # puts the arm64 ATL SDK in usr/ (needs curl, zstd, tar)
    pip install zstandard
    python3 tools/fetch_libs.py libmaliit-glib.so.3 ...   # bundle missing system libraries into usr/lib/extra (see the script's help)
    clickable build --skip-review
    # upload the .click from build/ to OpenStore

`fetch_sdk.sh` removes `libatl_webview_qt.so` (Qt 5.15, rejected by the OpenStore review for the ubuntu-sdk-20.04 framework),
so Android WebView does not work in the OpenStore build. The build uses `builder: precompiled` with `arch: arm64` so the click is arm64, not `all`.

The package name in `manifest.json` (`name`) must match the App Name registered on OpenStore, and the writable data folders
(`~/.local/share/<name>/`) follow it automatically.

## Build and install the sideload (unconfined) version (needs a computer)

You don't need to download ATL manually. The app downloads it itself (the atl-touch SDK,
about 50 MB) when you tap "Install ATL", or when you tap "Open" on an app and it is missing.
It verifies the sha256, unpacks it and keeps it in `~/.local/share/droidstore/atl-sdk/usr`.
Internet is only needed the first time.

    pip install clickable-ut                   # also needs Docker
    clickable build --skip-review              # the review rejects unconfined, this is expected
    clickable install                          # phone with USB and Developer Mode

- zstd decompression uses the decoder shipped inside the click (`vendor/zstandard`, arm64).
  If it fails to load, the system `zstd` program is tried.
- The phone lacks some libraries ATL needs (e.g. `libmaliit-glib`, `libportal`, `libswscale`).
  Before opening an app the store runs `ldd` and downloads whatever is missing from Ubuntu noble
  (arm64 .deb, in rounds since each library may need more) into `~/.local/share/droidstore/extra-lib`.
  The first time it downloads the Ubuntu index (about 20 MB) to pick the right version.
- Different SDK version: change `SDK_TAG` in `src/store.py` (tags on the NotKit/atl-touch releases page).
- Optional: if you put a ready SDK into the project's `usr/` (`./tools/fetch_sdk.sh`), it gets packaged
  inside the click and used without downloading.
- In Settings the ATL command is automatic. `LD_LIBRARY_PATH` is built from the `usr/lib` folders that contain .so files.
- `builder: pure` in `clickable.yaml` requires a fairly recent Clickable.
- `manifest.json` declares `architecture: all`, but the SDK and the decoder are arm64, so it is meant for arm64 devices.
- Tests: `python3 tests/test_backend.py`, `python3 tests/test_sdk.py`, `python3 tests/test_libs.py`, `python3 tests/test_details.py`, `python3 tests/test_import.py`, `python3 tests/test_bundled.py`[README.md](https://github.com/user-attachments/files/33613262/README.md)

