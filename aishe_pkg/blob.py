"""Blob — terminal metaball renderer.

Pure-Python 2D metaball field rendered to ANSI 24-bit color blocks.
Falls back to 256-color, then ASCII shading when 24-bit is unsupported.

Field model:
    F(x, y) = Σ_i  strength_i / (r_i + epsilon)
where r_i = distance from point mass i.
A pixel is "inside" when F > threshold. Smooth gradient via supersampling
(2x2 sub-samples per cell).
"""

from __future__ import annotations

import math
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ─── Capability detection ───────────────────────────────────────────────────

_GRADIENT = " ░▒▓█"


def _term_supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    term = os.environ.get("TERM", "")
    colorterm = os.environ.get("COLORTERM", "")
    if colorterm in ("truecolor", "24bit"):
        return True
    if "256color" in term:
        return True
    # Terminal.app + iTerm2 default to xterm-256color on macOS
    return term.endswith("256color") or term == "xterm-256color" or term == "xterm"


def _term_supports_truecolor() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    colorterm = os.environ.get("COLORTERM", "")
    if colorterm in ("truecolor", "24bit"):
        return True
    # iTerm2 sets this; Terminal.app on recent macOS too
    term_program = os.environ.get("TERM_PROGRAM", "")
    return term_program in ("iTerm.app", "Apple_Terminal") or "256color" in os.environ.get("TERM", "")


# ─── Point mass + field ─────────────────────────────────────────────────────

@dataclass
class PointMass:
    """A single orbiting point mass in the metaball field."""
    angle: float          # current orbit angle (radians)
    radius: float         # orbit radius (0..1, fraction of half-width)
    speed: float          # angular speed (radians per tick)
    phase: float          # noise phase offset
    strength: float = 1.0 # contribution to field

    def tick(self, t: float, wobble: float = 0.08) -> None:
        """Advance position. Adds a small breathing perturbation."""
        # Base orbit
        self.angle = (self.angle + self.speed) % (2 * math.pi)
        # Wobble: radius modulates with sin(t + phase) so blob breathes
        self.radius = self._base_radius * (1.0 + wobble * math.sin(t * 1.3 + self.phase))
        # Slight strength flicker
        self.strength = 0.9 + 0.1 * math.sin(t * 2.0 + self.phase * 1.7)

    _base_radius: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._base_radius = self.radius


@dataclass
class BlobParams:
    """All the knobs the blob exposes. Inspectable via `aishe pet inspect`."""
    width: int = 28
    height: int = 10
    threshold: float = 1.0       # F > threshold is "inside"
    n_points: int = 8
    orbit_radius: float = 0.45    # fraction of half-width
    orbit_speed: float = 0.12     # radians per frame
    wobble: float = 0.08
    # Palette: list of (r, g, b) tuples, low → high field intensity.
    # Constrained to shades of black/white + Indian flag colors
    # (saffron #FF9933, white #FFFFFF, green #138808, Ashoka Chakra navy #000080).
    # This is the default palette used by idle / listening / speaking /
    # greeting states — a Tiranga gradient: black → navy → saffron → white
    # → green → white.
    palette: List[Tuple[int, int, int]] = field(default_factory=lambda: [
        (0, 0, 0),         # black core
        (0, 0, 80),        # chakra navy
        (255, 153, 51),    # India saffron
        (255, 255, 255),   # white
        (19, 136, 8),      # India green
        (255, 255, 255),   # white highlight
    ])
    seed: int = 0

    def __post_init__(self) -> None:
        import random
        self._rng = random.Random(self.seed)


# ─── Field sampler ──────────────────────────────────────────────────────────

def _sample_field(
    x: float, y: float,
    cx: float, cy: float,
    half_w: float, half_h: float,
    points: List[PointMass],
) -> float:
    """Compute F(x, y) for the given point set."""
    total = 0.0
    for p in points:
        px = cx + math.cos(p.angle) * p.radius * half_w
        py = cy + math.sin(p.angle) * p.radius * half_h
        dx = x - px
        dy = y - py
        r = math.sqrt(dx * dx + dy * dy)
        # Soft 1/r with smoothing
        total += p.strength / (r * 0.6 + 0.5)
    return total


# ─── Frame render ───────────────────────────────────────────────────────────

def _lerp_palette(
    palette: List[Tuple[int, int, int]],
    t: float,  # 0..1
) -> Tuple[int, int, int]:
    """Sample palette at normalized position t in [0, 1]."""
    if t <= 0:
        return palette[0]
    if t >= 1:
        return palette[-1]
    n = len(palette) - 1
    pos = t * n
    i = int(pos)
    f = pos - i
    a = palette[i]
    b = palette[min(i + 1, n)]
    return (
        int(a[0] + (b[0] - a[0]) * f),
        int(a[1] + (b[1] - a[1]) * f),
        int(a[2] + (b[2] - a[2]) * f),
    )


