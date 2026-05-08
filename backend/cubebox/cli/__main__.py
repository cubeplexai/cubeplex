"""``python -m cubebox.cli`` entry point.

Delegates to the click group exposed by ``cubebox.cli.main`` (also wired
to the ``cubebox`` console script in ``pyproject.toml``).
"""

from cubebox.cli import main

if __name__ == "__main__":
    main()
