"""CircuitPython firmware for the physical Deadlock Soul Urn.

Behavioral source of truth: sim/src/lib/urn-engine.js and +page.svelte.
Target: Adafruit RP2040 Prop-Maker Feather with a WS2812B RGB strip,
TMAG5273 stand sensor, onboard LIS3DH accelerometer, I2S speaker, and flash audio.
"""

import gc
import math
import os
import random
import time

import audiobusio
import audiocore
import audiomixer
import audiomp3
import board
import digitalio

import settings


GRAVITY_M_S2 = 9.80665


# Stand identities. North and South describe the magnet face pointing upward.
STAND_NONE = 0
STAND_NORTH = 1
STAND_SOUTH = 2

# Per-power-on journey phases. No journey is loaded from or written to NVM.
PHASE_EMPTY = 0
PHASE_READY = 1
PHASE_TRANSIT = 2

# Runtime presentation states.
STATE_UNHOMED = 0
STATE_FILLING = 1
STATE_READY = 2
STATE_CARRIED = 3
STATE_RESTING_WRONG = 4
STATE_WRONG_SOURCE = 5
STATE_FILL_PAUSED = 6
STATE_REFILL_DELAY = 7
STATE_POWERED_OFF = 8

POSITION_CARRIED = 3
POSITION_GROUND = 4

STATE_NAMES = (
    "unhomed",
    "filling",
    "ready",
    "carried",
    "resting_wrong",
    "wrong_source",
    "fill_paused",
    "refill_delay",
    "powered_off",
)
STAND_NAMES = ("none", "north", "south")


def opposite_stand(stand):
    if stand == STAND_NORTH:
        return STAND_SOUTH
    if stand == STAND_SOUTH:
        return STAND_NORTH
    return STAND_NONE


class StandDetector:
    """Fast, relative stand release with debounced placement.

    Lifting is detected relative to the field measured while seated: once the
    signed field falls to STAND_RELEASE_FRACTION of that seated value for a
    couple of samples, the urn is off the pad. A dipole field falls with the
    cube of distance, so this happens within a few millimetres of lift instead
    of waiting for the field's long tail to reach an absolute threshold.

    Placement still needs STAND_ENTER_THRESHOLD_UT for STAND_ENTER_DEBOUNCE_S.
    After a release, the same pad must either fall away below the entry
    threshold or return close to its previous seated strength before it can be
    entered again, so hovering just above a pad cannot chatter on and off.
    An unavailable sensor always means off-pad.
    """

    RETRY_INTERVAL_S = 5.0

    def __init__(self, sensor=None, reconnect=None):
        self.sensor = sensor
        self.reconnect = reconnect
        self.next_reconnect_at = 0.0
        self.current = STAND_NONE
        self.candidate = STAND_NONE
        self.candidate_since = time.monotonic()
        self.z_ut = 0.0
        self.seated_ut = 0.0
        self.last_error_at = -100.0
        self._release_since = None
        self._release_samples = 0
        self._armed = [True, True, True]
        self._last_seated = [0.0, 0.0, 0.0]

    @property
    def available(self):
        return self.sensor is not None

    @staticmethod
    def _toward(stand, z_ut):
        return z_ut if stand == STAND_NORTH else -z_ut

    def _off_stand(self, now):
        changed = self.current != STAND_NONE
        if changed:
            self._last_seated[self.current] = self.seated_ut
        self.current = STAND_NONE
        self.candidate = STAND_NONE
        self.candidate_since = now
        self.z_ut = 0.0
        self.seated_ut = 0.0
        self._release_since = None
        self._release_samples = 0
        return changed

    def _unavailable(self, now, error):
        self.sensor = None
        self.next_reconnect_at = now + self.RETRY_INTERVAL_S
        if now - self.last_error_at >= self.RETRY_INTERVAL_S:
            print("TMAG5273 unavailable; assuming off-stand:", error)
            self.last_error_at = now
        return self._off_stand(now)

    def release_threshold_ut(self):
        return max(
            settings.STAND_RELEASE_THRESHOLD_UT,
            self.seated_ut * settings.STAND_RELEASE_FRACTION,
        )

    def _update_seated(self, now, z_ut):
        stand = self.current
        toward = self._toward(stand, z_ut)
        if toward > self.seated_ut:
            # Settling deeper onto the magnet raises the reference at once.
            self.seated_ut = toward
        elif toward > self.seated_ut * 0.85:
            # Follow slow drift while seated, but never follow a lift.
            self.seated_ut += (toward - self.seated_ut) * 0.02
        if toward < self.release_threshold_ut():
            if self._release_since is None:
                self._release_since = now
                self._release_samples = 0
            self._release_samples += 1
            self.candidate = STAND_NONE
            if (self._release_samples >= 2
                    and now - self._release_since + .000001 >= settings.STAND_RELEASE_DEBOUNCE_S):
                self._last_seated[stand] = self.seated_ut
                self._armed[stand] = False
                self.current = STAND_NONE
                self.candidate_since = now
                self.seated_ut = 0.0
                self._release_since = None
                return True
            return False
        self._release_since = None
        self.candidate = stand
        return False

    def _entry_candidate(self, z_ut):
        enter = settings.STAND_ENTER_THRESHOLD_UT
        for stand in (STAND_NORTH, STAND_SOUTH):
            toward = self._toward(stand, z_ut)
            if toward < enter * 0.8:
                self._armed[stand] = True
            if toward >= enter and (
                    self._armed[stand]
                    or toward >= self._last_seated[stand] * settings.STAND_REENTER_FRACTION):
                return stand
        return STAND_NONE

    def update(self, now):
        if self.sensor is None:
            if self.reconnect is None or now < self.next_reconnect_at:
                return self._off_stand(now)
            try:
                self.sensor = self.reconnect()
                if self.sensor is None:
                    raise RuntimeError("No magnetometer returned")
                print("TMAG5273 connected")
            except (OSError, RuntimeError, ValueError) as error:
                return self._unavailable(now, error)
        try:
            z_ut = self.sensor.magnetic[2]
            if settings.MAGNET_Z_INVERTED:
                z_ut = -z_ut
            z_ut -= settings.MAGNET_Z_BASELINE_UT
        except (OSError, RuntimeError, ValueError) as error:
            return self._unavailable(now, error)
        self.z_ut = z_ut

        if self.current != STAND_NONE:
            return self._update_seated(now, z_ut)

        observed = self._entry_candidate(z_ut)
        if observed != self.candidate:
            self.candidate = observed
            self.candidate_since = now
            return False
        if (observed != STAND_NONE
                and now - self.candidate_since + .000001 >= settings.STAND_ENTER_DEBOUNCE_S):
            self.current = observed
            self.seated_ut = self._toward(observed, z_ut)
            self._armed[observed] = True
            return True
        return False


class BellModel:
    """Physical clappers inside the urn's bells, driven by measured motion.

    Each bell is a damped clapper on a spring (a pendulum's small-angle
    restoring force) that hangs inside a shell. Gravity-removed acceleration
    shakes the shell; the clapper lags behind, and when it travels farther than
    its gap it strikes the shell and rebounds. The strike speed decides the
    loudness, exactly as with a real bell: tiny sways stay silent, gentle steps
    tinkle, and a hard stop or shake rings loudly. Settings may list several
    bells; the urn uses one.
    """

    def __init__(self):
        frequencies = settings.BELL_FREQUENCIES_HZ
        gaps = settings.BELL_GAPS_MM
        self.count = len(frequencies)
        self.omega = [2.0 * math.pi * frequency for frequency in frequencies]
        self.gap_m = [gap / 1000.0 for gap in gaps]
        self.position = [[0.0, 0.0, 0.0] for _ in range(self.count)]
        self.velocity = [[0.0, 0.0, 0.0] for _ in range(self.count)]
        self.last_strike_at = [-100.0] * self.count

    def reset(self):
        for index in range(self.count):
            position = self.position[index]
            velocity = self.velocity[index]
            position[0] = position[1] = position[2] = 0.0
            velocity[0] = velocity[1] = velocity[2] = 0.0

    def update(self, now, dynamic_g, dt):
        """Advance every clapper by dt; return strike strengths in 0..1."""
        strikes = []
        if dt <= 0:
            return strikes
        steps = int(math.ceil(dt / settings.BELL_SUBSTEP_S))
        h = dt / steps
        ax = dynamic_g[0] * GRAVITY_M_S2
        ay = dynamic_g[1] * GRAVITY_M_S2
        az = dynamic_g[2] * GRAVITY_M_S2
        zeta2 = 2.0 * settings.BELL_DAMPING
        bounce = 1.0 + settings.BELL_RESTITUTION
        softest = settings.BELL_STRIKE_SOFTEST_M_S
        span = settings.BELL_STRIKE_LOUDEST_M_S - softest
        for index in range(self.count):
            omega = self.omega[index]
            stiffness = omega * omega
            damping = zeta2 * omega
            gap = self.gap_m[index]
            x = self.position[index]
            v = self.velocity[index]
            fastest = 0.0
            for _ in range(steps):
                # Semi-implicit Euler: stable for these stiff, light springs.
                v[0] += (-ax - stiffness * x[0] - damping * v[0]) * h
                v[1] += (-ay - stiffness * x[1] - damping * v[1]) * h
                v[2] += (-az - stiffness * x[2] - damping * v[2]) * h
                x[0] += v[0] * h
                x[1] += v[1] * h
                x[2] += v[2] * h
                distance = math.sqrt(x[0] * x[0] + x[1] * x[1] + x[2] * x[2])
                if distance > gap:
                    nx = x[0] / distance
                    ny = x[1] / distance
                    nz = x[2] / distance
                    outward = v[0] * nx + v[1] * ny + v[2] * nz
                    if outward > 0:
                        if outward > fastest:
                            fastest = outward
                        impulse = bounce * outward
                        v[0] -= impulse * nx
                        v[1] -= impulse * ny
                        v[2] -= impulse * nz
                    x[0] = nx * gap
                    x[1] = ny * gap
                    x[2] = nz * gap
            if (fastest > softest
                    and now - self.last_strike_at[index] >= settings.BELL_RETRIGGER_S):
                self.last_strike_at[index] = now
                strikes.append(min(1.0, (fastest - softest) / span))
        return strikes


