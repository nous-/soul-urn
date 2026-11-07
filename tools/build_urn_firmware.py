#!/usr/bin/env python3
"""Build a CircuitPython module so source parsing cannot fragment audio RAM."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import stat
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


DEFAULT_MPY_CROSS = Path('/private/tmp/urn-mpy-cross-10.2.1')
MPY_CROSS_URL = ('https://adafruit-circuit-python.s3.amazonaws.com/bin/mpy-cross/'
                 'macos/mpy-cross-macos-10.2.1-arm64')


def ensure_compiler(path):
    """Use the given compiler, or download Adafruit's macOS 10.2.1 build once."""
    if path.is_file():
        return path
    if path != DEFAULT_MPY_CROSS or platform.system() != 'Darwin':
        raise SystemExit(f'mpy-cross not found at {path}. Download the CircuitPython 10.2.1 '
                         'mpy-cross for your OS and pass --mpy-cross /path/to/it.')
    print('Downloading CircuitPython 10.2.1 mpy-cross...')
    urllib.request.urlretrieve(MPY_CROSS_URL, path)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    subprocess.run(['xattr', '-d', 'com.apple.quarantine', str(path)], capture_output=True)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mpy-cross', type=Path, default=DEFAULT_MPY_CROSS,
                        help=f'CircuitPython 10.2.1 mpy-cross (default: {DEFAULT_MPY_CROSS}, downloaded if missing)')
    args = parser.parse_args()
    args.mpy_cross = ensure_compiler(args.mpy_cross)
    version = subprocess.check_output([str(args.mpy_cross), '--version'], text=True).strip()
    if 'CircuitPython 10.2.1' not in version or 'mpy v6.3' not in version:
        parser.error('Use Adafruit CircuitPython 10.2.1 mpy-cross (mpy v6.3).')
    build = ROOT / 'firmware/urn/build'
    build.mkdir(exist_ok=True)
    subprocess.run([str(args.mpy_cross), '-O3', '-s', 'urn_runtime.py',
                    '-o', str(build / 'urn_runtime.mpy'), str(ROOT / 'firmware/urn/code.py')], check=True)
    (build / 'code.py').write_text('"""Launch the compiled urn firmware."""\nimport urn_runtime\nurn_runtime.main()\n')
    (build / 'settings.py').write_bytes((ROOT / 'firmware/urn/settings.py').read_bytes())
    manifest = {'compiler': version, 'compiler_sha256': sha(args.mpy_cross),
                'source_sha256': sha(ROOT / 'firmware/urn/code.py'),
                'files': {n: sha(build / n) for n in ('code.py', 'settings.py', 'urn_runtime.mpy')}}
    (build / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
