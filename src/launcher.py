#!/usr/bin/env python3
"""Opens one installed Android app from the Lomiri app drawer (no store window).

The .desktop entries made by store.add_shortcut() run:  python3 launcher.py <pkg id> <store package name>
It starts ATL with exactly the same environment as the store's "Open" button and stays alive until the app
ends, so Lomiri sees one normal app process.
"""
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _notify(title, body):
    tool = shutil.which("notify-send")
    if tool:
        try:
            subprocess.run([tool, title, body[:300]], timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            pass


def main(argv):
    if len(argv) not in (2, 3):
        print("usage: launcher.py <package id> [store package name]", file=sys.stderr)
        return 2
    pkg_id = argv[1]
    if len(argv) == 3 and argv[2]:
        os.environ["APP_ID"] = argv[2]  # the store's data folder is named after its click package
    os.environ.setdefault("APP_ID", "droidstore")
    import store  # noqa: E402  (after APP_ID is set: store reads it at import time)

    result = store.launch(pkg_id)
    if not result.get("ok"):
        message = result.get("error") or "Could not open the app"
        if result.get("needs_sdk"):
            message = "ATL is not installed. Open DroidStore and tap Install ATL."
        print(message, file=sys.stderr)
        _notify("DroidStore", message)
        return 1
    if result.get("already_running"):
        return 0  # Lomiri brings the running window to the front; do not start (or wait for) a second copy
    proc = store._PROCS.get(pkg_id)
    if proc is None:
        return 0
    code = proc.wait()
    time.sleep(0.5)  # let the watcher thread finish writing the log
    if code not in (0, None):
        # The store shows this in its UI; from the app drawer nobody is looking, so say it with a notification.
        cause = store._cause_text(store._read_log_lines(pkg_id)) or store._describe_exit(code)
        _notify("DroidStore: the app closed unexpectedly", cause)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
