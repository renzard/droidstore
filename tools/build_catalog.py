#!/usr/bin/env python3
"""Builds a small catalog.json from the F-Droid index-v2 (runs on a PC/server).

Example:
  python3 tools/build_catalog.py --abi arm64-v8a -o catalog.json
Then upload catalog.json somewhere and put its URL in the ATL Store Settings.
"""
import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from catalog import parse_index_v2  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="https://f-droid.org/repo")
    ap.add_argument("--index", help="local index-v2.json instead of downloading")
    ap.add_argument("--abi", action="append", default=[], help="e.g. arm64-v8a (can be repeated)")
    ap.add_argument("-o", "--output", default="catalog.json")
    args = ap.parse_args()

    if args.index:
        with open(args.index, encoding="utf-8") as f:
            index = json.load(f)
    else:
        with urllib.request.urlopen(args.repo.rstrip("/") + "/index-v2.json", timeout=60) as r:
            index = json.load(r)

    catalog = parse_index_v2(index, args.abi or ["arm64-v8a"], args.repo)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(catalog, f)
    print("%d apps -> %s" % (len(catalog), args.output))


if __name__ == "__main__":
    main()
