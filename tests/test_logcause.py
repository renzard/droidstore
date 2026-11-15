"""The cause of a crash must be found even when a huge thread dump follows it.

    python3 tests/test_logcause.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

tmp = tempfile.mkdtemp()
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = os.path.join(tmp, var)

import store  # noqa: E402


def thread(name, state, java, n_native=30):
    out = ['"%s" prio=5 tid=9 %s' % (name, state), "  | group=\"\" sCount=1", "  kernel: (couldn't read)"]
    out += ["  native: #%02d pc 0000000000547d24  /sdk/libart.so (???)" % i for i in range(n_native)]
    out += ["  at %s" % j for j in java]
    return out


def write(pkg, lines):
    os.makedirs(store.CACHE_DIR, exist_ok=True)
    with open(store._launch_log_path(pkg), "w") as f:
        f.write("\n".join(lines) + "\n")


# 1) Spotify-like: a native-library error, then a long thread dump of mostly parked threads, then System.exit
log = ["[droidstore] android-translation-layer app.apk", "[droidstore] LD_LIBRARY_PATH=/x", ""]
log += ["android-translation-layer W jit_logger.cc:70] noise"] * 50
log += ["java.lang.UnsatisfiedLinkError: dlopen failed: library \"liborbit-jni-spotify.so\" needs libfoo.so",
        "\tat java.lang.Runtime.loadLibrary0(Runtime.java:1014)"]
log += ["Aborting thread:", ""]
log += thread("main", "Runnable", ["java.lang.Object.wait(Native method)"])
for i in range(60):
    log += thread("Firebase Background Thread #%d" % i, "Waiting", ["sun.misc.Unsafe.park(Native method)"])
log += thread("DefaultDispatcher-worker-4", "Native", ["java.lang.System.loadLibrary(System.java:1657)"])
log += ["android-translation-layer I 10-17 16:29:00 1 2 OpenjdkJvm.cc:314] System.exit called, status: 10",
        "[droidstore] ended after 11.2s: code 10"]
write("com.spot", log)

summary = store.get_launch_summary("com.spot")
assert "Cause (native)" in summary and "liborbit-jni-spotify.so" in summary, summary
assert "System.exit(10)" in summary, summary
assert "Firebase" not in summary, summary  # a thread NAME must not count as a Play Services error

shown = store.get_launch_log("com.spot", 1000, 300)
assert "UnsatisfiedLinkError" in shown, shown[:500]
assert "thread dump collapsed: 62 threads" in shown, shown[-800:]
assert "native: #" not in shown and "Firebase Background Thread" not in shown
assert "DefaultDispatcher-worker-4" in shown and "System.loadLibrary" in shown  # the running threads stay
assert len(shown.splitlines()) < 80, len(shown.splitlines())
full = store.get_launch_log("com.spot", 1000, 300, False, False)
assert "native: #" in full  # hide_noise=False still shows everything
print("test_logcause (native + dump): OK")

# 2) a caught exception early on is not the cause when the app names the fatal one
write("org.fatal", [
    "W/System.err: java.io.IOException: harmless, caught",
    "E/AndroidRuntime: FATAL EXCEPTION: main",
    "java.lang.NoSuchMethodError: No virtual method foo()V in class Landroid/view/View;",
    "\tat org.fatal.Main.onCreate(Main.java:10)",
])
c = store._analyze_log(store._read_log_lines("org.fatal"))
assert c["kind"] == "api" and "NoSuchMethodError" in c["line"], c
print("test_logcause (fatal marker): OK")

# 3) Play Services error
write("org.gms", ["java.lang.NoClassDefFoundError: com.google.android.gms.common.GoogleApiAvailability"])
assert store._analyze_log(store._read_log_lines("org.gms"))["kind"] == "gms"

# 4) only System.exit, no exception: say so honestly
write("org.exit", ["hello", "OpenjdkJvm.cc:314] System.exit called, status: 3"])
c = store._analyze_log(store._read_log_lines("org.exit"))
assert c["kind"] == "exit" and c["exit_status"] == 3 and "No exception" in c["advice"], c

# 5) a clean log has no cause; unknown package is safe
write("org.ok", ["[droidstore] x", "app running", "android-translation-layer W jit_logger.cc:1] spam"])
assert store.get_launch_summary("org.ok") == ""
assert store.get_launch_summary("../evil") == ""
assert store.get_launch_summary("org.missing") == ""

# 6) long lines that explain an error are not cut at 300 chars, others are
write("org.long", ["java.lang.RuntimeException: " + "y" * 900, "z" * 900])
out = store.get_launch_log("org.long", 10, 300).splitlines()
assert len(out[0]) > 900 and len(out[1]) < 330, [len(x) for x in out]
print("test_logcause: OK")

# 7) the bionic shim's "library 'x' not found" is listed, and libz.so gets a compat symlink when glibc has libz.so.1
write("org.shim", [
    "E/ATLProvider(1): java.lang.NullPointerException: Attempt to invoke virtual method on a null object reference",
    "E/[bionic_linker](1): shim bionic linker: library 'libz.so' not found",
])
text = store.get_launch_summary("org.shim")
assert "Cause (exception)" in text and "could not find: libz.so" in text, text
assert store._analyze_log(["shim bionic linker: library 'libz.so' not found"])["kind"] == "native"
d = tempfile.mkdtemp()
fake = os.path.join(d, "glibc")
os.makedirs(fake)
for n in ("libm.so.6", "libc.so.6", "libdl.so.2", "libz.so.1"):
    open(os.path.join(fake, n), "w").close()
store.COMPAT_LIB_DIR = os.path.join(d, "compat")
store._glibc_dirs = lambda: [fake]
res = store._ensure_compat_libs([])
assert os.path.islink(os.path.join(res, "libz.so")) and os.readlink(os.path.join(res, "libz.so")).endswith("libz.so.1")
print("test_logcause (shim libs + libz): OK")

# 2) Codify-like: Chaquopy cannot start Python because dlopen(NULL) fails; the cause must be that, not a generic Java error
log = ["[droidstore] android-translation-layer codify.apk", "[droidstore] this app embeds Python (Chaquopy): needs the fix", ""]
log += ["android-translation-layer F 11-14 14:41:27 20488 20488 runtime.cc:650] Cause (exception): com.chaquo.python.PyException: Traceback (most recent call last):",
        "android-translation-layer F 11-14 14:41:27 20488 20488 runtime.cc:650]   File \"chaquopy_java.pyx\", line 100, in chaquopy_java.init_module",
        "android-translation-layer F 11-14 14:41:27 20488 20488 runtime.cc:650] OSError: Cannot load library: ",
        "Fatal signal 6 (SIGABRT), code -6 (SI_TKILL)",
        "[droidstore] ended after 1.2s: killed by signal 6 (SIGABRT)"]
write("gr.learn.programming", log)
summary = store.get_launch_summary("gr.learn.programming")
assert "Cause (chaquopy)" in summary and "Chaquopy compat" in summary, summary

import zipfile  # noqa: E402
apk = os.path.join(tmp, "py.apk")
with zipfile.ZipFile(apk, "w") as z:
    z.writestr("lib/arm64-v8a/libchaquopy_java.so", "x")
plain = os.path.join(tmp, "plain.apk")
with zipfile.ZipFile(plain, "w") as z:
    z.writestr("classes.dex", "x")
assert store._uses_chaquopy(apk) and not store._uses_chaquopy(plain)
print("test_logcause (chaquopy): OK")

# 3) a harmless bionic_linker warning that mentions libchaquopy_java.so is NOT the cause
log = ["W/[bionic_linker](1): `/x/lib/libchaquopy_java.so` is not a prelinked library",
       "java.lang.UnsatisfiedLinkError: dlopen failed: library \"libfoo.so\" not found",
       "[droidstore] ended after 1.9s: code 1"]
write("com.warn", log)
assert "Cause (native)" in store.get_launch_summary("com.warn")
print("test_logcause (chaquopy warning ignored): OK")

# 4) a long Python traceback: the Python error (last line) and the compat status are in the summary
P = "android-translation-layer F 11-14 14:59:01 21836 21836 runtime.cc:650] "
log = ["[droidstore] this app embeds Python (Chaquopy)", "[droidstore] Chaquopy compat: patched ctypes in stdlib-common.imy", "",
       "Cause (exception): com.chaquo.python.PyException: Traceback (most recent call last):",
       "  File \"chaquopy_java.pyx\", line 100, in chaquopy_java.init_module",
       P + "  File \"./java/__init__.py\", line 29, in <module>",
       P + "  File \"./java/primitive.py\", line 7, in <module>",
       P + "AttributeError: 'NoneType' object has no attribute 'Py_DecRef'",
       P + "(Throwable with no stack trace)",
       "*** *** ***", "Fatal signal 11 (SIGSEGV)"]
write("com.tb", log)
summary = store.get_launch_summary("com.tb")
assert "AttributeError: 'NoneType' object has no attribute 'Py_DecRef'" in summary, summary
assert "Chaquopy compat: patched ctypes" in summary and "Throwable" not in summary, summary
print("test_logcause (python traceback): OK")

# 8) DuckDuckGo under ATL: a harmless WARNING comes first in the log (SQLite could not create its lock file), the app is
#    then killed by a missing Android class that ATL's own handler reports. The cause must be the missing class.
#    (These are the lines of the real log, see the "Cause" box that wrongly said app.db.lck.)
W = "W/SupportSQLiteLock(21921): "
ddg = [
    "[droidstore] android-translation-layer /x/com.duckduckgo.mobile.android.apk",
    W + "java.io.FileNotFoundException: /tmp/atl_cache/com.duckduckgo.mobile.android/app.db.lck (Permission denied)",
    W + "  at java.io.FileOutputStream.open0(Native Method)",
    W + "  at java.io.FileOutputStream.open(FileOutputStream.java:287)",
    W + "  at kotlinx.coroutines.scheduling.CoroutineScheduler$Worker.run(CoroutineScheduler.kt:704)",
    "STRAWTOGRASP: GetFieldID(0x35, delegate, Ljava/net/SocketImpl;) returning NULL",
    "android-translation-layer I 11-15 17:02:05 21921 21938 heap.cc:2841] Background sticky concurrent mark sweep GC freed",
    "hacky_uncaught_exception_handler: Thread: DefaultDispatcher-worker-14, Exception:",
    "java.lang.NoClassDefFoundError: Failed resolution of: Landroid/app/TaskStackBuilder;",
    "  at com.duckduckgo.app.global.shortcut.AppShortcutCreator.buildBookmarksShortcut(AppShortcutCreator.kt:167)",
    "",
    "",
    "  at com.duckduckgo.app.global.shortcut.AppShortcutCreator$refreshAppShortcuts$1.invokeSuspend(AppShortcutCreator.kt:106)",
    "  at kotlin.coroutines.jvm.internal.BaseContinuationImpl.resumeWith(ContinuationImpl.kt:34)",
    "  Suppressed: kotlinx.coroutines.internal.DiagnosticCoroutineContextException: [StandaloneCoroutine{Cancelling}@1053a7f, Dispatchers.IO]",
    "Caused by: java.lang.ClassNotFoundException: android.app.TaskStackBuilder",
    "android-translation-layer I 11-15 17:02:07 21921 21921 OpenjdkJvm.cc:314] System.exit called, status: 10",
    "[droidstore] ended after 4.1s: code 10",
]
write("com.duckduckgo.mobile.android", ddg)
c = store._analyze_log(store._read_log_lines("com.duckduckgo.mobile.android"))
assert c["kind"] == "api" and "TaskStackBuilder" in c["line"] and "app.db.lck" not in c["line"], c
assert c["context"] and all(x.strip() for x in c["context"]), c["context"]   # no empty lines between the frames
summary = store.get_launch_summary("com.duckduckgo.mobile.android")
assert summary.startswith("Cause (api): java.lang.NoClassDefFoundError: Failed resolution of: Landroid/app/TaskStackBuilder;"), summary
assert "Missing Android class: android.app.TaskStackBuilder" in summary, summary
assert "atl/src/api-impl/android/app/TaskStackBuilder.java" in summary and "meson.build" in summary, summary
assert "AppShortcutCreator.kt:167" in summary and "System.exit(10)" in summary, summary
assert "SupportSQLiteLock" not in summary and "app.db.lck" not in summary, summary
print("test_logcause (DuckDuckGo: missing class, not the SQLite warning): OK")

# the same without ATL's marker (an older ATL): the warning-level lines are still skipped when something else explains it
no_marker = [l for l in ddg if not l.startswith("hacky_uncaught_exception_handler")]
write("com.duckduckgo.nomarker", no_marker)
c = store._analyze_log(store._read_log_lines("com.duckduckgo.nomarker"))
assert c["kind"] == "api" and "TaskStackBuilder" in c["line"], c

# a log that only has warnings still gets a cause (a caught exception can be the only clue), and an error-level
# line wins over a warning-level one that comes first
write("org.onlywarn", ["W/System.err: java.lang.UnsatisfiedLinkError: dlopen failed: library \"libx.so\" not found"])
assert store._analyze_log(store._read_log_lines("org.onlywarn"))["kind"] == "native"
write("org.errwins", ["W/Foo(1): java.io.IOException: caught and logged",
                      "E/Bar(1): java.lang.NoSuchMethodError: No virtual method foo()V in class Landroid/view/View;"])
c = store._analyze_log(store._read_log_lines("org.errwins"))
assert c["kind"] == "api" and "NoSuchMethodError" in c["line"], c
assert "Missing Android class" not in store.get_launch_summary("org.errwins")   # a missing METHOD is not a missing class

# which class is missing: only the Android framework (ATL's api-impl), not androidx or app classes
m = store._missing_android_class
assert m(["java.lang.NoClassDefFoundError: Failed resolution of: Landroid/app/TaskStackBuilder;"]) == "android.app.TaskStackBuilder"
assert m(["Caused by: java.lang.ClassNotFoundException: android.app.TaskStackBuilder"]) == "android.app.TaskStackBuilder"
assert m(["java.lang.ClassNotFoundException: Didn't find class \"android.os.Foo\" on path: DexPathList"]) == "android.os.Foo"
assert m(["java.lang.NoClassDefFoundError: Failed resolution of: Landroidx/core/app/Foo;"]) is None
assert m(["java.lang.NoClassDefFoundError: com.google.android.gms.common.GoogleApiAvailability"]) is None
assert m(["java.lang.NoClassDefFoundError: Failed resolution of: Lcom/example/app/Foo;"]) is None
assert m(["nothing here"]) is None
write("org.nested", ["java.lang.NoClassDefFoundError: Failed resolution of: Landroid/app/NotificationManager$Policy;"])
assert "android/app/NotificationManager.java" in store.get_launch_summary("org.nested")
print("test_logcause (warnings skipped, missing class named): OK")

# ---- the log of a real DuckDuckGo crash (screenshot): ATL's handler printed an EMPTY line where the exception's name
# should be, the stack frames came first, then kotlinx's "Suppressed:" line, then the real cause in "Caused by:".
# The box used to say "Cause (exception): Suppressed: kotlinx.coroutines...DiagnosticCoroutineContextException".
real = [
    "[droidstore] android-translation-layer /x/com.duckduckgo.mobile.android.apk",
    "W/SupportSQLiteLock(29267):   at kotlinx.coroutines.scheduling.CoroutineScheduler$Worker.run(CoroutineScheduler.kt:704)",
    "STRAWTOGRASP: GetFieldID(0x35, delegate, Ljava/net/SocketImpl;) returning NULL",
    "hacky_uncaught_exception_handler: Thread: DefaultDispatcher-worker-8, Exception:",
    "",
    "  at com.duckduckgo.app.global.shortcut.AppShortcutCreator.buildBookmarksShortcut(AppShortcutCreator.kt:167)",
    "  at com.duckduckgo.app.global.shortcut.AppShortcutCreator.access$buildBookmarksShortcut(AppShortcutCreator.kt:80)",
    "  at kotlin.coroutines.jvm.internal.BaseContinuationImpl.resumeWith(ContinuationImpl.kt:34)",
    "  at kotlinx.coroutines.DispatchedTask.run(DispatchedTask.kt:100)",
    "  Suppressed: kotlinx.coroutines.internal.DiagnosticCoroutineContextException: [StandaloneCoroutine{Cancelling}@10b56a0, Dispatchers.IO]",
    "Caused by: java.lang.ClassNotFoundException: android.app.TaskStackBuilder",
    "  ... 11 more",
    "android-translation-layer I 11-15 22:15:23 29267 29293 OpenjdkJvm.cc:314] System.exit called, status: 10",
]
write("com.duckduckgo.real", real)
c = store._analyze_log(store._read_log_lines("com.duckduckgo.real"))
assert c["kind"] == "api" and c["line"] == "Caused by: java.lang.ClassNotFoundException: android.app.TaskStackBuilder", c
assert "Suppressed" not in c["line"], c
assert c["frame"] and "AppShortcutCreator.buildBookmarksShortcut(AppShortcutCreator.kt:167)" in c["frame"], c
summary = store.get_launch_summary("com.duckduckgo.real")
assert summary.startswith("Cause (api): Caused by: java.lang.ClassNotFoundException: android.app.TaskStackBuilder"), summary
assert "Where: at com.duckduckgo.app.global.shortcut.AppShortcutCreator.buildBookmarksShortcut" in summary, summary
assert "Missing Android class: android.app.TaskStackBuilder" in summary and "System.exit(10)" in summary, summary
assert "DiagnosticCoroutineContextException" not in summary and "SupportSQLiteLock" not in summary, summary

# a generic exception whose root cause (last "Caused by:") is more specific: the root cause is reported
write("org.chain", ["E/AndroidRuntime(1): FATAL EXCEPTION: main",
                    "java.lang.RuntimeException: Unable to start activity",
                    "  at android.app.ActivityThread.performLaunchActivity(ActivityThread.java:1)",
                    "  at org.chain.Main.onCreate(Main.java:12)",
                    "Caused by: java.lang.IllegalStateException: wrapper",
                    "Caused by: java.lang.NoSuchMethodError: No virtual method foo()V in class Landroid/view/View;",
                    "  ... 3 more"])
c = store._analyze_log(store._read_log_lines("org.chain"))
assert c["kind"] == "api" and "NoSuchMethodError" in c["line"] and "Main.java:12" in c["frame"], c
print("test_logcause (real DuckDuckGo log: Suppressed skipped, root cause + frame): OK")

# ---- does the installed ATL have the class? (api-impl.jar holds the classes as a dex file)
import zipfile  # noqa: E402


def fake_usr(with_class):
    usr = os.path.join(tmp, "usr_%s" % with_class)
    d = os.path.join(usr, "share", "android_translation_layer")
    os.makedirs(d, exist_ok=True)
    dex = b"dex\n035\0" + b"Landroid/app/Activity;\0" + (b"Landroid/app/TaskStackBuilder;\0" if with_class else b"")
    with zipfile.ZipFile(os.path.join(d, "api-impl.jar"), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("classes.dex", dex)
    return usr


assert store._atl_has_class("android.app.TaskStackBuilder", fake_usr(True)) is True
assert store._atl_has_class("android.app.TaskStackBuilder", fake_usr(False)) is False
assert store._atl_has_class("android.app.TaskStackBuilder", os.path.join(tmp, "nothing")) is None
old_root = store.sdk_root
store.sdk_root = lambda: fake_usr(False)
summary = store.get_launch_summary("com.duckduckgo.real")
assert "built before it was added" in summary and "build_all.sh" in summary, summary
assert "MISSING" in "\n".join(store._atl_build_diag()), store._atl_build_diag()
store.sdk_root = lambda: fake_usr(True)
store._API_IMPL_JAR_CACHE.clear()
summary = store.get_launch_summary("com.duckduckgo.real")
assert "It IS in this ATL's api-impl.jar" in summary, summary
assert "present" in "\n".join(store._atl_build_diag()), store._atl_build_diag()
store.sdk_root = old_root
print("test_logcause (ATL build check): OK")

# ---- a normal close: the app logged a caught ClassNotFoundException (androidx reflection probe), then the user closed
# it (exit code 0). That is not a crash, so no "Cause" box.
normal = [
    "[droidstore] android-translation-layer /x/app.apk",
    "E/WindowInsetsCompat(30023): java.lang.ClassNotFoundException: android.view.View$AttachInfo",
    "E/WindowInsetsCompat(30023):   at java.lang.Class.classForName(Native Method)",
    "E/WindowInsetsCompat(30023):   at androidx.core.view.WindowInsetsCompat$Impl20.loadReflectionField(WindowInsetsCompat.java:1172)",
    "SoundPool: GStreamer error: Resource not found.",
    "ATLWindow: window close event from compositor -> closing all activities and exiting",
    "I/Activity(30023): - onDestroy - yay!",
    "",
    "[droidstore] ended after 24.1s: code 0",
]
write("org.normal", normal)
assert store._analyze_log(store._read_log_lines("org.normal")) is None
assert store.get_launch_summary("org.normal") == ""
# the same exception with a crash (non-zero code) still gets its cause
write("org.crashed", normal[:-1] + ["[droidstore] ended after 24.1s: code 10"])
assert store._analyze_log(store._read_log_lines("org.crashed"))["kind"] == "api"
# code 0 but a fatal exception in the log: still analysed
write("org.fatal0", ["hacky_uncaught_exception_handler: Thread: main, Exception:", "java.lang.IllegalStateException: boom",
                     "[droidstore] ended after 3.0s: code 0"])
assert store._analyze_log(store._read_log_lines("org.fatal0"))["kind"] == "exception"
print("test_logcause (normal close is not a crash): OK")
