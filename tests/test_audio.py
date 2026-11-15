"""Sound: ATL must find the ALSA pulse plugin, the GTK media backend and the GStreamer plugins that ship in usr/.

    python3 tests/test_audio.py

The last check is end to end: it builds a stand-in for the ALSA pulse plugin and asks the real libasound to open
"default" with the configuration the store writes (skipped when gcc or libasound is not available).
"""
import ctypes.util
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

tmp = tempfile.mkdtemp()
for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = os.path.join(tmp, var)
os.environ["APP_ID"] = "droidstore.example_droidstore_0.1.0"
for var in ("ALSA_CONFIG_PATH", "GTK_PATH", "GST_PLUGIN_SYSTEM_PATH_1_0", "PULSE_SERVER"):
    os.environ.pop(var, None)

import store  # noqa: E402


def touch(usr, rel):
    path = os.path.join(usr, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").close()
    return path


# 1. An SDK without the sound parts (the prebuilt atl-touch one) is started exactly as before
bare = os.path.join(tmp, "bare-usr")
os.makedirs(os.path.join(bare, "lib"))
assert store._audio_env(bare, {}) == {}, store._audio_env(bare, {})
text = "\n".join(store._audio_diag({"XDG_RUNTIME_DIR": os.path.join(tmp, "run")}))
assert "sound/ALSA" in text and "NOT configured" in text, text
assert "sound/GTK media backend" in text and "NOT found" in text, text
assert "sound/GStreamer: no plugins" in text, text
assert "socket NOT found" in text, text

# 2. A full usr/: every variable points inside usr/
usr = os.path.join(tmp, "click", "usr")
pcm = touch(usr, "lib/alsa-lib/" + store.ALSA_PCM_PULSE)
ctl = touch(usr, "lib/alsa-lib/" + store.ALSA_CTL_PULSE)
touch(usr, "lib/gtk-4.0/4.0.0/media/" + store.GTK_MEDIA_BACKEND)
touch(usr, "lib/gstreamer-1.0/libgstplayback.so")
touch(usr, "lib/gstreamer-1.0/" + store.GST_SINK_PLUGIN)
touch(usr, "lib/gstreamer-1.0/readme.txt")  # not a plugin

env = store._audio_env(usr, {"GTK_PATH": "/opt/gtk-extra"})
assert env["ALSA_CONFIG_PATH"] == store.ALSA_CONF, env
assert env["GTK_PATH"] == os.path.join(usr, "lib", "gtk-4.0") + ":/opt/gtk-extra", env["GTK_PATH"]
assert env["GST_PLUGIN_SYSTEM_PATH_1_0"] == os.path.join(usr, "lib", "gstreamer-1.0"), env
assert env["GST_REGISTRY_FORK"] == "no", env
assert env["GST_DEBUG"] == "2", env  # GStreamer errors/warnings reach the log
assert store._plugin_files(os.path.join(usr, "lib", "gstreamer-1.0")) == ["libgstplayback.so", "libgstpulseaudio.so"]

conf = open(store.ALSA_CONF, encoding="utf-8").read()
assert 'pcm_type.pulse { lib "%s" }' % pcm in conf, conf
assert 'ctl_type.pulse { lib "%s" }' % ctl in conf, conf
assert "pcm.!default { type pulse }" in conf and "ctl.!default { type pulse }" in conf, conf

# the config is only rewritten when its text changes
before = os.stat(store.ALSA_CONF).st_mtime_ns
assert store._audio_env(usr, {})["ALSA_CONFIG_PATH"] == store.ALSA_CONF
assert os.stat(store.ALSA_CONF).st_mtime_ns == before

# without the ctl plugin only the PCM is configured
os.remove(ctl)
assert "ctl" not in store._alsa_conf_text(os.path.dirname(pcm)), store._alsa_conf_text(os.path.dirname(pcm))
touch(usr, "lib/alsa-lib/" + store.ALSA_CTL_PULSE)

# a path that would break the config syntax is refused instead of writing a broken config
assert store._write_alsa_conf('/tmp/a"b') is None
assert store._write_alsa_conf("/tmp/a\nb") is None

# 3. The log header says what ATL gets
run = os.path.join(tmp, "run")
os.makedirs(os.path.join(run, "pulse"))
touch(run, "pulse/native")
full_env = dict(env, XDG_RUNTIME_DIR=run)
text = "\n".join(store._audio_diag(full_env))
assert "default -> PulseAudio plugin" in text, text
assert "server socket found" in text, text
assert "GTK media backend (MediaPlayer, SoundPool): %s" % os.path.join(usr, "lib/gtk-4.0/4.0.0/media", store.GTK_MEDIA_BACKEND) in text, text
assert "2 plugins" in text and "PulseAudio output plugin: yes" in text, text
shutil.rmtree(os.path.join(usr, "lib", "gstreamer-1.0"))
os.makedirs(os.path.join(usr, "lib", "gstreamer-1.0"))
touch(usr, "lib/gstreamer-1.0/libgstplayback.so")
assert "libgstpulseaudio.so is missing" in "\n".join(store._audio_diag(dict(full_env))), "missing sink plugin not reported"
touch(usr, "lib/gstreamer-1.0/" + store.GST_SINK_PLUGIN)

# 4. _launch_env uses it for the bundled ATL, and only for it
atl = touch(usr, "bin/" + store.ATL_NAME)
os.chmod(atl, 0o755)
store.sdk_root = lambda: usr
launch_env = store._launch_env([atl])
assert launch_env["ALSA_CONFIG_PATH"] == store.ALSA_CONF, launch_env.get("ALSA_CONFIG_PATH")
assert launch_env["GST_PLUGIN_SYSTEM_PATH_1_0"].startswith(usr), launch_env
assert os.path.join(usr, "lib", "alsa-lib") in launch_env["LD_LIBRARY_PATH"].split(":"), launch_env["LD_LIBRARY_PATH"]
other = store._launch_env(["/somewhere/else/atl"])
assert "ALSA_CONFIG_PATH" not in other and "GTK_PATH" not in other, "a user-supplied ATL must not be changed"

# 5. A log with sound errors gets a note next to the cause of the crash (it is never THE cause)
crash = ['E/ART java.lang.UnsatisfiedLinkError: dlopen failed: library "libfoo.so" not found',
         'ERROR: Can\'t open "default" PCM device. No such file or directory']
summary = store._cause_text(crash)
assert summary.startswith("Cause (native)"), summary
assert "Sound: ATL could not open the sound device" in summary, summary
assert "Sound:" not in store._cause_text(crash[:1]), "no sound note without a sound error"
assert store._SOUND_RE.search("ALSA lib pcm.c:2664:(snd_pcm_open_noupdate) Unknown PCM default")
assert store._SOUND_RE.search("ALSA lib pulse.c:242:(pulse_connect) PulseAudio: Unable to connect: Connection refused")
assert not store._SOUND_RE.search("PCM state: PREPARED")

# 6. End to end with the real libasound: "default" (PCM) and the mixer must reach the plugin named in the config
cc = shutil.which("gcc") or shutil.which("cc")
if cc and ctypes.util.find_library("asound"):
    work = tempfile.mkdtemp()
    src_pcm = os.path.join(work, "pcm.c")
    src_ctl = os.path.join(work, "ctl.c")
    with open(src_pcm, "w") as f:
        f.write('char __snd_pcm_pulse_open_dlsym_pcm_001[] = "";\n'
                "int _snd_pcm_pulse_open(void **p, const char *n, void *r, void *c, int s, int m) { return -4242; }\n")
    with open(src_ctl, "w") as f:
        f.write('char __snd_ctl_pulse_open_dlsym_control_001[] = "";\n'
                "int _snd_ctl_pulse_open(void **p, const char *n, void *r, void *c, int m) { return -4343; }\n")
    e2e = os.path.join(work, "usr")
    os.makedirs(os.path.join(e2e, "lib", "alsa-lib"))
    for src, name in ((src_pcm, store.ALSA_PCM_PULSE), (src_ctl, store.ALSA_CTL_PULSE)):
        subprocess.run([cc, "-shared", "-fPIC", "-o", os.path.join(e2e, "lib", "alsa-lib", name), src], check=True)
    conf_path = store._audio_env(e2e, {})["ALSA_CONFIG_PATH"]
    probe = (
        "import ctypes\n"
        "lib = ctypes.CDLL('libasound.so.2')\n"
        "h = ctypes.c_void_p()\n"
        "print(lib.snd_pcm_open(ctypes.byref(h), b'default', 0, 0), lib.snd_ctl_open(ctypes.byref(h), b'default', 0))\n"
    )
    out = subprocess.run([sys.executable, "-c", probe], env=dict(os.environ, ALSA_CONFIG_PATH=conf_path),
                         capture_output=True, text=True, timeout=60)
    assert out.stdout.split() == ["-4242", "-4343"], (out.stdout, out.stderr)
    shutil.rmtree(work, ignore_errors=True)
    print("  end-to-end ALSA routing: checked with the real libasound")
else:
    print("  end-to-end ALSA routing: skipped (needs gcc and libasound)")

print("test_audio: OK")
