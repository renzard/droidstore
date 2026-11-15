# DroidStore

An Android app store for Ubuntu Touch. It downloads APKs (from F-Droid or from your own catalog),
verifies their sha256 and opens them through the Android Translation Layer (ATL).

**DroidStore has been used on a Fairphone 4 running Ubuntu Touch.** That is the only device it has been tried on so far
(see "What has been tested and what hasn't" below).

## Changes since 0.2.0

**New look**
- Redesigned interface in the style of the new F-Droid app, with dark and light themes that follow the system.
- Bottom bar with Discover, Search and My apps.
- Rows of new, recently updated and ATL-verified apps.
- Expandable app details (anti-features, links, permissions).
- Category chips and search history.

**Apps in the app drawer**
- Every installed app gets its own icon in the app drawer; tapping it opens the app directly, without opening the store.
- Can be turned off in Settings, which also has an "Update app drawer now" button.

**Sound**
- Sound works in apps that play audio (games, music and video players, apps using MediaPlayer or SoundPool) when the click
  is built with the bundled ATL.

**Compatibility fixes**
- Apps with embedded Python (e.g. Codify) no longer crash on start.
- Fixed "Permission denied: 'None'" when such an app starts.
- The on-screen keyboard opens in apps with plain text fields.
- DuckDuckGo (work in progress): fixes for the start-up crashes caused by Android classes that ATL does not have
  (`TaskStackBuilder`, `VpnService`, `AutofillService`). Its VPN-based App Tracking Protection and autofill are not
  available. DuckDuckGo may need more classes of this kind; see "Android classes that ATL does not have".

**Log and troubleshooting**
- The log page has a "Cause" box that explains why an app stopped and names the missing Android class, if there is one.
- New `ATL scan:` line at the top of the log, listing the Android classes an app refers to that ATL does not have.
- "Copy all" and "Copy selected" buttons to share the log easily.

## UI

The UI follows the new F-Droid look (Material 3 style, dark or light with the system theme): a bottom bar with
**Discover / Search / My apps**, horizontal rows of new / recently updated / ATL-verified apps, expandable app
details (anti-features, links, permissions), category chips and search history.
Shared pieces live in `qml/` (`Pal.qml` = colours + animation timings, `Ripple.qml` = touch feedback, `Reveal.qml` =
staggered fade-in, `AppRow.qml`, `AppCard.qml`, `ActionButton.qml`...). Change a colour or a duration once in `Pal.qml`.
The backend side is `store.discover()`, `store.categories()`, `store.search(..., category, sort)` and the search
history functions (`tests/test_discover.py`).

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
from the phone) is bundled. Not covered: files that GTK/WebKit load at runtime (pixbuf loaders, GSettings schemas) are not found by
ldd; if an app misses them, the log will say so. The files that sound needs are copied explicitly, see "Sound" below.

`tools/fetch_sdk.sh` (prebuilt SDK from atl-touch) still works as a shortcut when you don't want to compile ATL.
The dependency versions are set in `tools/versions.env` (WOLFSSL_REF, BIONIC_REF, ART_REF, OPENSLES_REF; an environment
variable wins over the file). Only wolfSSL is pinned by default, the rest follow upstream `master`, so builds can differ over time.
After a build that works, copy the commits listed in `usr/share/atl-build-info.txt` into `tools/versions.env` to freeze them.

CI (`.github/workflows/build.yml`): the tests run on every push and pull request; the ATL + click build runs on `main`,
on tags and by hand. `usr/` is cached while `atl/`, `tools/` scripts and `versions.env` are unchanged, so a UI-only change
does not recompile ATL (about an hour). Pushing a tag such as `v0.2.0` publishes the click as a GitHub Release.


Licenses: the store is AGPL-3.0 (`LICENSE`), ATL is GPL-3.0 (`atl/LICENSE.txt`).

## Sound

ATL plays sound in two ways, and both load their parts with `dlopen`, which `ldd` (and so `bundle_closure.py`) cannot see.
Before this was handled, a click built from source had no sound in any app:

