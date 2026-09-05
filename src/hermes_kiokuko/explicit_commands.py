import re

from .errors import KiokukoError
from .models import ExplicitCommand
from .security import scan

REMEMBER = re.compile(r"@kiokuko remember --scope (principal|principal_workspace)\n([\s\S]+)\Z")
CORRECT = re.compile(r"@kiokuko correct (mem_[a-zA-Z0-9_-]+) --expected-revision ([1-9][0-9]*)\n([\s\S]+)\Z")
FORGET = re.compile(r"@kiokuko forget (mem_[a-zA-Z0-9_-]+) --expected-revision ([1-9][0-9]*)\n?\Z")


def parse(message) -> ExplicitCommand | None:
    if not isinstance(message, str) or not message.startswith("@kiokuko"):
        return None
    if len(re.findall(r"(?m)^@kiokuko (?:remember|correct|forget)\b", message)) != 1:
        raise KiokukoError("INVALID_COMMAND")
    if match := REMEMBER.fullmatch(message):
        return ExplicitCommand("remember", scan(match[2]), match[1])
    if match := CORRECT.fullmatch(message):
        return ExplicitCommand("correct", scan(match[3]), entry_id=match[1], expected_revision=int(match[2]))
    if match := FORGET.fullmatch(message):
        return ExplicitCommand("forget", entry_id=match[1], expected_revision=int(match[2]))
    raise KiokukoError("INVALID_COMMAND")
