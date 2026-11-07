#!/usr/bin/env python3
"""Convert audio/source/ into the files the urn plays, and report their size.

Output:
  audio/processed/    copy this folder to the Feather as CIRCUITPY/sounds
  sim/static/audio      the same voice lines and effects for the web simulator

Voice lines and effects become mono 22.05 kHz 48 kbit/s MP3 with the game's
~1.4 s of trailing silence trimmed. The ten bell jingles become PCM WAV,
peak-normalized to -7 dBFS so the firmware's strike speed alone sets loudness.
The optional carry music becomes an untrimmed mono WAV loop; whether it goes on
the board is chosen when flashing (tools/flash_urn.sh --no-music).
Hero-specific lines are not used by the urn and are skipped. Needs ffmpeg.

    python3 tools/prepare_audio.py
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "audio" / "source"
BOARD = ROOT / "audio" / "processed"
SIMULATOR = ROOT / "sim" / "static" / "audio"

# Folders the firmware and simulator play, with the expected file counts.
MP3_FOLDERS = {
    "waiting/bridge": 17, "waiting/streets": 10,
    "picked_up_urn/bridge": 3, "picked_up_urn/streets": 8,
    "carrier_is_stalling": 5, "dropped_urn": 10, "urn_expires": 12, "delivered": 10,
    "soul_urn_pickup": 5, "soul_urn_dropoff": 2,
}
JINGLES = "carrying_jingles"
MUSIC_SOURCE = SOURCE / "carrying_ambient" / "music_idol_carry_lp_141bpm.mp3"
MUSIC_BOARD = BOARD / "carrying_music" / "music_idol_carry_lp_141bpm.wav"
JINGLE_PEAK_DB = -7.0
SAMPLE_RATE = "22050"
BITRATE = "48k"
FLASH_BYTES = 7_308_288  # CIRCUITPY file space on the RP2040 Prop-Maker Feather
FIRMWARE_BYTES = 150_000  # generous allowance for firmware, settings and libraries

# Reverse, drop the trailing silence, reverse back; leading silence is kept.
TRIM_TAIL = ("areverse,silenceremove=start_periods=1:start_threshold=-50dB:"
             "start_silence=0.15:detection=rms:window=0.02,areverse")


def ffmpeg(*args: str) -> None:
    # Trimming the tail produces harmless timestamp warnings; show output only on failure.
    result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit("ffmpeg failed on " + args[1] + ":\n" + result.stderr)


def peak_db(path: Path) -> float:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    match = re.search(r"max_volume: (-?[\d.]+) dB", result.stderr)
    return float(match.group(1)) if match else 0.0


def to_mp3(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg("-i", str(source), "-map_metadata", "-1", "-vn", "-af", TRIM_TAIL,
           "-ac", "1", "-ar", SAMPLE_RATE, "-codec:a", "libmp3lame", "-b:a", BITRATE, str(destination))


def to_wav(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    gain = JINGLE_PEAK_DB - peak_db(source)
    ffmpeg("-i", str(source), "-map_metadata", "-1", "-vn", "-ac", "1", "-ar", SAMPLE_RATE,
           "-af", f"volume={gain:.2f}dB", "-codec:a", "pcm_s16le", str(destination))


def remove_stale(root: Path, keep: set[Path]) -> None:
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file() and path not in keep and path.name != "manifest.json":
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def main() -> int:
    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg is required: brew install ffmpeg")

    problems = []
    jobs = []  # (source, relative path) for MP3 outputs
    for folder, expected in MP3_FOLDERS.items():
        files = sorted((SOURCE / folder).glob("*.mp3"))
        if len(files) != expected:
            problems.append(f"audio/source/{folder}: {len(files)} files, expected {expected}")
        jobs += [(path, path.relative_to(SOURCE)) for path in files]
    jingles = sorted((SOURCE / JINGLES).glob("footstep_urn_jingle_*.mp3"))
    if len(jingles) != 10:
        problems.append(f"audio/source/{JINGLES}: {len(jingles)} jingles, expected 10")

    board_files: set[Path] = set()
    simulator_files: set[Path] = set()
    for index, (source, relative) in enumerate(jobs, 1):
        board = BOARD / relative
        to_mp3(source, board)
        board_files.add(board)
        simulator = SIMULATOR / relative
        simulator.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(board, simulator)
        simulator_files.add(simulator)
        if index % 20 == 0:
            print(f"Converted {index}/{len(jobs)} voice lines and effects")
    for source in jingles:
        wav = BOARD / "carrying_jingles_wav" / (source.stem + ".wav")
        to_wav(source, wav)
        board_files.add(wav)
        mp3 = SIMULATOR / JINGLES / source.name  # the browser plays MP3
        to_mp3(source, mp3)
        simulator_files.add(mp3)

    music_bytes = 0
    if MUSIC_SOURCE.is_file():
        # A seamless loop: no trimming, no normalization, just mono 22.05 kHz PCM.
        MUSIC_BOARD.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg("-i", str(MUSIC_SOURCE), "-map_metadata", "-1", "-vn", "-ac", "1",
               "-ar", SAMPLE_RATE, "-codec:a", "pcm_s16le", str(MUSIC_BOARD))
        board_files.add(MUSIC_BOARD)
        music_bytes = -(-MUSIC_BOARD.stat().st_size // 4096) * 4096
    else:
        problems.append("audio/source/carrying_ambient/music_idol_carry_lp_141bpm.mp3 missing "
                        "(optional carry music)")

    remove_stale(BOARD, board_files)
    remove_stale(SIMULATOR, simulator_files)
    manifest = sorted("/audio/" + p.relative_to(SIMULATOR).as_posix() for p in simulator_files)
    (SIMULATOR / "manifest.json").write_text(json.dumps({"files": manifest}, indent=2) + "\n")

    on_flash = sum(-(-path.stat().st_size // 4096) * 4096 for path in board_files)
    available = FLASH_BYTES - FIRMWARE_BYTES
    print(f"\nBoard audio (4 KiB clusters), CIRCUITPY holds {FLASH_BYTES / 1_000_000:.2f} MB:")
    for label, used in (("with carry music", on_flash), ("without music", on_flash - music_bytes)):
        fits = "fits" if used <= available else "DOES NOT FIT"
        print(f"  {label:17} {used / 1_000_000:5.2f} MB  -> {fits}, "
              f"{(available - used) / 1_000_000:.2f} MB left after firmware and libraries")
    print(f"Ready: copy audio/processed to CIRCUITPY/sounds. Simulator updated ({len(manifest)} files).")
    if problems:
        print("\nWarning: missing or unexpected source audio:")
        for problem in problems:
            print("  " + problem)
        print("Run tools/extract_game_audio.py /path/to/Deadlock to extract them from the game.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
