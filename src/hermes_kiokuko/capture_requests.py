"""Conservative recognition of direct human requests, never embedded instructions."""
import re

HUMAN_ORIGINS = frozenset({'cli', 'cli_user', 'dm', 'group_chat'})


def direct_request(raw, config):
    if not isinstance(raw, str) or not raw.strip() or len(raw) > config['max_evidence_chars']:
        return False
    # Ambiguous mixed documents are deliberately left for explicit commands.
    if raw.lstrip().startswith(('@kiokuko', '---', '#', '>')) or re.search(r'```|~~~|(?m:^\s*>)|<[^>]+>|[「『“"]', raw):
        return False
    remember = config['detect_explicit_remember_requests'] and re.search(
        r'(?:覚えて(?:おいて|ください|ほしい|くれ|ね|$)|記憶して(?:ください|ほしい|くれ|ね|$)|(?m:^\s*(?:please\s+)?remember\s+(?:that\b|this\b|my\b|I\b|to\b)))', raw, re.I)
    correction = config['detect_corrections'] and re.search(
        r'(?:訂正して|修正して|(?m:^\s*(?:please\s+)?correct\s+(?:my\b|the\b|this\b|that\b)))', raw, re.I)
    return bool(remember or correction)
