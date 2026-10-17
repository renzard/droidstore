"""Tests the details page: index-v2 -> details.db -> store.get_details.

    python3 tests/test_details.py
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
index = {
    "packages": {
        "org.rich": {
            "metadata": {
                "name": {"en-US": "Rich App"},
                "summary": {"en-US": "short"},
                "description": {"en-US": "Line one.\nLine two.", "de": "Zeile"},
                "categories": ["Games", "Time"],
                "license": "GPL-3.0-only",
                "authorName": "Jane",
                "webSite": "https://example.org/?a=1&b=2",
                "sourceCode": "https://example.org/src",
                "added": 1600000000000,
                "lastUpdated": 1700000000000,
                "screenshots": {
                    "phone": {"en-US": [{"name": "/org.rich/en-US/phoneScreenshots/1.png"}, {"name": "2.png"}]}
                },
            },
            "versions": {
                "a": {
                    "file": {"name": "/org.rich_3.apk", "sha256": sha, "size": 2097152},
                    "manifest": {
                        "versionName": "3.0",
                        "versionCode": 3,
                        "usesSdk": {"minSdkVersion": 21, "targetSdkVersion": 33},
                        "usesPermission": [{"name": "android.permission.INTERNET"}, {"name": "android.permission.VIBRATE"}],
                    },
                    "whatsNew": {"en-US": "Fixed things"},
                    "antiFeatures": {"Ads": {}, "NonFreeNet": {}, "Weird": {}},
                }
            },
        },
        "org.bare": {
            "metadata": {"name": {"en-US": "Bare App"}},
            "versions": {"a": {"file": {"name": "/org.bare_1.apk", "sha256": sha, "size": 10}, "manifest": {"versionCode": 1}}},
        },
    }
}
with open(os.path.join(repo_dir, "index-v2.json"), "w") as f:
    json.dump(index, f)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=repo_dir, **kw)

    def log_message(self, *a):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
threading.Thread(target=server.serve_forever, daemon=True).start()
repo_url = "http://127.0.0.1:%d" % server.server_port

import store  # noqa: E402

events = []
store.pyotherside.send = lambda *a: events.append(a)
store.set_settings({"repo_url": repo_url})

assert not store.has_catalog()
store._update_catalog()
assert any(e[0] == "catalog_ready" for e in events), events
assert store.has_catalog()

# the search list does not carry the heavy data
cached = json.load(open(store.CATALOG_FILE))
assert all("detail" not in a for a in cached)

d = store.get_details("org.rich")
assert d["name"] == "Rich App" and d["version"] == "3.0" and d["size"] == 2097152
assert d["description"] == "Line one.\nLine two."
assert d["categories"] == ["Games", "Time"] and d["license"] == "GPL-3.0-only" and d["author"] == "Jane"
assert d["webSite"].startswith("https://example.org/")
assert d["screenshots"] == [repo_url + "/org.rich/en-US/phoneScreenshots/1.png", repo_url + "/org.rich/en-US/phoneScreenshots/2.png"][:1] + [
    repo_url + "/org.rich/en-US/phoneScreenshots/2.png"
], d["screenshots"]
assert d["permissions"] == ["INTERNET", "VIBRATE"]
assert d["whatsNew"] == "Fixed things"
assert d["minSdk"] == 21 and d["targetSdk"] == 33
assert d["antiFeatures"] == ["Advertising", "Promotes non-free network services", "Weird"], d["antiFeatures"]
assert d["hasDetail"] is True and d["installed"] is False

bare = store.get_details("org.bare")
assert bare["name"] == "Bare App" and bare["hasDetail"] is False and bare["screenshots"] == []

assert store.get_details("does.not.exist") == {}

# installed app that is no longer in the catalog
store._write_json(store.LIBRARY_FILE, {"gone.app": {"pkgId": "gone.app", "name": "Gone", "version": "1", "apk": "/x"}})
g = store.get_details("gone.app")
assert g["name"] == "Gone" and g["installed"] is True and g["hasDetail"] is False
print("test_details: OK")
