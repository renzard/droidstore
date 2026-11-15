"""ATL needs android.app.TaskStackBuilder: DuckDuckGo builds its app shortcuts with it at start-up
(AppShortcutCreator.buildBookmarksShortcut) and died with
"NoClassDefFoundError: Failed resolution of: Landroid/app/TaskStackBuilder;" because ATL had no such class.

    python3 tests/test_taskstack.py

1) the file exists and is listed in atl/src/api-impl/meson.build (a .java file that is not listed is silently not built)
2) with a JDK: it compiles against the REAL ATL sources, and its logic is run against small stubs of the ATL classes
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.path.join(ROOT, "atl", "src", "api-impl")
TSB = os.path.join(API, "android", "app", "TaskStackBuilder.java")
tmp = tempfile.mkdtemp()

# ---------------------------------------------------------------- 1) the source and the build file
src = open(TSB, encoding="utf-8").read()
assert src.startswith("package android.app;") and "public class TaskStackBuilder" in src
meson = open(os.path.join(API, "meson.build"), encoding="utf-8").read()
assert meson.count("\t'android/app/TaskStackBuilder.java',\n") == 1, "TaskStackBuilder.java must be listed once in meson.build"
# every method DuckDuckGo and the usual notification code call
for sig in ("static TaskStackBuilder create(Context", "TaskStackBuilder addNextIntent(Intent", "Intent[] getIntents()",
            "TaskStackBuilder addNextIntentWithParentStack(", "TaskStackBuilder addParentStack(Activity",
            "TaskStackBuilder addParentStack(Class<?>", "TaskStackBuilder addParentStack(ComponentName",
            "int getIntentCount()", "Intent editIntentAt(int", "void startActivities()", "void startActivities(Bundle",
            "PendingIntent getPendingIntent(int requestCode, int flags)",
            "PendingIntent getPendingIntent(int requestCode, int flags, Bundle"):
    assert "public " + sig in src or sig in src, sig
print("test_taskstack (source + meson.build): OK")

# ---------------------------------------------------------------- 2) compile with the JDK's compiler module
java = shutil.which("java")
JAVAC = [java, "-m", "jdk.compiler/com.sun.tools.javac.Main", "-proc:none", "-nowarn", "-source", "8", "-target", "8",
         "-Xlint:-options"] if java else None
can_compile = bool(java) and subprocess.run(JAVAC[:3] + ["-version"], capture_output=True).returncode == 0
if not can_compile:
    print("test_taskstack (compile + behaviour): SKIPPED (needs a JDK 11+ with the jdk.compiler module)")
    print("test_taskstack: OK")
    sys.exit(0)

# 2a) against the real ATL sources. The ATL tree needs ART's own libraries (org.xmlpull, android.system, libcore, ...)
#     that a plain JDK does not have, so javac reports errors in OTHER files; -XDshould-stop.ifError=FLOW makes it check
#     TaskStackBuilder.java anyway. Only errors in this file count. A copy with a deliberate mistake proves that errors
#     in this file would be reported (otherwise "no errors" could mean "javac gave up before looking").
def compile_real(path, sourcepath):
    out = os.path.join(tmp, "real-out")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    r = subprocess.run(JAVAC + ["-implicit:none", "-XDshould-stop.ifError=FLOW", "-Xmaxerrs", "5000",
                                "-sourcepath", sourcepath, "-d", out, path], capture_output=True, text=True, timeout=280)
    return [l for l in r.stderr.splitlines() if os.path.basename(path) in l and "error:" in l]


mutant_dir = os.path.join(tmp, "mutant", "android", "app")
os.makedirs(mutant_dir)
mutant = os.path.join(mutant_dir, "TaskStackBuilder.java")
open(mutant, "w", encoding="utf-8").write(src.replace("info.parentActivityName;", "info.doesNotExistInAtl;", 1))
assert compile_real(mutant, os.path.dirname(os.path.dirname(os.path.dirname(mutant))) + os.pathsep + API), \
    "javac did not report a deliberate mistake: the check below would prove nothing"
errors = compile_real(TSB, API)
assert not errors, "TaskStackBuilder.java does not match ATL's own classes:\n" + "\n".join(errors)
print("test_taskstack (compiles against the real ATL classes): OK")

# 2b) its logic, against small stubs of the ATL classes it uses (same signatures as in atl/src/api-impl)
STUBS = {
    "android/content/ComponentName.java": """package android.content;
