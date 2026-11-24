"""Build a standalone broker bundle as an unprivileged release builder."""

import argparse
import shutil
import tempfile
import zipapp
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    project = here.parents[1]
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "src"
        source.mkdir()
        shutil.copytree(
            project / "broker",
            source / "broker",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        (source / "__main__.py").write_text(
            "from broker.entry import main\nraise SystemExit(main())\n"
        )
        zipapp.create_archive(
            source,
            args.output / "lsab-broker",
            interpreter="/usr/bin/python3 -I",
            compressed=True,
        )
    shutil.copy2(here / "install.sh", args.output / "install.sh")


if __name__ == "__main__":
    main()
