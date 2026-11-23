"""Writes versions.json: the app version live on each store.

The changelog (#nouveautes in index.html) hides, on a phone, the versions
its store doesn't have yet. Run hourly by .github/workflows/store-versions.yml.

- Android: the Google Play Developer API, production track, a release
  counting from the start of its rollout ("inProgress") or once completed.
  Needs the Play service account JSON in PLAY_SERVICE_ACCOUNT_JSON (a
  GitHub secret); without it, the Android version already in the file is
  kept.
- iOS: Apple's public lookup (no key), the version on sale.

A store that can't be read keeps its previous value: the page shows every
version for a platform without one.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import jwt
import requests

PACKAGE = "com.sylv42240.watch_list"
APP_STORE_ID = "1641134141"
OUT = Path(__file__).resolve().parent.parent / "versions.json"
VERSION = re.compile(r"\d+(?:\.\d+)+")


def version_key(version):
    return tuple(int(part) for part in version.split("."))


def play_version(service_account):
    info = json.loads(service_account)
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": info["client_email"],
            "scope": "https://www.googleapis.com/auth/androidpublisher",
            "aud": info["token_uri"],
            "iat": now,
            "exp": now + 600,
        },
        info["private_key"],
        algorithm="RS256",
    )
    token = requests.post(
        info["token_uri"],
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
        timeout=30,
    )
    token.raise_for_status()
    headers = {"Authorization": f"Bearer {token.json()['access_token']}"}
    base = (
        "https://androidpublisher.googleapis.com/androidpublisher/v3/"
        f"applications/{PACKAGE}"
    )
    edit = requests.post(f"{base}/edits", headers=headers, timeout=30)
    edit.raise_for_status()
    edit_id = edit.json()["id"]
    try:
        track = requests.get(
            f"{base}/edits/{edit_id}/tracks/production", headers=headers, timeout=30
        )
        track.raise_for_status()
        versions = []
        for release in track.json().get("releases", []):
            if release.get("status") not in ("inProgress", "completed"):
                continue
            # Release names look like "164 (4.1.8)": the version name.
            found = VERSION.findall(release.get("name", ""))
            if found:
                versions.append(found[-1])
        return max(versions, key=version_key) if versions else None
    finally:
        # Read only: the edit is thrown away.
        requests.delete(f"{base}/edits/{edit_id}", headers=headers, timeout=30)


def app_store_version():
    lookup = requests.get(
        "https://itunes.apple.com/lookup",
        params={"id": APP_STORE_ID, "country": "fr"},
        timeout=30,
    )
    lookup.raise_for_status()
    results = lookup.json().get("results", [])
    return results[0].get("version") if results else None


def main():
    current = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    versions = dict(current)

    service_account = os.environ.get("PLAY_SERVICE_ACCOUNT_JSON")
    if service_account:
        try:
            versions["android"] = play_version(service_account) or current.get("android")
        except Exception as error:  # Keep the last known version.
            print(f"Play: {error}", file=sys.stderr)
    else:
        print("Play: no PLAY_SERVICE_ACCOUNT_JSON, Android unchanged", file=sys.stderr)

    try:
        versions["ios"] = app_store_version() or current.get("ios")
    except Exception as error:
        print(f"App Store: {error}", file=sys.stderr)

    print(json.dumps(versions))
    if versions != current:
        OUT.write_text(json.dumps(versions, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
