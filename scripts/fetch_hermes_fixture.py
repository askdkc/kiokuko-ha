"""Download the audited host into this checkout; never alter a Hermes profile."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Use an already downloaded archive without network")
    args = parser.parse_args()
    pin = json.loads((ROOT / "tests/hermes_e2e/pin.json").read_text())
    destination = ROOT / ".cache/hermes"
    if destination.exists():
        raise SystemExit(f"Refusing to overwrite {destination}")
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hermes-fixture-", dir=destination.parent) as temporary:
        temporary = Path(temporary)
        archive = args.archive
        if archive is None:
            archive = temporary / "source.tar.gz"
            url = f"https://codeload.github.com/{pin['repository']}/tar.gz/{pin['commit']}"
            with urlopen(url, timeout=60) as source, archive.open("wb") as target:
                shutil.copyfileobj(source, target)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != pin["archive_sha256"]:
            raise SystemExit("Archive checksum mismatch; no fixture installed")
        if not hasattr(tarfile, "data_filter"):
            raise SystemExit("Use Python 3.12+ (or a patched 3.11 with tarfile.data_filter)")
        unpacked = temporary / "unpacked"
        with tarfile.open(archive) as source:
            source.extractall(unpacked, filter="data")
        roots = list(unpacked.iterdir())
        if len(roots) != 1 or not roots[0].is_dir():
            raise SystemExit("Unexpected archive layout")
        root = roots[0]
        for name, expected in pin["files"].items():
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
                raise SystemExit(f"Host source mismatch: {name}")
        root.rename(destination)
    print(f"Audited Hermes fixture ready: {destination}")


if __name__ == "__main__":
    main()
