"""Version of the mapping algorithm.

Bump this whenever a change alters the city or music produced from the same data. Phase 5 puts
it in cache keys, so a bump makes every cached city rebuild instead of serving stale output.
The golden test in tests/test_mapping_determinism.py fails until you bump it.
"""

# 6: private repos flagged (5: wider buildings, 4: growth timeline, 3: stable past layouts,
# 2: floors).
MAPPING_VERSION = 6
