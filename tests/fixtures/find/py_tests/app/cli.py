"""Entry point."""

import argparse
import sys

from app.work import checksum


def main() -> None:
    """Print the checksum of ``--n``."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=1000)
    sys.stdout.write(f"{checksum(parser.parse_args().n)}\n")


if __name__ == "__main__":
    main()
