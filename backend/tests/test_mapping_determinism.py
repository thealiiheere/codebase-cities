"""Determinism: the same data must always produce the same city and the same music."""

import ast
import hashlib
import os
import random
import subprocess
import sys
from pathlib import Path

from app.mapping import MAPPING_VERSION, UserData, build_city, build_music
from tests.mapping_fakes import AS_OF, random_user

MAPPING_DIR = Path(__file__).resolve().parents[1] / "app" / "mapping"
SAMPLE = random_user(7)


def output_json(data: UserData) -> str:
    city = build_city(data, as_of=AS_OF)
    music = build_music(data, as_of=AS_OF)
    return city.model_dump_json() + music.model_dump_json()


def digest(data: UserData) -> str:
    return hashlib.sha256(output_json(data).encode()).hexdigest()


def test_same_input_same_output() -> None:
    assert output_json(SAMPLE) == output_json(SAMPLE)


def test_input_order_does_not_matter() -> None:
    shuffler = random.Random(1)
    repos = list(SAMPLE.repos)
    shuffler.shuffle(repos)
    reordered_repos = tuple(
        repo.model_copy(
            update={
                "languages": dict(reversed(repo.languages.items())),
                "daily_commits": dict(reversed(repo.daily_commits.items())),
            }
        )
        for repo in repos
    )
    reordered = SAMPLE.model_copy(
        update={
            "repos": reordered_repos,
            "contributions": dict(reversed(SAMPLE.contributions.items())),
        }
    )

    assert output_json(reordered) == output_json(SAMPLE)


def test_output_is_identical_across_processes() -> None:
    # Python randomizes str hashing per process (PYTHONHASHSEED). If anything in the mapping
    # depended on hash() or set/dict ordering of strings, these two runs would disagree.
    script = (
        "import hashlib;"
        "from tests.test_mapping_determinism import SAMPLE, output_json;"
        "print(hashlib.sha256(output_json(SAMPLE).encode()).hexdigest())"
    )
    backend = Path(__file__).resolve().parents[1]
    results = {
        subprocess.run(
            [sys.executable, "-c", script],
            cwd=backend,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for seed in ("0", "12345")
    }

    assert results == {digest(SAMPLE)}


# The exact output for SAMPLE. If this fails, the city or music for existing users changed:
# bump MAPPING_VERSION in app/mapping/version.py (so cached cities rebuild), then update both
# values here.
GOLDEN_VERSION = 3
GOLDEN_DIGEST = "400b6937e0cfa511ad00434568b63f43afa67333d00e7906caaeefdcc99bdcce"


def test_golden_output() -> None:
    assert MAPPING_VERSION == GOLDEN_VERSION
    assert digest(SAMPLE) == GOLDEN_DIGEST


# --- Purity guard ------------------------------------------------------------------------------

FORBIDDEN_IMPORTS = {
    "os",
    "sys",
    "time",
    "socket",
    "asyncio",
    "httpx",
    "sqlalchemy",
    "redis",
    "arq",
    "app.db",
    "app.cache",
    "app.config",
    "app.github",
    "app.ingestion",
    "app.models",
    "app.services",
    "app.api",
}
FORBIDDEN_CALLS = {"now", "today", "utcnow", "time", "hash"}


def test_mapping_package_stays_pure() -> None:
    problems = []
    for path in sorted(MAPPING_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if any(module == bad or module.startswith(bad + ".") for bad in FORBIDDEN_IMPORTS):
                    problems.append(f"{path.name} imports {module}")
                if module == "random" and path.name != "rng.py":
                    problems.append(f"{path.name} uses random directly; use SeededRng")
            if isinstance(node, ast.Call):
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", "")
                )
                if name in FORBIDDEN_CALLS:
                    problems.append(f"{path.name}:{node.lineno} calls {name}()")

    assert problems == []
