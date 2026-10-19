#!/usr/bin/env python3
"""Fail-safe validation of custom RE-CS-02 configuration and emitted firmware."""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

from customize_recs02 import EXCLUDE, INCLUDE, CS02, OTHER

BIN = 'immortalwrt-qualcommax-ipq60xx-jdcloud_re-cs-02-squashfs-factory.bin'
REQUIRED = {'docker', 'dockerd', 'containerd', 'runc', 'luci-app-dockerman'}


def parse_config(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'(CONFIG_[\w+\-]+)=(.*)', line)
        if match:
            values[match.group(1)] = match.group(2)
            continue
        match = re.fullmatch(r'# (CONFIG_[\w+\-]+) is not set', line)
        if match:
            values[match.group(1)] = 'n'
    return values


def ensure_config(config: Path) -> None:
    vals = parse_config(config)
    errors = []
    if vals.get('CONFIG_TARGET_DEVICE_' + CS02) != 'y':
        errors.append('JDCloud RE-CS-02 target is not enabled')
    for target in OTHER:
        if vals.get('CONFIG_TARGET_DEVICE_' + target) in ('y', 'm'):
            errors.append(f'Unexpected extra device target: {target}')
    for pkg in EXCLUDE:
        state = vals.get('CONFIG_PACKAGE_' + pkg, 'n')
        if state in ('y', 'm'):
            errors.append(f'Forbidden package still enabled: {pkg}={state}')
    for pkg in REQUIRED:
        if vals.get('CONFIG_PACKAGE_' + pkg) != 'y':
            errors.append(f'Docker required package missing: {pkg}')
    for pkg in INCLUDE:
        if vals.get('CONFIG_PACKAGE_' + pkg, 'n') != 'y':
            errors.append(f'Expected selected package missing: {pkg}')
    for pkg in ('kmod-veth', 'kmod-br-netfilter', 'kmod-nft-nat', 'fuse-overlayfs'):
        if vals.get('CONFIG_PACKAGE_' + pkg) != 'y':
            errors.append(f'Docker dependency missing: {pkg}')
    if errors:
        raise RuntimeError('Config verification failed:\n - ' + '\n - '.join(errors))
    print(f'OK: device RE-CS-02, {len(EXCLUDE)} exclusions, Docker stack and dependencies selected')


def git_commit(repo: Path) -> str:
    try:
        return subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return 'unknown'


def ensure_output(upstream: Path, out: Path) -> None:
    firmware = upstream / 'firmware'
    if not firmware.is_dir():
        raise RuntimeError('firmware/ directory absent; build has not succeeded')
    matching = list(firmware.glob('*jdcloud_re-cs-02-squashfs-factory.bin'))
    if not matching:
        options = [p.name for p in firmware.glob('*jdcloud_re-cs-02*')]
        raise RuntimeError('Target factory.bin NOT produced! Available files: ' + ', '.join(options))
    if len(matching) != 1 or matching[0].stat().st_size <= 1024 * 1024:
        raise RuntimeError('Bad or ambiguous RE-CS-02 factory.bin result')
    out.mkdir(parents=True, exist_ok=True)
    src = matching[0]
    if src.name != BIN:
        raise RuntimeError(f'Unexpected source filename: {src.name}; expected {BIN}; refusing to disguise a different build')
    dest = out / BIN

    # The manifest, when provided by upstream, is the strongest evidence of
    # actual image package contents. Do not claim a complete check if absent.
    manifests = list(firmware.glob('*jdcloud_re-cs-02*.manifest'))
    if manifests:
        if len(manifests) != 1:
            raise RuntimeError('Multiple RE-CS-02 manifests found')
        manifest = manifests[0]
        packages = {line.split()[0] for line in manifest.read_text(encoding='utf-8').splitlines()
                    if line and not line.startswith('#')}
        missing = sorted(REQUIRED - packages)
        bad = sorted(set(EXCLUDE) & packages)
        if missing or bad:
            raise RuntimeError(f'Output manifest verification failed: missing={missing}, forbidden={bad}')
        check_status = 'manifest-checked'
    else:
        check_status = 'no-per-image-manifest (config-only validation)'
        print('WARNING: target image manifest not present; package validation limited to .config')

    # Never populate output/ until validation has succeeded.
    shutil.copy2(src, dest)
    if manifests:
        shutil.copy2(manifests[0], out / manifests[0].name)
    with dest.open('rb') as image_stream:
        digest = hashlib.file_digest(image_stream, 'sha256').hexdigest()
    (out / (BIN + '.sha256')).write_text(f'{digest}  {BIN}\n', encoding='utf-8')
    buildtree = upstream / 'imm-nss'
    metadata = '\n'.join([
        f'upstream_builder_sha={git_commit(upstream)}',
        f'upstream_immortalwrt_sha={git_commit(buildtree)}',
        f'config_check=passed',
        f'package_check={check_status}',
        f'image_file={BIN}',
        f'image_size_bytes={dest.stat().st_size}',
        f'image_sha256={digest}',
    ]) + '\n'
    (out / 'build_metadata.txt').write_text(metadata, encoding='utf-8')
    print(metadata)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='command', required=True)
    cfg = sub.add_parser('config')
    cfg.add_argument('config', type=Path)
    fw = sub.add_parser('output')
    fw.add_argument('upstream', type=Path)
    fw.add_argument('outdir', type=Path)
    args = ap.parse_args()
    if args.command == 'config':
        ensure_config(args.config)
    else:
        ensure_output(args.upstream, args.outdir)
