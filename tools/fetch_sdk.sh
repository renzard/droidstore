#!/usr/bin/env bash
# Downloads the prebuilt ATL SDK from atl-touch and puts it in the project's usr/.
# Usage:  ./tools/fetch_sdk.sh            (or ATL_SDK_TAG=sdk-xxxxxxx ./tools/fetch_sdk.sh)
# Requires: curl, zstd, tar   (sudo apt install zstd)
set -euo pipefail

TAG="${ATL_SDK_TAG:-sdk-3d93da2}"   # the newest that appeared on the releases page, change it if there is a newer one
ARCH="${ARCH:-arm64}"
BASE="https://github.com/NotKit/atl-touch/releases/download/${TAG}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "Downloading ${TAG} (${ARCH})..."
curl -Lf -o "$tmp/sdk.tar.zst" "${BASE}/atl-sdk-${ARCH}.tar.zst"

if curl -Lfs -o "$tmp/sdk.sha256" "${BASE}/atl-sdk-${ARCH}.tar.zst.sha256"; then
    expected="$(awk '{print $1}' "$tmp/sdk.sha256")"
    actual="$(sha256sum "$tmp/sdk.tar.zst" | awk '{print $1}')"
    [ "$expected" = "$actual" ] || { echo "ERROR: sha256 does not match"; exit 1; }
    echo "sha256 OK"
else
    echo ".sha256 file not found, skipping verification"
fi

mkdir "$tmp/x"
zstd -dc "$tmp/sdk.tar.zst" | tar -C "$tmp/x" -x
[ -x "$tmp/x/usr/bin/android-translation-layer" ] || { echo "ERROR: usr/bin/android-translation-layer is missing"; exit 1; }

# OpenStore review rejects this one: it links Qt 5.15, newer than the ubuntu-sdk-20.04 framework allows.
# It is only used for Android WebView, so WebView will not work in the OpenStore build.
find "$tmp/x/usr" -name 'libatl_webview_qt.so' -print -delete

rm -rf "$HERE/usr"
mv "$tmp/x/usr" "$HERE/usr"
echo "Done: $HERE/usr ($(du -sh "$HERE/usr" | cut -f1))"
