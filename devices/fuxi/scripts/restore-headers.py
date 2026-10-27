#!/usr/bin/env python3
"""Restore fetched header trees that a re-cloned xnu work tree does not carry.

unixnu fetch records the EXTERNAL_HEADERS / DriverKit header set in
.cache/sources/*.json with the GitHub contents URL of every file. When
.cache/xnu-work is re-cloned those files are missing and the kernel build fails on
TrustCache/API.h or iokit/DriverKit. This restores whatever is missing, idempotently,
using the token from the environment when the API needs one.
"""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path('/root/UniXNU')
CACHE = ROOT / 'out/fuxi/tree/.cache'
WORK = CACHE / 'xnu-work'
SOURCES = CACHE / 'sources'
TOKEN_FILE = Path('/mnt/c/Users/Jason Wu/dsh/fork-add/tools/.gh-token')


def token():
    if os.environ.get('GITHUB_TOKEN'):
        return os.environ['GITHUB_TOKEN'].strip()
    if TOKEN_FILE.is_file():
        return TOKEN_FILE.read_text().strip()
    return ''


def main():
    if not SOURCES.is_dir():
        print('restore-headers: no source manifests, nothing to do')
        return 0
    bearer = token()
    restored = 0
    for manifest in sorted(SOURCES.glob('*.json')):
        try:
            entries = json.loads(manifest.read_text())
        except Exception:
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path = entry.get('path')
            if not path or (WORK / path).is_file():
                continue
            url = entry.get('url')
            if not url:
                continue
            command = ['curl', '-sSL']
            if bearer:
                command += ['-H', 'Authorization: Bearer ' + bearer]
            command += ['-H', 'Accept: application/vnd.github+json', url]
            result = subprocess.run(command, capture_output=True, text=True)
            try:
                content = base64.b64decode(json.loads(result.stdout)['content'])
            except Exception:
                print('restore-headers: failed for %s' % path)
                continue
            target = WORK / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            restored += 1
    print('restore-headers: %d file(s) restored' % restored)
    return 0


if __name__ == '__main__':
    sys.exit(main())
