"""Machine-readable and terminal output."""

import json
from collections.abc import Iterable


def emit(data: object, *, as_json: bool, text: str = "") -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif text:
        print(text)


def table(headers: tuple[str, ...], rows: Iterable[Iterable[object]]) -> str:
    cells = [headers, *(tuple(str(value) for value in row) for row in rows)]
    widths = [max(map(len, column)) for column in zip(*cells, strict=True)]
    return "\n".join(
        "  ".join(value.ljust(width) for value, width in zip(row, widths, strict=True)).rstrip()
        for row in cells
    )
