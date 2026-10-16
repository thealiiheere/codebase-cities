"""Music parameters: tempo, key, intensity, tracks, and patterns."""

import re

import pytest

from app.mapping import MusicParams, build_music
from app.mapping.music import INSTRUMENTS, MAX_BPM, MIN_BPM, ROLES
from app.mapping.theory import NOTE_NAMES, SCALE_NAMES
from tests.mapping_fakes import AS_OF, days_ago, random_user, repo, user

NOTE = re.compile(r"^[A-G]#?-?\d$")
DRUMS = {"kick", "snare", "hat"}


def music_of(*repos: object, **user_fields: object) -> MusicParams:
    return build_music(user(*repos, **user_fields), as_of=AS_OF)  # type: ignore[arg-type]


STEADY = {days_ago(day): 2 for day in range(365)}
BURSTY = {days_ago(day): 40 for day in range(0, 365, 30)}


def test_steady_activity_is_calm_and_even() -> None:
    music = music_of(repo("r", 1), contributions=STEADY)

    assert music.tempo_bpm == MIN_BPM
    assert music.tempo_variation == 0


def test_bursty_activity_is_faster_and_more_varied() -> None:
    steady = music_of(repo("r", 1), contributions=STEADY)
    bursty = music_of(repo("r", 1), contributions=BURSTY)

    assert bursty.tempo_bpm > steady.tempo_bpm
    assert bursty.tempo_variation > 0.7
    assert MIN_BPM <= bursty.tempo_bpm <= MAX_BPM


def test_inactive_profile_is_calm_and_quiet() -> None:
    music = music_of(repo("r", 1))

    assert music.tempo_bpm == MIN_BPM
    assert music.intensity == 0


def test_intensity_follows_the_last_90_days() -> None:
    recent = music_of(repo("r", 1), contributions={days_ago(5): 60})
    old = music_of(repo("r", 1), contributions={days_ago(200): 60})

    assert recent.intensity > 0.5
    assert old.intensity == 0


def test_falls_back_to_repo_commits_without_a_calendar() -> None:
    music = music_of(repo("r", 1, daily_commits={days_ago(1): 80}))

    assert music.intensity > 0.5


def test_key_and_scale_come_from_the_username() -> None:
    a, b = music_of(login="octocat"), music_of(login="OctoCat")
    others = {(music_of(login=f"user{i}").key, music_of(login=f"user{i}").scale) for i in range(40)}

    assert (a.key, a.scale) == (b.key, b.scale)
    assert a.key in NOTE_NAMES
    assert a.scale in SCALE_NAMES
    assert len(others) > 10  # Different users really do get different keys.


def test_top_five_languages_become_tracks_by_byte_share() -> None:
    languages = {"Python": 600, "Go": 200, "Rust": 100, "C": 50, "Zig": 30, "Lua": 20}
    music = music_of(repo("r", 1, languages=languages))

    assert [t.language for t in music.tracks] == ["Python", "Go", "Rust", "C", "Zig"]
    assert [t.role for t in music.tracks] == list(ROLES)
    assert music.tracks[0].share == 0.6
    volumes = [t.volume for t in music.tracks]
    assert volumes == sorted(volumes, reverse=True)
    assert all(0 < v <= 1 for v in volumes)


def test_language_bytes_add_up_across_repos() -> None:
    music = music_of(
        repo("a", 1, languages={"Go": 10, "Python": 15}),
        repo("b", 2, languages={"Go": 10}),
    )

    assert [(t.language, t.share) for t in music.tracks] == [("Go", 0.57), ("Python", 0.43)]


def test_no_code_means_no_tracks() -> None:
    assert music_of().tracks == ()
    assert music_of(repo("r", 1, languages={})).tracks == ()


def test_a_language_keeps_its_instrument_across_users() -> None:
    instruments = {music_of(repo("r", 1), login=f"user{i}").tracks[0].instrument for i in range(10)}

    assert len(instruments) == 1
    assert instruments <= set(INSTRUMENTS["melody"])


@pytest.mark.parametrize("data", [random_user(seed) for seed in range(30)], ids=lambda u: u.login)
def test_patterns_are_well_formed(data: object) -> None:
    music = build_music(data, as_of=AS_OF)  # type: ignore[arg-type]

    assert MIN_BPM <= music.tempo_bpm <= MAX_BPM
    assert 0 <= music.intensity <= 1
    for track in music.tracks:
        assert len(track.steps) == music.bars * music.steps_per_bar
        assert track.instrument in INSTRUMENTS[track.role]
        for step in track.steps:
            if track.role == "drums":
                assert set(step) <= DRUMS
            else:
                assert all(NOTE.match(note) for note in step), step
        if track.role == "chords":
            assert all(len(step) in (0, 3) for step in track.steps)
        if track.role in ("melody", "bass", "chords"):
            assert track.steps[0], "every bar starts on its chord"
