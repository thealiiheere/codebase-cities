"""Just enough music theory: keys, scales, and turning scale degrees into note names.

Note names use the format Tone.js plays directly, e.g. "C4" (middle C) or "F#3".
"""

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# Semitones above the key's root for each scale degree.
SCALES: dict[str, tuple[int, ...]] = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "major-pentatonic": (0, 2, 4, 7, 9),
    "minor-pentatonic": (0, 3, 5, 7, 10),
}
SCALE_NAMES = tuple(SCALES)

# Four-chord progressions, as the scale degree each chord is built on (0 = the key's root).
PROGRESSIONS: tuple[tuple[int, int, int, int], ...] = (
    (0, 4, 5, 3),  # I-V-vi-IV, the "pop" progression
    (0, 5, 3, 4),  # I-vi-IV-V, the 50s progression
    (5, 3, 0, 4),  # vi-IV-I-V
    (0, 3, 0, 4),  # I-IV-I-V
)


def note_name(key: int, scale: str, degree: int, octave: int) -> str:
    """The note at `degree` steps up `scale` from the root of `key` (0 = C ... 11 = B).

    Degrees past the end of the scale continue into the next octave; negative ones go down.
    """
    intervals = SCALES[scale]
    octave_shift, index = divmod(degree, len(intervals))
    semitone = key + intervals[index]
    return f"{NOTE_NAMES[semitone % 12]}{octave + octave_shift + semitone // 12}"


def triad(key: int, scale: str, root_degree: int, octave: int) -> tuple[str, ...]:
    """A three-note chord stacked in thirds on a scale degree, staying inside the scale."""
    return tuple(note_name(key, scale, root_degree + step, octave) for step in (0, 2, 4))
