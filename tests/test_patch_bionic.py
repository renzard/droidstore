"""tools/patch_bionic_translation.py adds the dlopen(NULL) guard, once, and the result compiles and works.

    python3 tests/test_patch_bionic.py
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("patch", os.path.join(ROOT, "tools", "patch_bionic_translation.py"))
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)

SRC = """#include <stdio.h>
void *dlopen(const char *filename, int flag)
{
	return NULL;
}
void *
android_dlopen_ext(const char *filename, int flags, const void *ext)
{
	return NULL;
}
void *unrelated(int a, int b) { return 0; }
"""

new, n = patch.patch_text(SRC)
assert n == 2, n
assert new.count(patch.MARK) == 2 and "unrelated(int a, int b) { return 0; }" in new
assert patch.patch_text(new) == (new, 0)  # idempotent

tmp = tempfile.mkdtemp()
with open(os.path.join(tmp, "dl.c"), "w") as f:
    f.write(SRC)
assert patch.main(["x", tmp]) == 0
assert patch.main(["x", tmp]) == 0  # second run: already patched
empty = tempfile.mkdtemp()
with open(os.path.join(empty, "a.c"), "w") as f:
    f.write("int x;\n")
assert patch.main(["x", empty]) == 2  # nothing found must be an error, not a silent no-op

if shutil.which("gcc"):
    with open(os.path.join(tmp, "main.c"), "w") as f:
        f.write('#include <stddef.h>\n#include <dlfcn.h>\nint main(void){ return dlopen(NULL, RTLD_NOW) ? 0 : 1; }\n')
    subprocess.check_call(["gcc", "-shared", "-fPIC", "-o", os.path.join(tmp, "libshim.so"), os.path.join(tmp, "dl.c"), "-ldl"])
    subprocess.check_call(["gcc", "-o", os.path.join(tmp, "m"), os.path.join(tmp, "main.c"), "-L" + tmp, "-lshim", "-ldl"])
    assert subprocess.call([os.path.join(tmp, "m")], env={"LD_LIBRARY_PATH": tmp}) == 0
print("test_patch_bionic: OK")
