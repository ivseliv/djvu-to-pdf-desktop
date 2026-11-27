import hashlib
import logging
import os
import pathlib
import shutil
import stat
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager

import click

from dpsprep.workdir import WorkingDirectory


if sys.platform == 'win32':
    import msvcrt
else:
    import fcntl


PERSISTENT_TMP = pathlib.Path('/var/tmp')  # See the initialize_workdir documentation below.
HASHING_BUFFER_SIZE = 64 * 1024
logger = logging.getLogger(__name__)


def get_file_hash(path: os.PathLike | str, settings_key: bytes | None = None) -> str:
    # Keep the original name for an existing GUI-managed job. Its manifest
    # binds the source and render settings before the worker is started.
    h = hashlib.blake2b(digest_size=4 if settings_key is None else 16)
    if settings_key is not None:
        h.update(hashlib.sha256(settings_key).digest())

    with open(path, 'rb') as file:
        data = file.read(HASHING_BUFFER_SIZE)

        while len(data) > 0:
            h.update(data)
            data = file.read(HASHING_BUFFER_SIZE)

    return h.hexdigest()


def _check_owned_directory(path: pathlib.Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise click.ClickException(f'Unsafe working directory: {path}')
    if os.name == 'posix' and (info.st_uid != os.getuid() or info.st_mode & 0o022):
        raise click.ClickException(f'Unsafe working directory permissions: {path}')


@contextmanager
def initialize_workdir(
    src: os.PathLike | str,
    dest: os.PathLike | str | None,
    tmp_root: os.PathLike | str | None,
    delete_existing: bool = False,
    *,
    settings_key: bytes = b'',
) -> Iterator[WorkingDirectory]:
    """Create a working directory and initialize a WorkingDirectory structure.

    Cross-platform temporary directories are difficult to handle. The standard library's documentation
    for tempfile.gettempdir() lists a procedure for determining which directory to use.

    On Linux distributions, the usual /tmp is severely restricted both in capacity and persistence
    because it is usually stored in RAM. Both of these are solved by /var/tmp (see [1]).

    We can largely avoid caring about the persistence issue, however some DjVu documents can produce
    gigabytes of PDF files. So, on Linux, we use /var/tmp whenever possible. The specific access check
    we use was suggested in [2]. In case it fails, perhaps we should try creating dummy files where
    our intermediate results will be stored.

    Finally, we allow explicit overrides via the tmp_root variable. We assume the it is writable.
    Click checks this for us explicitly in the CLI.

    [1] https://www.pathname.com/fhs/pub/fhs-2.3.html#VARTMPTEMPORARYFILESPRESERVEDBETWEE
    [2] https://github.com/kcroker/dpsprep/issues/59
    """
    if tmp_root:
        logger.debug(f'Using custom temporary directory {tmp_root}.')
    elif os.name == 'posix' and os.access(PERSISTENT_TMP, os.W_OK | os.X_OK):
        tmp_root = PERSISTENT_TMP
        logger.debug(f'Using non-ephemeral storage {PERSISTENT_TMP}.')
    else:
        tmp_root = tempfile.gettempdir()
        logger.debug(f'Using default system storage {tmp_root}.')

    src_ = pathlib.Path(src)
    tmp = pathlib.Path(tmp_root)
    desktop_job = os.environ.get('DPSPREP_DESKTOP_JOB') == str(tmp.absolute())
    if desktop_job:
        _check_owned_directory(tmp)
    root_name = 'dpsprep' if desktop_job or os.name != 'posix' else f'dpsprep-{os.getuid()}'
    root = tmp / root_name
    root.mkdir(mode=0o700, exist_ok=True)
    _check_owned_directory(root)
    if os.name == 'posix':
        root.chmod(0o700)
    key = get_file_hash(src_, None if desktop_job else settings_key)
    working = root / key
    workdir = WorkingDirectory(
        src=src_,
        dest=pathlib.Path(src_.with_suffix('.pdf').name if dest is None else dest),
        working=working,
    )

    # Keep the lock inode outside the removable cache. A process exit or
    # crash releases the OS lock. Never unlink a lock while a waiter can
    # still open it: two processes could otherwise lock different inodes.
    lock_path = root / f'.{key}.lock'
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'r+b') as lock:
        try:
            if sys.platform == 'win32':
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise click.ClickException('Another conversion is using this working directory.') from error
        if working.exists() or working.is_symlink():
            _check_owned_directory(working)
            if delete_existing:
                destroy_workdir(workdir)
        working.mkdir(mode=0o700, exist_ok=True)
        logger.info(f'Using working directory {working}.')
        yield workdir


def destroy_workdir(workdir: WorkingDirectory) -> None:
    shutil.rmtree(workdir.working)
