"""User-tunable settings for the Soul Urn firmware."""

# Hardware -----------------------------------------------------------------

# ALITOVE 2 m / 120-pixel WS2812B RGB strip. If cut, enter the actual count.
PIXEL_COUNT = 120
PIXEL_ORDER = "GRB"
PIXEL_BRIGHTNESS = 0.15
AUDIO_ROOT = "/sounds"  # CIRCUITPY/sounds on the board's onboard flash.

# The first LED is at the upper board; the strip wraps down to the bottom.
# Reverse logical pixels so filling still progresses from bottom to top.
REVERSE_PIXEL_CHAIN = True
VIRTUAL_PIXEL_COUNT = 32  # Map the simulator's two 16-pixel regions onto the strip.

# Stand detection -----------------------------------------------------------

# The TMAG5273 reports microtesla. With the sensor mounted consistently in the
# urn, North-up and South-up magnets should produce opposite signs on Z.
MAGNET_Z_INVERTED = False
# Assembled-urn calibration, 2026-11-04: off-stand median -2321.8 uT;
# stand medians +18032.2 / -22443.8 uT, about +/-20,000 uT after correction.
# Keep this fixed across boots so powering on a stand still detects its field.
MAGNET_Z_BASELINE_UT = -2321.8
STAND_ENTER_THRESHOLD_UT = 7000.0
# Placing: the field must exceed the entry threshold for this long.
STAND_ENTER_DEBOUNCE_S = 0.25
# Lifting: release as soon as the field falls to this fraction of the value
# measured while seated (about 20,000 uT here, so ~10,000 uT). A dipole field
# falls with distance cubed, so this is a few millimetres of lift. The old
# absolute 3,000 uT + 0.45 s debounce needed several cm and ~1 s.
STAND_RELEASE_FRACTION = 0.5
STAND_RELEASE_THRESHOLD_UT = 3000.0  # Absolute floor for the release point.
STAND_RELEASE_DEBOUNCE_S = 0.03  # Two consecutive samples spanning 30 ms.
# After a lift, the same pad re-enters only once the field drops away, or comes
# back to this fraction of its previous seated strength (set straight back).
STAND_REENTER_FRACTION = 0.8
BOOT_SENSOR_SETTLE_S = 1.5

# Gameplay timing -----------------------------------------------------------

FILL_TIME_S = 15.777959  # Same charge duration as sim/src/lib/urn-engine.js.
REFILL_DELAY_S = 5.0  # Starts after the delivered voice finishes.
DELIVERY_EXPIRY_S = 180.0
DELIVERY_CELEBRATION_S = 3.2
PICKUP_FLASH_S = 1.2
DROP_FLASH_S = 1.5

READY_CHATTER_DELAY_S = 10.0
CARRYING_CHATTER_DELAY_S = 10.0
STREETS_CHATTER_DELAY_S = 5.0
UNHOMED_CHATTER_DELAY_S = 10.0
CHATTER_JITTER = 0.2

# Audio: one reused MP3 decoder plus a pool of streamed PCM WAV bell voices.
VOICE_VOLUME = 0.8
FILLING_VOLUME = 0.34
CARRYING_JINGLE_VOLUME = 0.72  # Level of the hardest possible strike.
CARRYING_JINGLE_DUCK = 0.33  # Bells are multiplied by this under dialogue.
JINGLE_VOICE_COUNT = 1  # One jingle at a time; a clearly harder strike restarts it.
JINGLE_SOFTEST_LEVEL = 0.15  # Fraction of full volume for the softest strike.
JINGLE_STRIKE_CURVE = 0.7  # <1 lifts soft strikes; 1 is linear in speed.
# Envelope of the jingle samples (~0.25 s of rattle, then a ~0.35 s decay),
# used to skip strikes masked by louder ringing and to steal the quietest voice.
JINGLE_SUSTAIN_S = 0.25
JINGLE_DECAY_S = 0.35
JINGLE_MASKING = 0.6
MOTION_JINGLE_MIN_INTERVAL_S = 0.06

# Carry music: loops while a full urn is carried, quieter under dialogue.
# It plays only if CARRY_MUSIC is True and the file is on the board;
# `tools/flash_urn.sh --no-music` leaves it off.
CARRY_MUSIC = True
CARRY_MUSIC_FILE = "carrying_music/music_idol_carry_lp_141bpm.wav"
CARRY_MUSIC_VOLUME = 0.30
CARRY_MUSIC_DUCK = 0.35

# Bell physics ---------------------------------------------------------------

