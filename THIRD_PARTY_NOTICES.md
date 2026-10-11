# Third-party software and source availability

Worker Annotation Desktop 0.1.5 is licensed GPL-3.0-or-later; see LICENSE.
These notices do not change each dependency's own license.

## libmpv / mpv and its linked libraries

- Binary: `libmpv-2.dll`, baseline Windows x86_64, shinchiro release 20260928,
  mpv revision `e470f8986e` (mpv 0.41 development snapshot).
- This is an **unofficial third-party build**, listed at https://mpv.io/installation/.
- Exact archive URL and SHA256 are pinned in `runtime/manifest.json`.
- Binary release: https://github.com/shinchiro/mpv-winbuild-cmake/releases/tag/20260928
- Build recipes/toolchain at this release:
  https://github.com/shinchiro/mpv-winbuild-cmake/tree/20260928
- mpv source: https://github.com/mpv-player/mpv/tree/e470f8986e
- mpv license/copyright overview: `licenses/mpv-Copyright.txt`.
  This includes GPLv2-or-later components; the application is distributed under
  GPLv3-or-later, with compatible third-party dependencies retaining their terms.
- libmpv includes native FFmpeg codecs and other statically linked libraries;
  no separate ffmpeg executable or system codec installation is required.
  Dependency build definitions are in the pinned build-source `packages/` tree.
  The upstream binary supplier is responsible for providing the corresponding
  source of its complete linked binary. The source references here identify
  upstream sources/build recipes and are not a claim that the app repository
  contains all source code of the supplier's linked libraries.

## PySide6 / Qt / Shiboken6 6.8.3

- Qt for Python: https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.8.3
- Qt sources: https://code.qt.io/cgit/qt/qtbase.git/tag/?h=v6.8.3
- The open-source license option is used. PySide/Qt components are distributed
  under LGPLv3 and/or GPLv3 with applicable Qt exceptions; see `licenses/`.
- Qt DLLs remain separate shared libraries in `_internal/PySide6`; users can
  replace them with a compatible modified build. Application source and build
  instructions are available from this repository. No proprietary Qt license
  is required by this project.

## python-mpv 1.0.8

- Source: https://github.com/jaseg/python-mpv/tree/v1.0.8
- GPLv2-or-later or LGPLv2.1-or-later; selected GPL-compatible use here.
- License texts are copied from the installed distribution into release licenses.

## CPython 3.12, PyInstaller and packaging

- CPython: PSF license, https://github.com/python/cpython/tree/3.12
- PyInstaller 6.22.3: https://github.com/pyinstaller/pyinstaller/tree/v6.22.3
  GPLv2-or-later with the bootloader distribution exception.
- PyInstaller dependencies and their bundled notices are copied at build time.
- Bootstrap-only extractor `7zr.exe` 26.04 is downloaded from official 7-Zip:
  https://github.com/ip7z/7zip/tree/26.04 . Its SHA256 is pinned in the manifest.
  7zr reports public-domain status; upstream 7-Zip license is included for reference.
  The extractor stays in `.cache`, is not installed system-wide and is not
  distributed as part of the portable application.

No third-party runtime binary, local media, labels or personal machine paths are
stored in the Git source repository. Binary release archives contain the runtime
and notices necessary to run the application.
