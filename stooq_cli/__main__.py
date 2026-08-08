"""Entry point for the stooq command."""

from __future__ import annotations

import sys


def main() -> None:
    if {"-h", "--help"} & set(sys.argv[1:]):
        print(
            "stooq: a quantitative markets terminal for Stooq data.\n\n"
            "Usage:\n"
            "  stooq            launch the terminal\n"
            "  stooq --version  print the version\n\n"
            "Inside the app, press ? for keyboard help."
        )
        return
    if {"-V", "--version"} & set(sys.argv[1:]):
        from . import __version__

        print(f"stooq-cli {__version__}")
        return
    from .app import run

    run()


if __name__ == "__main__":
    main()
