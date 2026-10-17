#!/usr/bin/env python3
"""Bundles extra system libraries (arm64 .deb from Ubuntu noble) into usr/lib/extra, for the confined build.

A confined click app cannot download and load libraries at runtime, so run this on your PC before building.
First put the SDK in place with ./tools/fetch_sdk.sh, then list the missing sonames. On the phone:

  LD_LIBRARY_PATH=$(find ~/.local/share/atlstore*/atl-sdk/usr/lib -name '*.so*' -printf '%h\\n' | sort -u | paste -sd:) \\
      ldd ~/.local/share/atlstore*/atl-sdk/usr/bin/android-translation-layer | grep 'not found'

(or use the unconfined build once: it prints the missing libraries). Then on the PC:

  pip install zstandard
  python3 tools/fetch_libs.py libmaliit-glib.so.3 libportal.so.1 libswscale.so.7

Repeat with any new "not found" libraries until none are left.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp()
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = os.path.join(tmp, var)
sys.path.insert(0, os.path.join(ROOT, "src"))

import store  # noqa: E402

store.EXTRA_LIB_DIR = os.path.join(ROOT, "usr", "lib", "extra")
store._deb_arch = lambda: "arm64"  # the phone is arm64, whatever this PC is


def main(sonames):
    if not sonames:
        print(__doc__)
        return 1
    holder = {}
    failed = 0
    for soname in sonames:
        try:
            store._fetch_system_lib(soname, holder)
            print("ok   ", soname)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("FAIL ", soname, "-", exc)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
