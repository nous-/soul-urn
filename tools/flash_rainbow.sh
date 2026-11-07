#!/usr/bin/env bash
# Put the lights-only rainbow test on CIRCUITPY as code.py.
# The urn runtime and sounds stay on the board; tools/flash_urn.sh restores the game.
#
#   bash tools/flash_rainbow.sh
#   bash tools/flash_rainbow.sh --board /Volumes/OTHER
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
board="/Volumes/CIRCUITPY"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --board) board="$2"; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if [[ ! -d "$board" ]]; then
  echo "CIRCUITPY not found at $board. Plug in the Feather (USB data cable) and retry." >&2
  exit 1
fi

stamp="$(date +%Y-%m-%d-%H%M%S)"
backup="$root/firmware/urn/device-backups/$stamp-before-rainbow"
mkdir -p "$backup"
[[ -f "$board/code.py" ]] && cp "$board/code.py" "$backup/"
echo "Board code.py backed up to $backup"

# The board restarts by itself when code.py changes.
COPYFILE_DISABLE=1 cp "$root/firmware/rainbow-test/code.py" "$board/code.py"
rm -f "$board/._code.py"
sync

echo "Rainbow test flashed. Run: bash tools/flash_urn.sh to restore the game."
