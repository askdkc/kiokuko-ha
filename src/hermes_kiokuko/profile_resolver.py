"""Deterministic target hints. Ranking never establishes permission or success."""
from dataclasses import dataclass
from pathlib import PurePosixPath
import re

POLICY_VERSION = 'task-profile-v1'
MAX_CANDIDATES = 64
MAX_TARGETS = 8
MAX_HINT_CHARS = 400
# Quoted paths may contain Unicode/spaces; bare identifiers deliberately stay narrow.
IDENTIFIER = re.compile(r'`([^`\n]{1,240})`|(?<![\w/])((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9_-]+)(?![\w/])')


@dataclass(frozen=True)
class Candidate:
    profile_id: str
    target_index: int
    target: str
    score: int
    exact: bool
    current: bool


@dataclass(frozen=True)
class Decision:
    candidate: Candidate
    action: str


def identifiers(text):
    """Extract explicit tokens only; never infer intent, success or constraints."""
    if not isinstance(text, str):
        return ()
    found = []
    for match in IDENTIFIER.finditer(text[:600]):
        value = next(value for value in match.groups() if value is not None)
        if value not in found and len(found) < MAX_TARGETS:
            found.append(value)
    return tuple(found)


def relative_target(value, root):
    path = PurePosixPath(value)
    if path.is_absolute():
        try:
            path = path.relative_to(PurePosixPath(root))
        except ValueError:
            return None
    if not path.parts or any(part.startswith('.') for part in path.parts) or '\\' in value:
        return None
    if len(str(path)) > 240 or ':' in value or str(path) == '.':
        return None
    return str(path)


def exact_match(selectors, target):
    # A basename may resolve to a historical relative path; an explicit directory
    # never becomes another directory just because the filename happens to match.
    return any(value == target or ('/' not in value and value == PurePosixPath(target).name)
               for value in selectors)


def decide(candidates, selectors, *, complete, mode):
    """Return at most three targets; only one unambiguous, current exact target adopts."""
    ordered = sorted(candidates, key=lambda c: (not c.exact, -c.score, c.target, c.profile_id))
    distinct = {}
    for candidate in ordered:
        distinct.setdefault(candidate.target, candidate)
    exact_targets = {c.target for c in candidates if c.exact}
    decisions = []
    for candidate in distinct.values():
        if not candidate.current:
            continue
        adopt = (mode == 'resolve' and complete and len(selectors) == 1 and candidate.exact
                 and len(exact_targets) == 1)
        decisions.append(Decision(candidate, 'adopt' if adopt else 'suggest'))
        if len(decisions) == 3:
            break
    return tuple(decisions)