class MotionTracker:
    """Classifies movement, stillness, freefall/impact, and bell strikes."""

    def __init__(self, accelerometer):
        self.accelerometer = accelerometer
        self.previous = None
        self.sample_dt_s = 0.0
        self._sample_at = None
        self.magnitude_g = 1.0
        self.raw_jerk_g = 0.0
        self.jerk_g = 0.0
        self.dynamic_g = 0.0
        self.stability_delta_g = 0.0
        self._still_reference = None
        self._gravity = None
        self._history = []
        self._smooth_history = []
        self.smooth_jerk_g = 0.0
        self.contact_g = 0.0
        self.smoothed = None
        self._settled = None
        self.lift_g = 0.0
        self.stationary_since = time.monotonic()
        self.freefall_deadline = 0.0
        self.freefall_started = None
        self._freefall_samples = 0
        self.bells = BellModel()
        self.bell_strikes = []
        self.motion_jingle_detected = False

    def update(self, now):
        self.sample_dt_s = 0.0 if self._sample_at is None else max(0.0, now - self._sample_at)
        self._sample_at = now
        acceleration = self.accelerometer.acceleration
        gravity = GRAVITY_M_S2
        vector = (
            acceleration[0] / gravity,
            acceleration[1] / gravity,
            acceleration[2] / gravity,
        )
        self.magnitude_g = math.sqrt(
            vector[0] * vector[0]
            + vector[1] * vector[1]
            + vector[2] * vector[2]
        )

        if self.previous is None:
            self.raw_jerk_g = 0.0
            self.jerk_g = 0.0
            self._still_reference = vector
            self.stationary_since = now
        else:
            dx = vector[0] - self.previous[0]
            dy = vector[1] - self.previous[1]
            dz = vector[2] - self.previous[2]
            self.raw_jerk_g = math.sqrt(dx * dx + dy * dy + dz * dz)
            self.jerk_g = self._span_jerk(now, vector, self._history)
        self.previous = vector
        self._history.append((now, vector))
        if len(self._history) > 8:
            self._history.pop(0)
        self._update_lift(now, vector)
        if self._smooth_history:
            self.smooth_jerk_g = self._span_jerk(now, self.smoothed, self._smooth_history)
        self._smooth_history.append((now, self.smoothed))
        if len(self._smooth_history) > 8:
            self._smooth_history.pop(0)

        # A fixed reference catches slow cumulative turns that a per-sample
        # jerk threshold misses. Do not require exactly 1 G: sensor bias and
        # scale error can change the resting magnitude with holding direction.
        # Reset the reference only on movement, so it cannot follow a slow turn.
        # Stillness uses the ~60 ms smoothed vector: the urn's own speaker and
        # sensor noise shake single samples but are not the urn moving.
        smoothed = self.smoothed
        dx = smoothed[0] - self._still_reference[0]
        dy = smoothed[1] - self._still_reference[1]
        dz = smoothed[2] - self._still_reference[2]
        self.stability_delta_g = math.sqrt(dx * dx + dy * dy + dz * dz)
        low_g = self.magnitude_g < settings.FREEFALL_G
        moving = (
            low_g
            or self.smooth_jerk_g > settings.MOTION_JERK_G
            or self.stability_delta_g > settings.MOTION_STILL_VECTOR_DELTA_G
        )
        if moving:
            self.stationary_since = now
            self._still_reference = smoothed

        # Freefall must last long enough to be a real fall (FREEFALL_MIN_S
        # of ~0 G is a drop of about half g*t^2), not one swing of a shake.
        if low_g:
            if self.freefall_started is None:
                self.freefall_started = now
                self._freefall_samples = 0
            self._freefall_samples += 1
            if (self._freefall_samples >= 2 and
                    now - self.freefall_started + .000001 >= settings.FREEFALL_MIN_S):
                self.freefall_deadline = now + settings.FREEFALL_WINDOW_S
        else:
            self.freefall_started = None
            self._freefall_samples = 0

        hard_drop = False
        if (
            self.freefall_deadline > now and not low_g
            and (self.magnitude_g >= settings.DROP_IMPACT_G or
                 (self.magnitude_g >= 1.0 and
                  self.jerk_g >= settings.DROP_IMPACT_JERK_G))
        ):
            hard_drop = True
            self.freefall_deadline = 0.0

        self._update_bells(now, vector)
        return moving, hard_drop

    def _span_jerk(self, now, vector, history):
        """Change over the newest span of at least 25 ms, scaled to 25 ms.

        A fast loop must not multiply one sample's sensor noise (up to 0.19 G
        on the bench) into a large jerk: compare against an older sample
        instead. Longer gaps scale down, as before.
        """
        reference = None
        for index in range(len(history) - 1, -1, -1):
            if now - history[index][0] >= .025 - .000001:
                reference = history[index]
                break
        if reference is None:
            reference = history[0]
        old = reference[1]
        dx = vector[0] - old[0]
        dy = vector[1] - old[1]
        dz = vector[2] - old[2]
        change = math.sqrt(dx * dx + dy * dy + dz * dz)
        age = now - reference[0]
        if age <= .025:
            return change
        return change * .025 / age

    def _update_lift(self, now, vector):
        """Smoothed deviation from the slowly settled pose, for lifts off a surface.

        Speaker buzz and sensor noise average out of the ~60 ms smoothing; a
        hand lifting or tilting the urn does not.
        """
        dt = self.sample_dt_s
        if self.smoothed is None or dt > settings.BELL_MAX_GAP_S:
            self.smoothed = self._settled = vector
            self.lift_g = 0.0
            return
        fast = dt / (settings.LIFT_SMOOTH_S + dt) if dt > 0 else 0.0
        slow = dt / (settings.LIFT_SETTLE_S + dt) if dt > 0 else 0.0
        smoothed = self.smoothed
        smoothed = (
            smoothed[0] + fast * (vector[0] - smoothed[0]),
            smoothed[1] + fast * (vector[1] - smoothed[1]),
            smoothed[2] + fast * (vector[2] - smoothed[2]),
        )
        self.smoothed = smoothed
        settled = self._settled
        self._settled = (
            settled[0] + slow * (smoothed[0] - settled[0]),
            settled[1] + slow * (smoothed[1] - settled[1]),
            settled[2] + slow * (smoothed[2] - settled[2]),
        )
        dx = smoothed[0] - self._settled[0]
        dy = smoothed[1] - self._settled[1]
        dz = smoothed[2] - self._settled[2]
        self.lift_g = math.sqrt(dx * dx + dy * dy + dz * dz)

    def anchor_rest(self):
        """The urn was just confirmed resting: its pose now is the reference."""
        if self.smoothed is not None:
            self._settled = self.smoothed
            self.lift_g = 0.0

    def _update_bells(self, now, vector):
        """Remove gravity with a slow baseline, then drive the clappers."""
        dt = self.sample_dt_s
        if self._gravity is None or dt > settings.BELL_MAX_GAP_S:
            # First sample, or the loop stalled: start again from rest.
            self._gravity = vector
            self.bells.reset()
            self.dynamic_g = 0.0
            self.bell_strikes = []
            self.motion_jingle_detected = False
            return
        follow = dt / (settings.BELL_GRAVITY_TAU_S + dt) if dt > 0 else 0.0
        gravity = self._gravity
        gravity = (
            gravity[0] + follow * (vector[0] - gravity[0]),
            gravity[1] + follow * (vector[1] - gravity[1]),
            gravity[2] + follow * (vector[2] - gravity[2]),
        )
        self._gravity = gravity
        dynamic = (vector[0] - gravity[0], vector[1] - gravity[1], vector[2] - gravity[2])
        self.dynamic_g = math.sqrt(
            dynamic[0] * dynamic[0] + dynamic[1] * dynamic[1] + dynamic[2] * dynamic[2]
        )
        # How far above this sensor's own resting 1 G reading the sample is.
        self.contact_g = self.magnitude_g - math.sqrt(
            gravity[0] * gravity[0] + gravity[1] * gravity[1] + gravity[2] * gravity[2]
        )
        self.bell_strikes = self.bells.update(now, dynamic, dt)
        self.motion_jingle_detected = bool(self.bell_strikes)

    def still_for(self, now):
        return now - self.stationary_since


