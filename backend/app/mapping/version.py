"""Version of the mapping algorithm.

Bump this whenever a change alters the city or music produced from the same data. Phase 5 puts
it in cache keys, so a bump makes every cached city rebuild instead of serving stale output.
The golden test in tests/test_mapping_determinism.py fails until you bump it.
"""

MAPPING_VERSION = 3  # 3: past cities keep the latest layout (2: whole floors).
