#!/usr/bin/env bash
# Runs INSIDE the build container (see build_atl.sh). Builds the dependencies of ATL and ATL itself
# (from ./atl) and copies everything that was installed into /src/usr.
set -euo pipefail

SRC=/src
OUT=/tmp/out
JOBS="${JOBS:-$(nproc)}"
# Dependencies are cloned from upstream. Pin them to a tag/commit for reproducible builds.
WOLFSSL_REF="${WOLFSSL_REF:-v5.8.2-stable}"
BIONIC_REF="${BIONIC_REF:-master}"
ART_REF="${ART_REF:-master}"
OPENSLES_REF="${OPENSLES_REF:-master}"

mkdir -p "$OUT" /tmp/deps
: > /tmp/build-info.txt

# Installs normally into / (so the next component can find it) and records every file the step added.
record_install() {  # usage: record_install <command...>
    touch /tmp/marker; sleep 1
    "$@"
    find /usr /etc -newer /tmp/marker \( -type f -o -type l \) -print0 2>/dev/null \
        | while IFS= read -r -d '' f; do mkdir -p "$OUT$(dirname "$f")"; cp -a "$f" "$OUT$f"; done
}

clone() {  # usage: clone <url> <ref> <dir>
    git clone --depth 1 --branch "$2" "$1" "/tmp/deps/$3" 2>/dev/null \
        || { git clone "$1" "/tmp/deps/$3" && git -C "/tmp/deps/$3" checkout "$2"; }
    echo "$3 $1 $(git -C /tmp/deps/$3 rev-parse HEAD)" >> /tmp/build-info.txt
}

echo "== wolfSSL (with JNI) =="
clone https://github.com/wolfSSL/wolfssl.git "$WOLFSSL_REF" wolfssl
( cd /tmp/deps/wolfssl && autoreconf -i &&
  ./configure --prefix=/usr --libdir=/usr/lib --enable-shared --disable-opensslall --disable-opensslextra \
      --enable-aescbc-length-checks --enable-curve25519 --enable-ed25519 --enable-ed25519-stream --enable-oldtls \
      --enable-base64encode --enable-tlsx --enable-scrypt --disable-examples --enable-crl --enable-jni \
      --enable-sessioncerts &&
  make -j"$JOBS" && record_install make install )

echo "== bionic_translation =="
clone https://gitlab.com/android_translation_layer/bionic_translation.git "$BIONIC_REF" bionic_translation
( cd /tmp/deps/bionic_translation && meson setup builddir --prefix=/usr --libdir=lib &&
  meson compile -C builddir && record_install meson install -C builddir )

echo "== art_standalone =="
clone https://gitlab.com/android_translation_layer/art_standalone.git "$ART_REF" art_standalone
( cd /tmp/deps/art_standalone && make -j"$JOBS" ____LIBDIR=lib && record_install make ____LIBDIR=lib install )

echo "== libOpenSLES (needed by Spotify and other apps with native audio) =="
clone https://gitlab.com/android_translation_layer/libopensles-standalone.git "$OPENSLES_REF" libopensles
( cd /tmp/deps/libopensles && meson setup builddir --prefix=/usr --libdir=lib &&
  meson compile -C builddir && record_install meson install -C builddir ) || echo "WARNING: libOpenSLES failed to build; apps that load libOpenSLES.so (e.g. Spotify) will not start"

echo "== android_translation_layer (from ./atl) =="
( cd "$SRC/atl" && rm -rf /tmp/atl-build && meson setup /tmp/atl-build --prefix=/usr --libdir=lib &&
  meson compile -C /tmp/atl-build && record_install meson install -C /tmp/atl-build )
echo "atl (local source, ./atl)" >> /tmp/build-info.txt

# Only keep what ATL needs at runtime: no headers, no static libs, no docs
rm -rf "$OUT"/usr/include "$OUT"/usr/share/doc "$OUT"/usr/share/man "$OUT"/usr/lib/pkgconfig "$OUT"/usr/lib/*.a "$OUT"/etc
mkdir -p "$OUT/usr/share"
cp /tmp/build-info.txt "$OUT/usr/share/atl-build-info.txt"

# Make the package self-contained: bundle every library ATL needs that the phone does not have
DEVICE_LIBS=""
[ -f "$SRC/tools/device-libs.txt" ] && DEVICE_LIBS="--device-libs $SRC/tools/device-libs.txt"
python3 "$SRC/tools/bundle_closure.py" "$OUT/usr" $DEVICE_LIBS

rm -rf "$SRC/usr"
cp -a "$OUT/usr" "$SRC/usr"
for l in libOpenSLES.so libmediandk.so; do [ -e "$SRC/usr/lib/$l" ] || echo "WARNING: usr/lib/$l is missing"; done
[ -x "$SRC/usr/bin/android-translation-layer" ] || { echo "ERROR: usr/bin/android-translation-layer is missing"; exit 1; }
echo "Done: $SRC/usr ($(du -sh "$SRC/usr" | cut -f1))"
