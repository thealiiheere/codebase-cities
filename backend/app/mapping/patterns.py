"""Note patterns for each track role.

A pattern is a sequence of steps (16 per bar, i.e. sixteenth notes). Each step lists what plays
on it: note names for pitched instruments, drum names ("kick", "snare", "hat") for drums, or
nothing for a rest. The frontend just plays them in a loop.
"""

from collections.abc import Callable
from dataclasses import dataclass

from app.mapping.rng import SeededRng
from app.mapping.scaling import clamp
from app.mapping.theory import note_name, triad

STEPS_PER_BAR = 16
BARS = 4

Step = tuple[str, ...]
Pattern = tuple[Step, ...]
REST: Step = ()


@dataclass(frozen=True)
class PatternContext:
    key: int
    scale: str
    # One chord root (scale degree) per bar.
    progression: tuple[int, ...]
    # 0..1: how many of the possible notes actually play.
    density: float
    # 0..1: how much off-beat notes are favored.
    syncopation: float


def beat_strength(step: int, syncopation: float) -> float:
    """How natural it is for a note to land on this step of the bar (0..1)."""
    if step % STEPS_PER_BAR == 0:
        return 1.0
    if step % 4 == 0:
        return 0.85
    if step % 2 == 0:
        return 0.5 + 0.3 * syncopation
    return 0.2 + 0.4 * syncopation


def melody(rng: SeededRng, ctx: PatternContext) -> Pattern:
    """A random walk over the scale that returns to the chord's root at each bar line."""
    steps: list[Step] = []
    degree = ctx.progression[0]
    for root in ctx.progression:
        for step in range(STEPS_PER_BAR):
            if step == 0:
                degree = root
            elif rng.chance(ctx.density * beat_strength(step, ctx.syncopation)):
                degree = int(clamp(degree + rng.pick((-2, -1, -1, 1, 1, 2)), -2, 9))
            else:
                steps.append(REST)
                continue
            steps.append((note_name(ctx.key, ctx.scale, degree, 4),))
    return tuple(steps)


def bass(rng: SeededRng, ctx: PatternContext) -> Pattern:
    """The chord's root on the downbeat, the root or fifth mid-bar, a few passing notes."""
    steps: list[Step] = []
    for root in ctx.progression:
        for step in range(STEPS_PER_BAR):
            if step == 0:
                degree: int | None = root
            elif step == 8:
                degree = root + rng.pick((0, 4))
            elif step % 2 == 0 and rng.chance(0.4 * ctx.density * beat_strength(step, 0)):
                degree = root
            else:
                degree = None
            steps.append(REST if degree is None else (note_name(ctx.key, ctx.scale, degree, 2),))
    return tuple(steps)


def chords(rng: SeededRng, ctx: PatternContext) -> Pattern:
    """A sustained chord per bar, re-struck halfway through when the music is dense."""
    steps: list[Step] = []
    for root in ctx.progression:
        chord = triad(ctx.key, ctx.scale, root, 3)
        for step in range(STEPS_PER_BAR):
            restrike = step == 8 and rng.chance(ctx.density - 0.3)
            steps.append(chord if step == 0 or restrike else REST)
    return tuple(steps)


def arpeggio(rng: SeededRng, ctx: PatternContext) -> Pattern:
    """The chord's notes played one at a time going up: eighths, or sixteenths when dense."""
    every = 1 if ctx.density > 0.6 else 2
    steps: list[Step] = []
    for root in ctx.progression:
        tones = (*triad(ctx.key, ctx.scale, root, 4), note_name(ctx.key, ctx.scale, root, 5))
        played = 0
        for step in range(STEPS_PER_BAR):
            if step % every == 0 and rng.chance(0.6 + 0.4 * ctx.density):
                steps.append((tones[played % len(tones)],))
                played += 1
            else:
                steps.append(REST)
    return tuple(steps)


def drums(rng: SeededRng, ctx: PatternContext) -> Pattern:
    """Kick on 1 and 3, snare on 2 and 4, hats that fill in as the music gets denser."""
    steps: list[Step] = []
    for _ in ctx.progression:
        for step in range(STEPS_PER_BAR):
            hits: list[str] = []
            if step in (0, 8) or (step in (6, 10, 14) and rng.chance(0.4 * ctx.syncopation)):
                hits.append("kick")
            if step in (4, 12):
                hits.append("snare")
            hat_chance = 0.5 + 0.5 * ctx.density if step % 2 == 0 else 0.6 * ctx.density
            if rng.chance(hat_chance):
                hits.append("hat")
            steps.append(tuple(hits))
    return tuple(steps)


GENERATORS: dict[str, Callable[[SeededRng, PatternContext], Pattern]] = {
    "melody": melody,
    "bass": bass,
    "chords": chords,
    "arpeggio": arpeggio,
    "drums": drums,
}
