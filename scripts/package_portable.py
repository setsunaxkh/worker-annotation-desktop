"""Package a complete onedir build, licenses and source availability notices."""
from pathlib import Path
import argparse
import hashlib
import shutil
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", type=Path, default=ROOT / "dist/WorkerAnnotator")
    args = parser.parse_args()
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    app = args.app_dir.resolve()
    required = [app / 'WorkerAnnotator.exe', app / '_internal/runtime/mpv/libmpv-2.dll',
                app / '_internal/PySide6/plugins/platforms/qwindows.dll']
    for file in required:
        if not file.is_file():
            raise RuntimeError(f'Missing portable component: {file}')
    for name in ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', '标注工具使用教程.md'):
        shutil.copy2(ROOT / name, app / name)
    shutil.copytree(ROOT / 'licenses', app / 'licenses', dirs_exist_ok=True)
    out = ROOT / 'release'
    out.mkdir(exist_ok=True)
    archive = out / f'WorkerAnnotator-{version}-windows-x64.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(app.rglob('*')):
            if path.is_file():
                z.write(path, Path('WorkerAnnotator') / path.relative_to(app))
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    print(f'Portable ZIP: {archive}\nSHA256: {digest}')


if __name__ == '__main__':
    main()
