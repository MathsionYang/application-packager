# Application Packager

A Windows desktop application for building one Inno Setup bundle from independently installable products:

- Product C is required.
- Products A and B are optional and can be selected independently.
- Multiple VC/.NET or other runtime installers can be added.
- Runtime installer failure or cancellation is logged and skipped so later runtime and product installers continue.
- A custom `.ico` file can be selected for the generated bundle installer.
- Selected third-party DLLs can be hashed, cached into dependency sub-installers, and reused when their hashes are unchanged.

## Repository layout

- `qt_app/` - Qt/PySide6 desktop application, packager backend, tests, and release artifacts.
- `prototype/` - HTML prototype used for UI review.
- `安装包工坊-原型.html` - standalone prototype page.
- `桌面软件统一安装包-需求分析与方案.md` - requirements and packaging design.

## Run from source

Windows with Python 3.13 and Inno Setup 7:

```powershell
cd qt_app
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Select the Inno Setup compiler (`ISCC.exe`), product C, optional products A/B, runtime installers, output directory, and optional installer icon in the application. The default compiler location is `D:\PCAPP\Inno Setup 7\ISCC.exe`.

## Build the standalone application

```powershell
cd qt_app
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

The onedir output is written to `qt_app/dist/DesktopPackager/`. The distributable archive is `qt_app/dist/DesktopPackager-Windows-x64.zip`.

## Verify

```powershell
cd qt_app
python -m pytest -q
```

See [`qt_app/README.md`](qt_app/README.md) for the complete Chinese usage guide, and [`qt_app/DEPLOY_REPORT.md`](qt_app/DEPLOY_REPORT.md) for the latest build and verification evidence.

## Installation behavior

The outer installer displays one installation-root page. After a root is selected, each selected product is installed into its own subdirectory under that root, for example `D:\Apps\Product-C`. Existing products reuse their registered installation path unless the root is changed.

A, B, and C are independent Inno Setup installations. Each product registers its own uninstall entry, writes an uninstaller into its installation directory, and adds an uninstall shortcut to its Start Menu group. Removing one product does not remove the others.

Changing the root during an upgrade changes this installation's destination; it does not migrate or clean up the old directory. For an upgrade in place, keep the existing root. After a root change, the Windows uninstall entry points to the new installation; do not use the old directory's uninstaller.
