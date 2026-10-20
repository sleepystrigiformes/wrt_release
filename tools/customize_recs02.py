#!/usr/bin/env python3
"""Specialize ZqinKing/wrt_release (latest main) for JDCloud RE-CS-02 + Docker.

Run before upstream build.sh. No hidden downloads, firmware changes, or signing.
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

DEVICE_KEY = 'jdcloud_ipq60xx_immwrt'
CS02 = 'qualcommax_ipq60xx_DEVICE_jdcloud_re-cs-02'
OTHER = (
    'qualcommax_ipq60xx_DEVICE_jdcloud_re-ss-01',
    'qualcommax_ipq60xx_DEVICE_jdcloud_re-cs-07',
    'qualcommax_ipq60xx_DEVICE_redmi_ax5-jdcloud',
)

# Package and LuCI names as they appear in OpenWrt Kconfig. 'emmc' means
# emmc-health GUI, NOT low-level eMMC/MMC drivers needed for booting.
EXCLUDE = (
    'luci-app-istorex', 'istorex', 'luci-app-store', 'istore',
    'luci-app-quickstart', 'quickstart',
    'luci-app-oaf', 'oaf',
    'luci-app-pbr', 'pbr',
    'luci-app-adguardhome', 'adguardhome',
    'luci-app-smartdns', 'smartdns',
    'luci-app-easytier', 'easytier',
    'luci-app-mosdns', 'mosdns',
    'luci-app-cupsd', 'cupsd', 'cups',
    'luci-app-emmc-health',
    'luci-app-wol', 'luci-app-etherwake', 'etherwake',
    # 2026-10-19: VIKINGYFY/immortalwrt main miniupnpd 2.3.11
    # cannot apply feeds/packages/net/miniupnpd/patches/020-descr-filter-fix.patch.
    # Disable the unused UPnP service instead of shipping an unpatched build.
    # This removes automatic UPnP / NAT-PMP / PCP port mapping.
    'luci-app-upnp', 'luci-i18n-upnp-zh-cn',
    'miniupnpd', 'miniupnpd-nftables', 'miniupnpd-iptables',
)
# These are intentionally built into the image, not downloaded afterwards.
INCLUDE = (
    'docker', 'dockerd', 'containerd', 'runc',
    'luci-app-dockerman', 'luci-i18n-dockerman-zh-cn',
)


def config_line(key: str, value: str) -> str:
    return f'# {key} is not set' if value == 'n' else f'{key}={value}'


def rewrite_config(text: str, changes: dict[str, str], label: str) -> str:
    """Replace all old entries to avoid order-dependent duplicate Kconfig."""
    regex = re.compile(r'^\s*(?:#\s*)?(CONFIG_[A-Za-z0-9_+-]+)(?:\s+is not set|\s*=.*)$')
    output: list[str] = []
    marker = '# --- RE-CS-02 custom build: ' + label + ' ---'
    for line in text.splitlines():
        if line == marker:
            continue
        m = regex.match(line)
        if m and m.group(1) in changes:
            continue
        output.append(line)
    while output and not output[-1].strip():
        output.pop()
    while output and not output[0].strip():
        output.pop(0)
    output.extend(['', marker])
    output.extend(config_line(k, v) for k, v in changes.items())
    return '\n'.join(output) + '\n'


def patch(root: Path) -> None:
    assert (root / 'build.sh').is_file(), 'This must point to the cloned ZqinKing/wrt_release root'
    device = root / 'wrt_core/deconfig' / f'{DEVICE_KEY}.config'
    base = root / 'wrt_core/deconfig/compile_base.config'
    ini = root / 'wrt_core/compilecfg' / f'{DEVICE_KEY}.ini'
    for file in (device, base, ini):
        if not file.is_file():
            raise FileNotFoundError(file)

    device_text = device.read_text(encoding='utf-8')
    # Guard against blindly changing a different architecture/device.
    if f'CONFIG_TARGET_DEVICE_{CS02}=y' not in device_text:
        raise RuntimeError('RE-CS-02 target not found in upstream device configuration')

    device_changes = {
        'CONFIG_TARGET_qualcommax': 'y',
        'CONFIG_TARGET_qualcommax_ipq60xx': 'y',
        'CONFIG_TARGET_DEVICE_' + CS02: 'y',
        # Keep original ath11k/QCN9074 firmware and Athena LED support.
        'CONFIG_TARGET_DEVICE_PACKAGES_' + CS02:
            '"ipq-wifi-jdcloud_re-cs-02 ath11k-firmware-qcn9074-ddwrt '
            'kmod-ath11k-pci luci-app-athena-led luci-i18n-athena-led-zh-cn"',
    }
    for other in OTHER:
        device_changes['CONFIG_TARGET_DEVICE_' + other] = 'n'
        device_changes['CONFIG_TARGET_DEVICE_PACKAGES_' + other] = '""'

    for package in EXCLUDE:
        device_changes['CONFIG_PACKAGE_' + package] = 'n'
    for package in INCLUDE:
        device_changes['CONFIG_PACKAGE_' + package] = 'y'
    # Additional direct dependencies (most also selected by docker_deps).
    for package in ('tini', 'fuse-overlayfs', 'kmod-veth', 'kmod-br-netfilter',
                    'kmod-nft-nat', 'iptables-nft', 'nftables'):
        device_changes['CONFIG_PACKAGE_' + package] = 'y'

    # Device config is concatenated BEFORE compile_base.config; drop hardcoded
    # unwanted apps from *both* so subsequent concatenation cannot re-enable.
    base_changes = {'CONFIG_PACKAGE_' + pkg: 'n' for pkg in EXCLUDE}
    device.write_text(rewrite_config(device_text, device_changes, 'device and Docker'), encoding='utf-8')
    base.write_text(rewrite_config(base.read_text(encoding='utf-8'), base_changes,
                                   'remove unwanted apps'), encoding='utf-8')

    # Keep important NSS and docker_deps fragments from the original project.
    ini_text = ini.read_text(encoding='utf-8')
    if not re.search(r'^CONFIG_FRAGMENTS=', ini_text, re.MULTILINE):
        raise RuntimeError('Upstream CONFIG_FRAGMENTS not found; review repository changes')
    ini_text = re.sub(r'^CONFIG_FRAGMENTS=.*$', 'CONFIG_FRAGMENTS=nss,docker_deps,proxy',
                      ini_text, flags=re.MULTILINE)
    ini.write_text(ini_text, encoding='utf-8')

    tools_dir = root / 'tools'
    tools_dir.mkdir(exist_ok=True)
    checker = Path(__file__).with_name('verify_recs02.py')
    shutil.copy2(checker, tools_dir / checker.name)
    # The checker imports this module to share the exact allow/deny package lists.
    shutil.copy2(Path(__file__), tools_dir / 'customize_recs02.py')
    # Inject early fail-fast verification after 'make defconfig' and BEFORE
    # downloading sources / compiling. Do not rely on the order of fragments.
    build_file = root / 'build.sh'
    build_text = build_file.read_text(encoding='utf-8')
    hook = 'python3 "$REPO_ROOT/tools/verify_recs02.py" config "$PWD/.config"'
    if hook not in build_text:
        match = re.findall(r'^make defconfig\s*$', build_text, re.MULTILINE)
        if len(match) != 1:
            raise RuntimeError('Expected one make defconfig in build.sh; upstream changed')
        build_text = re.sub(r'^make defconfig\s*$', 'make defconfig\n' + hook,
                            build_text, count=1, flags=re.MULTILINE)
        build_file.write_text(build_text, encoding='utf-8')
    print('Patched:', device)
    print('Patched:', base)
    print('Preserved default fragments: nss,docker_deps,proxy')
    print('UPnP disabled: miniupnpd/luci-app-upnp excluded to avoid incompatible 020-descr-filter-fix.patch')
    print('Pre-build validator installed: tools/verify_recs02.py')
    print('Upstream sources remain on main; no immutable SHA was pinned.')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('upstream_dir', type=Path, help='ZqinKing/wrt_release checkout')
    args = ap.parse_args()
    patch(args.upstream_dir.resolve())
