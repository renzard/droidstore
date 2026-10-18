"""Tests the automatic download of a system library (libmaliit-glib) from a fake Ubuntu pool.

    python3 tests/test_libs.py
"""
import http.server
import io
import os
import sys
import tarfile
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

tmp = tempfile.mkdtemp()
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = os.path.join(tmp, var)

arch = "arm64" if os.uname().machine == "aarch64" else "amd64"
pool = os.path.join(tmp, "pool", "universe", "m", "maliit-framework")
os.makedirs(pool)

# fake .deb: ar with debian-binary, control.tar.xz, data.tar.xz
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:xz") as tar:
    real = tarfile.TarInfo("./usr/lib/x/libmaliit-glib.so.2.0.0")
    real.size = 1000
    tar.addfile(real, io.BytesIO(b"x" * 1000))
    link = tarfile.TarInfo("./usr/lib/x/libmaliit-glib.so.2")
    link.type = tarfile.SYMTYPE
    link.linkname = "libmaliit-glib.so.2.0.0"
    tar.addfile(link)
    other = tarfile.TarInfo("./usr/share/doc/readme")
    other.size = 3
    tar.addfile(other, io.BytesIO(b"abc"))


def ar_entry(name, body):
    head = ("%-16s%-12s%-6s%-6s%-8s%-10d`\n" % (name, "0", "0", "0", "100644", len(body))).encode()
    return head + body + (b"\n" if len(body) % 2 else b"")


deb = b"!<arch>\n" + ar_entry("debian-binary", b"2.0\n") + ar_entry("data.tar.xz", buf.getvalue())
with open(os.path.join(pool, "libmaliit-glib2_2.3.0-4build5_%s.deb" % arch), "wb") as f:
    f.write(deb)
with open(os.path.join(pool, "libmaliit-glib2_2.3.0-4build4_%s.deb" % arch), "wb") as f:
    f.write(b"old")


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=tmp, **kw)

    def log_message(self, *a):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["ATL_PORTS_BASE"] = "http://127.0.0.1:%d" % server.server_port

import store  # noqa: E402

# ---- multiple rounds, all from the noble index ----
def make_deb(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return b"!<arch>\n" + ar_entry("debian-binary", b"2.0\n") + ar_entry("data.tar.xz", buf.getvalue())


import gzip  # noqa: E402

pool2 = os.path.join(tmp, "pool", "main", "f", "foo")
os.makedirs(pool2)


def put(name, files):
    with open(os.path.join(pool2, "%s_1.0_%s.deb" % (name, arch)), "wb") as f:
        f.write(make_deb(files))


put("libfoo-bar2", {"./usr/lib/x/libfoo-bar.so.2": b"1" * 50})
put("libva3", {"./usr/lib/x/libVA.so.3": b"2" * 50})
put("libmaliit-glib2", {"./usr/lib/x/libmaliit-glib.so.2": b"3" * 50, "./usr/share/doc/readme": b"abc"})
# the decoy: a newer package that is NOT in the noble index -> must not be picked
os.makedirs(os.path.join(tmp, "pool", "universe", "m", "maliit-framework"), exist_ok=True)
with open(os.path.join(tmp, "pool", "universe", "m", "maliit-framework", "libmaliit-glib2_9.9_%s.deb" % arch), "wb") as f:
    f.write(b"decoy")
for suite, component, packages in (
    ("noble", "main", [("libfoo-bar2", "1.0"), ("libmaliit-glib2", "0.9")]),
    ("noble-updates", "main", [("libva3", "1.0"), ("libmaliit-glib2", "1.0")]),
):
    d = os.path.join(tmp, "dists", suite, component, "binary-%s" % arch)
    os.makedirs(d)
    with gzip.open(os.path.join(d, "Packages.gz"), "wt") as f:
        for name, ver in packages:
            f.write("Package: %s\nVersion: %s\nFilename: pool/main/f/foo/%s_1.0_%s.deb\n\n" % (name, ver, name, arch))
        f.write("Package: unrelated\nVersion: 1\nFilename: pool/x\n\n")

E = store.EXTRA_LIB_DIR


def have(name):
    return os.path.exists(os.path.join(E, name))


def fake_missing(cmd):
    out = []
    if not have("libmaliit-glib.so.2"):
        out.append("libmaliit-glib.so.2")
    elif not have("libfoo-bar.so.2"):
        out.append("libfoo-bar.so.2")  # only shows up after the first round
    elif not have("libVA.so.3"):
        out.append("libVA.so.3")
    return out


store._missing_libs = fake_missing

# old library from a previous download scheme (no marker): must be deleted and re-downloaded correctly
os.makedirs(E, exist_ok=True)
with open(os.path.join(E, "libmaliit-glib.so.2"), "wb") as f:
    f.write(b"stale!!")
assert store._ensure_libs(["/x/atl"]) is None
for name in ("libmaliit-glib.so.2", "libfoo-bar.so.2", "libVA.so.3"):
    assert have(name), (name, os.listdir(E))
assert os.path.getsize(os.path.join(E, "libmaliit-glib.so.2")) == 50
assert not have("readme")
assert E in store._launch_env(["/x/atl"])["LD_LIBRARY_PATH"].split(":")

# unknown library: clean error message, no endless loop
store._missing_libs = lambda cmd: ["libwhatever.so.1"]
msg = store._ensure_libs(["/x/atl"])
assert "libwhatever.so.1" in msg, msg
assert "libx11-6" in store._candidate_packages("libX11.so.6")
assert store._candidate_packages("libOpenCL.so.1") == ["ocl-icd-libopencl1"]
print("test_libs: OK")