def render_frame(
    params: BlobParams,
    points: List[PointMass],
    use_color: bool = True,
    use_truecolor: bool = True,
) -> str:
    """Render one frame of the blob as a string of lines.

    Returns a list of (row) strings without a trailing newline. Each cell is:
    - truecolor: a 24-bit ANSI background-colored space ' '
    - 256-color: same, with 24-bit-equivalent nearest color
    - ASCII: a single character from ' .:-=+*#%@'
    """
    w, h = params.width, params.height
    cx = (w - 1) / 2.0
    cy = (h - 1) / 2.0
    half_w = w / 2.0
    half_h = h / 2.0

    # Field is normalized against a meaningful range: threshold is the
    # "edge" (norm=0.0), and ~3.5 * threshold is the "core" (norm=1.0).
    # This makes the visual response independent of how many points are
    # configured — a 5-point blob and a 12-point blob both have visible
    # warm cores.
    t_min = params.threshold
    t_max = params.threshold * 3.5

    # For truecolor, build the full ANSI sequence once per unique color (cheap)
    rows: List[str] = []
    fg_codes: List[List[str]] = []  # for truecolor

    for y in range(h):
        line = ""
        row_codes: List[str] = []
        for x in range(w):
            # 2x2 supersample for smoother edges
            f_sum = 0.0
            for sy in (0.0, 0.5):
                for sx in (0.0, 0.5):
                    f_sum += _sample_field(
                        x + sx, y + sy, cx, cy, half_w, half_h, points,
                    )
            f_avg = f_sum / 4.0
            # Map F to [0, 1] using threshold-anchored range, smoothstep
            if f_avg <= t_min:
                norm = 0.0
            elif f_avg >= t_max:
                norm = 1.0
            else:
                t = (f_avg - t_min) / (t_max - t_min)
                # Smoothstep for nicer gradient
                norm = t * t * (3 - 2 * t)

            if not use_color:
                # ASCII shading: index 0 = empty (space)
                if norm < 0.05:
                    ch = " "
                else:
                    idx = max(1, int(norm * (len(_GRADIENT) - 1)))
                    ch = _GRADIENT[idx]
                line += ch
            elif use_truecolor:
                r, g, b = _lerp_palette(params.palette, norm)
                code = f"\033[48;2;{r};{g};{b}m "
                row_codes.append(code)
            else:
                # 256-color: map (r,g,b) → nearest 6x6x6 cube
                r, g, b = _lerp_palette(params.palette, norm)
                idx = 16 + (36 * (r // 51)) + (6 * (g // 51)) + (b // 51)
                code = f"\033[48;5;{idx}m "
                row_codes.append(code)
        if use_color:
            line = "".join(row_codes) + "\033[0m"
        rows.append(line)

    return "\n".join(rows)


# ─── Convenience: build points ──────────────────────────────────────────────

def make_points(params: BlobParams, t: float = 0.0) -> List[PointMass]:
    """Create the default point-mass configuration."""
    rng = params._rng
    pts: List[PointMass] = []
    for i in range(params.n_points):
        # Distribute angles around the circle, then jitter
        base_angle = (2 * math.pi * i) / max(params.n_points, 1)
        pts.append(PointMass(
            angle=base_angle + rng.uniform(-0.2, 0.2),
            radius=params.orbit_radius * rng.uniform(0.4, 1.0),
            speed=params.orbit_speed * rng.uniform(0.6, 1.4) * (1 if i % 2 == 0 else -1),
            phase=rng.uniform(0, 2 * math.pi),
        ))
    # Advance to t so we can render a non-initial frame
    if t > 0:
        for _ in range(int(t * 10)):
            for p in pts:
                p.tick(t, params.wobble)
    return pts


# ─── Static frame builder ───────────────────────────────────────────────────

def static_frame(
    width: int = 28,
    height: int = 10,
    state: str = "idle",
    seed: int = 0,
    use_color: Optional[bool] = None,
) -> str:
    """One-shot frame, suitable for `aishe pet status`.

    `state` slightly perturbs the parameters (idle=calm, thinking=more points,
    error=asymmetric).
    """
    if use_color is None:
        use_color = _term_supports_color()
    truecolor = _term_supports_truecolor() if use_color else False

    params = BlobParams(width=width, height=height, seed=seed)

    if state == "thinking":
        params.n_points = 12
        params.orbit_speed = 0.18
    elif state == "error":
        params.n_points = 5
        params.wobble = 0.25
        # Error palette — saffron stands in for "warning" (courage/sacrifice
        # in the flag's symbolism). Stays within B/W + Indian flag colors.
        params.palette = [
            (0, 0, 0),         # black
            (40, 30, 12),      # deep saffron shadow (B/W + saffron mix)
            (160, 96, 32),     # darkened saffron
            (255, 153, 51),    # India saffron
            (255, 230, 200),   # saffron-tinted white
            (255, 255, 255),   # white
        ]
    elif state == "listening":
        params.n_points = 10
        params.wobble = 0.15
    elif state == "speaking":
        # Vertical squash
        params.height = max(6, height - 2)
        params.orbit_speed = 0.22

    points = make_points(params, t=0.0)
    # Advance a few ticks so it doesn't look like a perfect ring
    for _ in range(5):
        for p in points:
            p.tick(0.5, params.wobble)

    return render_frame(params, points, use_color=use_color, use_truecolor=truecolor)


# ─── Self-test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Quick visual smoke test
    for state in ("idle", "thinking", "listening", "speaking", "error"):
        print(f"\n  ── {state} ──")
        print(static_frame(state=state, width=32, height=10))
        print()
