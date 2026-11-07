"""Standalone lights-only firmware for the RP2040 Prop-Maker Feather.

Copy this file to CIRCUITPY/code.py. Solid third-strip rainbow bands move from
the bottom of the urn to the top, with every LED continuously illuminated.
"""

import time

import board
import digitalio
import neopixel


# Independent from the game firmware and its settings.py.
PIXEL_COUNT = 120
PIXEL_BRIGHTNESS = 0.15
REVERSE_PIXEL_CHAIN = True  # First physical LED is at the top; strip wraps down.
BAND_PIXELS = max(1, PIXEL_COUNT // 3)
PIXELS_PER_SECOND = 30.0
FRAME_SECONDS = 0.04

RAINBOW = (
    (255, 0, 0),      # Red
    (255, 80, 0),     # Orange
    (255, 220, 0),    # Yellow
    (0, 255, 0),      # Green
    (0, 255, 255),    # Cyan
    (0, 0, 255),      # Blue
    (128, 0, 255),    # Violet
    (255, 0, 128),    # Pink, returning to red
)


def render_frame(pixels, elapsed):
    """Move solid bands upward; logical index zero is the bottom of the urn."""
    cycle_pixels = BAND_PIXELS * len(RAINBOW)
    offset = int(elapsed * PIXELS_PER_SECOND) % cycle_pixels
    phase = PIXEL_COUNT - 1 + offset
    for logical_index in range(PIXEL_COUNT):
        color_index = ((phase - logical_index) // BAND_PIXELS) % len(RAINBOW)
        physical_index = (
            PIXEL_COUNT - 1 - logical_index
            if REVERSE_PIXEL_CHAIN else logical_index
        )
        pixels[physical_index] = RAINBOW[color_index]
    pixels.show()
    return offset


def main():
    # Enable the Feather's external NeoPixel power terminal.
    external_power = digitalio.DigitalInOut(board.EXTERNAL_POWER)
    external_power.switch_to_output(value=True)
    time.sleep(0.1)

    pixels = neopixel.NeoPixel(
        board.EXTERNAL_NEOPIXELS,
        PIXEL_COUNT,
        brightness=PIXEL_BRIGHTNESS,
        auto_write=False,
        pixel_order=neopixel.GRB,
        bpp=3,
    )
    print(
        "RAINBOW_MARQUEE_START",
        "pixels=", PIXEL_COUNT,
        "band=", BAND_PIXELS,
        "speed=", PIXELS_PER_SECOND,
        "direction=bottom-to-top",
        "brightness=", PIXEL_BRIGHTNESS,
    )
    started = time.monotonic()
    next_report = started
    frames = 0
    while True:
        now = time.monotonic()
        offset = render_frame(pixels, now - started)
        frames += 1
        if now >= next_report:
            print("RAINBOW_MARQUEE_RUNNING", "frames=", frames, "offset=", offset)
            next_report = now + 5.0
        time.sleep(max(0.0, FRAME_SECONDS - (time.monotonic() - now)))


if __name__ == "__main__":
    main()
