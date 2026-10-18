#!/usr/bin/env python3
"""Copies every shared library that ATL needs and the phone does not have into <usr>/lib/extra,
so the click package is self-contained and the user never has to download anything.

Runs inside the build container (after ATL was built), not on the phone:

    bundle_closure.py <usr> [--device-libs FILE]

<usr> is the staged usr/ folder. FILE is the list of libraries the phone already has: create it once on the
phone with   ldconfig -p | awk '/=>/ {print $1}' | sort -u > device-libs.txt   and put it in tools/.
Without it every dependency is bundled except the C library and GPU/display libraries (which must come from
the phone: its EGL/GL/Wayland are libhybris-specific), which gives a bigger package.
"""
import argparse
import fnmatch
import os
import re
import shutil
import subprocess
import sys

# Never bundle: the C runtime and everything tied to the phone's graphics stack
NEVER = ["ld-linux-*", "linux-vdso*", "libc.so*", "libm.so*", "libdl.so*", "libpthread.so*", "librt.so*",
         "libresolv.so*", "libutil.so*", "libnsl.so*", "libgcc_s.so*", "libstdc++.so*",
         "libEGL*", "libGLES*", "libGL.so*", "libGLX*", "libOpenGL*", "libvulkan*", "libgbm*",
         "libwayland-*", "libhybris*", "libandroid-properties*", "libdrm*"]
LDD_LINE = re.compile(r"^\s*(\S+)\s+=>\s+(\S+)")


def is_elf(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def our_lib_dirs(usr):
    dirs = set()
    for dirpath, _dirs, files in os.walk(os.path.join(usr, "lib")):
        if any(".so" in n for n in files):
            dirs.add(dirpath)
    return sorted(dirs)


def needed(path, ld_path):
    """(soname, resolved path or None) for every dependency ldd reports."""
    env = dict(os.environ, LD_LIBRARY_PATH=ld_path)
    out = subprocess.run(["ldd", path], env=env, capture_output=True, text=True).stdout
    result = []
    for line in out.splitlines():
        m = LDD_LINE.match(line)
        if m:
            result.append((m.group(1), None if m.group(2) == "not" else m.group(2)))
        elif "not found" in line:
            result.append((line.split()[0], None))
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("usr")
    ap.add_argument("--device-libs")
    args = ap.parse_args()
    usr = os.path.abspath(args.usr)
    extra = os.path.join(usr, "lib", "extra")
    on_device = set()
    if args.device_libs:
        with open(args.device_libs) as f:
            on_device = {l.strip() for l in f if l.strip()}
        print("device list: %d libraries the phone already has" % len(on_device))
    else:
        print("no device list: bundling everything except the C runtime and the graphics stack")

    os.makedirs(extra, exist_ok=True)
    bundled, unresolved = set(), set()
    queue = [os.path.join(d, n) for d, _s, files in os.walk(usr) for n in files
             if os.path.islink(os.path.join(d, n)) is False and is_elf(os.path.join(d, n))]
    seen = set(queue)
    while queue:
        path = queue.pop()
        ld_path = ":".join(our_lib_dirs(usr))
        for soname, resolved in needed(path, ld_path):
            if resolved is None:
                unresolved.add(soname)
                continue
            real = os.path.realpath(resolved)
            if real.startswith(usr + os.sep):
                continue  # already ours
            if soname in on_device or any(fnmatch.fnmatch(soname, p) for p in NEVER):
                continue
            dest = os.path.join(extra, soname)
            if os.path.exists(dest):
                continue
            shutil.copy2(real, dest)
            bundled.add(soname)
            if dest not in seen:
                seen.add(dest)
                queue.append(dest)
    print("bundled %d libraries into %s (%s)" % (len(bundled), extra,
          subprocess.run(["du", "-sh", extra], capture_output=True, text=True).stdout.split()[0]))
    for s in sorted(bundled):
        print("  +", s)
    if unresolved:
        print("WARNING: not found even in the build container: " + ", ".join(sorted(unresolved)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