class AudioLibrary:
    """Indexes the same curated categories as the web simulator."""

    def __init__(self, root):
        self.root = root
        self.tracks = {}
        self.last_track = {}
        self.bags = {}
        directories = {
            "wait_on_pad": ("waiting/bridge", "", ".mp3"),
            "wait_off_pad": ("waiting/streets", "", ".mp3"),
            "pickup_from_pad": ("picked_up_urn/bridge", "", ".mp3"),
            "pickup_from_ground": ("picked_up_urn/streets", "", ".mp3"),
            "pickup_effect": ("soul_urn_pickup", "soul_urn_pickup_", ".mp3"),
            "dropoff_effect": ("soul_urn_dropoff", "soul_urn_cashin_complete_", ".mp3"),
            "carrying": ("carrier_is_stalling", "", ".mp3"),
            "hard_drop": ("dropped_urn", "", ".mp3"),
            "delivered": ("delivered", "", ".mp3"),
            "urn_expires": ("urn_expires", "", ".mp3"),
            "carrying_jingle": ("carrying_jingles_wav", "footstep_urn_jingle_", ".wav"),
        }
        for key in directories:
            relative, prefix, extension = directories[key]
            directory = root + "/" + relative
            try:
                self.tracks[key] = sorted(
                    directory + "/" + name for name in os.listdir(directory)
                    if not name.startswith(".")
                    and name.startswith(prefix) and name.lower().endswith(extension)
                )
            except OSError as error:
                self.tracks[key] = []
                print("Missing audio directory", directory, error)
            print("Audio", key, len(self.tracks[key]))

        # These lines ask for a pad without falsely claiming the empty urn is full.
        prompt_paths = (
            "waiting/bridge/spirit_jar_idol_waiting_on_bridge_03.mp3",  # "I believe you're missing something."
            "dropped_urn/spirit_jar_idol_dropped_05.mp3",  # "I think there's been a mistake."
            "dropped_urn/spirit_jar_idol_dropped_06.mp3",  # "...this is where you're supposed to leave me."
        )
        self.tracks["unhomed_prompt"] = []
        for relative in prompt_paths:
            path = root + "/" + relative
            try:
                os.stat(path)
                self.tracks["unhomed_prompt"].append(path)
            except OSError:
                print("Missing needs-pad prompt", path)
        print("Audio unhomed_prompt", len(self.tracks["unhomed_prompt"]))
        self.filling_start = root + "/soul_urn_pickup/soul_urn_cashin_timer_01.mp3"
        self.filling_loop = root + "/soul_urn_pickup/soul_urn_cashin_timer_lp_01.mp3"

    def choose(self, key):
        files = self.tracks.get(key, ())
        if not files:
            return None
        if key == "carrying_jingle":
            return self._next_from_bag(key, files)
        index = random.randrange(len(files))
        chosen = files[index]
        if len(files) > 1 and chosen == self.last_track.get(key):
            chosen = files[(index + 1) % len(files)]
        self.last_track[key] = chosen
        return chosen

    def _next_from_bag(self, key, files):
        """Play every jingle once, in a fresh random order, before any repeats."""
        bag = self.bags.get(key)
        if not bag:
            bag = list(files)
            for index in range(len(bag) - 1, 0, -1):
                other = random.randrange(index + 1)
                bag[index], bag[other] = bag[other], bag[index]
            # Never start a new round with the jingle that just played.
            if len(bag) > 1 and bag[-1] == self.last_track.get(key):
                bag[0], bag[-1] = bag[-1], bag[0]
            self.bags[key] = bag
        chosen = bag.pop()
        self.last_track[key] = chosen
        return chosen


