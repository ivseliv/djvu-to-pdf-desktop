"""Pure conversion settings used by the desktop interface.

Keeping command construction outside the UI makes it straightforward to test and
ensures that the GUI uses only dpsprep's public CLI surface.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


IMAGE_MODES = ('infer', 'bitonal', 'grayscale', 'rgb')


@dataclass(frozen=True)
class ConversionSettings:
    """The subset of dpsprep CLI options exposed by the first desktop release."""

    source: Path
    output: Path
    preserve_text: bool = True
    preserve_bookmarks: bool = True
    image_mode: str = 'infer'
    quality: int = 50
    max_dpi: int | None = None
    pool_size: int | None = None
    overwrite: bool = True
    stream_output: bool = False
    tmp_root: Path | None = None
    preserve_working: bool = False

    def validate(self) -> None:
        if self.source.suffix.lower() not in {'.djvu', '.djv'}:
            raise ValueError('Choose a .djvu or .djv source file.')
        if self.output.suffix.lower() != '.pdf':
            raise ValueError('The destination file must have a .pdf extension.')
        if self.image_mode not in IMAGE_MODES:
            raise ValueError(f'Unknown image mode: {self.image_mode}')
        if not 1 <= self.quality <= 100:
            raise ValueError('Image quality must be between 1 and 100.')
        if self.max_dpi is not None and not 72 <= self.max_dpi <= 1200:
            raise ValueError('Maximum image resolution must be between 72 and 1200 DPI.')
        if self.pool_size is not None and self.pool_size < 1:
            raise ValueError('The number of worker processes must be at least 1.')


def default_output_path(source: Path) -> Path:
    """Return the adjacent PDF name used when the user selects a DjVu source."""
    return source.with_suffix('.pdf')


def _command_prefix(executable: str | Sequence[str] | None) -> list[str]:
    """Locate the converter executable in source and frozen-app modes."""
    if executable is not None:
        return [executable, '-m', 'dpsprep'] if isinstance(executable, str) else list(executable)
    if getattr(sys, 'frozen', False):
        # PyInstaller turns sys.executable into the GUI app itself. The special
        # argument is handled before Tk is initialized in app.main().
        return [sys.executable, '--run-dpsprep']
    return [sys.executable, '-m', 'dpsprep']


def build_command(
    settings: ConversionSettings, executable: str | Sequence[str] | None = None,
) -> list[str]:
    """Build a public-CLI invocation for one conversion.

    `dpsprep` transfers the source text layer and document outlines/bookmarks
    when present. There is no disabling CLI flag for bookmarks, so
    `preserve_bookmarks` is presentation-only for now.
    """
    settings.validate()
    command = _command_prefix(executable)

    if settings.overwrite:
        command.append('--overwrite')
    if settings.stream_output:
        command.append('--stream-to-dest')
    if settings.tmp_root is not None:
        command.extend(('--tmp', str(settings.tmp_root)))
    if settings.preserve_working:
        command.append('--preserve-working')
    if settings.pool_size is not None:
        command.extend(('--pool-size', str(settings.pool_size)))
    if settings.max_dpi is not None:
        command.extend(('--max-dpi', str(settings.max_dpi)))

    command.extend(('--mode', settings.image_mode, '--quality', str(settings.quality)))

    if not settings.preserve_text:
        command.append('--no-text')

    command.extend((str(settings.source), str(settings.output)))
    return command
