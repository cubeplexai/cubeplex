#!/usr/bin/env python3
"""Insert CubePlex's Neko clipboard closer before </head>.

The closer cannot live in the existing `sed s#...#...#` trim: the panel CSS
uses `#dcddde`, which collides with sed's `#` delimiter and aborts the image
build.
"""

from __future__ import annotations

from pathlib import Path

HTML = Path("/var/www/index.html")
SNIPPET = Path("/tmp/cubeplex-clipboard-close.html")
NEEDLE = "</head>"


def main() -> None:
    html = HTML.read_text(encoding="utf-8")
    snippet = SNIPPET.read_text(encoding="utf-8").strip()
    if NEEDLE not in html:
        raise SystemExit("Neko index.html has no </head>; cannot inject clipboard closer")
    HTML.write_text(html.replace(NEEDLE, snippet + NEEDLE, 1), encoding="utf-8")


if __name__ == "__main__":
    main()