public class ComponentName {
    private final String pkg, cls;
    public ComponentName(String pkg, String cls) { this.pkg = pkg; this.cls = cls; }
    public ComponentName(Context c, Class<?> k) { this(c.getPackageName(), k.getName()); }
    public String getPackageName() { return pkg; }
    public String getClassName() { return cls; }
    public boolean equals(Object o) { return o instanceof ComponentName && ((ComponentName) o).cls.equals(cls); }
    public int hashCode() { return cls.hashCode(); }
    public String toString() { return pkg + "/" + cls; }
}""",
    "android/content/Context.java": """package android.content;
import android.content.pm.PackageManager;
import android.os.Bundle;
import java.util.ArrayList;
public class Context {
    public final PackageManager pm = new PackageManager();
    public final ArrayList<Intent> started = new ArrayList<Intent>();
    public String getPackageName() { return "org.test"; }
    public PackageManager getPackageManager() { return pm; }
    public void startActivity(Intent intent, Bundle options) { started.add(intent); }
}""",
    # like ATL's Intent: the copy constructor copies action and component, not the flags
    "android/content/Intent.java": """package android.content;
import android.content.pm.PackageManager;
public class Intent {
    public String action; public ComponentName component; public int flags;
    public Intent() {}
    public Intent(Intent o) { action = o.action; component = o.component; }
    public Intent setComponent(ComponentName c) { component = c; return this; }
    public ComponentName getComponent() { return component; }
    public Intent addFlags(int f) { flags |= f; return this; }
    public static Intent makeMainActivity(ComponentName c) { Intent i = new Intent(); i.action = "android.intent.action.MAIN"; i.component = c; return i; }
    public ComponentName resolveActivity(PackageManager pm) { return component; }
}""",
    "android/content/pm/ActivityInfo.java": """package android.content.pm;
public class ActivityInfo { public String packageName; public String parentActivityName; }""",
    "android/content/pm/PackageManager.java": """package android.content.pm;
import android.content.ComponentName;
import java.util.HashMap;
public class PackageManager {
    public static class NameNotFoundException extends Exception {}
    private final HashMap<String, ActivityInfo> activities = new HashMap<String, ActivityInfo>();
    public void add(String cls, String parent) { ActivityInfo i = new ActivityInfo(); i.packageName = "org.test"; i.parentActivityName = parent; activities.put(cls, i); }
    public ActivityInfo getActivityInfo(ComponentName c, int flags) throws NameNotFoundException {
        ActivityInfo i = activities.get(c.getClassName());
        if (i == null) throw new NameNotFoundException();
        return i;
    }
}""",
    "android/app/PendingIntent.java": """package android.app;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
public class PendingIntent {
    public static int requestCode; public static Intent[] intents;
    public static PendingIntent getActivities(Context c, int code, Intent[] i, int flags, Bundle options) { requestCode = code; intents = i; return new PendingIntent(); }
}""",
    "android/app/Activity.java": """package android.app;
