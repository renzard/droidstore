"""ATL's Context.getCacheDir() must never return null, and the store starts ATL in a writable folder.

Background: ATL's getCacheDir() tried /tmp/atl_cache/<package> and returned null when that failed (a confined
app may not write to /tmp). Chaquopy (Python in Android apps, e.g. Codify) did str(getCacheDir()) = 'None' and tried
to create a folder called None in the current directory: "PermissionError: [Errno 13] Permission denied: 'None'".

    python3 tests/test_cachedir.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

tmp = tempfile.mkdtemp()
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = os.path.join(tmp, var)
os.environ["APP_ID"] = "droidstore.example_droidstore_0.1.0"

import store  # noqa: E402

store.pyotherside = type("P", (), {"send": staticmethod(lambda *a: None)})

# ---------------------------------------------------------------- 1) the log says what really happened
P = "atl: "
log = [
    "[droidstore] this app embeds Python (Chaquopy)",
    "[droidstore] Chaquopy compat: already patched",
    "",
    "Cause (chaquopy): ...",
    P + "Application.onCreate() threw",
    "    com.chaquo.python.PyException: PermissionError: [Errno 13] Permission denied: 'None'",
    "    at <python>.os.makedirs(<frozen os>:225)",
    "    at <python>.os.makedirs(<frozen os>:215)",
    "    at <python>.java.android.initialize_tempfile(__init__.py:101)",
    "    at com.chaquo.python.PyObject.callAttrThrowsNative(Native Method)",
    "    at gr.learn.programming.App.onCreate(App.kt:13)",
    "malloc(): unaligned tcache chunk detected",
    "[droidstore] ended after 2.0s: code 1",
]
# the real log has the exception line first; drop the placeholder line used above
log = [l for l in log if not l.startswith("Cause (chaquopy)")]
os.makedirs(store.CACHE_DIR, exist_ok=True)
with open(store._launch_log_path("gr.learn.programming"), "w") as f:
    f.write("\n".join(log) + "\n")
cause = store._analyze_log(store._read_log_lines("gr.learn.programming"))
assert cause and cause["kind"] == "chaquopy", cause
assert "getCacheDir" in cause["advice"] and "'None'" in cause["advice"], cause["advice"]
assert "not an Android permission" in cause["advice"], cause["advice"]
summary = store.get_launch_summary("gr.learn.programming")
assert "Cause (chaquopy)" in summary and "getCacheDir" in summary, summary

# another Chaquopy failure keeps the generic explanation
with open(store._launch_log_path("org.other"), "w") as f:
    f.write("com.chaquo.python.PyException: ImportError: No module named foo\n")
other = store._analyze_log(store._read_log_lines("org.other"))
assert other["kind"] == "chaquopy" and "getCacheDir" not in other["advice"], other
print("test_cachedir (log advice): OK")

# ---------------------------------------------------------------- 2) ATL starts in a writable folder
cwd = store._launch_cwd()
assert cwd == store.ATL_CWD and os.path.isdir(cwd) and os.access(cwd, os.W_OK), cwd
assert cwd.startswith(store.DATA_DIR + os.sep), cwd  # inside the store's own (confined-writable) folder

out = os.path.join(tmp, "out")
os.makedirs(out)
fake = os.path.join(tmp, "fake-atl")
with open(fake, "w") as f:
    # what Chaquopy did: create a relative folder called None
    f.write('#!/bin/sh\npwd > "$OUT/cwd.txt"\nmkdir -p None/chaquopy 2> "$OUT/err.txt" && echo created > "$OUT/ok.txt"\n')
os.chmod(fake, 0o755)
apk = os.path.join(store.APK_DIR, "c.apk")
open(apk, "wb").write(b"x")
store._write_json(store.LIBRARY_FILE, {"org.c": {"pkgId": "org.c", "name": "C", "apk": apk}})
store.set_settings({"atl_bin": fake, "extra_env": "OUT=" + out})

here = os.getcwd()
os.chdir(os.path.join(tmp))          # a folder that must stay clean
res = store.launch("org.c")
os.chdir(here)
assert res["ok"], res
deadline = time.time() + 5
while time.time() < deadline and not os.path.exists(os.path.join(out, "ok.txt")):
    time.sleep(0.05)
assert open(os.path.join(out, "cwd.txt")).read().strip() == os.path.realpath(store.ATL_CWD), open(os.path.join(out, "cwd.txt")).read()
assert os.path.exists(os.path.join(out, "ok.txt")), open(os.path.join(out, "err.txt")).read()
assert os.path.isdir(os.path.join(store.ATL_CWD, "None", "chaquopy"))
assert not os.path.exists(os.path.join(tmp, "None"))
assert "working folder: " + store.ATL_CWD in open(store._launch_log_path("org.c")).read()
print("test_cachedir (working folder): OK")

# when the folder cannot be made the launch still works (old behaviour)
real = store.ATL_CWD
store.ATL_CWD = os.path.join(tmp, "nope", "cwd")
open(os.path.join(tmp, "nope"), "w").close()   # a FILE where the parent folder should be
assert store._launch_cwd() is None
store.ATL_CWD = real

# ---------------------------------------------------------------- 3) the ATL source: the REAL getCacheDir() code
ctx = os.path.join(ROOT, "atl", "src", "api-impl", "android", "content", "Context.java")
src = open(ctx, encoding="utf-8").read()
start = src.index("\t// true if dir exists")
end_marker = "\t\treturn null;\n\t}\n"
end = src.index(end_marker, src.index("Unable to create cache directory")) + len(end_marker)
methods = src[start:end]
assert methods.count('"/tmp/atl_cache/"') == 1 and 'new File(getDataDirFile(), "cache")' in methods, methods
assert "private static boolean isUsableDir" in methods and "public File getCacheDir()" in methods
assert "FileOutputStream" in methods, "isUsableDir() must create a file: File.canWrite() is access(2), AppArmor does not check it"

java = shutil.which("java")
compiles = False
if java:
    probe = os.path.join(tmp, "Probe.java")
    open(probe, "w").write("public class Probe { public static void main(String[] a) { } }\n")
    compiles = subprocess.run([java, probe], capture_output=True).returncode == 0   # a JRE without the compiler fails here
if not compiles:
    print("test_cachedir (ATL Java): SKIPPED (needs a JDK 11+ with the 'java File.java' launcher)")
else:
    base = os.path.join(tmp, "jbase")                 # never touch the real /tmp/atl_cache
    os.makedirs(base)
    code = methods.replace('"/tmp/atl_cache/"', '"%s/atl_cache/"' % base)
    harness = """import java.io.File;
