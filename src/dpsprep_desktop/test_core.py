from pathlib import Path

import pytest

from . import __version__
from .core import ConversionSettings, build_command, default_output_path


def test_desktop_version_is_semantic() -> None:
    assert __version__.count('.') == 2


def test_default_output_path() -> None:
    assert default_output_path(Path('book.DJVU')) == Path('book.pdf')


def test_build_command_keeps_source_text_and_sets_options() -> None:
    command = build_command(
        ConversionSettings(
            source=Path('book.djvu'), output=Path('book.pdf'), image_mode='bitonal',
            quality=65, pool_size=4,
        ),
        executable='python',
    )
    assert command == [
        'python', '-m', 'dpsprep', '--overwrite', '--pool-size', '4', '--mode', 'bitonal',
        '--quality', '65', 'book.djvu', 'book.pdf',
    ]
    assert '--no-text' not in command


def test_build_command_accepts_the_frozen_app_worker_prefix() -> None:
    command = build_command(
        ConversionSettings(source=Path('book.djvu'), output=Path('out.pdf')),
        executable=['DjVu to PDF', '--run-dpsprep'],
    )
    assert command[:4] == ['DjVu to PDF', '--run-dpsprep', '--overwrite', '--mode']


def test_build_command_can_request_the_space_efficient_desktop_pipeline() -> None:
    command = build_command(
        ConversionSettings(source=Path('book.djvu'), output=Path('out.pdf'), stream_output=True),
        executable='python',
    )
    assert '--stream-to-dest' in command


def test_build_command_can_limit_render_resolution() -> None:
    command = build_command(
        ConversionSettings(
            source=Path('book.djvu'), output=Path('out.pdf'), max_dpi=300,
        ),
        executable='python',
    )
    assert '--max-dpi' in command
    assert command[command.index('--max-dpi') + 1] == '300'


def test_invalid_quality_is_rejected() -> None:
    with pytest.raises(ValueError, match='quality'):
        build_command(
            ConversionSettings(source=Path('book.djvu'), output=Path('out.pdf'), quality=101),
        )
