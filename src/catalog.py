"""Converts the F-Droid index-v2 into a small, flat catalog.

Used both by the app (src/store.py) and by tools/build_catalog.py,
so you can build the catalog on your own server later.
"""


def pick_localized(value, default=""):
    """index-v2 stores texts as {"en-US": "..."}."""
    if not value:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("en-US", "en"):
            if key in value:
                return value[key]
        return next(iter(value.values()), default)
    return default


def pick_locale_key(value):
    """Which locale pick_localized chooses (so screenshot URLs are built correctly)."""
    if isinstance(value, dict) and value:
        for key in ("en-US", "en"):
            if key in value:
                return key
        return next(iter(value))
    return ""


def _screenshots(meta, pkg_id, repo_url, limit=6):
    shots = (meta.get("screenshots") or {}).get("phone") or {}
    key = pick_locale_key(shots)
    urls = []
    for item in (shots.get(key) or [])[:limit]:
        name = item.get("name") if isinstance(item, dict) else item
        if not name:
            continue
        if name.startswith("/"):
            urls.append(repo_url + name)
        else:
            urls.append("%s/%s/%s/phoneScreenshots/%s" % (repo_url, pkg_id, key, name))
    return urls


def build_detail(pkg_id, meta, version_info, repo_url):
    """Data for the details page (not part of the search list, stored separately)."""
    permissions = []
    for perm in version_info.get("permissions") or []:
        name = perm.get("name") if isinstance(perm, dict) else perm
        if name:
            permissions.append(name.replace("android.permission.", ""))
    return {
        "description": pick_localized(meta.get("description"))[:3000],
        "whatsNew": pick_localized(version_info.get("whatsNew"))[:600],
        "categories": list(meta.get("categories") or [])[:6],
        "license": meta.get("license") or "",
        "author": meta.get("authorName") or "",
        "webSite": meta.get("webSite") or "",
        "sourceCode": meta.get("sourceCode") or "",
        "issueTracker": meta.get("issueTracker") or "",
        "added": meta.get("added") or 0,
        "lastUpdated": meta.get("lastUpdated") or 0,
        "screenshots": _screenshots(meta, pkg_id, repo_url),
        "permissions": permissions,
        "antiFeatures": sorted((version_info.get("antiFeatures") or {}).keys()),
        "targetSdk": version_info.get("targetSdk") or 0,
    }


def abi_compatible(native_abis, device_abis):
    """Empty native = pure Java/Kotlin, runs everywhere."""
    if not native_abis:
        return True
    return any(abi in device_abis for abi in native_abis)


def parse_index_v2(index, device_abis, repo_url):
    repo_url = repo_url.rstrip("/")
    catalog = []
    for pkg_id, entry in (index.get("packages") or {}).items():
        meta = entry.get("metadata") or {}
        best = None
        for version in (entry.get("versions") or {}).values():
            manifest = version.get("manifest") or {}
            native = manifest.get("nativecode") or []
            if not abi_compatible(native, device_abis):
                continue
            file_info = version.get("file") or {}
            if not file_info.get("name") or not file_info.get("sha256"):
                continue
            code = manifest.get("versionCode", 0)
            if best is None or code > best["versionCode"]:
                best = {
                    "versionCode": code,
                    "version": manifest.get("versionName", ""),
                    "apk": repo_url + file_info["name"],
                    "sha256": file_info["sha256"],
                    "size": file_info.get("size", 0),
                    "native": bool(native),
                    "minSdk": (manifest.get("usesSdk") or {}).get("minSdkVersion", 0),
                    "_info": {
                        "permissions": manifest.get("usesPermission") or [],
                        "whatsNew": version.get("whatsNew"),
                        "antiFeatures": version.get("antiFeatures") or {},
                        "targetSdk": (manifest.get("usesSdk") or {}).get("targetSdkVersion", 0),
                    },
                }
        if best is None:
            continue

        info = best.pop("_info")
        icon = pick_localized(meta.get("icon"), {})
        icon_name = icon.get("name") if isinstance(icon, dict) else ""
        catalog.append(
            {
                "id": pkg_id,
                "name": pick_localized(meta.get("name")) or pkg_id,
                "summary": pick_localized(meta.get("summary")),
                "icon": (repo_url + icon_name) if icon_name else "",
                **best,
                "detail": build_detail(pkg_id, meta, info, repo_url),
            }
        )
    catalog.sort(key=lambda a: a["name"].lower())
    return catalog
