"""Deutung des Signal-Backup-Formats: Namen, Nachrichtenarten, Zeitstempel."""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone

# Felder, die Schlüsselmaterial oder Geheimnisse enthalten. Sie werden beim Import
# aus dem gespeicherten raw-JSON entfernt, sofern nicht ausdrücklich anders verlangt.
SECRET_KEYS = frozenset(
    {
        "svrPin",
        "profileKey",
        "identityKey",
        "mediaRootBackupKey",
        "backupKey",
        "packKey",
        "key",
        "localKey",
        "entropy",
        "encryptedUsername",
        "accountEntropyPool",
        "masterKey",
    }
)

# Reihenfolge der Nachrichtenarten, die ein chatItem tragen kann.
MESSAGE_BODIES = (
    ("standardMessage", "standard"),
    ("updateMessage", "update"),
    ("remoteDeletedMessage", "deleted"),
    ("stickerMessage", "sticker"),
    ("contactMessage", "contact"),
    ("paymentNotification", "payment"),
    ("giftBadge", "giftBadge"),
    ("viewOnceMessage", "viewOnce"),
    ("directStoryReplyMessage", "storyReply"),
)

SEND_STATES = ("pending", "sent", "delivered", "read", "viewed", "failed", "skipped")


def to_int(value) -> int | None:
    """Backup-Zeitstempel und -Größen kommen als String oder Zahl."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def ms_to_local(ms: int | None) -> datetime | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone()


def b64_to_hex(value: str | None) -> str | None:
    """plaintextHash steht als base64 im Backup, die Dateizuordnung braucht Hex."""
    if not value:
        return None
    try:
        return base64.b64decode(value).hex()
    except Exception:
        return None


def strip_secrets(obj):
    """Kopiert eine JSON-Struktur ohne Schlüsselmaterial."""
    if isinstance(obj, dict):
        return {k: strip_secrets(v) for k, v in obj.items() if k not in SECRET_KEYS}
    if isinstance(obj, list):
        return [strip_secrets(v) for v in obj]
    return obj


def format_e164(e164: str | None) -> str | None:
    if not e164:
        return None
    return e164 if e164.startswith("+") else f"+{e164}"


def recipient_kind(rec: dict) -> str:
    for key in ("contact", "self", "group", "distributionList", "releaseNotes", "callLink"):
        if key in rec:
            return key
    return "unknown"


def recipient_fields(rec: dict, self_label: str = "Ich") -> dict:
    """Zieht Anzeigename und Identifikatoren aus einem recipient-Eintrag."""
    kind = recipient_kind(rec)
    out = {
        "kind": kind,
        "display_name": None,
        "given_name": None,
        "family_name": None,
        "nickname": None,
        "profile_name": None,
        "system_name": None,
        "username": None,
        "e164": None,
        "aci": None,
        "pni": None,
        "group_title": None,
        "avatar_color": None,
        "registered": None,
        "blocked": None,
        "hidden": None,
    }

    if kind == "contact":
        c = rec["contact"]
        nickname = c.get("nickname") or {}
        nick = " ".join(x for x in (nickname.get("given"), nickname.get("family")) if x).strip()
        system = " ".join(
            x for x in (c.get("systemGivenName"), c.get("systemFamilyName")) if x
        ).strip()
        profile = " ".join(
            x for x in (c.get("profileGivenName"), c.get("profileFamilyName")) if x
        ).strip()
        out.update(
            nickname=nick or None,
            system_name=system or None,
            profile_name=profile or None,
            given_name=c.get("systemGivenName") or c.get("profileGivenName"),
            family_name=c.get("systemFamilyName") or c.get("profileFamilyName"),
            username=c.get("username"),
            e164=format_e164(c.get("e164")),
            aci=c.get("aci"),
            pni=c.get("pni"),
            avatar_color=c.get("avatarColor"),
            registered=1 if "registered" in c else (0 if "notRegistered" in c else None),
            blocked=1 if c.get("blocked") else 0,
            hidden=1 if c.get("hidden") else 0,
        )
        out["display_name"] = (
            nick or system or profile or out["username"] or out["e164"] or f"Unbekannt"
        )

    elif kind == "self":
        out["display_name"] = self_label
        out["avatar_color"] = rec["self"].get("avatarColor")

    elif kind == "group":
        g = rec["group"] or {}
        snapshot = g.get("snapshot") or {}
        title = (snapshot.get("title") or {}).get("title") or g.get("title")
        out["group_title"] = title
        out["display_name"] = title or "Gruppe"
        out["blocked"] = 1 if g.get("blocked") else 0
        out["hidden"] = 1 if g.get("hidden") else 0

    elif kind == "distributionList":
        dl = (rec["distributionList"] or {}).get("distributionList") or {}
        out["display_name"] = dl.get("name") or "Story"

    elif kind == "releaseNotes":
        out["display_name"] = "Signal (Neuigkeiten)"

    elif kind == "callLink":
        out["display_name"] = (rec["callLink"] or {}).get("name") or "Anruf-Link"

    else:
        out["display_name"] = "Unbekannt"

    return out


def message_kind(item: dict) -> tuple[str, dict | None]:
    for key, kind in MESSAGE_BODIES:
        if key in item:
            return kind, item[key]
    return "other", None


def update_subkind(update: dict) -> tuple[str, str | None]:
    """Systemnachrichten: (kind, subkind). Anrufe bekommen eine eigene Art."""
    if "individualCall" in update:
        return "call", (update["individualCall"] or {}).get("type")
    if "groupCall" in update:
        return "call", "GROUP_CALL"
    if "simpleUpdate" in update:
        return "update", (update["simpleUpdate"] or {}).get("type")
    if "groupChange" in update:
        return "update", "GROUP_CHANGE"
    if "expirationTimerChange" in update:
        return "update", "EXPIRATION_TIMER"
    if "profileChange" in update:
        return "update", "PROFILE_CHANGE"
    if "learnedProfileChange" in update:
        return "update", "LEARNED_PROFILE"
    if "threadMerge" in update:
        return "update", "THREAD_MERGE"
    if "sessionSwitchover" in update:
        return "update", "SESSION_SWITCHOVER"
    return "update", next(iter(update), None)


UPDATE_TEXT = {
    "MESSAGE_REQUEST_ACCEPTED": "Nachrichtenanfrage angenommen",
    "IDENTITY_UPDATE": "Sicherheitsnummer hat sich geändert",
    "IDENTITY_VERIFIED": "Sicherheitsnummer als verifiziert markiert",
    "IDENTITY_DEFAULT": "Verifizierung der Sicherheitsnummer aufgehoben",
    "CHANGE_NUMBER": "Rufnummer wurde geändert",
    "JOINED_SIGNAL": "ist Signal beigetreten",
    "END_SESSION": "Sitzung beendet",
    "CHAT_SESSION_REFRESH": "Chat-Sitzung aktualisiert",
    "BAD_DECRYPT": "Eine Nachricht konnte nicht entschlüsselt werden",
    "PAYMENTS_ACTIVATED": "Zahlungen aktiviert",
    "UNSUPPORTED_PROTOCOL_MESSAGE": "Nicht unterstützte Nachricht",
    "REPORTED_SPAM": "Als Spam gemeldet",
    "BLOCKED": "Blockiert",
    "UNBLOCKED": "Entblockt",
    "THREAD_MERGE": "Chatverläufe wurden zusammengeführt",
    "LEARNED_PROFILE": "Profilname wurde bekannt",
    "SESSION_SWITCHOVER": "Sitzung gewechselt",
    "GROUP_CHANGE": "Gruppe wurde geändert",
    "EXPIRATION_TIMER": "Verschwindende Nachrichten geändert",
    "PROFILE_CHANGE": "Profil wurde geändert",
}

CALL_TEXT = {
    ("AUDIO_CALL", "INCOMING", "ACCEPTED"): "Eingehender Sprachanruf",
    ("AUDIO_CALL", "INCOMING", "MISSED"): "Verpasster Sprachanruf",
    ("AUDIO_CALL", "OUTGOING", "ACCEPTED"): "Ausgehender Sprachanruf",
    ("AUDIO_CALL", "OUTGOING", "MISSED"): "Ausgehender Sprachanruf ohne Antwort",
    ("VIDEO_CALL", "INCOMING", "ACCEPTED"): "Eingehender Videoanruf",
    ("VIDEO_CALL", "INCOMING", "MISSED"): "Verpasster Videoanruf",
    ("VIDEO_CALL", "OUTGOING", "ACCEPTED"): "Ausgehender Videoanruf",
    ("VIDEO_CALL", "OUTGOING", "MISSED"): "Ausgehender Videoanruf ohne Antwort",
}


def describe_call(call: dict) -> str:
    key = (call.get("type"), call.get("direction"), call.get("state"))
    if key in CALL_TEXT:
        return CALL_TEXT[key]
    parts = [p for p in key if p]
    return "Anruf" + (f" ({', '.join(parts)})" if parts else "")


EXT_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "audio/aac": ".m4a",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/x-signal-plain": ".txt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/zip": ".zip",
}


def extension_for(content_type: str | None, file_name: str | None) -> str:
    if file_name and "." in file_name:
        ext = "." + file_name.rsplit(".", 1)[1].lower()
        if 1 < len(ext) <= 6:
            return ext
    return EXT_BY_TYPE.get((content_type or "").lower(), ".bin")


_UNSAFE = re.compile(r"[^\w.\- ]+", re.UNICODE)


def safe_filename(name: str, limit: int = 80) -> str:
    cleaned = _UNSAFE.sub("_", name).strip(" ._") or "datei"
    return cleaned[:limit]


def unique_filename(
    chat_name: str,
    backup_label: str,
    chat_id: int,
    ext: str,
    *,
    grouped: bool,
    registry: dict[str, bool],
) -> str:
    """Kollisionsfreier Dateiname je Chat.

    Zwei Backups enthalten dieselben Kontakte, also treten Chats mit identischem
    Namen doppelt auf. Ohne Unterscheidung überschriebe die zweite Datei die
    erste. `grouped` stellt das Backup-Label voran, sobald über mehrere Backups
    exportiert wird; bei einer Restkollision entscheidet die chat_id.
    """
    if grouped:
        base = f"{safe_filename(backup_label, 40)}__{safe_filename(chat_name, 60)}"
    else:
        base = safe_filename(chat_name, 60) or "chat"
    candidate = base + ext
    if candidate not in registry:
        registry[candidate] = True
        return candidate
    candidate = f"{base}_{chat_id}{ext}"
    registry[candidate] = True
    return candidate


def media_class(content_type: str | None, flag: str | None = None) -> str:
    """Grobe Klasse für Darstellung und Filter."""
    ct = (content_type or "").lower()
    if flag == "VOICE_MESSAGE":
        return "voice"
    if ct.startswith("image/"):
        return "gif" if ct == "image/gif" or flag == "GIF" else "image"
    if ct.startswith("video/"):
        return "video"
    if ct.startswith("audio/"):
        return "audio"
    if ct == "application/pdf":
        return "pdf"
    if ct.startswith("text/"):
        return "text"
    return "file"
