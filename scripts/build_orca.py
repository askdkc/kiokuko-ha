"""Reproduce the bundled writer from the audited upstream archive (no runtime npm)."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / 'src/hermes_kiokuko/orca'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true', help='Fetch the checksum-pinned archive when absent')
    args = parser.parse_args()
    pin = json.loads((ASSETS/'pin.json').read_text())
    cache = ROOT/'.cache'
    source = cache/'orca-source'
    archive = cache/'orca-source.tar.gz'
    if not source.exists():
        if not archive.exists():
            if not args.fetch:
                parser.error('Run with --fetch to obtain the pinned Orca source')
            cache.mkdir(exist_ok=True)
            url = f'https://codeload.github.com/{pin["repository"]}/tar.gz/{pin["commit"]}'
            with urllib.request.urlopen(url, timeout=60) as response:
                archive.write_bytes(response.read())
        if hashlib.sha256(archive.read_bytes()).hexdigest() != pin['archive_sha256']:
            raise RuntimeError('Orca archive checksum mismatch')
        staging = cache/'orca-unpack'
        staging.mkdir()
        try:
            with tarfile.open(archive) as tar:
                tar.extractall(staging, filter='data')
            roots = list(staging.iterdir())
            if len(roots) != 1 or not roots[0].is_dir():
                raise RuntimeError('Invalid Orca archive layout')
            roots[0].rename(source)
        finally:
            shutil.rmtree(staging)
    for name, expected in pin['files'].items():
        if hashlib.sha256((source/name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Orca source mismatch: ' + name)
    subprocess.run(['npm','ci','--ignore-scripts','--no-audit','--no-fund'], cwd=source,check=True)
    subprocess.run(['node','tools/orca/build.mjs'], cwd=ROOT,check=True)
    actual = hashlib.sha256((ASSETS/'bridge.mjs').read_bytes()).hexdigest()
    if actual != pin['bundle_sha256']:
        raise RuntimeError('Bundle differs from the audited pin; review the bridge change before updating its digest')
    print('Pinned Orca bundle reproduced successfully')


if __name__ == '__main__':
    main()
