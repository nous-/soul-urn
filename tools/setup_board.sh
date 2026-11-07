#!/usr/bin/env bash
# One-time board setup: install CircuitPython 10.2.1 on the RP2040 Prop-Maker
# Feather and copy the libraries the urn needs into CIRCUITPY/lib.
#
#   bash tools/setup_board.sh
#
# Safe to re-run: it skips the CircuitPython install if 10.2.1 is already on the
# board, and recopies the libraries.
set -euo pipefail

version="10.2.1"
board_id="adafruit_feather_rp2040_prop_maker"
uf2_name="adafruit-circuitpython-${board_id}-en_US-${version}.uf2"
uf2_url="https://downloads.circuitpython.org/bin/${board_id}/en_US/${uf2_name}"
libs=(adafruit_bus_device adafruit_register adafruit_lis3dh.mpy adafruit_tmag5273.mpy neopixel.mpy adafruit_pixelbuf.mpy)

boot="/Volumes/RPI-RP2"
board="/Volumes/CIRCUITPY"
cache="${TMPDIR:-/tmp}/urn-board-setup"
mkdir -p "$cache"

wait_for() {  # wait_for <path> <seconds>
  for ((i = 0; i < $2 * 2; i++)); do [[ -e "$1" ]] && return 0; sleep 0.5; done
  return 1
}

# 1. CircuitPython
if [[ -f "$board/boot_out.txt" ]] && grep -q "CircuitPython $version" "$board/boot_out.txt"; then
  echo "CircuitPython $version is already on the board."
else
  if [[ ! -f "$cache/$uf2_name" ]]; then
    echo "Downloading CircuitPython $version..."
    curl -fL --progress-bar "$uf2_url" -o "$cache/$uf2_name.part"
    mv "$cache/$uf2_name.part" "$cache/$uf2_name"
  fi
  if [[ ! -d "$boot" ]]; then
    echo
    echo "Put the board in bootloader mode: plug it in over USB (a data cable),"
    echo "hold its BOOT button and tap RESET. Waiting for the RPI-RP2 drive..."
    wait_for "$boot" 300 || { echo "RPI-RP2 never appeared." >&2; exit 1; }
  fi
  echo "Installing CircuitPython $version..."
  cp "$cache/$uf2_name" "$boot/" || true   # the drive disconnects as soon as it's written
  echo "Waiting for the board to restart as CIRCUITPY..."
  sleep 3
  wait_for "$board/boot_out.txt" 120 || { echo "CIRCUITPY never appeared. Unplug and replug the board, then re-run." >&2; exit 1; }
fi

# 2. Libraries, from the latest 10.x bundle
if [[ ! -d "$cache/bundle" ]]; then
  echo "Finding the latest CircuitPython 10.x library bundle..."
  bundle_url=$(curl -fsSL https://api.github.com/repos/adafruit/Adafruit_CircuitPython_Bundle/releases/latest |
    python3 -c 'import json,sys
for a in json.load(sys.stdin)["assets"]:
    if "-10.x-mpy-" in a["name"] and a["name"].endswith(".zip"): print(a["browser_download_url"]); break')
  [[ -n "$bundle_url" ]] || { echo "Couldn't find the 10.x bundle." >&2; exit 1; }
  echo "Downloading $(basename "$bundle_url")..."
  curl -fL --progress-bar "$bundle_url" -o "$cache/bundle.zip"
  mkdir -p "$cache/bundle"
  unzip -q "$cache/bundle.zip" -d "$cache/bundle"
fi
lib_src=$(find "$cache/bundle" -maxdepth 2 -type d -name lib | head -1)

echo "Copying libraries to CIRCUITPY/lib..."
mkdir -p "$board/lib"
for lib in "${libs[@]}"; do
  [[ -e "$lib_src/$lib" ]] || { echo "  $lib not in the bundle (skipped)"; continue; }
  rm -rf "$board/lib/$lib"
  COPYFILE_DISABLE=1 cp -R "$lib_src/$lib" "$board/lib/"
  echo "  $lib"
done
find "$board/lib" -name '._*' -delete 2>/dev/null || true
sync

echo
echo "Board ready. Next: extract the sounds, then run bash tools/flash_urn.sh."
