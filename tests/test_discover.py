"""Tests the Discover tab, categories and search history.

    python3 tests/test_discover.py
"""
import hashlib
import http.server
import json
import os
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

tmp = tempfile.mkdtemp()
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = os.path.join(tmp, var)

repo_dir = os.path.join(tmp, "repo")
os.makedirs(repo_dir)
sha = hashlib.sha256(b"apk").hexdigest()


def pkg(name, added, updated, cats):
    return {
        "metadata": {"name": {"en-US": name}, "categories": cats, "added": added, "lastUpdated": updated},
        "versions": {"a": {"file": {"name": "/%s.apk" % name, "sha256": sha, "size": 10},
                           "manifest": {"versionName": "1", "versionCode": 1}}},
    }


index = {"packages": {
    "org.a": pkg("Alpha", 100, 500, ["Games", "Time"]),
    "org.b": pkg("Beta", 300, 200, ["Games"]),
    "org.c": pkg("Gamma", 200, 400, ["Tools"]),
    "org.d": {"metadata": {"name": {"en-US": "Delta"}},
              "versions": {"a": {"file": {"name": "/d.apk", "sha256": sha, "size": 1}, "manifest": {"versionCode": 1}}}},
}}
with open(os.path.join(repo_dir, "index-v2.json"), "w") as f:
    json.dump(index, f)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=repo_dir, **kw)

    def log_message(self, *a):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
threading.Thread(target=server.serve_forever, daemon=True).start()

import store  # noqa: E402

events = []
store.pyotherside.send = lambda *a: events.append(a)
store.set_settings({"repo_url": "http://127.0.0.1:%d" % server.server_port})
store._update_catalog()
assert store.has_catalog(), events

d = store.discover(limit=2)
assert [a["pkgId"] for a in d["new"]] == ["org.b", "org.c"], d["new"]
assert [a["pkgId"] for a in d["updated"]] == ["org.a", "org.c"], d["updated"]
assert d["total"] == 4
assert d["categories"][0] == {"name": "Games", "count": 2}, d["categories"]
assert {c["name"] for c in d["categories"]} == {"Games", "Time", "Tools"}

assert [a["pkgId"] for a in store.search("", False, 10, 0, "Games")] == ["org.a", "org.b"]
assert [a["pkgId"] for a in store.search("alp", False, 10, 0, "Games")] == ["org.a"]
assert store.search("", False, 10, 0, "Nope") == []
assert [a["pkgId"] for a in store.search("", False, 10, 0, "", "new")][:3] == ["org.b", "org.c", "org.a"]
assert [a["pkgId"] for a in store.search("", False, 10, 0, "", "updated")][:3] == ["org.a", "org.c", "org.b"]
assert len(store.search("")) == 4  # without a category nothing changes

assert store.get_search_history() == []
store.add_search_history("keyboard")
store.add_search_history("a")  # too short
store.add_search_history("chromium")
store.add_search_history("Keyboard")  # same query: moves to the top, no duplicate
assert store.get_search_history() == ["Keyboard", "chromium"], store.get_search_history()
for i in range(15):
    store.add_search_history("query %d" % i)
assert len(store.get_search_history()) == store.HISTORY_MAX
assert store.clear_search_history() == [] and store.get_search_history() == []
print("test_discover: OK")
