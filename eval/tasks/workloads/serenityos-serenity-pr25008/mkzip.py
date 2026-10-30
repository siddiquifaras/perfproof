"""Write a deterministic zip of generated English-like text.

Usage: mkzip.py OUT.zip

The text is 40 MB of words drawn with a Zipf-like weight from a fixed
vocabulary, so the Deflate stream uses many symbols longer than 8 bits.
"""

import random
import sys
import zipfile

SIZE = 40 * 1024 * 1024
VOCABULARY = 6000


def make_words() -> list[str]:
    """Return the fixed vocabulary of pseudo-words."""
    rng = random.Random(1)  # noqa: S311 - a fixed seed, not for secrets
    letters = "etaoinshrdlucmfwypvbgkjqxz"
    out: list[str] = []
    for _ in range(VOCABULARY):
        n = rng.randint(2, 11)
        out.append(
            "".join(
                letters[min(int(rng.expovariate(0.12)), len(letters) - 1)]
                for _ in range(n)
            )
        )
    return out


def main() -> None:
    """Write the zip named by the first argument."""
    rng = random.Random(2)  # noqa: S311 - a fixed seed, not for secrets
    vocab = make_words()
    weights = [1.0 / (i + 1) for i in range(VOCABULARY)]
    parts: list[str] = []
    size = 0
    while size < SIZE:
        chunk = " ".join(rng.choices(vocab, weights, k=64)) + "\n"
        parts.append(chunk)
        size += len(chunk)
    data = "".join(parts).encode()
    with zipfile.ZipFile(
        sys.argv[1], "w", zipfile.ZIP_DEFLATED, compresslevel=6
    ) as z:
        z.writestr("text.txt", data)


if __name__ == "__main__":
    main()