| Android API | What ATL uses | What it needs at runtime |
| --- | --- | --- |
| `AudioTrack`, `AudioRecord`, OpenSL ES (games, players) | ALSA device `default` (`atl/src/api-impl-jni/audio/audio.h`) | an ALSA config + the ALSA **pulse plugin**, because Ubuntu Touch's sound server is PulseAudio. A bundled `libasound` only knows the phone's compiled-in paths. |
| `MediaPlayer`, `SoundPool` | GTK `GtkMediaFile` | GTK's media backend `libmedia-gstreamer.so` and GStreamer plugins |

What the build now does (`tools/build_atl_inner.sh`, before `bundle_closure.py`, so their libraries are bundled too):

    usr/lib/alsa-lib/                    libasound_module_pcm_pulse.so, libasound_module_ctl_pulse.so
    usr/lib/gtk-4.0/4.0.0/media/         libmedia-gstreamer.so
    usr/lib/gstreamer-1.0/               about 25 plugins: playback, audio convert/resample, PulseAudio output,
                                         MP3 (mpg123), AAC/M4A (faad), Ogg/Vorbis/Opus, FLAC, WAV, MP4, Matroska, ...

What the store does when it starts a bundled ATL (`store._audio_env`): it writes `<data>/atl-xdg/asound.conf`, which sends ALSA's
`default` PCM and mixer to PulseAudio through the bundled plugin, and sets `ALSA_CONFIG_PATH`, `GTK_PATH`,
`GST_PLUGIN_SYSTEM_PATH_1_0` and `GST_REGISTRY_FORK=no`. An SDK without these folders (e.g. the prebuilt atl-touch one) is started as before.

`SoundPool: GStreamer error: Resource not found.` (GTK only says this) means GStreamer could not open the sound file or find a plugin for it.
The store now starts a bundled ATL with `GST_DEBUG=2`, so GStreamer's own error/warning line, which names the file or the element, follows it in the
log (a `GST_DEBUG` in Settings -> extra_env wins). This applies to every ATL under `usr/`, also the downloaded atl-touch SDK that has no GStreamer plugins of its own.

**Sounds from an app's own resources** (`MediaPlayer.create(context, R.raw.x)`, `SoundPool.load`): the ATL of the downloaded atl-touch SDK asks
GStreamer for `<app data>/<resource name>` (for example `.../gr.learn.programming_local.apk_/sound_click`) and does not always extract it
from the APK first, so the log shows GStreamer's `No such file` and the app gets `MediaPlayer: prepare failed`. The sound system itself was fine.
Before an app starts, the store now copies the audio files of the APK's `res/raw` folder to that place (`_prepare_raw_audio`, the APK is not
changed) and writes `audio resources: ...` lines at the top of the log. A resource whose name the app's build shortened (`res/xy/ab.ogg`)
cannot be mapped back and is only listed. Test: `python3 tests/test_rawaudio.py`.

If an app is silent, open its log: the first lines say what ATL got, for example

    [droidstore] sound/ALSA (AudioTrack, OpenSL ES): default -> PulseAudio plugin, config .../atl-xdg/asound.conf
    [droidstore] sound/PulseAudio: server socket found (/run/user/32011/pulse/native)
    [droidstore] sound/GTK media backend (MediaPlayer, SoundPool): .../usr/lib/gtk-4.0/4.0.0/media/libmedia-gstreamer.so
    [droidstore] sound/GStreamer: 25 plugins in .../usr/lib/gstreamer-1.0, PulseAudio output plugin: yes

A line that says `NOT configured` / `NOT found` means the `usr/` in the package was built without that part (rebuild ATL, the CI does it
when `tools/` changes). `server socket NOT found` points at PulseAudio itself or at the `audio` policy group of `droidstore.apparmor`
(look for `apparmor="DENIED"` in the syslog). If an app crashes and its log has ALSA/PulseAudio errors, the crash summary adds a `Sound:` note.
Not included on purpose: video codecs, and `gstreamer1.0-libav` (many more audio formats, but tens of MB of dependencies).
Test: `python3 tests/test_audio.py` (it also checks the ALSA routing against the real `libasound` when gcc is available).

## Apps with embedded Python (Chaquopy), e.g. Codify

