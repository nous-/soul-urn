#!/usr/bin/env python3
"""Extract every sound the urn uses from a Deadlock install into audio/source/.

Reads the game's pak01 VPK archives directly (no other tools needed) and saves
the 75 voice lines, 17 sound effects and the carry music under the names the
firmware and simulator use.

    python3 tools/extract_game_audio.py "/path/to/Deadlock"

The path can be the Deadlock install folder or any folder that contains it.
On Steam it is usually .../steamapps/common/Deadlock.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "audio" / "source"

# Voice lines the urn plays, by category folder. Files keep their game names.
# The game also keeps cut lines under neighbouring numbers (for example the old
# "center temple" ones), so only the lines the game still uses are listed.
VO = "sounds/vo/spirit_jar/spirit_jar_"
VOICE_LINES = {
    "waiting/bridge": ["idol_waiting_on_bridge_%02d" % n for n in (*range(1, 14), 17, 18, 19, 20)],
    "waiting/streets": ["idol_waiting_in_street_" + n for n in
                        ("02", "02_alt_01", "03", "04", "05", "06", "07", "08", "09", "11")],
    "picked_up_urn/bridge": ["idol_picked_up_on_bridge_%02d" % n for n in (2, 6, 7)],
    "picked_up_urn/streets": ["idol_picked_up_on_street_%02d" % n for n in (1, 2, 3, 4, 5, 7, 8, 9)],
    "carrier_is_stalling": ["idol_holder_stalls_%02d" % n for n in range(1, 6)],
    "dropped_urn": ["idol_dropped_%02d" % n for n in range(1, 11)],
    "urn_expires": ["koth_give_up_" + n for n in
                    ("01", "02", "03", "04", "05", "06", "07", "08", "09", "09_alt_01", "10", "10_alt_01")],
    "delivered": ["idol_delivered_%02d" % n for n in range(1, 11)],
}
VOICE = {
    f"{VO}{name}.vsnd_c": f"{folder}/spirit_jar_{name}.mp3"
    for folder, names in VOICE_LINES.items() for name in names
}

# Effects and music, by category folder, with their game names.
EFFECT_FILES = {
    "soul_urn_pickup": ["sounds/world/urn/soul_urn_pickup_%02d" % n for n in (1, 2, 3)]
                       + ["sounds/world/urn/soul_urn_cashin_timer_01",      # filling start
                          "sounds/world/urn/soul_urn_cashin_timer_lp_01"],  # filling loop
    "soul_urn_dropoff": ["sounds/world/urn/soul_urn_cashin_complete_01",
                         "sounds/world/urn/soul_urn_cashin_complete_opponent_01"],
    "carrying_jingles": ["sounds/player/footsteps/shared/urn/footstep_urn_jingle_%02d" % n for n in range(1, 11)],
    "carrying_ambient": ["sounds/music/music_idol_carry_lp_141bpm"],
}
EFFECTS = {
    f"{path}.vsnd_c": f"{folder}/{path.rsplit('/', 1)[1]}.mp3"
    for folder, paths in EFFECT_FILES.items() for path in paths
}
SOUNDS = {**VOICE, **EFFECTS}

def find_vpk(game: Path) -> Path | None:
    for candidate in [game / "game" / "citadel" / "pak01_dir.vpk", *game.glob("**/citadel/pak01_dir.vpk")]:
        if candidate.is_file():
            return candidate
    return None


def read_directory(dir_vpk: Path, wanted: set[str]) -> tuple[dict, int]:
    """Parse a VPK v1/v2 directory; return wanted entries and where embedded data starts."""
    with dir_vpk.open("rb") as stream:
        signature, version, tree_size = struct.unpack("<III", stream.read(12))
        if signature != 0x55AA1234:
            raise SystemExit(f"{dir_vpk} is not a VPK directory file")
        header = 12
        if version == 2:
            stream.read(16)
            header = 28
        tree = stream.read(tree_size)
    position = 0

    def string() -> str:
        nonlocal position
        end = tree.index(b"\0", position)
        value = tree[position:end].decode("utf-8", "replace")
        position = end + 1
        return value

    entries = {}
    while extension := string():
        while (path := string()) != "":
            while name := string():
                _, preload_size, archive, offset, length, _ = struct.unpack_from("<IHHIIH", tree, position)
                position += 18
                preload = tree[position:position + preload_size]
                position += preload_size
                full = (f"{path}/" if path.strip() else "") + f"{name}.{extension}"
                if full in wanted:
                    entries[full] = (archive, offset, length, preload)
    return entries, header + tree_size


def read_entry(dir_vpk: Path, entry: tuple, data_start: int) -> bytes:
    archive, offset, length, preload = entry
    if archive == 0x7FFF:
        source, offset = dir_vpk, data_start + offset
    else:
        source = dir_vpk.with_name(dir_vpk.name.replace("_dir.vpk", f"_{archive:03d}.vpk"))
    with source.open("rb") as stream:
        stream.seek(offset)
        return preload + stream.read(length)


def audio_payload(resource: bytes) -> bytes:
    """A compiled .vsnd_c is a small resource header followed by the audio file."""
    payload = resource[struct.unpack_from("<I", resource)[0]:]
    if not (payload[:3] == b"ID3" or (payload[0] == 0xFF and payload[1] & 0xE0 == 0xE0)):
        raise ValueError("not MP3 audio")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("game", type=Path, help="Deadlock install folder (or any folder containing it)")
    parser.add_argument("--force", action="store_true", help="replace existing files")
    args = parser.parse_args()

    game = args.game.expanduser()
    dir_vpk = find_vpk(game) if game.is_dir() else None
    if dir_vpk is None:
        sys.exit(f"No Deadlock game files (game/citadel/pak01_dir.vpk) found under {game}")
    print(f"Reading {dir_vpk}")

    entries, data_start = read_directory(dir_vpk, set(SOUNDS))
    saved = kept = 0
    missing = []
    for game_path, local in SOUNDS.items():
        destination = SOURCE / local
        if destination.is_file() and not args.force:
            kept += 1
            continue
        if game_path not in entries:
            missing.append(game_path)
            continue
        try:
            audio = audio_payload(read_entry(dir_vpk, entries[game_path], data_start))
        except (ValueError, struct.error) as error:
            missing.append(f"{game_path} ({error})")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(audio)
        saved += 1
    print(f"Extracted {saved} sounds, {kept} already present "
          f"({len(VOICE)} voice lines, {len(EFFECTS)} effects and music).")
    if missing:
        print("Not found in this game version:")
        for path in missing:
            print("  " + path)
        return 1
    print("Next: python3 tools/prepare_audio.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
