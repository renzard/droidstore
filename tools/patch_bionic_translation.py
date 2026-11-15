#!/usr/bin/env python3
"""Makes dlopen(NULL) work in the bionic_translation shim linker.

Why: Android apps that embed Python through Chaquopy (for example Codify) run `import ctypes` on startup, and
ctypes does `PyDLL(None)` = dlopen(NULL). On real Android that returns the handle of the global scope. In
the shim it returned NULL without an error message, so the app died with
    OSError: Cannot load library:          (ctypes/__init__.py, line 376)
before it showed a single screen.

What it does: every C function in the bionic_translation source tree that is the dlopen entry point
(dlopen, android_dlopen_ext, bionic_dlopen, __loader_dlopen ...) gets a guard at the top:
if the file name is NULL, return what the system's dlopen(NULL, flags) returns (RTLD_NEXT skips the shim itself).

Usage: tools/patch_bionic_translation.py <bionic_translation source dir>
Exit status 0 when at least one entry point is patched (or all were already), 2 when none was found.
The change is idempotent (marked with DROIDSTORE_DLOPEN_NULL).
"""
import os
import re
import sys

MARK = "DROIDSTORE_DLOPEN_NULL"
NAMES = ("dlopen", "android_dlopen_ext", "bionic_dlopen", "__loader_dlopen", "__loader_android_dlopen_ext")
# return type, name, parameter list, then the opening brace (maybe on the next line)
FUNC_RE = re.compile(
    r"(?P<head>^[ \t]*(?:[A-Za-z_][\w \t]*?\*?[ \t]*)?void[ \t]*\*\s*)(?P<name>%s)[ \t]*\((?P<params>[^)]*)\)[ \t]*\n?[ \t]*\{"
    % "|".join(re.escape(n) for n in NAMES),
    re.M)


def _param_names(params):
    names = []
    for p in params.split(","):
        m = re.search(r"([A-Za-z_]\w*)\s*(?:\[\s*\])?\s*$", p.strip())
        names.append(m.group(1) if m else None)
    return names


def _guard(params):
    names = _param_names(params)
    if len(names) < 2 or not names[0] or not names[1] or names[0] == "void":
        return None
    return (
        "\n\t/* %s: dlopen(NULL) is the global scope on Android (Python's ctypes needs it) */\n"
        "\tif (%s == NULL) {\n"
        "\t\tstatic void *(*droidstore_real_dlopen)(const char *, int);\n"
        "\t\tif (!droidstore_real_dlopen)\n"
        "\t\t\tdroidstore_real_dlopen = (void *(*)(const char *, int))dlsym(RTLD_NEXT, \"dlopen\");\n"
        "\t\tif (droidstore_real_dlopen)\n"
        "\t\t\treturn droidstore_real_dlopen(NULL, %s);\n"
        "\t}\n" % (MARK, names[0], names[1]))


def patch_text(text):
    """Returns (new_text, number_of_functions_patched)."""
    if MARK in text:
        return text, 0
    count = 0
    out = []
    pos = 0
    for m in FUNC_RE.finditer(text):
        guard = _guard(m.group("params"))
        if guard is None:
            continue
        out.append(text[pos:m.end()])
        out.append(guard)
        pos = m.end()
        count += 1
    if not count:
        return text, 0
    out.append(text[pos:])
    new = "".join(out)
    # RTLD_NEXT needs _GNU_SOURCE before the first include, and dlsym needs <dlfcn.h>
    new = "#ifndef _GNU_SOURCE\n#define _GNU_SOURCE\n#endif\n#include <dlfcn.h>\n" + new
    return new, count


def main(argv):
    if len(argv) != 2 or not os.path.isdir(argv[1]):
        print(__doc__)
        return 1
    patched = already = 0
    for root, dirs, files in os.walk(argv[1]):
        dirs[:] = [d for d in dirs if d not in (".git", "builddir", "build")]
        for name in files:
            if not name.endswith(".c"):
                continue
            path = os.path.join(root, name)
            with open(path, "r", errors="replace") as f:
                text = f.read()
            if MARK in text:
                already += 1
                continue
            new, n = patch_text(text)
            if n:
                with open(path, "w") as f:
                    f.write(new)
                print("patched %d dlopen entry point(s) in %s" % (n, path))
                patched += n
    if not patched and not already:
        print("ERROR: no dlopen entry point found in %s; dlopen(NULL) is NOT fixed" % argv[1], file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