class AudioPlayer:
    """One reused MP3 decoder, plus streamed WAV bell voice(s) (one by default).

    Effects and speech play sequentially on voice 0. Filling shares that decoder
    because the simulator has no dialogue while charging. Short PCM WAV jingles on
    the remaining voices overlap speech without allocating extra MP3 decoders.
    Each bell strike plays at a level set by its strike speed; a strike that
    would be masked by a louder bell still ringing is skipped, and a new strike
    takes over the quietest (most decayed) voice instead of cutting a loud one.
    """

    def __init__(self, output, decoder_path=None):
        self.output = output
        self.decoder = None
        if decoder_path is not None:
            self.prime_decoder(decoder_path)
        gc.collect()
        self.jingle_voices = settings.JINGLE_VOICE_COUNT
        # Carry music is optional: it plays only if enabled and deployed.
        self.music_path = settings.AUDIO_ROOT + "/" + settings.CARRY_MUSIC_FILE
        self.music_enabled = settings.CARRY_MUSIC and self._exists(self.music_path)
        self.music_voice = 1 + self.jingle_voices
        self.music_file = None
        self.music_sample = None
        print("Carry music", "on" if self.music_enabled else "off")
        self.mixer = audiomixer.Mixer(
            voice_count=1 + self.jingle_voices + (1 if self.music_enabled else 0),
            sample_rate=settings.AUDIO_SAMPLE_RATE,
            channel_count=1,
            bits_per_sample=16,
            samples_signed=True,
            buffer_size=settings.AUDIO_MIXER_BUFFER_SIZE,
        )
        self.output.play(self.mixer)
        self.voice = self.mixer.voice[0]
        self.queue = []
        self.completed = []
        self.current_file = None
        self.current_group = None
        self.current_kind = None
        self.filling_active = False
        self.filling_started = False
        self.filling_elapsed = 0.0
        self.filling_library = None
        self.carrying_active = False
        self.jingle_library = None
        self.last_motion_jingle_at = -100.0
        self.jingle_files = [None] * self.jingle_voices
        self.jingle_samples = [None] * self.jingle_voices
        self.jingle_started_at = [-100.0] * self.jingle_voices
        self.jingle_levels = [0.0] * self.jingle_voices

    @property
    def busy(self):
        """Foreground effects/dialogue only; ambience never delays chatter."""
        return bool(self.queue) or self.current_kind == "dialogue"

    def prime_decoder(self, path):
        """Allocate the large decoder before sensor/controller objects fragment RAM."""
        stream = None
        try:
            gc.collect()
            if settings.DEBUG and hasattr(gc, "mem_free"):
                print("Free RAM before MP3 decoder:", gc.mem_free())
            stream = open(path, "rb")
            self.decoder = audiomp3.MP3Decoder(stream)
        except (OSError, RuntimeError, ValueError, MemoryError) as error:
            print("MP3 decoder initialization failed:", error)
        finally:
            if stream is not None:
                stream.close()
            if settings.DEBUG and hasattr(gc, "mem_free"):
                print("Free RAM after MP3 decoder:", gc.mem_free())

    def play_groups(self, groups, library, replace=True):
        if replace:
            self.stop()
        for group in groups:
            path = library.choose(group)
            if path is not None:
                self.queue.append((group, path))
        self._start_next()

    def take_completed(self):
        completed = self.completed
        self.completed = []
        return completed

    def stop(self):
        """Interrupt foreground audio; carrying WAV voices are reconciled separately."""
        self.queue = []
        self.completed = []
        self._close_current()
        self.filling_started = False

    def _close_current(self):
        self.voice.stop()
        if self.current_file is not None:
            self.current_file.close()
        self.current_file = None
        self.current_group = None
        self.current_kind = None

    def _play_mp3(self, path, kind, group=None, elapsed_s=0.0, loop=False):
        self._close_current()
        try:
            stream = open(path, "rb")
            self.current_file = stream
            # All converted files are 48 kbit/s CBR. Seek before assigning the
            # decoder file; MP3 frame boundaries make this an approximate seek.
            if elapsed_s > 0:
                stream.seek(int(settings.MP3_BITRATE_BPS * elapsed_s / 8))
            if self.decoder is None:
                self.decoder = audiomp3.MP3Decoder(stream)
            else:
                self.decoder.file = stream
            self.current_kind = kind
            self.current_group = group
            self.voice.level = (
                settings.VOICE_VOLUME if kind == "dialogue" else settings.FILLING_VOLUME
            )
            self.voice.play(self.decoder, loop=loop)
            print("Playing", group or kind, path)
            return True
        except (OSError, RuntimeError, ValueError, MemoryError) as error:
            print("Audio failed", path, error)
            self._close_current()
            if group is not None:
                self.completed.append(group)
            return False

    def _start_next(self):
        while self.queue and self.current_kind != "dialogue":
            group, path = self.queue.pop(0)
            if self._play_mp3(path, "dialogue", group=group):
                return

    def set_filling(self, active, library, elapsed_s=0.0):
        if active and not self.filling_active:
            self.filling_started = False
            self.filling_elapsed = elapsed_s
        self.filling_active = active
        self.filling_library = library
        if not active:
            self.filling_started = False
            if self.current_kind in ("fill_start", "fill_loop"):
                self._close_current()

    def set_carrying(self, active, library, now):
        self.jingle_library = library
        if active and not self.carrying_active:
            self.last_motion_jingle_at = -100.0
            self._start_music()
        elif not active:
            for index in range(self.jingle_voices):
                self._close_jingle(index)
            self._stop_music()
        self.carrying_active = active

    @staticmethod
    def _exists(path):
        try:
            os.stat(path)
            return True
        except OSError:
            return False

    def _start_music(self):
        if not self.music_enabled or self.music_file is not None:
            return
        try:
            self.music_file = open(self.music_path, "rb")
            self.music_sample = audiocore.WaveFile(self.music_file)
            voice = self.mixer.voice[self.music_voice]
            voice.level = self._music_level()
            voice.play(self.music_sample, loop=True)
        except (OSError, RuntimeError, ValueError, MemoryError) as error:
            print("Carry music failed", error)
            self._stop_music()

    def _stop_music(self):
        if self.music_enabled:
            self.mixer.voice[self.music_voice].stop()
        if self.music_sample is not None:
            self.music_sample.deinit()
        self.music_sample = None
        if self.music_file is not None:
            self.music_file.close()
        self.music_file = None

    def _music_level(self):
        duck = settings.CARRY_MUSIC_DUCK if self.busy else 1.0
        return settings.CARRY_MUSIC_VOLUME * duck

    @staticmethod
    def jingle_level(strength):
        """Map a 0..1 strike strength to a mixer level, softest strikes audible."""
        strength = max(0.0, min(1.0, strength))
        floor = settings.JINGLE_SOFTEST_LEVEL
        return settings.CARRYING_JINGLE_VOLUME * (
            floor + (1.0 - floor) * strength ** settings.JINGLE_STRIKE_CURVE
        )

    def _ringing_level(self, index, now):
        """Approximate what is still audible from this voice's jingle."""
        if self.jingle_files[index] is None:
            return 0.0
        age = now - self.jingle_started_at[index]
        sustain = settings.JINGLE_SUSTAIN_S
        if age <= sustain:
            return self.jingle_levels[index]
        fade = 1.0 - (age - sustain) / settings.JINGLE_DECAY_S
        return self.jingle_levels[index] * fade if fade > 0 else 0.0

    def play_motion_jingle(self, now, strength=1.0):
        """Ring a bell strike while carrying full; louder strikes ring louder."""
        if not self.carrying_active or self.jingle_library is None:
            return False
        if now - self.last_motion_jingle_at < settings.MOTION_JINGLE_MIN_INTERVAL_S:
            return False
        level = self.jingle_level(strength)
        quietest = 0
        quietest_level = None
        loudest_level = 0.0
        for index in range(self.jingle_voices):
            ringing = self._ringing_level(index, now)
            if ringing > loudest_level:
                loudest_level = ringing
            if quietest_level is None or ringing < quietest_level:
                quietest = index
                quietest_level = ringing
        # A soft tap under a loud ring is inaudible on a real bell, too.
        if level < loudest_level * settings.JINGLE_MASKING:
            return False
        self.last_motion_jingle_at = now
        return self._play_jingle(quietest, level, now)

    def _close_jingle(self, index):
        self.mixer.voice[index + 1].stop()
        if self.jingle_samples[index] is not None:
            self.jingle_samples[index].deinit()
        self.jingle_samples[index] = None
        if self.jingle_files[index] is not None:
            self.jingle_files[index].close()
        self.jingle_files[index] = None
        self.jingle_levels[index] = 0.0

    def _duck(self):
        return settings.CARRYING_JINGLE_DUCK if self.busy else 1.0

    def _play_jingle(self, index, level, now):
        self._close_jingle(index)
        path = self.jingle_library.choose("carrying_jingle")
        if path is None:
            return False
        try:
            self.jingle_files[index] = open(path, "rb")
            self.jingle_samples[index] = audiocore.WaveFile(self.jingle_files[index])
            voice = self.mixer.voice[index + 1]
            voice.level = level * self._duck()
            voice.play(self.jingle_samples[index])
            self.jingle_levels[index] = level
            self.jingle_started_at[index] = now
            if settings.DEBUG:
                print("Bell", round(level, 2), path)
            return True
        except (OSError, RuntimeError, ValueError, MemoryError) as error:
            print("Jingle failed", path, error)
            self._close_jingle(index)
            return False

    def update(self, now):
        if self.current_kind is not None and not self.voice.playing:
            kind = self.current_kind
            group = self.current_group
            self._close_current()
            if kind == "dialogue":
                self.completed.append(group)
            elif kind == "fill_start" and self.filling_active:
                self._play_mp3(self.filling_library.filling_loop, "fill_loop", loop=True)
        self._start_next()
        if self.filling_active and not self.busy and not self.filling_started:
            self.filling_started = True
            if not self._play_mp3(
                self.filling_library.filling_start,
                "fill_start",
                elapsed_s=self.filling_elapsed,
            ):
                self._play_mp3(self.filling_library.filling_loop, "fill_loop", loop=True)

        if self.music_file is not None:
            self.mixer.voice[self.music_voice].level = self._music_level()
        duck = self._duck()
        for index in range(self.jingle_voices):
            if self.jingle_files[index] is None:
                continue
            voice = self.mixer.voice[index + 1]
            if not voice.playing:
                self._close_jingle(index)
            else:
                voice.level = self.jingle_levels[index] * duck


