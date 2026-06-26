# PyInstaller spec for claude-gists -> a single standalone executable.
#
#   uvx --from pyinstaller pyinstaller claude-gists.spec
#   # or:  make binary
#
# Produces dist/claude-gists (dist/claude-gists.exe on Windows) with no Python
# or pip dependencies required on the target machine.

from PyInstaller.utils.hooks import collect_all

# Textual ships CSS / data files and lazily imports its widgets, so pull the
# whole package (data, binaries, hidden imports) in to avoid runtime ImportErrors.
datas, binaries, hiddenimports = collect_all("textual")

a = Analysis(
    ["claude_gists/__main__.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="claude-gists",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # a TUI needs a terminal
    disable_windowed_traceback=False,
)
