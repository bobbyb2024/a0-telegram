"""Prompt injection defense for the Telegram plugin.

Sanitizes all untrusted external content (messages, usernames, captions,
filenames) before it reaches the LLM agent context.
"""

import os
import re
import unicodedata

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
MAX_MESSAGE_CONTENT = 4096
MAX_USERNAME = 100
MAX_CAPTION_CONTENT = 1024
MAX_FILENAME = 255
MAX_BULK_INPUT_CHARS = 200_000
MAX_MESSAGE_LIMIT = 500  # Cap for summarize limit arg

# ---------------------------------------------------------------------------
# Zero-width and invisible characters to strip
# ---------------------------------------------------------------------------
_INVISIBLE_CHARS = re.compile(
    "["
    "\u200b"  # zero-width space
    "\u200c"  # zero-width non-joiner
    "\u200d"  # zero-width joiner
    "\u200e"  # left-to-right mark
    "\u200f"  # right-to-left mark
    "\u2060"  # word joiner
    "\u2061"  # function application
    "\u2062"  # invisible times
    "\u2063"  # invisible separator
    "\u2064"  # invisible plus
    "\ufeff"  # zero-width no-break space / BOM
    "\u00ad"  # soft hyphen
    "\u034f"  # combining grapheme joiner
    "\u061c"  # arabic letter mark
    "\u115f"  # hangul choseong filler
    "\u1160"  # hangul jungseong filler
    "\u17b4"  # khmer vowel inherent aq
    "\u17b5"  # khmer vowel inherent aa
    "\u180e"  # mongolian vowel separator
    "\u2028"  # line separator
    "\u2029"  # paragraph separator
    "\u202a"  # left-to-right embedding
    "\u202b"  # right-to-left embedding
    "\u202c"  # pop directional formatting
    "\u202d"  # left-to-right override
    "\u202e"  # right-to-left override
    "\u202f"  # narrow no-break space
    "\ufff9"  # interlinear annotation anchor
    "\ufffa"  # interlinear annotation separator
    "\ufffb"  # interlinear annotation terminator
    "]+"
)

# ---------------------------------------------------------------------------
# Injection patterns (compiled once at module load)
# ---------------------------------------------------------------------------
# These catch common LLM prompt injection prefixes.  We match them
# case-insensitively at the start of a line (after optional whitespace).
_INJECTION_PHRASES = [
    # Classic instruction override
    r"ignore all previous instructions",
    r"ignore prior instructions",
    r"ignore above instructions",
    r"ignore the above",
    r"disregard all previous",
    r"disregard prior instructions",
    r"forget all previous",
    r"forget your instructions",
    # Role hijacking
    r"you are now",
    r"you must now",
    r"you will now",
    r"you should now",
    r"from now on",
    r"pretend you are",
    r"act as if",
    r"roleplay as",
    # Instruction injection
    r"new instructions:",
    r"override:",
    r"system:",
    r"reminder:",
    r"important:",
    r"attention:",
    r"actually,? (?:the user|i) (?:want|meant|need)",
    # Model-specific tokens
    r"\[INST\]",
    r"\[/INST\]",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"<<SYS>>",
    r"<</SYS>>",
    r"</s>",
    r"<\|endoftext\|>",
    r"<\|system\|>",
    r"<\|user\|>",
    r"<\|assistant\|>",
    # Chat role markers
    r"Human:",
    r"Assistant:",
    r"### Instruction",
    r"### System",
    r"## System",
    # Meta-manipulation
    r"the (?:previous|above|preceding) instructions (?:are|were)",
    r"do not follow (?:the|your) (?:previous|original)",
]

_INJECTION_RE = re.compile(
    r"^\s*(?:" + "|".join(_INJECTION_PHRASES) + r")",
    re.IGNORECASE | re.MULTILINE,
)

