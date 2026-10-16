"""Language colors: the single place that decides what color a language is.

Known languages use GitHub's own linguist colors, so cities look familiar to developers. Any
other language gets a color derived from its name, so it is stable without being listed here.
"""

import colorsys

from app.mapping.rng import stable_hash

# District name for repos whose primary language GitHub couldn't determine.
OTHER = "Other"

LANGUAGE_COLORS: dict[str, str] = {
    "C": "#555555",
    "C#": "#178600",
    "C++": "#f34b7d",
    "CSS": "#563d7c",
    "Clojure": "#db5855",
    "CoffeeScript": "#244776",
    "Dart": "#00b4ab",
    "Dockerfile": "#384d54",
    "Elixir": "#6e4a7e",
    "Elm": "#60b5cc",
    "Erlang": "#b83998",
    "Go": "#00add8",
    "HTML": "#e34c26",
    "Haskell": "#5e5086",
    "Java": "#b07219",
    "JavaScript": "#f1e05a",
    "Julia": "#a270ba",
    "Jupyter Notebook": "#da5b0b",
    "Kotlin": "#a97bff",
    "Lua": "#000080",
    "Makefile": "#427819",
    "Nix": "#7e7eff",
    "OCaml": "#ef7a08",
    "Objective-C": "#438eff",
    "PHP": "#4f5d95",
    "Perl": "#0298c3",
    "PowerShell": "#012456",
    "Python": "#3572a5",
    "R": "#198ce7",
    "Ruby": "#701516",
    "Rust": "#dea584",
    "SCSS": "#c6538c",
    "Scala": "#c22d40",
    "Shell": "#89e051",
    "Solidity": "#aa6746",
    "Svelte": "#ff3e00",
    "Swift": "#f05138",
    "TeX": "#3d6117",
    "TypeScript": "#3178c6",
    "Vim Script": "#199f4b",
    "Vue": "#41b883",
    "Zig": "#ec915c",
    OTHER: "#8b949e",
}


def language_color(language: str) -> str:
    known = LANGUAGE_COLORS.get(language)
    if known is not None:
        return known
    # Hue from the name; fixed saturation and lightness keep these colors readable at night.
    hue = (stable_hash(language) % 360) / 360
    red, green, blue = colorsys.hls_to_rgb(hue, 0.55, 0.6)
    return "#{:02x}{:02x}{:02x}".format(*(round(channel * 255) for channel in (red, green, blue)))
