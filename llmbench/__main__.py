"""Enables `python -m llmbench ...`."""

import sys

from .cli import main

if __name__ == "__main__":
    # Propagate the command's exit code so `python -m llmbench ...` fails the
    # shell (and CI) when a command errors, instead of always exiting 0.
    sys.exit(main())
