"""Read DjVu through DjVuLibre's standalone ddjvu and djvused programs."""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from PIL import Image

from . import djvu_sexpr


class JobFailed(RuntimeError):  # ruff: ignore[error-suffix-on-exception-name]
    """DjVuLibre failed to read the requested document or page."""


def _tool(name: str) -> str:
    if getattr(sys, 'frozen', False):
        filename = f'{name}.exe' if os.name == 'nt' else name
        for root in (Path(getattr(sys, '_MEIPASS', sys.executable)), Path(sys.executable).parent):
            executable = root / 'djvulibre' / 'bin' / filename
            if executable.is_file():
                return str(executable)
        raise JobFailed(f'The bundled DjVuLibre tool {filename} is missing.')
    executable_path = shutil.which(name)
    if executable_path is None:
        raise JobFailed(f'Install DjVuLibre and make {name} available on PATH.')
    return executable_path


def _run(tool: str, *args: str) -> bytes:
    try:
        process = subprocess.run(
            [_tool(tool), *args],
            capture_output=True,
            check=False,
            # DjVuLibre converts command-line filenames using this locale.
            # Keep metadata machine-readable while accepting Unicode paths.
            env={**os.environ, 'LC_ALL': 'en_US.UTF-8' if sys.platform == 'darwin' else 'C.UTF-8'},
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except OSError as error:
        raise JobFailed(f'Could not start {tool}: {error}') from error
    if process.returncode:
        detail = process.stderr.decode('utf-8', errors='replace').strip()
        raise JobFailed(f'{tool} exited with status {process.returncode}: {detail}')
    return process.stdout


@dataclass(frozen=True, slots=True)
class _PageInfo:
    width: int
    height: int
    rotation: int
    dpi: int
    bitonal: bool


_SIZE = re.compile(rb'^width=(\d+) height=(\d+)(?: rotation=(\d))?[ \t]*\r?$', re.MULTILINE)
_DPI = re.compile(rb'^\s*INFO \[\d+\].*?,\s*(\d+) dpi\b', re.MULTILINE)
_CHUNKS = re.compile(rb'^[ \t]+([A-Za-z0-9]{4}) \[\d+\]', re.MULTILINE)
_BATCH = 16


class Document:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).absolute()
        count = self._query('n').strip()
        if not count.isdigit() or int(count) < 1:
            raise JobFailed(f'DjVuLibre reported no pages in {self.path}.')
        self.pages = tuple(Page(self, i + 1) for i in range(int(count)))
        self._infos: dict[int, _PageInfo] = {}
        self._texts: dict[int, bytes] = {}

    def _query(self, command: str) -> bytes:
        # Input is absolute, script commands are fixed and page indices numeric.
        # -n also makes these reads non-saving in djvused itself.
        return _run('djvused', '-n', '-e', command, str(self.path))

    def _batch(self, number: int, command: str) -> list[tuple[int, re.Match[bytes], bytes]]:
        first = 1 + (number - 1) // _BATCH * _BATCH
        numbers = range(first, min(first + _BATCH, len(self.pages) + 1))
        output = self._query(';'.join(f'select {i}; size; {command}' for i in numbers))
        headers = list(_SIZE.finditer(output))
        if len(headers) != len(numbers):
            raise JobFailed(f'Cannot identify page geometry in {self.path} at page {first}.')
        ends = [h.start() for h in headers[1:]] + [len(output)]
        return [(i, h, output[h.end():end]) for i, h, end in zip(numbers, headers, ends, strict=True)]

    def page_info(self, number: int) -> _PageInfo:
        if number not in self._infos:
            for i, header, dump in self._batch(number, 'dump'):
                dpi = _DPI.search(dump)
                chunks = set(_CHUNKS.findall(dump))
                self._infos[i] = _PageInfo(
                    int(header[1]), int(header[2]), int(header[3] or 0),
                    int(dpi[1]) if dpi else 300,
                    bool(chunks & {b'Sjbz', b'Smmr'}) and not chunks & {b'FGbz', b'FG44', b'BG44', b'BGjp', b'FGjp', b'BG2k', b'FG2k'},
                )
        return self._infos[number]

    def page_text(self, number: int) -> djvu_sexpr.ListExpression:
        if number not in self._texts:
            self._texts = {i: data for i, _, data in self._batch(number, 'print-txt')}
        return djvu_sexpr.parse(self._texts[number])

    @cached_property
    def outline(self) -> djvu_sexpr.ListExpression:
        return djvu_sexpr.parse(self._query('print-outline'))

    @cached_property
    def page_names(self) -> dict[str, int]:
        """Map directory component IDs and unambiguous names to zero-based PDF page indices."""
        ids: dict[str, int] = {}
        aliases: dict[str, int | None] = {}
        for row in self._query('ls').decode('utf-8', errors='replace').splitlines():
            entry = re.fullmatch(r'\s*(\d+)\s+P\s+\d+  (.+)', row)
            if entry is None:
                continue
            index = int(entry[1]) - 1
            if not 0 <= index < len(self.pages):
                continue
            component, *names = re.split(r' [FT]=', entry[2], maxsplit=2)
            ids[component] = index
            for name in names:
                aliases[name] = index if aliases.get(name, index) == index else None
        return {name: i for name, i in aliases.items() if i is not None} | ids


@dataclass(frozen=True, slots=True)
class Page:
    document: Document
    number: int

    @property
    def raw_size(self) -> tuple[int, int]:
        info = self.document.page_info(self.number)
        return info.width, info.height

    @property
    def rotation(self) -> int:
        return self.document.page_info(self.number).rotation

    @property
    def size(self) -> tuple[int, int]:
        width, height = self.raw_size
        return (height, width) if self.rotation % 2 else (width, height)

    @property
    def width(self) -> int:
        return self.size[0]

    @property
    def height(self) -> int:
        return self.size[1]

    @property
    def dpi(self) -> int:
        return self.document.page_info(self.number).dpi

    @property
    def text(self) -> djvu_sexpr.ListExpression:
        return self.document.page_text(self.number)

    def render(self, mode: str, size: tuple[int, int]) -> Image.Image:
        if mode == 'infer':
            mode = 'bitonal' if self.document.page_info(self.number).bitonal else 'rgb'
        fmt = {'bitonal': 'pbm', 'grayscale': 'pgm', 'rgb': 'ppm'}[mode]
        width, height = size
        raster = _run(
            'ddjvu', f'-page={self.number}', f'-format={fmt}', f'-size={width}x{height}',
            '-aspect=no', str(self.document.path), '-',
        )
        image = Image.open(io.BytesIO(raster), formats=['PPM'])
        if image.size != size:
            raise JobFailed(f'DjVu page {self.number} rendered at {image.size}, expected {size}.')
        image.load()
        return image
