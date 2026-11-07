#!/usr/bin/env bash
# Build the urn firmware and copy it, plus the board audio, onto CIRCUITPY.
#
#   bash tools/flash_urn.sh               # with the carry music
#   bash tools/flash_urn.sh --no-music    # without it (and remove it from the board)
#   bash tools/flash_urn.sh --board /Volumes/OTHER
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
board="/Volumes/CIRCUITPY"
music=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-music) music=0 ;;
    --music) music=1 ;;
    --board) board="$2"; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
mpy="/private/tmp/urn-mpy-cross-10.2.1"
sounds="$root/audio/processed"
music_dir="carrying_music"

if [[ ! -d "$board" ]]; then
  echo "CIRCUITPY not found at $board. Plug in the Feather (USB data cable) and retry." >&2
  exit 1
fi
if [[ ! -d "$sounds" ]]; then
  echo "No board audio yet. Run tools/extract_game_audio.py /path/to/Deadlock, then tools/prepare_audio.py." >&2
  exit 1
fi
if [[ $music -eq 1 && ! -d "$sounds/$music_dir" ]]; then
  echo "Carry music not prepared (audio/processed/$music_dir). Use --no-music or add it to audio/source." >&2
  exit 1
fi

if [[ ! -x "$mpy" ]]; then
  echo "Downloading CircuitPython 10.2.1 mpy-cross..."
  curl -fL https://adafruit-circuit-python.s3.amazonaws.com/bin/mpy-cross/macos/mpy-cross-macos-10.2.1-arm64 -o "$mpy"
  chmod +x "$mpy"
  xattr -d com.apple.quarantine "$mpy" 2>/dev/null || true
fi

echo "Building..."
python3 "$root/tools/build_urn_firmware.py" --mpy-cross "$mpy" >/dev/null

stamp="$(date +%Y-%m-%d-%H%M%S)"
backup="$root/firmware/urn/device-backups/$stamp-before-flash"
mkdir -p "$backup"
for f in code.py settings.py urn_runtime.mpy boot_out.txt; do
  [[ -f "$board/$f" ]] && cp "$board/$f" "$backup/"
done
echo "Board backed up to $backup"

# Free space check: what's on the board now counts as reusable.
need_kb=$(du -sk "$sounds" | cut -f1)
[[ $music -eq 0 ]] && need_kb=$((need_kb - $(du -sk "$sounds/$music_dir" 2>/dev/null | cut -f1 || echo 0)))
have_kb=$(df -k "$board" | awk 'NR==2 {print $4}')
[[ -d "$board/sounds" ]] && have_kb=$((have_kb + $(du -sk "$board/sounds" | cut -f1)))
if (( need_kb + 200 > have_kb )); then
  echo "Not enough space on the board: audio needs ${need_kb} KB, ${have_kb} KB available." >&2
  exit 1
fi

# Replace /sounds wholesale: ~4 MB, and it clears any leftovers from older layouts.
# COPYFILE_DISABLE stops macOS from writing "._" metadata files onto the board.
echo "Copying audio ($( [[ $music -eq 1 ]] && echo with || echo without ) carry music, $((need_kb / 1024)) MB)..."
rm -rf "$board/sounds"
mkdir -p "$board/sounds"
(cd "$sounds" && find . -type f ! -name '.*' $( [[ $music -eq 0 ]] && echo "! -path ./$music_dir/*" )) |
  { n=0; while IFS= read -r file; do
    mkdir -p "$board/sounds/$(dirname "$file")"
    COPYFILE_DISABLE=1 cp "$sounds/$file" "$board/sounds/$file"
    n=$((n + 1)); printf '\r  %d files copied (the board writes slowly; ~1-2 minutes)' "$n"
  done; echo; }
find "$board/sounds" -name '._*' -type f -delete 2>/dev/null || true
copied=$(find "$board/sounds" -type f ! -name '.*' | wc -l | tr -d ' ')
echo "Copied $copied audio files."

# Firmware last: the board restarts by itself when code.py changes.
cp "$root/firmware/urn/build/urn_runtime.mpy" "$root/firmware/urn/build/settings.py" "$board/"
cp "$root/firmware/urn/build/code.py" "$board/"
sync

echo "Flashed ($( [[ $music -eq 1 ]] && echo with || echo without ) carry music)."
echo "Watch serial with: screen /dev/tty.usbmodem* 115200"
