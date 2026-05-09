"""Procedural particle-based screensaver — VISUALLY identical to
screensaver_replay (same Gaussian-ellipse sprites, same brightness scaling)
but with particles generated procedurally instead of replayed from a fixed
data file. No two boots produce the same sequence → no burn-in.

Reuses screensaver_replay's renderer helpers so any tuning of the look
stays in one place.

Statistical fingerprint matches assets/idle.gif (analyzed 2026-05-09):
  density: ~70 visible blobs at any instant
  spawn:   ~23 new particles per frame (Poisson) to maintain that
  size:    horizontal-biased; matches idle.gif bbox distribution
  speed:   median ~4.6 px/frame, slight leftward drift
  shape:   horizontal Gaussian ellipses (rx > ry for larger sizes)
  fading:  short-lived (1-3) firefly flash; long-lived stay bright until edge
"""
import math
import random
from PIL import Image

from .screensaver_replay import _get_scaled

_particles = []
_DRIFTER_MAX_AGE = 200


class _Particle:
    __slots__ = ("x", "y", "vx", "vy", "age", "lifetime", "peak",
                 "rx", "ry", "is_flash")

    def __init__(self, w, h, drift_x, drift_y, median_speed):
        self.x = random.uniform(0, w)
        self.y = random.uniform(0, h)
        angle = random.uniform(0, 2 * math.pi)
        speed = random.expovariate(1.0 / median_speed)
        self.vx = math.cos(angle) * speed + drift_x
        self.vy = math.sin(angle) * speed + drift_y
        self.age = 0
        natural = _sample_lifetime()
        self.is_flash = (natural <= 3)
        # Drifters die only at the screen edge or _DRIFTER_MAX_AGE cap
        self.lifetime = natural if self.is_flash else _DRIFTER_MAX_AGE
        self.rx, self.ry = _sample_size()
        self.peak = _sample_peak(self.rx, self.ry)


def _sample_lifetime():
    r = random.random()
    if r < 0.30:
        return 1
    if r < 0.58:
        return random.randint(2, 3)
    if r < 0.78:
        return random.randint(4, 6)
    if r < 0.91:
        return random.randint(7, 12)
    return random.randint(13, 44)


def _sample_size():
    """(rx, ry) half-extents. Distribution matches idle.gif bbox stats:
    most are tiny 3x3, with a long tail toward horizontal blobs (aspect 2-2.3
    for the larger ones)."""
    r = random.random()
    if r < 0.31:
        return (1, 1)                                            # 31% tiny  (3x3)
    if r < 0.53:
        return random.choice(((1, 1), (2, 1)))                    # 22% small
    if r < 0.72:
        return random.choice(((2, 1), (2, 2), (3, 1)))            # 19% medium
    if r < 0.88:
        return random.choice(((3, 2), (4, 2)))                    # 16% bigger
    # 12% rare large drifters with horizontal aspect
    return (random.randint(4, 7), random.randint(2, 4))


def _sample_peak(rx, ry):
    """Peak brightness — bigger blobs tend higher (verified vs idle.gif)."""
    big = max(rx, ry)
    if big <= 1:
        return random.randint(60, 150)
    if big <= 2:
        return random.randint(80, 180)
    if big <= 3:
        return random.randint(100, 220)
    return random.randint(150, 241)


def paint(device, target_population=70, spawn_rate=23.0,
          drift_x=-0.4, drift_y=0.0, median_speed=4.6):
    """Advance the particle sim one step and render to the device."""
    img = _render(device, target_population, spawn_rate, drift_x, drift_y, median_speed)
    device.display(img)


def paint_to_image(device, target_population=70, spawn_rate=23.0,
                    drift_x=-0.4, drift_y=0.0, median_speed=4.6):
    """Same as paint() but returns the image instead of displaying.
    Used during transitions where we need to composite the screensaver under
    a symbol GIF overlay. Still mutates particle state (1 tick advance per call)."""
    return _render(device, target_population, spawn_rate, drift_x, drift_y, median_speed)


def _render(device, target_population, spawn_rate, drift_x, drift_y, median_speed):
    """Internal: advance sim and return the rendered image."""
    global _particles
    w, h = device.width, device.height

    # 1. Age, move, cull. Larger per-frame velocity perturbation (±0.6 vs ±0.3)
    # makes particles wobble more — feels more alive / random.
    survivors = []
    for p in _particles:
        p.age += 1
        if p.age >= p.lifetime:
            continue
        p.vx += random.uniform(-0.6, 0.6)
        p.vy += random.uniform(-0.6, 0.6)
        p.x += p.vx
        p.y += p.vy
        margin = max(p.rx, p.ry) + 2
        if p.x < -margin or p.x >= w + margin:
            continue
        if p.y < -margin or p.y >= h + margin:
            continue
        survivors.append(p)
    _particles = survivors

    # 2. Spawn (Poisson approximation)
    n_new = max(0, int(round(spawn_rate + random.gauss(0, math.sqrt(spawn_rate)))))
    if len(_particles) + n_new > target_population * 2:
        n_new = max(0, target_population * 2 - len(_particles))
    for _ in range(n_new):
        _particles.append(_Particle(w, h, drift_x, drift_y, median_speed))

    # 3. Render — reuse the replay renderer's sprite cache + brightness scaling
    img_l = Image.new("L", (w, h), 0)

    # Sort dim-first so brighter sprites win on overlap
    _particles.sort(key=lambda p: p.peak)

    for p in _particles:
        # Brightness envelope. Use (age + 0.5) so newly-spawned particles
        # (age=0) are already visible on their first paint — without this,
        # the first frame of the screensaver is BLACK (everyone at age=0
        # produces env=0), which breaks the screen-change crossfade by
        # giving it a black target image to fade to.
        if p.is_flash:
            if p.lifetime <= 1:
                env = 1.0
            else:
                frac = (p.age + 0.5) / p.lifetime
                env = 1.0 - abs(2.0 * frac - 1.0)
        else:
            env = min(1.0, (p.age + 0.5) / 2.0)
        if env <= 0:
            continue
        # Tiny per-frame brightness jitter (±8%) so drifters subtly twinkle
        # instead of being a flat-bright spot — adds organic feel.
        jitter = 1.0 + random.uniform(-0.08, 0.08)
        b = max(0, min(255, int(p.peak * env * jitter)))
        scaled = _get_scaled(p.rx, p.ry, b)
        if scaled is None:
            continue
        sw, sh = scaled.size
        x = int(p.x) - sw // 2
        y = int(p.y) - sh // 2
        img_l.paste(scaled, (x, y), mask=scaled)

    if device.mode == "L":
        return img_l
    return img_l.convert(device.mode)


def reset():
    """Clear all particles — used on screen transitions to start fresh."""
    global _particles
    _particles = []
