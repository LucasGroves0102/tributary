"""What any correct split of a filing looks like, whatever the parser (pure, no I/O).

The parity tests prove the Python and Rust parsers agree; these prove what they agree on is right.
"Item 7.01Regulation FD Disclosure" made both parsers key 3,465 8-K sections "7", an item 8-Ks don't
have, and every parity test passed. The same rules run in the database on every stored filing
(migration 072, `ops.section_key_valid` and `ops.core_section_keys`, through `ops.check_sections`):
KEY_PATTERNS and CORE_KEYS are their twins, and test_invariants.py checks the patterns are the
migration's, character for character.

- A section key must be one the form has: an 8-K item (1.01-1.05, 2.01-2.06, 3.01-3.03, 4.01-4.02,
  5.01-5.08, 6.01-6.10, 7.01, 8.01, 9.01), a 10-K item (1-16 with their letters, and 4A, the
  executive officers item many 10-Ks still number), a 10-Q item of its Part (I-1 to I-4, II-1,
  II-1A, II-2 to II-6), or an exhibit ("EX-99.1"). Other forms: anything.
- An original 10-K has items 1, 1A, 7 and 8, and an original 10-Q Part I items 1 and 2 (an
  amendment carries only what it amends; a 10-K laid out by a cross-reference index numbers none,
  which the database check allows for and the fixtures don't need).
"""

import re
from collections.abc import Iterable

EXHIBIT = r"^EX-[0-9]{1,3}(\.[0-9]{1,3})?[A-Z]?$"
# form prefix -> the keys it has (ops.section_key_valid's patterns, in its order)
KEY_PATTERNS: dict[str, str] = {
    "8-K": r"^(1\.0[1-5]|2\.0[1-6]|3\.0[1-3]|4\.0[12]|5\.0[1-8]|6\.(0[1-9]|10)|7\.01|8\.01|9\.01)$",
    "10-K": r"^(1|1A|1B|1C|2|3|4|4A|5|6|7|7A|8|9|9A|9B|9C|10|11|12|13|14|15|16)$",
    "10-Q": r"^(I-[1-4]|II-(1|1A|2|3|4|5|6))$",
}
CORE_KEYS: dict[str, tuple[str, ...]] = {"10-K": ("1", "1A", "7", "8"), "10-Q": ("I-1", "I-2")}


def valid_key(form: str, key: str) -> bool:
    if key.startswith("EX-"):
        return re.match(EXHIBIT, key) is not None
    for prefix, pattern in KEY_PATTERNS.items():
        if form.startswith(prefix):
            return re.match(pattern, key) is not None
    return True


def invalid_keys(form: str, keys: Iterable[str]) -> list[str]:
    return sorted(k for k in keys if not valid_key(form, k))


def missing_core(form: str, keys: Iterable[str]) -> list[str]:
    """The core items an original 10-K or 10-Q lacks (exact form: amendments have none)."""
    have = set(keys)
    return [k for k in CORE_KEYS.get(form, ()) if k not in have]
