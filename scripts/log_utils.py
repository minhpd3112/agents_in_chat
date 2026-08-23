"""Shared quiet logging for AIC helper scripts.

Status goes to stderr (one line, no timestamps) so stdout stays
clean for scripting and piping.
"""

import sys


def info(msg: str) -> None:
    """Progress or status line."""
    print(msg, file=sys.stderr)


def warn(msg: str) -> None:
    """Recoverable problem."""
    print(f"Warning: {msg}", file=sys.stderr)


def error(msg: str) -> None:
    """Operation failed, usually followed by a non-zero exit code."""
    print(f"Error: {msg}", file=sys.stderr)
