"""The mapping engine: pure functions from a user's GitHub data to a city and its music.

No I/O happens here: no database, no network, no clock, no global randomness. Everything is
passed in (including the `as_of` date that "recent" is measured from), so the same input always
produces exactly the same output.
"""

from app.mapping.city import Bridge, Building, CityLayout, District, Plaza, build_city
from app.mapping.inputs import RepoData, UserData
from app.mapping.music import MusicParams, Track, build_music
from app.mapping.snapshot import snapshot_at
from app.mapping.version import MAPPING_VERSION

__all__ = [
    "MAPPING_VERSION",
    "Bridge",
    "Building",
    "CityLayout",
    "District",
    "MusicParams",
    "Plaza",
    "RepoData",
    "Track",
    "UserData",
    "build_city",
    "build_music",
    "snapshot_at",
]
