"""Collect actual installed distributions' upstream license files for releases."""
from importlib import metadata
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ('PySide6', 'PySide6-Essentials', 'PySide6-Addons', 'shiboken6',
            'python-mpv', 'pyinstaller', 'altgraph', 'packaging', 'pefile',
            'pyinstaller-hooks-contrib', 'pywin32-ctypes', 'setuptools')


def main() -> None:
    dest = ROOT / 'licenses/python-packages'
    for name in PACKAGES:
        dist = metadata.distribution(name)
        for file in dist.files or []:
            parts = file.parts
            if not parts or not parts[0].endswith('.dist-info'):
                continue
            upper = str(file).upper()
            if not any(word in upper for word in ('LICENSE', 'COPYING', 'COPYRIGHT', 'NOTICE')):
                continue
            # Wheels include a commercial-option reference although this app uses OSS terms.
            if 'COMMERCIAL' in upper:
                continue
            src = Path(dist.locate_file(file))
            if src.is_file():
                target = dest / f'{name}-{dist.version}' / Path(*parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
    print(f'Collected dependency notices: {dest}')


if __name__ == '__main__':
    main()
