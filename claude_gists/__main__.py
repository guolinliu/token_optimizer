"""Enable ``python -m claude_gists`` and serve as the PyInstaller entry point.

Uses an absolute import so it works both as a module (``-m``) and as the
top-level script PyInstaller runs (where there is no parent package).
"""

from claude_gists.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
