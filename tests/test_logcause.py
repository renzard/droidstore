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
log = ["[atlstore] android-translation-layer app.apk", "[atlstore] LD_LIBRARY_PATH=/x", ""]
log += ["android-translation-layer W jit_logger.cc:70] noise"] * 50
log += ["java.lang.UnsatisfiedLinkError: dlopen failed: library \"liborbit-jni-spotify.so\" needs libfoo.so",
        "\tat java.lang.Runtime.loadLibrary0(Runtime.java:1014)"]
log += ["Aborting thread:", ""]
log += thread("main", "Runnable", ["java.lang.Object.wait(Native method)"])
for i in range(60):
    log += thread("Firebase Background Thread #%d" % i, "Waiting", ["sun.misc.Unsafe.park(Native method)"])
log += thread("DefaultDispatcher-worker-4", "Native", ["java.lang.System.loadLibrary(System.java:1657)"])
log += ["android-translation-layer I 10-17 16:29:00 1 2 OpenjdkJvm.cc:314] System.exit called, status: 10",
        "[atlstore] ended after 11.2s: code 10"]
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
write("org.ok", ["[atlstore] x", "app running", "android-translation-layer W jit_logger.cc:1] spam"])
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
