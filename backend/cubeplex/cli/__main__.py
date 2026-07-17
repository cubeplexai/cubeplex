"""``python -m cubeplex.cli`` entry point.

Delegates to the click group exposed by ``cubeplex.cli.main`` (also wired
to the ``cubeplex`` console script in ``pyproject.toml``).
"""

from cubeplex.cli import main

if __name__ == "__main__":
    main()