# Delimiter tags we use to wrap content — must be escaped inside user data
_DELIMITER_TAGS = [
    "<telegram_user_content>",
    "</telegram_user_content>",
    "<telegram_caption_content>",
    "</telegram_caption_content>",
    "<telegram_messages>",
    "</telegram_messages>",
]

_DELIMITER_RE = re.compile(
    "|".join(re.escape(tag) for tag in _DELIMITER_TAGS),
    re.IGNORECASE,
)

# Telegram chat ID pattern (positive or negative integer)
_CHAT_ID_RE = re.compile(r"^-?\d{1,20}$")

# Allowed URL hosts for image downloads (SSRF defense)
_ALLOWED_IMAGE_HOSTS = {
    "api.telegram.org",
}


# ---------------------------------------------------------------------------
# Text normalization (Unicode defense)
# ---------------------------------------------------------------------------

def _normalize_text(text: str) -> str:
    """Normalize Unicode to defeat homoglyph and invisible-char attacks.

    1. NFKC normalization maps look-alike characters and decomposes
       compatibility characters.
    2. Strip zero-width / invisible characters that can split keywords.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE_CHARS.sub("", text)
    return text


# ---------------------------------------------------------------------------
# Sanitization functions
# ---------------------------------------------------------------------------

def sanitize_content(text: str, max_length: int = MAX_MESSAGE_CONTENT) -> str:
    """Sanitize a Telegram message body for safe LLM consumption.

    - Normalizes Unicode (homoglyph / invisible char defense)
    - Neutralises known injection patterns
    - Escapes our own delimiter tags so they can't be spoofed
    - Truncates to *max_length* AFTER sanitization (prevents boundary attacks)
    """
    if not text:
        return ""
    # Normalize BEFORE pattern matching to defeat homoglyphs / zero-width
    text = _normalize_text(text)
    # Escape delimiter tags
    text = _DELIMITER_RE.sub(_escape_tag, text)
    # Block injection patterns
    text = _INJECTION_RE.sub("[blocked: suspected prompt injection]", text)
    # Truncate AFTER sanitization to prevent boundary attacks
    text = text[:max_length]
    return text


def sanitize_username(name: str, max_length: int = MAX_USERNAME) -> str:
    """Sanitize a Telegram username / display name."""
    if not name:
        return "Unknown"
    name = _normalize_text(name)
    name = name[:max_length]
    # Collapse to single line
    name = name.replace("\n", " ").replace("\r", " ")
    # Escape delimiter tags
    name = _DELIMITER_RE.sub(_escape_tag, name)
    # Neutralise injection phrases in usernames
    name = _INJECTION_RE.sub("[blocked]", name)
    return name


def sanitize_caption(text: str, max_length: int = MAX_CAPTION_CONTENT) -> str:
    """Sanitize Telegram media caption."""
    if not text:
        return ""
    text = _normalize_text(text)
    text = _DELIMITER_RE.sub(_escape_tag, text)
    text = _INJECTION_RE.sub("[blocked: suspected prompt injection]", text)
    text = text[:max_length]
    return text


def sanitize_filename(name: str, max_length: int = MAX_FILENAME) -> str:
    """Sanitize an attachment filename."""
    if not name:
        return "file"
    # Strip null bytes first (path-traversal via embedded \x00)
    name = name.replace("\x00", "")
    name = name[:max_length]
    # Strip path traversal
    name = name.replace("/", "_").replace("\\", "_").replace("..", "_")
    # Remove newlines
    name = name.replace("\n", "").replace("\r", "")
    return name or "file"


def sanitize_chat_title(name: str, max_length: int = MAX_USERNAME) -> str:
    """Sanitize a chat or group title from the Telegram API."""
    if not name:
        return "unknown"
    name = _normalize_text(name)
    name = name[:max_length]
    name = name.replace("\n", " ").replace("\r", " ")
    name = _DELIMITER_RE.sub(_escape_tag, name)
    name = _INJECTION_RE.sub("[blocked]", name)
    return name


def truncate_bulk(text: str, max_length: int = MAX_BULK_INPUT_CHARS) -> str:
    """Truncate large message batches (for summarize)."""
    if len(text) <= max_length:
        return text
    suffix = "\n[... truncated for safety ...]"
    return text[:max_length - len(suffix)] + suffix


def clamp_limit(limit: int, default: int = 100, maximum: int = MAX_MESSAGE_LIMIT) -> int:
    """Clamp a user-provided message limit to a safe range."""
    if limit < 1:
        return default
    return min(limit, maximum)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_chat_id(value: str, name: str = "chat_id") -> str:
    """Validate that a string is a valid Telegram chat ID.

    Returns the validated string or raises ValueError.
    """
    if not value:
        raise ValueError(f"{name} is required.")
    value = value.strip()
    if not _CHAT_ID_RE.match(value):
        raise ValueError(f"Invalid {name}: must be an integer (positive or negative).")
    return value


def validate_topic_key(value: str, name: str = "topic_key") -> str:
    """Validate a plain chat_id OR a '{chat_id}:topic:{thread_id}' composite key.

    Returns the validated string or raises ValueError.
    """
    if not value:
        raise ValueError(f"{name} is required.")
    value = value.strip()

    if ":topic:" in value:
        parts = value.split(":topic:", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid {name}: malformed topic key.")
        chat_part = validate_chat_id(parts[0], f"{name} (chat_id part)")
        thread_part = parts[1].strip()
        if not re.match(r'^\d+$', thread_part):
            raise ValueError(f"Invalid {name}: thread_id must be a positive integer.")
        return f"{chat_part}:topic:{thread_part}"

    return validate_chat_id(value, name)


def validate_image_url(url: str) -> bool:
    """Check that a URL is from an allowed Telegram CDN host (SSRF defense).

    Only HTTPS is accepted — HTTP is intentionally excluded to prevent
    MITM/redirect-based SSRF attacks.
    """
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return False
        if parsed.hostname not in _ALLOWED_IMAGE_HOSTS:
            return False
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Auth key generation
# ---------------------------------------------------------------------------

def generate_auth_key(length: int = 32) -> str:
    """Generate a cryptographically secure URL-safe auth key."""
    import secrets
    return secrets.token_urlsafe(length)


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def require_auth(config: dict) -> None:
    """Raise ValueError if bot token is not configured."""
    bot_token = (config.get("bot", {}).get("token", "") or "").strip()
    if not bot_token:
        raise ValueError(
            "No Telegram bot token configured. "
            "Set TELEGRAM_BOT_TOKEN or configure the token "
            "in the Telegram plugin settings."
        )


# ---------------------------------------------------------------------------
# Secure file write helper
# ---------------------------------------------------------------------------

def secure_write_json(path, data, indent: int = 2):
    """Write JSON to a file with restrictive permissions (0o600) and atomic rename.

    Uses a process-unique temp filename to avoid collisions when called
    concurrently for the same destination path.  The rename is atomic on
    POSIX (same filesystem), so readers never see a partial file.
    """
    import json
    import logging as _logging
    from pathlib import Path
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique name prevents concurrent writers from truncating each other's work
    tmp_path = path.with_name(f"{path.stem}.{os.getpid()}.{id(data)}.tmp")
    try:
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)
        os.replace(str(tmp_path), str(path))
    except Exception as primary_err:
        # Cleanup partial tmp file
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        # Degraded fallback: still write securely (no guarantee of atomicity)
        _logging.getLogger("sanitize").warning(
            "secure_write_json: atomic write failed (%s), "
            "falling back to non-atomic write for %s",
            type(primary_err).__name__, path,
        )
        try:
            fd2 = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd2, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent)
        except Exception:
            # Last-resort plain write; at least try to chmod afterwards
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent)
            try:
                os.chmod(str(path), 0o600)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _escape_tag(match: re.Match) -> str:
    """Replace angle brackets in a matched delimiter tag so it's inert."""
    return match.group(0).replace("<", "&lt;").replace(">", "&gt;")