# One damped clapper inside one bell, driven by gravity-removed acceleration.
# A strike happens when the clapper swings past its gap; its speed sets the
# volume and plays one random jingle. A longer gap needs more motion; a lower
# frequency responds to slower, larger movements.
BELL_FREQUENCIES_HZ = (2.8,)
BELL_GAPS_MM = (4.5,)
BELL_DAMPING = 0.12  # Damping ratio; lower rings on longer after motion stops.
BELL_RESTITUTION = 0.45  # Clapper bounce off the shell.
BELL_STRIKE_SOFTEST_M_S = 0.03  # Slower contact is silent.
BELL_STRIKE_LOUDEST_M_S = 0.30  # Strike speed for full volume.
BELL_RETRIGGER_S = 0.08  # Per bell; contact chatter is one strike.
BELL_SUBSTEP_S = 0.005  # Integration step, independent of loop timing.
BELL_GRAVITY_TAU_S = 0.30  # Slow baseline that removes gravity and bias.
BELL_MAX_GAP_S = 0.25  # A longer stall restarts the clappers at rest.
MP3_BITRATE_BPS = 48000  # Converted files are CBR; used for approximate resume seek.
AUDIO_SAMPLE_RATE = 22050
AUDIO_MIXER_BUFFER_SIZE = 4096

# Motion classification -----------------------------------------------------

# Acceleration values are in G. A real drop requires both FREEFALL_G and a
# subsequent DROP_IMPACT_G inside FREEFALL_WINDOW_S. Merely setting the urn
# down should not satisfy that sequence.
ACCELEROMETER_RANGE_G = 4
FREEFALL_G = 0.35
FREEFALL_WINDOW_S = 0.9
DROP_IMPACT_G = 1.7
DROP_IMPACT_JERK_G = 1.0

# Movement uses rapid vector change and accumulated change from a fixed rest
# reference, without assuming every orientation measures exactly 1 G.
# Both are measured on the ~60 ms smoothed vector (LIFT_SMOOTH_S), so the
# urn's own speaker and sensor noise do not count as movement.
MOTION_JERK_G = 0.10
MOTION_STILL_VECTOR_DELTA_G = 0.10
PICKUP_JERK_G = 0.24
PICKUP_MOTION_S = 0.15
# On the ground, a lift is a smoothed (~60 ms) deviation from the settled pose
# held for a moment. Raw sample-to-sample jerk is not used there: bench noise
# alone reaches 0.19 G per sample, and the urn's own speaker shakes the sensor.
LIFT_SMOOTH_S = 0.06
LIFT_SETTLE_S = 1.0
GROUND_PICKUP_LIFT_G = 0.08
GROUND_PICKUP_HOLD_S = 0.06
GROUND_PICKUP_LIFT_TALKING_G = 0.18  # While the urn speaks; bell strikes ignored.
GROUND_PICKUP_HOLD_TALKING_S = 0.15
GROUND_PICKUP_STRIKE = 0.15  # Bell strike that alone means lifted; a 0.5 G table bump is ~0.06.
SET_DOWN_STILL_S = 2.5
# Require a recent contact pulse before the quiet confirmation window.
# Contact: the reading rises this far above the sensor's own resting 1 G.
SET_DOWN_CONTACT_RISE_G = 0.08
SET_DOWN_CONTACT_JERK_G = 0.14
SET_DOWN_CONTACT_WINDOW_S = 1.0
SET_DOWN_CANCEL_JERK_G = 0.35
# 0.12 s of ~0 G is a ~7 cm fall. A vigorous vertical shake can hold below
# FREEFALL_G for 50-90 ms per swing, so the old 25 ms (3 mm) invented drops
# when someone shook the urn and then held it still.
FREEFALL_MIN_S = 0.12
# Freefall-like shaking is provisional until the impact is followed by quiet.
DROP_CONFIRM_QUIET_S = 0.4
DROP_CONFIRM_WINDOW_S = 1.5
DROP_RECOVERY_GUARD_S = 0.8
DROP_RECOVERY_QUIET_S = 0.2

# Empty/off-stand indication: one brief red flash every five seconds.
UNHOMED_FLASH_INTERVAL_S = 5.0
UNHOMED_FLASH_DURATION_S = 0.3

# Runtime ------------------------------------------------------------------

SENSOR_SAMPLE_S = 0.01  # Loop period target; work time is subtracted.
LIGHT_FRAME_S = 0.04
DEBUG = True
DEBUG_SENSOR_INTERVAL_S = 1.0