ATL's bionic shim cannot `dlopen(NULL)`, which Python's `ctypes` does on import, so these apps crashed at start
(`OSError: Cannot load library:`). Before launching such an app the store changes `ctypes/__init__` inside **its own copy**
of the APK (the app's source is not touched): `dlopen(NULL)` falls back to the global scope. The first lines of the app's
log say `Chaquopy compat: ...` with the result. This is a workaround, other problems of the app under ATL may remain.
`tools/patch_bionic_translation.py` tries the same fix inside ATL's own linker when ATL is built from source.

### "PermissionError: Permission denied: 'None'" when such an app starts

This is **not an Android permission** (Codify's manifest asks for none). ATL's `Context.getCacheDir()` tried
`/tmp/atl_cache/<package>`, which a confined app may not write to, and returned `null`. Chaquopy turned that into the text
`None` and Python tried to create a folder with that name in its current directory, which was the store's read-only package
folder. Two fixes, either one is enough, together they cover everything:

- `atl/src/api-impl/android/content/Context.java`: `getCacheDir()` falls back to `<app data>/cache` when `/tmp` cannot be
  used, and never returns `null` unless every place fails. Needs an ATL rebuild (the CI does it when `atl/` changes).
  A folder counts as usable only if a file can really be created in it: `File.canWrite()` is `access(2)`, which AppArmor does
  not check, so a `/tmp/atl_cache/<package>` folder left behind by an unconfined run looked writable and then every `open()` in it
  failed (DuckDuckGo's log: SQLite's `app.db.lck (Permission denied)`).
- `src/store.py`: ATL is started in a writable folder (`<data>/atl-xdg/cwd`, shown as `working folder:` at the top of the
  log), so a relative path no longer fails. Works with the ATL that is already in the package.

With the first fix the log still shows `Unable to create cache directory >/tmp/atl_cache/...<` when the app is confined:
that is the first attempt failing, the fallback is used right after it. Test: `python3 tests/test_cachedir.py`.

## Android classes that ATL does not have (e.g. DuckDuckGo: `android.app.TaskStackBuilder`)

An app that uses an Android framework class ATL does not implement dies with
`NoClassDefFoundError: Failed resolution of: Landroid/app/X;` (ATL's handler then ends the app with `System.exit(10)`).
The fix is a class under `atl/src/api-impl/android/...` **and** a line in `atl/src/api-impl/meson.build` (a `.java` file that is
not listed there is silently not built). ATL has to be rebuilt afterwards (the CI does it when `atl/` changes).
`android.app.TaskStackBuilder` was added this way: DuckDuckGo needs it at start-up to build its app shortcuts.
Test: `python3 tests/test_taskstack.py` (compiles the class against the real ATL sources and checks what it does; needs a JDK).
Other classes may be missing for other apps: the log's cause box now names the class (see below).

**Still `ClassNotFoundException: android.app.TaskStackBuilder` after this?** Then the ATL inside the installed click is older than
the `atl/` source: the class lives in ATL's `api-impl.jar`, and a UI-only change, a plain `clickable build` (it reuses the old `usr/`
folder) or a click built with `tools/fetch_sdk.sh` does not contain it. The log now says so itself: the top of the log has a line
`ATL build: android.app.TaskStackBuilder present/MISSING in api-impl.jar`, and the cause box explains it. Fix: install a click built
by the CI from `main` (the `droidstore-click` artifact) or run `./tools/build_all.sh`, then open DuckDuckGo again.

### Fallback for an older ATL: `compat/atl-compat.jar`

The real fix above needs an ATL built from `atl/`. Until you have one (a click made with plain `clickable build` or with
`tools/fetch_sdk.sh` runs the downloaded atl-touch SDK, which does not have `android.app.TaskStackBuilder`), the store
works around it by itself: `compat/atl-compat.jar` holds a tiny dex with that class, and `store.launch()` adds it to the
APK as one more `classesN.dex` before ATL starts (the app's own class loader reads every `classesN.dex` of its APK, so it
works with any ATL version). The APK is rewritten the same way as for the Chaquopy and raw-audio fixes, once
(`META-INF/droidstore-compat.txt` marks it); a re-downloaded APK is patched again.

It is done **only** when both are true: this ATL's `api-impl.jar` lacks the class, and the APK's dex files refer to it
(DuckDuckGo does). Any other app, and any ATL built from `atl/`, is left exactly as before. The top of the log says
`ATL compat: ...` when it happened.

Note: giving the jar to ATL as an extra file on its command line did **not** work with the downloaded SDK (the log showed
the jar on the command line and the class still missing), which is why the APK itself is patched.

The jar is written by hand, with no Android SDK: `python3 tools/make_compat_dex.py` (and `--check` to see that the committed
file is current). It is the same API as `atl/src/api-impl/android/app/TaskStackBuilder.java`, simplified: `addParentStack`
does nothing and the intents are not copied or flagged. Tests: `python3 tests/test_compat_dex.py` (strict dex parse + a small
Dalvik interpreter runs the real bytes) and `python3 tests/test_compat_launch.py` (when the APK is patched and when it is not).
This was not run on a real ART here: if DuckDuckGo still shows `TaskStackBuilder` in the log after this, send the log again.

### `android.net.VpnService` (DuckDuckGo: `NoClassDefFoundError: Failed resolution of: Landroid/net/VpnService;`)

The next class DuckDuckGo needs at start-up: its dependency-injection component refers to `VpnService` (its App Tracking
Protection service extends it). ATL cannot give an app a VPN interface, so the class is only a stub that makes the VPN
feature "not start": `prepare()` returns null, `protect()` returns false, and `Builder.establish()` returns null, which is
what Android itself returns when the VPN was not prepared or was revoked. Everything else in the app is unaffected.

- Real fix (needs an ATL rebuild): `atl/src/api-impl/android/net/VpnService.java` (with `VpnService.Builder`), listed in
  `atl/src/api-impl/meson.build`.
- Fallback for the downloaded ATL SDK: `compat/atl-compat-vpn.jar`, two dex files (`VpnService`, `VpnService$Builder`). It has
  its own marker, `META-INF/droidstore-compat-vpn.txt`, so an APK that was already patched for `TaskStackBuilder` by an older
  store is still patched for this one. Same rules as above: only when this ATL lacks the class and the APK uses it.
- `python3 tools/make_compat_dex.py` writes both jars (`--check` checks both). Tests: `python3 tests/test_compat_vpn_dex.py`
  (strict parse with ART's rules, every instruction decoded, the real bytes interpreted) and `tests/test_compat_launch.py`.

### `android.service.autofill.AutofillService` (the next DuckDuckGo start-up crash)

Same kind of stub: `atl/src/api-impl/android/service/autofill/AutofillService.java` (real fix, listed in `meson.build`) and
`compat/atl-compat-autofill.jar` (fallback, marker `META-INF/droidstore-compat-autofill.txt`). ATL has no autofill framework,
so nothing ever calls the service; the abstract `onFillRequest`/`onSaveRequest` are left out because their parameter classes
do not exist in ATL either. Tests: `tests/test_compat_vpn_dex.py` (both jars) and `tests/test_compat_launch.py`.

### Finding the next missing class: the `ATL scan:` line

Missing classes show up one at a time (the app dies at the first one). To not play this game one crash at a time, the top of
the launch log has a line `ATL scan: the app refers to N android.* classes that this ATL's api-impl.jar does not have: ...`: the
`android.*` classes the APK's dex files refer to, minus what ATL's `api-impl.jar` defines and what the app brings itself.
Most of them are never used at run time (it is a list of candidates, not of errors); the one that is used ends the app.
Send the whole log: the candidates that look like services, managers or providers are the ones to add next
(`tools/make_compat_dex.py`: a spec like `AUTOFILL_SPEC`, a jar in `JARS`, an entry in `ATL_COMPAT_SETS` in `src/store.py`, and
the matching `.java` for the real fix). Test: `python3 tests/test_atl_scan.py`.

## Which ATL runs? (read this before changing `atl/`)

The line `keyboard: ATL = ... -> ...` at the top of the log says it. `the downloaded atl-touch SDK (NOT built from ./atl)` means the click
has no `usr/` folder: **nothing in `atl/` reaches the phone** (not `TaskStackBuilder`, not the keyboard patch, not the sound parts: the
`sound/... NOT configured/found` lines are then expected). Only a click that contains `usr/` (CI artifact of `main`, or `./tools/build_all.sh`) runs
the ATL built from `atl/`. Careful: the `atl/` snapshot is older than the SDK in some places (its `MediaPlayer.java` is a 100-line stub,
the SDK's is longer), so switching to the built ATL can lose features unless `atl/` is first brought up to date with upstream.

## What the "Cause" box of the log says

The red box at the top of the log page explains why the last launch ended (`store.get_launch_summary`). It starts at the exception that
ended the app: the line after `FATAL EXCEPTION`, `Exception in thread` or ATL's `hacky_uncaught_exception_handler: Thread: ..., Exception:`.
Without such a marker it takes the first line that looks like an error, but lines logged at warning level or below (`W/...`) only count
when nothing else explains the crash: apps log caught exceptions there (SQLite's `W/SupportSQLiteLock ... app.db.lck`), and one of them was
shown as the cause of a crash that was really a missing class. For a missing `android.*` class the box also names it and the file to add.
An app that ends normally (`[droidstore] ended after ...: code 0`, no fatal exception) gets no Cause box at all: exceptions that the app
caught and logged on the way (androidx logs `E/WindowInsetsCompat ... ClassNotFoundException: android.view.View$AttachInfo` and carries on)
used to be shown as the reason, which made a normal close look like a crash.
A trace that ATL prints without the exception's name (an empty line after `Exception:`) is handled too: kotlinx's `Suppressed: ...
DiagnosticCoroutineContextException` line is never the cause, the last `Caused by:` is, and a `Where:` line names the first frame of the app.
Test: `python3 tests/test_logcause.py`.

## Apps in the app drawer (open them like any Ubuntu Touch app)

Every installed app (from the catalog or your own APK) gets its own entry in the Lomiri app drawer, with its name and icon.
Tapping it starts the app directly, without opening the store. How it works:

- `store.add_shortcut()` writes `~/.local/share/applications/droidstore-app-<package>.desktop` (Lomiri lists these like normal apps).
- The entry runs `src/launcher.py <package>`, which starts ATL with the same environment as the store's "Open" button
  and stays alive until the app ends. Problems are written to the usual log (`launch_<package>.log`) and shown as a notification.
- The icon comes from the catalog (downloaded once) or, for your own APKs, from inside the APK; otherwise the store logo.
- Entries are created on install/import, removed on Delete, and re-synced in the background every time the store starts
  (identical files are not rewritten). Settings has a switch and an "Update app drawer now" button; every installed app also
  has a "+" button (list) / "Add to app drawer" (app page) that shows the exact error if it fails.
- **Permissions:** the default profile (`droidstore.apparmor`, used by the OpenStore build) has **no** `write_path`: OpenStore's
  automatic review flags `write_path` ("found redflagged fields: write_path") and sends the app to manual review. So the OpenStore build
  cannot add app drawer entries and the switch is **off by default** (Settings). For a sideload build with app drawer entries, point
  `hooks.droidstore.apparmor` in `manifest.json` at `droidstore-drawer.apparmor` (a `write_path` for
  `~/.local/share/applications/droidstore-app-*.desktop*` and nothing else) and build with `clickable build --skip-review`,
  or use `droidstore-unconfined.apparmor`. The store does not need to list that folder (it remembers its own entries in
  `shortcuts.json`). If AppArmor still denies it on your setup, look for `apparmor="DENIED"` in the syslog.
- Tests: `python3 tests/test_shortcuts.py` and `python3 tests/test_apparmor.py` (the drawer profile must match the file names the store writes; the default profile must have no redflagged fields).

## What it does

- Search an app catalog (F-Droid index-v2), filtered to the device architecture; the list loads more apps as you scroll,
  so the whole catalog is reachable (apps whose native code does not fit the device are hidden)
- "Only without native code" filter (pure Java/Kotlin, more likely to work in ATL)
- App page (tap an app): screenshots, description, what's new, categories, license, permissions, warnings, links
- Download with progress and sha256 verification
- Library of installed apps: Open / Delete
- Updates: the store compares the version of every installed app with the catalog. Apps with a newer version show
  "Update" (store list, library, app page), and the Installed tab has an "Update all" button. Your own APKs are never updated.
  After refreshing the catalog the badges are recalculated.
- Add your own APK (+ button at the top): tap "Choose from an app" and the system asks which app
  (File Manager etc.) to pick the APK from; or pick it from the Downloads/Documents list, or type its path.
  The package and version are read from the manifest, the APK is copied to the library and opened with ATL
  just like catalog apps. It warns you if the APK has native code that doesn't fit the device.
- ✔ marker for apps confirmed to work (`compat.json` file)
- Texts (name, summary, description, what's new) use the device language when the repository has it, English otherwise.
  `tools/build_catalog.py --lang el` does the same for a prebuilt catalog.
- Settings: ATL command, repository, optional URL of a prebuilt `catalog.json`

## Layout

    manifest.json, droidstore.apparmor, droidstore.desktop   click packaging
    qml/Main.qml, qml/AppDetailsPage.qml, qml/SettingsPage.qml, qml/ImportPage.qml, qml/PickerPage.qml  UI (Lomiri QML)
    src/store.py                                         backend (pyotherside)
    src/launcher.py                                      opens one app from the app drawer (used by the .desktop entries)
    src/catalog.py                                       converts the F-Droid index into a small catalog
    tools/build_catalog.py                               builds catalog.json on a PC/server
    tests/test_backend.py, tests/test_updates.py, ...    offline tests of the backend (updates, integrity checks, languages)
    compat.json                                          list of confirmed apps

## What has been tested and what hasn't

- The backend (catalog, download, sha256, library, launch, APK import) is tested offline: `python3 tests/test_backend.py`
- The store (QML, backend and click package) has been used and tested on a device: a **Fairphone 4** running Ubuntu Touch.
  It has not been tried on other devices yet.
- The compat dex files for DuckDuckGo (`compat/`) pass the offline tests, which run the real bytes in a small Dalvik
  interpreter; they were written without an Android SDK and are being tried on the Fairphone 4.
- Unknowns that depend on atl-touch (see the repo's `ci/README.md`):
  - the `LD_LIBRARY_PATH` the store builds is a guess; I haven't seen the atl-touch launcher
  - the SDK is built for Ubuntu Touch 24.04-2.x
  - whether launching an APK from an external process opens a proper window in Lomiri

## Limitations

- OpenStore build (default): the app is **confined** (AppArmor policy groups in `droidstore.apparmor`) and ships ATL inside the click,
  so nothing executable is downloaded at runtime. It has been tested on a Fairphone 4, and ATL runs under confinement; if you hit
  a problem on your setup, look for `apparmor="DENIED"` lines in the syslog and add the matching policy group.
- Sideload build: point `hooks.droidstore.apparmor` in `manifest.json` at `droidstore-unconfined.apparmor`. This one can download the SDK
  and libraries at runtime (see below) but OpenStore rejects `unconfined`.
- The whole F-Droid index is big and parsing it needs memory. For production use, build a small catalog
  on a PC/server and put its URL in Settings:
  `python3 tools/build_catalog.py --abi arm64-v8a -o catalog.json`
- The F-Droid index signature is not verified. What is checked: each APK's sha256 against the index, and the index itself
  against the sha256 in the repository's `entry.json` (this catches corrupted or truncated downloads, it does not prove who
  published the index; checking the signature of `entry.jar` against the F-Droid certificate is still to do).
- Downloads at runtime (sideload build only): the ATL SDK is refused unless its sha256 is known (the `.sha256` file next to it,
  or a hash pinned in `SDK_SHA256` in `src/store.py`; only the pinned one proves it is authentic). For development you can
  set `ATL_SDK_ALLOW_UNVERIFIED=1`. System libraries come from `https://ports.ubuntu.com` and each .deb is checked
  against the SHA256 of the Ubuntu index.
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
- `manifest.json` declares `architecture: arm64` (the SDK and the decoder are arm64). Raise `version` for every release.
- Tests: `python3 tests/test_audio.py`, `python3 tests/test_backend.py`, `python3 tests/test_sdk.py`, `python3 tests/test_libs.py`, `python3 tests/test_details.py`, `python3 tests/test_import.py`, `python3 tests/test_bundled.py`, `python3 tests/test_updates.py`

