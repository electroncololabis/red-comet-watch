"""Build a relocatable private Python bundle and a per-user Inno Setup installer."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from hguc_watch import DESKTOP_VERSION

ROOT = Path(__file__).resolve().parents[1]


def dependencies():
    pending = list(tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['dependencies'])
    seen, found = set(), {}
    while pending:
        requirement = Requirement(pending.pop())
        key = canonicalize_name(requirement.name), tuple(sorted(requirement.extras))
        if key in seen:
            continue
        seen.add(key)
        dist = metadata.distribution(requirement.name)
        assert dist.version in requirement.specifier, (requirement.name, dist.version)
        found[key[0]] = dist
        for child in dist.requires or []:
            parsed = Requirement(child)
            if not parsed.marker or any(parsed.marker.evaluate({'extra': extra}) for extra in ('', *requirement.extras)):
                pending.append(child)
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path)
    parser.add_argument('--iscc', type=Path, default=shutil.which('ISCC.exe'),
                        help='Inno Setup compiler path when ISCC.exe is not on PATH')
    args = parser.parse_args()
    if args.iscc is None or not args.iscc.is_file():
        parser.error('Install Inno Setup, then add ISCC.exe to PATH or use --iscc.')
    stage = args.stage or ROOT / 'build' / ('windows-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    stage = stage.resolve()
    package = stage / 'package'
    package.mkdir(parents=True)
    runtime = package / 'runtime'
    base = Path(sys.base_prefix)
    for name in ('python.exe', 'pythonw.exe', 'python3.dll', 'python312.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt'):
        runtime.mkdir(exist_ok=True)
        shutil.copy2(base / name, runtime / name)
    for name in ('Lib', 'DLLs', 'tcl'):
        if (base / name).exists():
            shutil.copytree(base / name, runtime / name, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('site-packages', '__pycache__', '*.pyc', 'test', 'tests', 'idlelib', 'ensurepip'))
    site = runtime / 'Lib' / 'site-packages'
    site.mkdir(exist_ok=True)
    lock = {}
    for name, dist in dependencies().items():
        lock[name] = dist.version
        for file in dist.files or []:
            if '..' in file.parts or '__pycache__' in file.parts or file.suffix == '.pyc' or file.name == 'direct_url.json':
                continue
            source = Path(dist.locate_file(file))
            if not source.is_file():
                continue
            target = site / file
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    shutil.copytree(ROOT / 'src' / 'hguc_watch', site / 'hguc_watch', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.prompt.md', '*.prompts.md'))
    # Explicit module paths isolate the installed runtime from machine-wide Python.
    (runtime / 'python312._pth').write_text('Lib\nDLLs\nLib/site-packages\nimport site\n', encoding='utf-8')
    shutil.copy2(ROOT / 'packaging' / '使用说明.txt', package / '使用说明.txt')
    (package / 'dependencies.json').write_text(json.dumps(lock, indent=2), encoding='utf-8')
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:x64',
                    '/reference:System.Windows.Forms.dll',
                    '/out:' + str(package / 'RedCometWatch.exe'),
                    str(ROOT / 'packaging/Launcher.cs')], check=True)
    release = ROOT / 'dist' / f'赤色彗星安装包-{DESKTOP_VERSION}'
    release.mkdir(parents=True, exist_ok=True)
    with (stage / 'compiler.log').open('w', encoding='utf-8') as log:
        subprocess.run([str(args.iscc.resolve()), '/DPackageDir=' + str(package), '/DReleaseDir=' + str(release),
                        '/DAppVersion=' + DESKTOP_VERSION,
                        str(ROOT / 'packaging/setup.iss')], stdout=log, stderr=subprocess.STDOUT, check=True)
    setup = release / f'RedCometWatch-{DESKTOP_VERSION}-Windows-x64-Setup.exe'
    shutil.copy2(ROOT / 'packaging/使用说明.txt', release / '使用说明.txt')
    digest = hashlib.sha256(setup.read_bytes()).hexdigest()
    (release / 'SHA256.txt').write_text(f'{digest}  {setup.name}\n', encoding='utf-8')
    manifest = {'version': DESKTOP_VERSION, 'stage': str(stage), 'package': str(package), 'setup': str(setup), 'sha256': digest,
                'size_mb': round(setup.stat().st_size / 1024 ** 2, 1), 'dependencies': lock}
    (ROOT / 'build/windows-latest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