import android.content.Intent;
public class Activity { public Intent parent; public Intent getParentActivityIntent() { return parent; } }""",
    "android/os/Bundle.java": "package android.os;\npublic class Bundle {}",
    "android/util/Log.java": "package android.util;\npublic class Log { public static int e(String tag, String msg) { return 0; } }",
}
HARNESS = """import android.app.*;
import android.content.*;
public class Check {
    static class Child {}
    static void check(boolean ok, String what) { if (!ok) { System.out.println("FAIL: " + what); System.exit(1); } }
    static Intent to(String cls) { return new Intent().setComponent(new ComponentName("org.test", cls)); }
    static String names(Intent[] a) { StringBuilder s = new StringBuilder(); for (Intent i : a) s.append(i.component.getClassName()).append(' '); return s.toString().trim(); }
    public static void main(String[] args) throws Exception {
        final int ROOT_FLAGS = 0x10000000 | 0x00008000 | 0x00004000;   // NEW_TASK | CLEAR_TASK | TASK_ON_HOME
        Context ctx = new Context();
        ctx.pm.add("Root", null); ctx.pm.add("Mid", "Root"); ctx.pm.add("Child", "Mid"); ctx.pm.add("Check$Child", "Mid");

        // what DuckDuckGo does: create(context).addNextIntent(a).addNextIntent(b).getIntents()
        Intent a = to("A"), b = to("B");
        TaskStackBuilder tsb = TaskStackBuilder.create(ctx).addNextIntent(a).addNextIntent(b);
        check(tsb.getIntentCount() == 2 && tsb.editIntentAt(1) == b, "count / editIntentAt");
        Intent[] intents = tsb.getIntents();
        check(intents.length == 2 && names(intents).equals("A B"), "order: " + names(intents));
        check(intents[0].flags == ROOT_FLAGS && intents[1].flags == 0, "only the first intent starts a new task");
        check(intents[0] != a && intents[1] != b && a.flags == 0, "copies: the intents the app gave are not changed");

        // nothing added
        check(TaskStackBuilder.create(ctx).getIntents().length == 0, "empty stack");
        try { TaskStackBuilder.create(ctx).getPendingIntent(0, 0); check(false, "getPendingIntent on empty"); } catch (IllegalStateException e) { }
        try { TaskStackBuilder.create(ctx).startActivities(); check(false, "startActivities on empty"); } catch (IllegalStateException e) { }

        // parents from the manifest (Child -> Mid -> Root), root first, the root is the app's main activity
        Intent[] p = TaskStackBuilder.create(ctx).addParentStack(new ComponentName("org.test", "Child")).addNextIntent(to("Child")).getIntents();
        check(names(p).equals("Root Mid Child"), "parent stack: " + names(p));
        check("android.intent.action.MAIN".equals(p[0].action), "root is the main activity");
        p = TaskStackBuilder.create(ctx).addNextIntentWithParentStack(to("Child")).getIntents();
        check(names(p).equals("Root Mid Child"), "addNextIntentWithParentStack: " + names(p));
        p = TaskStackBuilder.create(ctx).addParentStack(Child.class).getIntents();
        check(names(p).equals("Root Mid"), "addParentStack(Class): " + names(p));
        Activity act = new Activity(); act.parent = to("Mid");
        p = TaskStackBuilder.create(ctx).addParentStack(act).getIntents();
        check(names(p).equals("Root Mid"), "addParentStack(Activity): " + names(p));
        check(TaskStackBuilder.create(ctx).addParentStack(new Activity()).getIntentCount() == 0, "activity without a parent");
        try { TaskStackBuilder.create(ctx).addParentStack(new ComponentName("org.test", "Nope")); check(false, "unknown activity"); }
        catch (IllegalArgumentException e) { }

        // starting: from the bottom up, so the last intent is on top
        TaskStackBuilder.create(ctx).addNextIntent(a).addNextIntent(b).startActivities();
        check(ctx.started.size() == 2 && ctx.started.get(0).component.getClassName().equals("A")
              && ctx.started.get(1).component.getClassName().equals("B"), "startActivities order");

        // pending intent: all intents go to PendingIntent.getActivities with the request code
        TaskStackBuilder.create(ctx).addNextIntent(a).addNextIntent(b).getPendingIntent(7, 0);
        check(PendingIntent.requestCode == 7 && names(PendingIntent.intents).equals("A B"), "getPendingIntent");
        System.out.println("OK");
    }
}
"""
stub_root = os.path.join(tmp, "stubs")
files = []
for rel, text in STUBS.items():
    path = os.path.join(stub_root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(text)
    files.append(path)
harness = os.path.join(stub_root, "Check.java")
open(harness, "w", encoding="utf-8").write(HARNESS)
out = os.path.join(tmp, "stub-out")
os.makedirs(out)
r = subprocess.run(JAVAC + ["-d", out, TSB, harness] + files, capture_output=True, text=True, timeout=280)
assert r.returncode == 0, r.stderr
run = subprocess.run([java, "-cp", out, "Check"], capture_output=True, text=True, timeout=120)
assert run.returncode == 0 and run.stdout.strip() == "OK", (run.stdout, run.stderr)
print("test_taskstack (behaviour): OK")
print("test_taskstack: OK")
