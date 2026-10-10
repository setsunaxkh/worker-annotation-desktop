# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH)
runtime = root / 'runtime' / 'mpv'
if not (runtime / 'libmpv-2.dll').is_file():
    raise RuntimeError('Run bootstrap.cmd / scripts/fetch_runtime.py first')
datas = [(str(root / 'LICENSE'), 'licenses'),
         (str(root / 'THIRD_PARTY_NOTICES.md'), 'licenses'),
         (str(root / 'licenses'), 'licenses/upstream'),
         (str(root / 'runtime' / 'manifest.json'), 'runtime')]
binaries = [(str(runtime / 'libmpv-2.dll'), 'runtime/mpv')]
a = Analysis([str(root / 'scripts' / 'run_app.py')],
             pathex=[str(root / 'src')], binaries=binaries, datas=datas,
             hiddenimports=['mpv'],
             excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
                       'PySide6.QtQml', 'PySide6.QtQuick'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name='WorkerAnnotator', debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               name='WorkerAnnotator')
