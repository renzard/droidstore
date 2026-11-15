"""The AppArmor profiles must parse, and the confined one must allow exactly the files the store writes.

    python3 tests/test_apparmor.py
"""
import fnmatch
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

for name in ("droidstore.apparmor", "droidstore-drawer.apparmor", "droidstore-unconfined.apparmor"):
    with open(os.path.join(ROOT, name)) as f:
        json.load(f)  # a broken profile makes the click fail to install

with open(os.path.join(ROOT, "droidstore.apparmor")) as f:
    default = json.load(f)
assert default.get("template") != "unconfined", "the default build must stay confined"
# OpenStore flags these fields for manual review ("found redflagged fields"), so the default profile must not have them
for bad in ("write_path", "read_path", "template", "abstractions", "policy_groups_extra"):
    assert bad not in default, "the default profile has the redflagged field %s" % bad

with open(os.path.join(ROOT, "droidstore-drawer.apparmor")) as f:
    profile = json.load(f)
assert profile.get("template") != "unconfined"

home = "@{HOME}/.local/share/applications/"
rules = profile.get("write_path", [])
assert rules, "the drawer profile needs write_path for the launcher entries"
for rule in rules:
    assert rule.startswith(home) and "**" not in rule and not rule.endswith("/"), rule  # only our own files

sys.path.insert(0, os.path.join(ROOT, "src"))
import tempfile  # noqa: E402

tmp = tempfile.mkdtemp()
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = os.path.join(tmp, var)
import store  # noqa: E402

for pkg in ("com.duckduckgo.mobile.android", "org.example.app", "local.my_app"):
    path = store._shortcut_path(pkg)
    for written in (path, path + ".part"):  # the entry and the temp file it is renamed from
        base = os.path.basename(written)
        assert any(fnmatch.fnmatchcase(base, os.path.basename(r)) for r in rules), (base, rules)

with open(os.path.join(ROOT, "manifest.json")) as f:
    manifest = json.load(f)
assert manifest["hooks"]["droidstore"]["apparmor"] == "droidstore.apparmor"

print("test_apparmor: OK")
