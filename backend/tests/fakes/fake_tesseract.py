"""Faux Tesseract pour les tests : vérifie les arguments, lit un PNG sur stdin, répond en TSV.

Mode par variable d'environnement FAKE_TESSERACT_MODE : normal | fail | hang.
Répond toujours le spécimen TD3 public de l'ICAO.
"""

import os
import sys
import time

LINES = [
    "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
    "L898902C36UTO7408122F1204159ZE184226B<<<<<10",
]
HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
)


def main() -> int:
    mode = os.environ.get("FAKE_TESSERACT_MODE", "normal")
    args = sys.argv[1:]
    data = sys.stdin.buffer.read()
    if not data.startswith(b"\x89PNG"):
        print("not a png", file=sys.stderr)
        return 2
    expected = ["stdin", "stdout", "-l", "mrz", "--oem", "1", "--psm", "6", "tsv"]
    if any(e not in args for e in expected) or "--tessdata-dir" not in args:
        return 3
    if mode == "fail":
        print("Error opening data file", file=sys.stderr)
        return 1
    if mode == "hang":
        time.sleep(30)
    rows = [HEADER, "1\t1\t0\t0\t0\t0\t0\t0\t1000\t200\t-1\t"]
    for i, line in enumerate(LINES, start=1):
        rows.append(f"4\t1\t1\t1\t{i}\t0\t20\t{i * 60}\t960\t40\t-1\t")
        rows.append(f"5\t1\t1\t1\t{i}\t1\t20\t{i * 60}\t960\t40\t91.5\t{line}")
    sys.stdout.write("\n".join(rows) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