class SoulLights:
    """Map the simulator's 32 virtual pixels along the installed RGB/RGBW chain."""

    def __init__(self, pixels):
        self.pixels = pixels
        self.last_frame = -100.0
        count = settings.PIXEL_COUNT
        # Index math is identical every frame; precompute it once so a frame
        # spends its time on colors, not on integer division per pixel.
        if settings.REVERSE_PIXEL_CHAIN:
            self._physical = [count - 1 - index for index in range(count)]
        else:
            self._physical = list(range(count))
        self._virtual = [index * settings.VIRTUAL_PIXEL_COUNT // count for index in range(count)]
        self._rings = [virtual % 16 for virtual in self._virtual]
        self._rgbw = len(settings.PIXEL_ORDER) == 4

    @staticmethod
    def _scale(color, level):
        return (int(color[0] * level), int(color[1] * level),
                int(color[2] * level), int(color[3] * level))

    def _put(self, index, color):
        self.pixels[self._physical[index]] = self._format_color(color)

    def _format_color(self, color):
        if self._rgbw:
            return color
        # Preserve the former RGBW animation's white highlights on an RGB strip.
        white = color[3]
        red = color[0] + white
        green = color[1] + white
        blue = color[2] + white
        return (red if red < 255 else 255, green if green < 255 else 255,
                blue if blue < 255 else 255)

    def _ring_index(self, index, count):
        if count == settings.PIXEL_COUNT:
            return self._rings[index]
        return (index * settings.VIRTUAL_PIXEL_COUNT // count) % 16

    def show_search(self, now, missing_sensor=True):
        """Visible boot indication while hardware initializes and sensors settle."""
        if now - self.last_frame < settings.LIGHT_FRAME_S:
            return
        self.last_frame = now
        self.pixels.fill(self._format_color((0, 0, 0, 0)))
        if missing_sensor:
            self._missing_sensor(now, settings.PIXEL_COUNT)
        else:
            self._unhomed(now, settings.PIXEL_COUNT)
        self.pixels.show()

    def render(self, now, controller):
        if controller.powered and now - self.last_frame < settings.LIGHT_FRAME_S:
            return
        self.last_frame = now
        count = settings.PIXEL_COUNT
        self.pixels.fill(self._format_color((0, 0, 0, 0)))

        if not controller.powered:
            self.pixels.show()
            return
        if controller.state == STATE_UNHOMED:
            self._unhomed(now - controller.boot_at, count)
        elif not getattr(controller.stand, "available", True):
            self._missing_sensor(now, count)
        elif now < controller.drop_flash_until:
            pulse = 0.45 + 0.55 * abs(math.sin(now * 18.0))
            color = self._scale((255, 18, 0, 90), pulse)
            self.pixels.fill(self._format_color(color))
        elif now < controller.delivery_until:
            self._delivery(now, count)
        elif now < controller.pickup_until:
            self._pickup(now, count)
        elif controller.state == STATE_FILLING:
            self._filling(now, count, controller.fill_fraction(now))
        elif controller.state == STATE_FILL_PAUSED:
            self._fill_paused(now, count, controller.fill_fraction(now))
        elif controller.state == STATE_READY:
            self._ready(now, count)
        elif controller.state == STATE_CARRIED:
            self._carried(now, count, controller.motion.jerk_g)
        elif controller.state == STATE_RESTING_WRONG:
            self._resting_wrong(now, count)
        elif controller.state == STATE_WRONG_SOURCE:
            self._wrong_source(now, count)

        self.pixels.show()

    def _missing_sensor(self, now, count):
        self._unhomed(now, count)

    def _unhomed(self, now, count):
        # The frame starts black; show one short red pulse per five seconds.
        if now % settings.UNHOMED_FLASH_INTERVAL_S < settings.UNHOMED_FLASH_DURATION_S:
            self.pixels.fill(self._format_color((255, 0, 0, 0)))

    def _filling(self, now, count, fraction):
        filled = int(math.ceil(fraction * count))
        drift = int(now * 18.0)
        for index in range(filled):
            wave = (self._virtual[index] * 23 + drift) % 96
            if wave < 32:
                color = (0, 150, 95, 12)
            elif wave < 64:
                color = (15, 85, 180, 4)
            else:
                color = (75, 25, 160, 6)
            self._put(index, color)

    def _fill_paused(self, now, count, fraction):
        level = 0.55 + 0.25 * (0.5 + 0.5 * math.sin(now * 2.0))
        for index in range(int(math.ceil(fraction * count))):
            color = (170, 65, 230, 0) if self._ring_index(index, count) % 2 else (255, 150, 40, 0)
            self._put(index, self._scale(color, level))

    def _ready(self, now, count):
        # Full: the whole strip shows the filling colors, still cycling.
        self._filling(now, count, 1.0)

    def _carried(self, now, count, jerk_g):
        speed = 10.0 + min(18.0, jerk_g * 30.0)
        head = int(now * speed) % 16
        for index in range(count):
            ring_index = self._ring_index(index, count)
            trail = (head - ring_index) % 16
            if trail == 0:
                color = (85, 235, 160, 120)
            elif trail <= 3:
                color = self._scale((25, 160, 190, 35), (4 - trail) / 4.0)
            else:
                color = (2, 25, 38, 2)
            self._put(index, color)

    def _resting_wrong(self, now, count):
        breath = 0.25 + 0.20 * (0.5 + 0.5 * math.sin(now * 2.0))
        for index in range(count):
            color = (80, 20, 100, 5) if self._ring_index(index, count) % 4 else (150, 55, 0, 25)
            self._put(index, self._scale(color, breath))

    def _wrong_source(self, now, count):
        alternate = int(now * 3.0) & 1
        for index in range(count):
            on = ((self._virtual[index] // 4) & 1) == alternate
            self._put(index, (210, 35, 0, 45) if on else (35, 2, 0, 0))

    def _pickup(self, now, count):
        head = int(now * 22.0) % 16
        for index in range(count):
            distance = (head - self._ring_index(index, count)) % 16
            if distance < 4:
                self._put(index, self._scale((70, 230, 175, 190), (4 - distance) / 4.0))
            else:
                self._put(index, (4, 35, 45, 5))

    def _delivery(self, now, count):
        sparkle = int(now * 37.0)
        fade = 0.55 + 0.35 * abs(math.sin(now * 7.0))
        for index in range(count):
            if (index * 11 + sparkle) % 17 < 3:
                self._put(index, self._scale((80, 220, 120, 255), fade))
            else:
                self._put(index, self._scale((0, 80, 70, 25), fade))


FULL_URN_AUDIO_GROUPS = (
    "wait_on_pad", "wait_off_pad", "pickup_from_pad", "pickup_from_ground",
    "pickup_effect", "dropoff_effect", "carrying", "hard_drop", "delivered",
    "urn_expires",
)


class UrnController:
    """CircuitPython port of the simulator engine, driven by absolute monotonic time.

    Public physical actions mirror the simulator; update() translates debounced
    sensor readings into those actions. Audio completion supplies the browser's
    dialogue-end callbacks. Every power-on creates a fresh round.
    """

    def __init__(self, stand, motion, library, audio, lights, now=None, random_source=None):
        self.stand = stand
        self.motion = motion
        self.library = library
        self.audio = audio
        self.lights = lights
        self.random_source = random_source or random.random
        self.now = time.monotonic() if now is None else now
        self.boot_at = self.now
        self.sim_time = 0.0
        self.powered = False
        self.source = STAND_NONE
        self.phase = PHASE_EMPTY
        self.state = STATE_POWERED_OFF
        self.position = STAND_NORTH
        self.fill_elapsed = 0.0
        self.delivery_elapsed = 0.0
        self.expiry_spoken = False
        self.refill_at = None
        self.pending_set_down_at = None
        self.next_chatter = float("inf")
        self.pickup_until = 0.0
        self.delivery_until = 0.0
        self.drop_flash_until = 0.0
        self.last_debug = -100.0
        self._dialogue_waiting = False
        self._delivery_waiting = False
        self._set_down_contact_at = None
        self._physical_set_down_at = None
        self._ground_moving_since = None
        self._last_pick_up_at = None
        self._hardware_drop_guard_until = None
        self._hardware_drop_quiet_since = None
        self._hardware_drop_pending_release = False
        self._hardware_drop_candidate_until = None
        self._hardware_drop_candidate_quiet_since = None

    @property
    def destination(self):
        return opposite_stand(self.source)

    @property
    def is_full(self):
        return self.phase in (PHASE_READY, PHASE_TRANSIT)

    @property
    def charging_active(self):
        return (
            self.powered and self.state == STATE_FILLING
            and self.source != STAND_NONE and self.position == self.source
        )

    def fill_fraction(self, now=None):
        if self.is_full:
            return 1.0
        if self.state not in (STATE_FILLING, STATE_FILL_PAUSED) or settings.FILL_TIME_S <= 0:
            return 0.0
        return min(1.0, self.fill_elapsed / settings.FILL_TIME_S)

    def _set_state(self, new_state):
        if new_state != self.state:
            print("State", STATE_NAMES[self.state], "->", STATE_NAMES[new_state])
        self.state = new_state

    def _schedule_chatter(self, now):
        if self.state == STATE_CARRIED:
            delay = settings.CARRYING_CHATTER_DELAY_S
        elif self.state == STATE_RESTING_WRONG:
            delay = settings.STREETS_CHATTER_DELAY_S
        elif self.state in (STATE_UNHOMED, STATE_FILL_PAUSED):
            delay = settings.UNHOMED_CHATTER_DELAY_S
        elif self.state in (STATE_READY, STATE_WRONG_SOURCE):
            delay = settings.READY_CHATTER_DELAY_S
        else:
            self.next_chatter = float("inf")
            return
        jitter = 1.0 - settings.CHATTER_JITTER + self.random_source() * 2 * settings.CHATTER_JITTER
        self.next_chatter = now + delay * jitter

    def _interrupt_audio(self):
        self.audio.stop()
        self._dialogue_waiting = False
        self._delivery_waiting = False

    def _play_groups(self, groups, now, delivery=False):
        # Delivery is emitted while still full, before changing the phase to empty.
        allowed = tuple(
            group for group in groups
            if group not in FULL_URN_AUDIO_GROUPS or self.is_full
        )
        self._interrupt_audio()
        if not allowed:
            return
        self.audio.play_groups(allowed, self.library, replace=True)
        self._dialogue_waiting = any(
            group not in ("pickup_effect", "dropoff_effect") for group in allowed
        )
        self._delivery_waiting = delivery
        if self._dialogue_waiting:
            self.next_chatter = float("inf")
        # Missing audio/category files must never freeze the round indefinitely.
        if not self.audio.busy:
            self._finish_audio_timing(now)

    def _finish_audio_timing(self, now):
        if self._delivery_waiting:
            if self.state == STATE_REFILL_DELAY:
                self.refill_at = now + settings.REFILL_DELAY_S
            self._delivery_waiting = False
        if self._dialogue_waiting:
            self._dialogue_waiting = False
            self._schedule_chatter(now)

    def _sync_background(self, now):
        filling = getattr(self.audio, "set_filling", None)
        if filling is not None:
            filling(self.charging_active, self.library, self.fill_elapsed)
        carrying = getattr(self.audio, "set_carrying", None)
        if carrying is not None:
            carrying(
                self.powered and self.state == STATE_CARRIED
                and self.position == POSITION_CARRIED,
                self.library,
                now,
            )

    def power_on(self, now):
        if self.powered:
            return
        self.now = now
        self.boot_at = now
        self.sim_time = 0.0
        self.powered = True
        self.source = STAND_NONE
        self.phase = PHASE_EMPTY
        self.fill_elapsed = 0.0
        self.delivery_elapsed = 0.0
        self.expiry_spoken = False
        self.refill_at = None
        self.pending_set_down_at = None
        self.next_chatter = float("inf")
        self.pickup_until = self.delivery_until = self.drop_flash_until = 0.0
        self._set_down_contact_at = self._physical_set_down_at = None
        self._ground_moving_since = self._last_pick_up_at = None
        self._hardware_drop_guard_until = None
        self._hardware_drop_quiet_since = None
        self._hardware_drop_pending_release = False
        self._hardware_drop_candidate_until = None
        self._hardware_drop_candidate_quiet_since = None
        self._interrupt_audio()
        if self.position in (STAND_NORTH, STAND_SOUTH):
            self.begin_filling(self.position, now)
        else:
            self.set_unhomed(now)
        self._sync_background(now)

    def power_off(self, now):
        self.powered = False
        self._set_state(STATE_POWERED_OFF)
        self.source = STAND_NONE
        self.phase = PHASE_EMPTY
        self.fill_elapsed = 0.0
        self.delivery_elapsed = 0.0
        self.expiry_spoken = False
        self.refill_at = None
        self.pending_set_down_at = None
        self.next_chatter = float("inf")
        self.pickup_until = self.delivery_until = self.drop_flash_until = 0.0
        self._set_down_contact_at = self._physical_set_down_at = None
        self._ground_moving_since = self._last_pick_up_at = None
        self._hardware_drop_guard_until = None
        self._hardware_drop_quiet_since = None
        self._hardware_drop_pending_release = False
        self._hardware_drop_candidate_until = None
        self._hardware_drop_candidate_quiet_since = None
        self._interrupt_audio()
        self._sync_background(now)
        self.lights.render(now, self)
        self.now = now

    def resolve_boot(self, now):
        self.position = (
            self.stand.current if self.stand.current != STAND_NONE else POSITION_CARRIED
        )
        print("Boot stand=", STAND_NAMES[self.stand.current], "fresh round")
        self.power_on(now)

    def set_unhomed(self, now):
        self._set_state(STATE_UNHOMED)
        self.phase = PHASE_EMPTY
        self.source = STAND_NONE
        self.fill_elapsed = 0.0
        self._schedule_chatter(now)
        self.drop_flash_until = now + settings.DROP_FLASH_S
        self._play_groups(("unhomed_prompt",), now)

    def begin_filling(self, stand, now):
        self.source = stand
        self.phase = PHASE_EMPTY
        self._set_state(STATE_FILLING)
        self.fill_elapsed = 0.0
        self.delivery_elapsed = 0.0
        self.expiry_spoken = False
        self.refill_at = None
        self.pending_set_down_at = None
        self.next_chatter = float("inf")
        self._interrupt_audio()
        print("Filling at", STAND_NAMES[stand])
        self._sync_background(now)

    def resume_filling(self, now):
        self.phase = PHASE_EMPTY
        self._set_state(STATE_FILLING)
        self.pending_set_down_at = None
        self.refill_at = None
        self.next_chatter = float("inf")
        self._interrupt_audio()
        self._sync_background(now)

    def complete_fill(self, now):
        if not self.charging_active or self.phase != PHASE_EMPTY:
            return
        self.fill_elapsed = settings.FILL_TIME_S
        self.phase = PHASE_READY
        self._set_state(STATE_READY)
        self._schedule_chatter(now)
        self._sync_background(now)
        print("Urn is full; destination is", STAND_NAMES[self.destination])

    def place_on(self, stand, now):
        if stand not in (STAND_NORTH, STAND_SOUTH):
            return
        self.advance(now)
        if self.powered and self.phase == PHASE_READY and stand == self.destination:
            print("Pickup required before delivery")
            return
        self.position = stand
        self.pending_set_down_at = None
        self._set_down_contact_at = self._physical_set_down_at = None
        self._ground_moving_since = None
        self._hardware_drop_guard_until = None
        self._hardware_drop_quiet_since = None
        self._hardware_drop_pending_release = False
        self._hardware_drop_candidate_until = None
        self._hardware_drop_candidate_quiet_since = None
        if not self.powered:
            return
        if self.phase == PHASE_EMPTY:
            if self.source == STAND_NONE:
                self.begin_filling(stand, now)
            elif stand != self.source:
                self.refill_at = None
                self._set_state(STATE_FILL_PAUSED)
                self._schedule_chatter(now)
                self.drop_flash_until = now + settings.DROP_FLASH_S
                self._play_groups(("unhomed_prompt",), now)
            elif self.state != STATE_REFILL_DELAY and self.state != STATE_FILLING:
                self.resume_filling(now)
        elif self.phase == PHASE_TRANSIT:
            if stand == self.destination:
                self.deliver(stand, now)
            elif stand == self.source:
                self.wrong_source(now)
        elif self.phase == PHASE_READY:
            self._set_state(STATE_READY)
            self._schedule_chatter(now)
        self._sync_background(now)

    def pick_up(self, now):
        self.advance(now)
        previous_position = self.position
        if previous_position == POSITION_CARRIED:
            return
        self.position = POSITION_CARRIED
        self.pending_set_down_at = None
        self._set_down_contact_at = None
        self._physical_set_down_at = None
        self._ground_moving_since = None
        self._last_pick_up_at = now
        self._hardware_drop_guard_until = None
        self._hardware_drop_quiet_since = None
        self._hardware_drop_pending_release = False
        self._hardware_drop_candidate_until = None
        self._hardware_drop_candidate_quiet_since = None
        self.motion.stationary_since = now
        if not self.powered:
            return
        if self.phase == PHASE_EMPTY:
            if self.source == STAND_NONE:
                self.set_unhomed(now)
            else:
                self._set_state(STATE_FILL_PAUSED)
                self.refill_at = None
                self._schedule_chatter(now)
                self._play_groups(("unhomed_prompt",), now)
        elif self.phase == PHASE_READY:
            self.phase = PHASE_TRANSIT
            self._set_state(STATE_CARRIED)
            self.delivery_elapsed = 0.0
            self.expiry_spoken = False
            self.pickup_until = now + settings.PICKUP_FLASH_S
            self._schedule_chatter(now)
            self._play_groups(("pickup_effect", "pickup_from_pad"), now)
        elif self.phase == PHASE_TRANSIT:
            # Only the first lift of a full urn from its source pad gets the
            # pickup effect and flash; re-lifts just speak.
            recovered = previous_position == POSITION_GROUND or self.state == STATE_RESTING_WRONG
            self._set_state(STATE_CARRIED)
            self._schedule_chatter(now)
            self._play_groups(
                ("pickup_from_ground" if recovered else "pickup_from_pad",), now
            )
        self._sync_background(now)

    def gentle_set_down(self, now):
        self.advance(now)
        if self.position != POSITION_CARRIED:
            return
        self.position = POSITION_GROUND
        self._set_down_contact_at = self._physical_set_down_at = None
        self._ground_moving_since = None
        self._hardware_drop_candidate_until = None
        self._hardware_drop_candidate_quiet_since = None
        self._anchor_rest()
        if self.powered and self.phase == PHASE_TRANSIT:
            self.next_chatter = float("inf")
            self.pending_set_down_at = now + settings.SET_DOWN_STILL_S
        self._sync_background(now)

    def drop(self, now):
        self.advance(now)
        if self.position != POSITION_CARRIED:
            return
        self.position = POSITION_GROUND
        self.pending_set_down_at = None
        self._anchor_rest()
        self._set_down_contact_at = self._physical_set_down_at = None
        self._ground_moving_since = None
        self._hardware_drop_candidate_until = None
        self._hardware_drop_candidate_quiet_since = None
        if self.powered and self.phase == PHASE_TRANSIT:
            self._set_state(STATE_RESTING_WRONG)
            self._schedule_chatter(now)
            self.drop_flash_until = now + settings.DROP_FLASH_S
            self._play_groups(("hard_drop",), now)
        self._sync_background(now)

    def _contact_rise(self):
        rise = getattr(self.motion, "contact_g", None)
        return self.motion.magnitude_g - 1.0 if rise is None else rise

    def _anchor_rest(self):
        anchor = getattr(self.motion, "anchor_rest", None)
        if anchor is not None:
            anchor()

    def wrong_source(self, now, speak=True):
        self._set_state(STATE_WRONG_SOURCE)
        self._schedule_chatter(now)
        if speak:
            self._play_groups(("wait_on_pad",), now)
        else:
            self._interrupt_audio()

    def deliver(self, destination, now):
        if not (
            self.powered and self.phase == PHASE_TRANSIT
            and destination == self.destination and self.position == destination
        ):
            return
        print("Delivered to", STAND_NAMES[destination])
        # Select full-urn-only speech before clearing the delivered round.
        self._play_groups(("dropoff_effect", "delivered"), now, delivery=True)
        self.source = destination
        self.phase = PHASE_EMPTY
        self._set_state(STATE_REFILL_DELAY)
        self.fill_elapsed = 0.0
        self.delivery_elapsed = 0.0
        self.expiry_spoken = False
        self.pending_set_down_at = None
        self.refill_at = None if self.audio.busy else now + settings.REFILL_DELAY_S
        self.next_chatter = float("inf")
        self.delivery_until = now + settings.DELIVERY_CELEBRATION_S
        self._sync_background(now)

    def start_refill(self, now):
        if self.state != STATE_REFILL_DELAY or self.position != self.source:
            return
        self._set_state(STATE_FILLING)
        self.refill_at = None
        self.next_chatter = float("inf")
        self._interrupt_audio()
        self._sync_background(now)

    def talk_now(self, now):
        if not self.powered:
            return
        if self.state == STATE_FILLING:
            self.next_chatter = float("inf")
            return
        key = None
        if self.state in (STATE_UNHOMED, STATE_FILL_PAUSED):
            key = "unhomed_prompt"
        elif self.position in (STAND_NORTH, STAND_SOUTH) and self.state in (STATE_READY, STATE_WRONG_SOURCE):
            key = "wait_on_pad"
        elif self.position == POSITION_CARRIED and self.phase == PHASE_TRANSIT:
            key = "carrying"
        elif self.position == POSITION_GROUND:
            key = "wait_off_pad"
        self._schedule_chatter(now)
        if key is not None:
            self._play_groups((key,), now)

    def advance(self, now):
        if now < self.now:
            return
        # Also process completion callbacks when an action shares this timestamp.
        self.audio.take_completed()
        if (self._dialogue_waiting or self._delivery_waiting) and not self.audio.busy:
            self._finish_audio_timing(now)
        previous_time = self.now
        elapsed = now - previous_time
        self.now = now
        self.sim_time = now - self.boot_at
        if not self.powered:
            return
        fill_from = previous_time
        if (
            self.state == STATE_REFILL_DELAY and self.refill_at is not None
            and self.refill_at <= now + 0.000001 and self.position == self.source
        ):
            fill_from = max(previous_time, self.refill_at)
            self.start_refill(self.refill_at)
        if self.charging_active:
            remaining = settings.FILL_TIME_S - self.fill_elapsed
            filling_seconds = now - fill_from
            self.fill_elapsed = min(settings.FILL_TIME_S, self.fill_elapsed + filling_seconds)
            if self.fill_elapsed >= settings.FILL_TIME_S - 0.000001:
                self.complete_fill(fill_from + remaining)
        if self.phase == PHASE_TRANSIT:
            self.delivery_elapsed += elapsed
            if not self.expiry_spoken and self.delivery_elapsed >= settings.DELIVERY_EXPIRY_S:
                self.expiry_spoken = True
                self._schedule_chatter(now)
                self._play_groups(("urn_expires",), now)
        if self.pending_set_down_at is not None and now + 0.000001 >= self.pending_set_down_at:
            self.pending_set_down_at = None
            if self.position == POSITION_GROUND and self.phase == PHASE_TRANSIT:
                self._set_state(STATE_RESTING_WRONG)
                self._schedule_chatter(now)
                self._play_groups(("wait_off_pad",), now)
        if now + 0.000001 >= self.next_chatter and not self.audio.busy:
            self.talk_now(now)
        self._sync_background(now)

    def handle_stand_change(self, previous, present, now):
        print("Stand", STAND_NAMES[previous], "->", STAND_NAMES[present], "z=", self.stand.z_ut)
        if present == STAND_NONE:
            # An impact can arrive before the magnetic release debounce. The
            # physical lift/drop was already processed; release is not pickup.
            if self._hardware_drop_pending_release:
                self._hardware_drop_pending_release = False
                if self.position == POSITION_GROUND:
                    return
            pending_drop_until = self._hardware_drop_candidate_until
            pending_drop_quiet = self._hardware_drop_candidate_quiet_since
            print("Pickup trigger: stand release z=", round(self.stand.z_ut, 1))
            self.pick_up(now)
            # Magnetic release confirms the lift, not the impact's outcome.
            # Keep an in-progress hardware drop check across public pickup.
            self._hardware_drop_candidate_until = pending_drop_until
            self._hardware_drop_candidate_quiet_since = pending_drop_quiet
        else:
            # A rapid physical transfer may never leave NONE stable for the
            # debounce interval. Changing pads still implies a lift; public
            # place_on() retains the simulator's no-delivery-without-pickup guard.
            if previous in (STAND_NORTH, STAND_SOUTH) and present != previous:
                self.pick_up(now)
            self.place_on(present, now)

    def _handle_motion(self, now, moving, hard_drop):
        raw_stand = getattr(self.stand, "candidate", self.stand.current)
        if hard_drop and raw_stand == STAND_NONE:
            self._set_down_contact_at = self._physical_set_down_at = None
            self._ground_moving_since = None
            self.pending_set_down_at = None
            # A rebound can itself contain a short airborne interval. Extend
            # the settling guard without replaying or interrupting the drop.
            if self._hardware_drop_guard_until is not None and self.position == POSITION_GROUND:
                self._hardware_drop_guard_until = now + getattr(settings, "DROP_RECOVERY_GUARD_S", .8)
                self._hardware_drop_quiet_since = None
                return
            # Vigorous shaking can contain low-G/impact pairs. Require the
            # impact to settle before translating it to the public drop action.
            self._hardware_drop_candidate_until = now + getattr(settings, "DROP_CONFIRM_WINDOW_S", 1.5)
            self._hardware_drop_candidate_quiet_since = None
        if self._hardware_drop_candidate_until is not None:
            if raw_stand != STAND_NONE or now > self._hardware_drop_candidate_until:
                self._hardware_drop_candidate_until = None
                self._hardware_drop_candidate_quiet_since = None
            else:
                if moving or hard_drop:
                    self._hardware_drop_candidate_quiet_since = None
                else:
                    if self._hardware_drop_candidate_quiet_since is None:
                        self._hardware_drop_candidate_quiet_since = now
                    quiet = now - self._hardware_drop_candidate_quiet_since
                    if quiet + .000001 >= getattr(settings, "DROP_CONFIRM_QUIET_S", .4):
                        waiting_for_release = self.stand.current != STAND_NONE
                        if self.position in (STAND_NORTH, STAND_SOUTH):
                            self.pick_up(now)
                        elif self.position == POSITION_GROUND:
                            # Flight may have begun too gently to infer pickup.
                            self.position = POSITION_CARRIED
                        self.drop(now)
                        self._hardware_drop_guard_until = now + getattr(settings, "DROP_RECOVERY_GUARD_S", .8)
                        self._hardware_drop_quiet_since = None
                        self._hardware_drop_pending_release = waiting_for_release
                return
        if self.stand.current != STAND_NONE:
            self._set_down_contact_at = None
            self._physical_set_down_at = None
            self._ground_moving_since = None
            return
        if self.position == POSITION_GROUND:
            self._set_down_contact_at = None
            self._physical_set_down_at = None
            if self._hardware_drop_guard_until is not None:
                self._ground_moving_since = None
                if moving:
                    self._hardware_drop_quiet_since = None
                else:
                    if self._hardware_drop_quiet_since is None:
                        self._hardware_drop_quiet_since = now
                    quiet = now - self._hardware_drop_quiet_since
                    if (now >= self._hardware_drop_guard_until
                            and quiet >= getattr(settings, "DROP_RECOVERY_QUIET_S", .2)):
                        self._hardware_drop_guard_until = None
                        self._hardware_drop_quiet_since = None
                return
            if (self.pending_set_down_at is not None
                    and self._contact_rise() >= settings.SET_DOWN_CONTACT_RISE_G
                    and self.motion.jerk_g >= getattr(settings, "SET_DOWN_CONTACT_JERK_G", .14)
                    and self.motion.jerk_g < settings.PICKUP_JERK_G
                    and not getattr(self.motion, "motion_jingle_detected", False)):
                # Gentle settling can be below the broad movement threshold.
                self.pending_set_down_at = now + settings.SET_DOWN_STILL_S
                self.motion.stationary_since = now
                self._ground_moving_since = None
                return
            # A faint tinkle can come from bumping the table; only a firm
            # bell strike counts as the urn being lifted off the ground.
            strikes = getattr(self.motion, "bell_strikes", None)
            if strikes is None:
                firm_strike = getattr(self.motion, "motion_jingle_detected", False)
            else:
                firm_strike = any(
                    strength >= settings.GROUND_PICKUP_STRIKE for strength in strikes
                )
            lift = getattr(self.motion, "lift_g", None)
            if lift is not None:
                # Real sensor: only smoothed, sustained movement or a firm bell
                # strike means a lift. Single-sample jerk and raw "moving" are
                # noise- and speaker-sensitive while the urn talks on the ground.
                # While the urn itself is talking, its speaker shakes the
                # sensor: ignore bell strikes and ask for a clearly bigger lift.
                talking = self.audio.busy
                if firm_strike and not talking:
                    print("Pickup trigger: bell strike", [round(x, 2) for x in strikes])
                    self.pick_up(now)
                    return
                needed = settings.GROUND_PICKUP_LIFT_TALKING_G if talking else settings.GROUND_PICKUP_LIFT_G
                hold = settings.GROUND_PICKUP_HOLD_TALKING_S if talking else settings.GROUND_PICKUP_HOLD_S
                if lift < needed:
                    self._ground_moving_since = None
                    return
                if self._ground_moving_since is None:
                    self._ground_moving_since = now
                if now - self._ground_moving_since + .000001 >= hold:
                    print("Pickup trigger: lift", round(lift, 3), "talking=", talking)
                    self.pick_up(now)
                return
            if not moving and not firm_strike:
                self._ground_moving_since = None
                return
            if self._ground_moving_since is None:
                self._ground_moving_since = now
            if (self.motion.jerk_g >= settings.PICKUP_JERK_G or
                    firm_strike or
                    now - self._ground_moving_since >= getattr(settings, "PICKUP_MOTION_S", 0.15)):
                self.pick_up(now)
            return
        if self.position != POSITION_CARRIED or self.phase != PHASE_TRANSIT:
            return

        # Let a real contact settle before translating it to the simulator's
        # gentleSetDown action. The contact lifetime and quiet dwell are
        # separate: a short rebound must not consume the quiet candidate time.
        contact_window = getattr(settings, "SET_DOWN_CONTACT_WINDOW_S", 1.)
        contact = (
            self._contact_rise() >= settings.SET_DOWN_CONTACT_RISE_G
            and self.motion.jerk_g >= getattr(settings, "SET_DOWN_CONTACT_JERK_G", .14)
            and self._last_pick_up_at != now
        )
        if contact:
            if settings.DEBUG and self._set_down_contact_at is None:
                print("Set-down contact rise=", round(self._contact_rise(), 3),
                      "jerk=", round(self.motion.jerk_g, 3))
            self._set_down_contact_at = now
            self._physical_set_down_at = None
            self.motion.stationary_since = now
            return
        if moving:
            # A new deliberate jerk cancels the candidate silently. A mild
            # rebound can restart quiet while the contact is still recent.
            if ((self._physical_set_down_at is not None
                 and self.motion.jerk_g >= settings.SET_DOWN_CANCEL_JERK_G) or
                    (self._set_down_contact_at is not None and
                     now - self._set_down_contact_at > contact_window)):
                self._set_down_contact_at = None
            self._physical_set_down_at = None
            return
        quiet_for = self.motion.still_for(now)
        if self._set_down_contact_at is not None:
            quiet_started = now - quiet_for
            if quiet_started - self._set_down_contact_at > contact_window + 0.000001:
                self._set_down_contact_at = None
                self._physical_set_down_at = None
            else:
                self._physical_set_down_at = quiet_started
            if self._physical_set_down_at is not None and quiet_for + .000001 >= settings.SET_DOWN_STILL_S:
                print("Set-down confirmed after", round(quiet_for, 2), "s quiet")
                self.gentle_set_down(now)
                # The physical confirmation represents contact at the start
                # of this quiet period, not another 2.5 seconds from this poll.
                self.pending_set_down_at = quiet_started + settings.SET_DOWN_STILL_S
                self.advance(now)

    def update(self, now):
        self.audio.update(now)
        # Settling delays ground confirmation; it does not invent a new lift.
        # _handle_motion applies the separate deliberate-pickup conditions.
        moving, hard_drop = self.motion.update(now)
        settling_contact = (
            self._contact_rise() >= settings.SET_DOWN_CONTACT_RISE_G
            and self.motion.jerk_g >= getattr(settings, "SET_DOWN_CONTACT_JERK_G", .14)
        )
        if self.pending_set_down_at is not None and (moving or hard_drop or settling_contact):
            self.pending_set_down_at = now + settings.SET_DOWN_STILL_S
        self.advance(now)
        previous_stand = self.stand.current
        if self.stand.update(now):
            self.handle_stand_change(previous_stand, self.stand.current, now)
        self._handle_motion(now, moving, hard_drop)
        self._sync_background(now)
        if self.state == STATE_CARRIED and self.position == POSITION_CARRIED:
            strikes = getattr(self.motion, "bell_strikes", None)
            if strikes is None:
                if getattr(self.motion, "motion_jingle_detected", False):
                    self.audio.play_motion_jingle(now)
            else:
                # Loudest first: a soft strike in the same poll cannot mask it.
                for strength in sorted(strikes, reverse=True):
                    self.audio.play_motion_jingle(now, strength)
        self.lights.render(now, self)
        if settings.DEBUG and now - self.last_debug >= settings.DEBUG_SENSOR_INTERVAL_S:
            print(
                "debug state=", STATE_NAMES[self.state],
                "position=", ("none", "north", "south", "carried", "ground")[self.position],
                "stand=", STAND_NAMES[self.stand.current],
                "z_uT=", round(self.stand.z_ut, 1),
                "g=", round(self.motion.magnitude_g, 2),
                "jerk=", round(self.motion.jerk_g, 2),
                "dyn_g=", round(getattr(self.motion, "dynamic_g", 0.), 2),
                "lift_g=", round(getattr(self.motion, "lift_g", 0.), 3),
                "still_s=", round(self.motion.still_for(now), 1),
                "talking=", self.audio.busy,
                "seat_uT=", round(getattr(self.stand, "seated_ut", 0.), 0),
                "fill=", round(self.fill_fraction() * 100),
                "jingles=", getattr(self.audio, "carrying_active", False),
                "pending_drop=", self._hardware_drop_candidate_until is not None,
            )
            self.last_debug = now


def connect_magnetometer(i2c):
    import adafruit_tmag5273

    sensor = adafruit_tmag5273.TMAG5273(i2c)
    sensor.z_range_wide = True
    return sensor


def main():
    # This enables the board's 5V NeoPixel terminal and I2S amplifier.
    external_power = digitalio.DigitalInOut(board.EXTERNAL_POWER)
    external_power.switch_to_output(value=True)
    time.sleep(0.1)

    # Reserve the large contiguous decoder allocation before mixer/pixel buffers.
    audio_output = audiobusio.I2SOut(
        board.I2S_BIT_CLOCK,
        board.I2S_WORD_SELECT,
        board.I2S_DATA,
    )
    player = AudioPlayer(
        audio_output,
        settings.AUDIO_ROOT + "/soul_urn_pickup/soul_urn_pickup_01.mp3",
    )

    # Python drivers load after the large native audio allocations are reserved.
    import neopixel

    order = getattr(neopixel, settings.PIXEL_ORDER)
    pixels = neopixel.NeoPixel(
        board.EXTERNAL_NEOPIXELS,
        settings.PIXEL_COUNT,
        brightness=settings.PIXEL_BRIGHTNESS,
        auto_write=False,
        pixel_order=order,
        bpp=len(settings.PIXEL_ORDER),
    )
    lights = SoulLights(pixels)
    lights.show_search(time.monotonic())

    try:
        # Audio lives directly in CIRCUITPY/sounds on the board's onboard flash.
        library = AudioLibrary(settings.AUDIO_ROOT)
    except (OSError, RuntimeError, ValueError) as error:
        print("Onboard audio initialization failed; continuing lights-only:", error)
        library = AudioLibrary("/missing")

    # The onboard LIS3DH and STEMMA QT sensor share this board's I2C bus.
    import adafruit_lis3dh

    i2c = board.I2C()
    accelerometer = adafruit_lis3dh.LIS3DH_I2C(
        i2c,
        address=0x18,
    )
    # 4 G halves the quantization step of 8 G for stillness and bell drive;
    # drop impacts only need to exceed DROP_IMPACT_G, so clipping at 4 G is fine.
    accelerometer.range = getattr(
        adafruit_lis3dh, "RANGE_%d_G" % settings.ACCELEROMETER_RANGE_G, adafruit_lis3dh.RANGE_4_G
    )

    stand = StandDetector(reconnect=lambda: connect_magnetometer(i2c))
    motion = MotionTracker(accelerometer)
    controller = UrnController(
        stand,
        motion,
        library,
        player,
        lights,
    )

    # Allow the magnetometer and debounce logic to establish a boot position.
    settle_until = time.monotonic() + settings.BOOT_SENSOR_SETTLE_S
    while time.monotonic() < settle_until:
        now = time.monotonic()
        stand.update(now)
        motion.update(now)
        lights.show_search(now, missing_sensor=not stand.available)
        time.sleep(settings.SENSOR_SAMPLE_S)

    controller.resolve_boot(time.monotonic())
    loop_count = 0
    loop_worst = 0.0
    report_at = time.monotonic() + settings.DEBUG_SENSOR_INTERVAL_S
    while True:
        started = time.monotonic()
        controller.update(started)
        finished = time.monotonic()
        busy = finished - started
        # Hold a steady sample period instead of adding a full sleep after work.
        remaining = settings.SENSOR_SAMPLE_S - busy
        if remaining > 0:
            time.sleep(remaining)
        if settings.DEBUG:
            loop_count += 1
            if busy > loop_worst:
                loop_worst = busy
            if finished >= report_at:
                print("loop hz=", loop_count / settings.DEBUG_SENSOR_INTERVAL_S,
                      "worst_ms=", round(loop_worst * 1000))
                loop_count = 0
                loop_worst = 0.0
                report_at = finished + settings.DEBUG_SENSOR_INTERVAL_S


if __name__ == "__main__":
    main()
