"""Worker lifecycle and publication for a desktop conversion, independent of Tk."""

from __future__ import annotations

import errno
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import __version__
from .core import ConversionSettings, build_command
from .jobs import create_job, lock_job, matches_job, read_manifest, remove_job, write_manifest


if TYPE_CHECKING:
    import queue

PROGRESS_PREFIX = '__DPSPREP_PROGRESS__ '
COMBINE_PROGRESS_PREFIX = '__DPSPREP_COMBINE_PROGRESS__ '
QPDF_PROGRESS_PREFIX = '__DPSPREP_QPDF_PROGRESS__ '
TEXT_PROGRESS_PREFIX = '__DPSPREP_TEXT_PROGRESS__ '
VALIDATION_PROGRESS_PREFIX = '__DPSPREP_VALIDATION_ACTIVE__ '


class ConversionRunner:
    def __init__(self, events: queue.Queue[tuple[str, Any]]) -> None:
        self.events = events
        self.process: subprocess.Popen[str] | None = None
        self.resume_job: Path | None = None
        self.cancel_requested = threading.Event()

    def run(self, settings: ConversionSettings) -> None:
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            job = self.resume_job
            if job is None or not matches_job(job, settings):
                job = create_job(settings, __version__)
            with lock_job(job) as lock_fd:
                self.resume_job = job
                result = self._run_job(job, settings, lock_fd)
        except Exception as error:  # ruff: ignore[blind-except] -- always notify the GUI when its worker exits.
            self.events.put(('log', f'Ошибка конвертации: {error}\n'))
            result = 1
        finally:
            self.process = None
        self.events.put(('complete', result))

    def _run_job(self, job: Path, settings: ConversionSettings, lock_fd: int) -> int:
        manifest = read_manifest(job) or {}
        staged_output = job / 'result.pdf'
        if (manifest.get('state') == 'published' and manifest.get('output') == str(settings.output.absolute())
                and settings.output.is_file()):
            remove_job(job)
            self.resume_job = None
            return 0
        output_ready = manifest.get('state') in {'saving', 'save_failed'} and staged_output.is_file()
        write_manifest(job, {'output': str(settings.output.absolute()), 'overwrite': settings.overwrite})
        if not output_ready:
            self._check_cancelled()

            worker_settings = replace(
                settings,
                output=staged_output,
                overwrite=True,
                tmp_root=job,
                preserve_working=True,
            )
            if sys.platform == 'darwin' and getattr(sys, 'frozen', False):
                # A file chosen in a macOS open dialog can be readable by this
                # GUI process but not by the re-launched PyInstaller worker.
                # Keep both endpoints in the staging directory, then let the
                # GUI save the completed PDF using its dialog-granted access.
                # Creating an arbitrary sibling of the selected destination
                # can block under macOS privacy controls.
                staged_source = job / f'source{settings.source.suffix.lower()}'
                self.events.put(('status', 'Подготавливаю DjVu для конвертации…'))
                self._stage_source(settings.source, staged_source)
                worker_settings = replace(worker_settings, source=staged_source)

            command = build_command(worker_settings)
            write_manifest(job, {'state': 'running'})
            self._check_cancelled()
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                env={**os.environ, 'DPSPREP_GUI_PROGRESS': '1', 'PYTHONIOENCODING': 'utf-8'},
                start_new_session=(os.name == 'posix'),
                pass_fds=(lock_fd,) if os.name == 'posix' else (),
                creationflags=creation_flags,
            )
            try:
                write_manifest(job, {'pid': self.process.pid})
                if self.cancel_requested.is_set():
                    self._terminate_worker()
                return_code = self._read_worker_output()
            finally:
                if self.process.poll() is None:
                    self._terminate_worker()
                    self.process.wait()
            if return_code:
                write_manifest(job, {'state': 'failed', 'return_code': return_code})
                return return_code

        write_manifest(job, {'state': 'saving'})
        self.events.put(('status', 'Сохраняю готовый PDF…'))
        self._check_cancelled()
        self._publish_result(staged_output, settings.output, overwrite=settings.overwrite)
        write_manifest(job, {'state': 'published'})
        remove_job(job)
        self.resume_job = None
        return 0

    def _read_worker_output(self) -> int:
        assert self.process is not None
        assert self.process.stdout is not None
        with self.process.stdout as stream:
            for line in stream:
                self.events.put(_parse_progress(line))
        return self.process.wait()

    def cancel(self) -> None:
        self.cancel_requested.set()
        self._terminate_worker()

    def _check_cancelled(self) -> None:
        if self.cancel_requested.is_set():
            raise InterruptedError('Задание отменено.')

    def _terminate_worker(self) -> None:
        process = self.process
        if process and process.poll() is None:
            if os.name == 'posix':
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
            else:
                subprocess.run(
                    ['taskkill', '/PID', str(process.pid), '/T', '/F'],
                    check=False,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )

    def _copy_with_progress(self, source: Path, destination: Path, phase: str) -> None:
        total = source.stat().st_size
        if phase == 'save':
            free = shutil.disk_usage(destination.parent).free
            if free < total:
                raise OSError(
                    f'Недостаточно места для сохранения: нужно {total / 1024**3:.1f} ГБ, '
                    f'доступно {free / 1024**3:.1f} ГБ. Готовый PDF сохранён во временном задании.',
                )
        completed = 0
        with source.open('rb') as input_file, destination.open('wb') as output_file:
            while block := input_file.read(8 * 1024 * 1024):
                self._check_cancelled()
                output_file.write(block)
                completed += len(block)
                self.events.put(('transfer', (phase, completed, total)))
        shutil.copystat(source, destination)

    def _stage_source(self, source: Path, destination: Path) -> None:
        expected_size = source.stat().st_size
        if destination.exists() and destination.stat().st_size == expected_size:
            return
        self.events.put(('log', 'macOS: копирую исходный DjVu во временную папку.\n'))
        partial = destination.with_suffix(f'{destination.suffix}.partial')
        try:
            partial.unlink(missing_ok=True)
            self._copy_with_progress(source, partial, 'prepare')
            self._check_cancelled()
            partial.replace(destination)
        finally:
            partial.unlink(missing_ok=True)

    def _publish_result(self, source: Path, destination: Path, *, overwrite: bool = True) -> None:
        """Publish by atomic rename when possible, avoiding a second huge copy."""
        total = source.stat().st_size

        def publish(ready: Path) -> None:
            if overwrite:
                ready.replace(destination)
            elif os.name == 'nt':
                ready.rename(destination)  # Windows rename fails if the destination exists.
            else:
                os.link(ready, destination)
                ready.unlink()

        try:
            publish(source)
        except OSError as error:
            if error.errno not in {errno.EXDEV, errno.EACCES, errno.EPERM}:
                raise
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.dpsprep-', suffix='.partial', delete=False) as handle:
                partial = Path(handle.name)
            try:
                self._copy_with_progress(source, partial, 'save')
                self._check_cancelled()
                publish(partial)
                source.unlink()
            finally:
                partial.unlink(missing_ok=True)
        else:
            self.events.put(('transfer', ('save', total, total)))


def _parse_progress(line: str) -> tuple[str, object]:
    pairs = {PROGRESS_PREFIX: 'progress', COMBINE_PROGRESS_PREFIX: 'combine', TEXT_PROGRESS_PREFIX: 'text-progress'}
    singles = {QPDF_PROGRESS_PREFIX: 'qpdf', VALIDATION_PROGRESS_PREFIX: 'validation'}
    prefix, _, payload = line.partition(' ')
    prefix += ' '
    with suppress(ValueError):
        if prefix in pairs:
            completed, total = map(int, payload.split('/'))
            if 0 <= completed <= total and total > 0:
                return pairs[prefix], (completed, total)
        if prefix in singles:
            return singles[prefix], int(payload)
    return 'log', line
