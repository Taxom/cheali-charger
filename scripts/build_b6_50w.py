#!/usr/bin/env python3
"""Build only imaxB6-original 50W, check sizes, and export a traceable image."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
TARGET = "imaxB6-original_atmega32"
BASE = "382229dde21bc51701474fbfae0461fbd61eb104"


def run(*args, env=None, capture=False):
    result = subprocess.run(args, cwd=ROOT, env=env, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()
    dirty = bool(run("git", "status", "--porcelain", capture=True))
    if dirty and not args.allow_dirty:
        parser.error("Commit changes first; --allow-dirty is for development builds")
    commit = run("git", "rev-parse", "HEAD", capture=True)
    epoch = run("git", "show", "-s", "--format=%ct", "HEAD", capture=True)
    env = dict(os.environ, SOURCE_DATE_EPOCH=epoch)
    build = ROOT / "build/b6-50w"
    output = ROOT / "dist/b6-50w"
    run("cmake", "-S", str(ROOT), "-B", str(build), "-G", "Unix Makefiles",
        "-DCMAKE_TOOLCHAIN_FILE=" + str(ROOT / "avr-toolchain.cmake"),
        "-Denable-short-names=ON", env=env)
    run("cmake", "--build", str(build), "--target", TARGET,
        "--parallel", str(min(os.cpu_count() or 1, 8)), env=env)
    elf = build / "src/hardware/atmega32/targets/imaxB6-original" / TARGET
    hexfile = Path(str(elf) + ".hex")
    binary = Path(str(elf) + ".bin")
    # avr-objcopy validates Intel HEX record checksums while reading the image.
    # Converting it independently must produce the same flash bytes as the ELF.
    checked = build / "verified-flash.bin"
    run("avr-objcopy", "-I", "ihex", "-O", "binary", str(hexfile), str(checked))
    image = binary.read_bytes()
    if checked.read_bytes() != image or not 4 <= len(image) <= 32768:
        raise ValueError("HEX/BIN mismatch or image outside ATmega32 flash capacity")
    for marker in (b"AGM", b"float charge"):
        if marker not in image:
            raise ValueError(f"Missing Pb/AGM feature marker: {marker!r}")
    sections = {}
    for line in run("avr-size", "-A", str(elf), capture=True).splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[0].startswith("."):
            sections[fields[0]] = int(fields[1])
    flash = sections.get(".text", 0) + sections.get(".data", 0)
    ram = sum(sections.get(key, 0) for key in (".data", ".bss", ".noinit"))
    eeprom = sections.get(".eeprom", 0)
    if flash > 32768 or ram >= 2048 or eeprom > 1024:
        raise ValueError(f"Memory capacity exceeded: {flash=}, {ram=}, {eeprom=}")
    output.mkdir(parents=True, exist_ok=True)
    for old in output.glob("cheali-b6-50w-*"):
        if old.is_file():
            old.unlink()
    stem = "cheali-b6-50w-" + commit[:12] + ("-dirty" if dirty else "")
    artifacts = []
    for suffix, original in ((".elf", elf), (".hex", hexfile), (".bin", binary)):
        destination = output / (stem + suffix)
        shutil.copyfile(original, destination)
        artifacts.append(destination)
    manifest = {
        "source_commit": commit, "source_dirty": dirty,
        "base_repository": "gromdar/cheali-charger", "base_commit": BASE,
        "firmware_version": "2.02", "target": "imaxB6-original",
        "mcu": "atmega32", "f_cpu_hz": 16000000,
        "eeprom_versions": {"calibration": 10, "program_data": 3, "settings": 12},
        "charge_limits": {"watts": 50, "amps": 5},
        "discharge_limits": {"watts": 5, "amps": 1},
        "flash_bytes": flash, "flash_capacity_bytes": 32768,
        "static_ram_bytes": ram, "ram_remaining_for_stack_bytes": 2048 - ram,
        "eeprom_bytes": eeprom,
        "hex_sha256": hashlib.sha256(hexfile.read_bytes()).hexdigest(),
        "avr_gcc": run("avr-gcc", "--version", capture=True).splitlines()[0],
        "source_date_epoch": int(epoch), "hardware_tested": False,
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    artifacts.append(manifest_path)
    (output / "SHA256SUMS").write_text("".join(
        hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n"
        for path in artifacts), encoding="ascii")
    print(json.dumps(manifest, indent=2))
    print("Firmware artifacts:", output)


if __name__ == "__main__":
    main()
