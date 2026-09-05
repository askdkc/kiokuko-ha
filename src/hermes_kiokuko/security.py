import re
import unicodedata

from .errors import KiokukoError

SECRET = re.compile(
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|"
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})\b|"
    r"\b(?:password|passwd|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|"
    r"authorization|cookie|secret)[\"']?\s*[:=]\s*\S+|"
    r"\bBearer\s+[A-Za-z0-9._~+/-]{8,}|https?://[^\s/@:]+:[^\s/@]+@",
    re.I,
)
INJECTION = re.compile(
    r"ignore (?:all |the )?(?:previous|system|developer) instructions|"
    r"(?:reveal|exfiltrate|send).{0,40}(?:system prompt|private key|credentials)|"
    r"(?:system|developer)\s*:\s*(?:override|ignore)|"
    r"(?:以前|システム|開発者)の指示を無視|"
    r"(?:authorized_keys|ssh-rsa AAAA)", re.I | re.S,
)


def scan(text: str, *, max_chars: int = 600) -> str:
    if not isinstance(text, str) or not text.strip() or len(text) > max_chars:
        raise KiokukoError("INVALID_BODY")
    if any(unicodedata.category(c) in {"Cf", "Cs"} or
           (unicodedata.category(c) == "Cc" and c not in "\n\t\r") for c in text):
        raise KiokukoError("UNSAFE_UNICODE")
    if SECRET.search(text):
        raise KiokukoError("SECRET_REJECTED")
    if INJECTION.search(text):
        raise KiokukoError("INJECTION_REJECTED")
    return text


def host_scan(text: str) -> None:
    # The host scanner is an additional filter, never evidence of authorization.
    from tools.threat_patterns import first_threat_message
    if first_threat_message(text, scope="strict"):
        raise KiokukoError("INJECTION_REJECTED")