public class CacheDirHarness {
    static class Slog { static void w(String t, String m) { } }
    static final String TAG = "Context";
    static class Ctx {
        File cache_dir = null; File dataDir; String pkg;
        Ctx(File d, String p) { dataDir = d; pkg = p; }
        String getPackageName() { return pkg; }
        File getDataDirFile() { return dataDir; }
%s
    }
    static void check(boolean ok, String what) { if (!ok) { System.out.println("FAIL: " + what); System.exit(1); } }
    public static void main(String[] a) throws Exception {
        File data = new File(a[0], "data"); data.mkdirs();
        File root = new File(a[0], "atl_cache");
        // default place usable: unchanged behaviour
        File r1 = new Ctx(data, "org.good").getCacheDir();
        check(r1 != null && r1.equals(new File(root, "org.good")) && r1.isDirectory(), "default place: " + r1);
        check(!new File(r1, ".atl_write_probe").exists(), "the probe file must not be left behind");
        // the default place LOOKS writable (canWrite() is true, even for root) but no file can be created in it: this is what
        // AppArmor does to a /tmp folder that an unconfined run left behind (SQLite: "app.db.lck (Permission denied)").
        // A folder in the way of the probe file gives the same answer for open(), without needing AppArmor.
        File denied = new File(root, "org.denied");
        File inTheWay = new File(denied, ".atl_write_probe");
        check(inTheWay.mkdirs() && denied.canWrite(), "setup: a folder that looks writable");
        File r3 = new Ctx(data, "org.denied").getCacheDir();
        check(r3 != null && r3.equals(new File(data, "cache")), "a default place where no file can be created must not be used: " + r3);
        check(r3.listFiles().length == 0, "the probe file must not be left behind in the fallback");
        inTheWay.delete(); denied.delete(); new File(root, "org.good").delete();
        // default place impossible (a plain file is in the way, like /tmp not being writable): fall back, never null
        for (File f : root.listFiles()) f.delete();
        root.delete();
        check(root.createNewFile(), "setup");
        Ctx c = new Ctx(data, "gr.learn.programming");
        File r2 = c.getCacheDir();
        check(r2 != null, "returned null");
        check(r2.equals(new File(data, "cache")) && r2.isDirectory() && r2.canWrite(), "fallback: " + r2);
        check(c.getCacheDir().equals(r2), "stable");
        // deleted while running: made again
        r2.delete();
        check(c.getCacheDir() != null && c.getCacheDir().isDirectory(), "recreate");
        // nothing works: null, no exception
        File blocker = new File(a[0], "blocker"); blocker.createNewFile();
        check(new Ctx(blocker, "org.none").getCacheDir() == null, "null only when everything fails");
        System.out.println("OK");
    }
}
""" % code
    hfile = os.path.join(tmp, "CacheDirHarness.java")
    open(hfile, "w").write(harness)
    run = subprocess.run([java, hfile, base], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0 and run.stdout.strip() == "OK", (run.stdout, run.stderr)
    print("test_cachedir (ATL Java getCacheDir): OK")

print("test_cachedir: OK")
