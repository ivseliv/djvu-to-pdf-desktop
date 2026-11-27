"""Collect notices and matching source archives for an Apple Silicon release.

Run with the same uv environment, Python interpreter, Homebrew kegs and
PyInstaller version as build-arm64.sh. Downloads are checked before reuse.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from importlib import metadata
from pathlib import Path
from urllib.parse import urlparse

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / 'build' / 'release-inputs'
# The installed uv interpreter's BUILD marker identifies this exact runtime.
PBS_BUILD = '20260602'
PBS_SHA = '9150a58ba8fbf6e9f0683ec3d66365e9b3d1d8e8'
PBS_DIGEST = 'aefa51b0b942fb2730fb27498ded1f89a99783c4a9a96e26f0a7ee8807e867ff'
BREW_FORMULAS = ('djvulibre', 'qpdf', 'jpeg-turbo', 'libtiff', 'openssl@4', 'webp', 'xz', 'zstd')


def download(url: str, digest: str, filename: str) -> Path:
    path = CACHE / digest / filename
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent) as temp:
            subprocess.run(['curl', '--fail', '--silent', '--show-error', '--location', '--retry', '3',
                            '--proto', '=https', '--output', temp.name, url], check=True)
            if hashlib.file_digest(temp, 'sha256').hexdigest() != digest:
                raise ValueError(f'Download checksum mismatch: {filename}')
            shutil.copyfile(temp.name, path)
    with path.open('rb') as handle:
        if hashlib.file_digest(handle, 'sha256').hexdigest() != digest:
            raise ValueError(f'Cache checksum mismatch: {path}')
    return path


def release_distributions() -> list[metadata.Distribution]:
    pending = ['dpsprep', 'pyinstaller']
    found: dict[str, metadata.Distribution] = {}
    while pending:
        name = canonicalize_name(pending.pop())
        if name in found:
            continue
        dist = metadata.distribution(name)
        found[name] = dist
        pending.extend(dep.name for dep in map(Requirement, dist.requires or ())
                       if not dep.marker or dep.marker.evaluate({'extra': ''}))
    found.pop('dpsprep')
    if 'djvulibre-python' in found:
        raise ValueError('Release still depends on djvulibre-python.')
    return list(found.values())


def copy_notice(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def brew_kegs() -> list[Path]:
    prefix = Path(subprocess.check_output(['brew', '--prefix'], text=True).strip())
    return [(prefix / 'opt' / name).resolve(strict=True) for name in BREW_FORMULAS]


def interpreter_recipe() -> Path:
    if (Path(sys.base_prefix) / 'BUILD').read_text().strip() != PBS_BUILD:
        raise ValueError('Review and pin the python-build-standalone recipe for this interpreter.')
    return download(f'https://codeload.github.com/astral-sh/python-build-standalone/tar.gz/{PBS_SHA}',
                    PBS_DIGEST, f'python-build-standalone-{PBS_BUILD}.tar.gz')


def distribution_notices(destination: Path) -> None:
    for dist in release_distributions():
        name = canonicalize_name(dist.metadata['Name'])
        target = destination / f'{name}-{dist.version}'
        licenses = []
        for entry in dist.files or ():
            parts = Path(entry).parts
            if len(parts) < 2 or not parts[0].endswith('.dist-info'):
                continue
            if parts[1] in {'licenses', 'sboms'} or parts[1].upper().startswith(('LICENSE', 'COPYING', 'NOTICE')):
                licenses.append(entry)
                copy_notice(Path(dist.locate_file(entry)), target.joinpath(*parts[1:]))
        if not licenses and name == 'pdfrw' and dist.version == '0.4':
            copy_notice(download('https://raw.githubusercontent.com/pmaupin/pdfrw/v0.4/LICENSE.txt',
                                 '906f800667ea8f796bc0c0695606e703bb05732bb42b9d1a81af3d24e82a8473',
                                 'pdfrw-LICENSE.txt'), target / 'LICENSE.txt')
        elif not licenses:
            raise ValueError(f'Locate license for {name} {dist.version} before distributing.')


def write_notices(destination: Path) -> None:
    distribution_notices(destination)
    for keg in brew_kegs():
        for entry in keg.iterdir():
            if entry.is_file() and entry.name.upper().startswith(('COPYING', 'LICENSE', 'COPYRIGHT', 'NOTICE', 'PATENT')):
                copy_notice(entry, destination / keg.parent.name / entry.name)
        if keg.parent.name == 'jpeg-turbo':
            copy_notice(keg / 'share/doc/libjpeg-turbo/README.ijg', destination / 'jpeg-turbo/README.ijg')
    with tarfile.open(interpreter_recipe()) as archive:
        for member in archive:
            parts = Path(member.name).parts
            if member.isfile() and len(parts) == 2 and parts[-1].startswith('LICENSE'):
                stream = archive.extractfile(member)
                assert stream is not None
                target = destination / 'python-build-standalone' / parts[-1]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(stream.read())
    copy_notice(Path(sys.base_prefix) / f'lib/python{sys.version_info.major}.{sys.version_info.minor}/LICENSE.txt',
                destination / 'CPython' / 'LICENSE.txt')
    (destination / 'SOURCES.txt').write_text(
        'For the matching project and dependency sources, download the companion sources.tar.gz asset '
        'on the same release page at https://github.com/ivseliv/djvu-to-pdf-desktop/releases.\n'
        'This software is based in part on the work of the Independent JPEG Group. '
        'This product includes software developed by the FreeType Project (www.freetype.org).\n',
        encoding='utf-8',
    )


def python_source(dist: metadata.Distribution, lock: dict) -> Path:
    name = canonicalize_name(dist.metadata['Name'])
    matches = [p for p in lock['package'] if p['name'] == name and p['version'] == dist.version and 'sdist' in p]
    if matches:
        entry = matches[0]['sdist']
        url, digest = entry['url'], entry['hash'].removeprefix('sha256:')
    else:
        # PyInstaller and its build-only dependencies are supplied via uv --with.
        response = subprocess.check_output(['curl', '-fsSL', '--retry', '3',
                                            f'https://pypi.org/pypi/{name}/{dist.version}/json'])
        entry, = [item for item in json.loads(response)['urls'] if item['packagetype'] == 'sdist']
        url, digest = entry['url'], entry['digests']['sha256']
    return download(url, digest, Path(urlparse(url).path).name)


def write_sources(destination: Path) -> None:
    if subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=normal'], cwd=ROOT):
        raise ValueError('Commit the source used to build the release before archiving it.')
    lock = tomllib.loads((ROOT / 'uv.lock').read_text())
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        stage = Path(temp)
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        subprocess.run(['git', 'archive', '--format=tar.gz', '-o', str(stage / f'djvu-to-pdf-{commit}.tar.gz'),
                        commit], cwd=ROOT, check=True)
        for dist in release_distributions():
            archive = python_source(dist, lock)
            copy_notice(archive, stage / 'python-packages' / archive.name)
        for keg in brew_kegs():
            sbom = json.loads((keg / 'sbom.spdx.json').read_text())
            package, = [p for p in sbom['packages'] if p['SPDXID'].startswith('SPDXRef-Archive-')
                        and p['SPDXID'].endswith('-src')]
            digest, = [c['checksumValue'] for c in package['checksums'] if c['algorithm'] == 'SHA256']
            url = package['downloadLocation']
            archive = download(url, digest, Path(urlparse(url).path).name)
            target = stage / 'homebrew' / keg.parent.name
            copy_notice(archive, target / archive.name)
            copy_notice(keg / 'sbom.spdx.json', target / 'sbom.spdx.json')
            shutil.copytree(keg / '.brew', target / 'formula')
        recipe = interpreter_recipe()
        copy_notice(recipe, stage / 'interpreter' / recipe.name)
        with tarfile.open(recipe) as tar:
            source = tar.extractfile(f'python-build-standalone-{PBS_SHA}/pythonbuild/downloads.py')
            assert source is not None
            assignment = next(item for item in ast.parse(source.read()).body if isinstance(item, ast.Assign)
                              and isinstance(item.targets[0], ast.Name) and item.targets[0].id == 'DOWNLOADS')
            versions = ast.literal_eval(assignment.value)
        runtime = versions[f'cpython-{sys.version_info.major}.{sys.version_info.minor}']
        if runtime['version'] != platform.python_version():
            raise ValueError('The interpreter does not match its build recipe.')
        for item in (runtime, versions['tcl'], versions['tk']):
            archive = download(item['url'], item['sha256'], Path(urlparse(item['url']).path).name)
            copy_notice(archive, stage / 'interpreter' / archive.name)
        # The unmodified Pillow 12.3.0 macOS arm64 wheel carries its own
        # liblzma (5.8.3), independently of Homebrew's copy linked to libtiff.
        if metadata.version('Pillow') != '12.3.0':
            raise ValueError('Review the liblzma bundled with the new Pillow wheel.')
        archive = download('https://github.com/tukaani-project/xz/releases/download/v5.8.3/xz-5.8.3.tar.gz',
                           '3d3a1b973af218114f4f889bbaa2f4c037deaae0c8e815eec381c3d546b974a0', 'xz-5.8.3.tar.gz')
        copy_notice(archive, stage / 'pillow-vendored' / archive.name)
        write_notices(stage / 'notices')
        (stage / 'README.txt').write_text(
            f'DjVu to PDF, source commit {commit}. Extract the project archive and see desktop/README.md '
            'and desktop/macos/build-arm64.sh for the macOS build instructions. Exact upstream Python '
            'package sources, the native Homebrew sources and their formulae, and the CPython and Tcl/Tk '
            'sources and interpreter build recipe are in the adjacent directories.\n', encoding='utf-8')
        pending = stage / 'release.tar.gz'
        items = sorted(stage.iterdir())
        with tarfile.open(pending, 'w:gz', compresslevel=1) as output:
            for item in items:
                output.add(item, arcname=item.name)
        pending.replace(destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument('--notices', type=Path)
    output.add_argument('--sources', type=Path)
    args = parser.parse_args()
    if args.notices:
        write_notices(args.notices)
    else:
        write_sources(args.sources.resolve())
