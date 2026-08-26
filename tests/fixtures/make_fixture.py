"""Erzeugt ein künstliches, aber vollständiges Mini-Signal-Backup zum Testen.

Format identisch zum echten Export (main.jsonl + files/), Inhalte frei erfunden.
Kann als Skript (`python3 tests/fixtures/make_fixture.py <ziel>`) oder über die
Funktion `build_fixture(target_dir)` genutzt werden — Letzteres nutzt conftest.py.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import sys
import zlib
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------- Zeit

def _ms(y: int, mo: int, d: int, h: int = 12, mi: int = 0, s: int = 0) -> int:
    """Lokale Zeit -> ms seit Epoch, symmetrisch zu model.ms_to_local."""
    return int(datetime(y, mo, d, h, mi, s).timestamp() * 1000)


# --------------------------------------------------------------------- Dateien

def _make_png(width: int = 2, height: int = 2) -> bytes:
    """Baut ein winziges, aber gültiges PNG (unkomprimiertes RGB) aus Bytes."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    raw = b""
    for _ in range(height):
        raw += b"\x00" + b"\xff\x00\x00" * width  # Filter 0, rote Pixelzeile
    idat = zlib.compress(raw)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _hash_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_b64(data: bytes) -> str:
    """base64(sha256(data)) — genau das Format, das der Importer erwartet
    (siehe model.b64_to_hex: base64 rein, hex raus)."""
    return base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


def _write_media_file(backup_dir: Path, data: bytes, ext: str) -> tuple[str, str]:
    """Legt eine Datei unter files/<hex[:2]>/<hex>.<ext> ab.

    Gibt (plaintextHash als base64, sha256-hex) zurück.
    """
    digest_hex = _hash_hex(data)
    sub = backup_dir / "files" / digest_hex[:2]
    sub.mkdir(parents=True, exist_ok=True)
    (sub / f"{digest_hex}{ext}").write_bytes(data)
    return _hash_b64(data), digest_hex


# --------------------------------------------------------------------- Aufbau

def build_fixture(target_dir: Path) -> Path:
    """Erzeugt ein vollständiges Mini-Backup unter `target_dir` und gibt den Pfad zurück."""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    (target_dir / "metadata.json").write_text(
        json.dumps({"version": 1}), encoding="utf-8"
    )

    # ------------------------------------------------------------ Mediendateien
    photo_bytes = _make_png()
    photo_hash_b64, _ = _write_media_file(target_dir, photo_bytes, ".png")

    note_bytes = b"Fahrplan fuer den Ausflug am Samstag.\n"
    note_hash_b64, _ = _write_media_file(target_dir, note_bytes, ".txt")

    orphan_bytes = b"Diese Datei wird von keiner Nachricht referenziert.\n"
    _write_media_file(target_dir, orphan_bytes, ".txt")  # bewusst verwaist

    # Anhang, dessen Datei absichtlich fehlt: Hash existiert nur im JSON.
    missing_hash_b64 = _hash_b64(b"nie-auf-die-platte-geschriebener-inhalt")

    lines: list[dict] = []

    # ------------------------------------------------------------------ Header
    lines.append(
        {
            "version": 1,
            "backupTimeMs": str(_ms(2026, 3, 20, 9, 0, 0)),
            "currentAppVersion": "7.1.0",
            "firstAppVersion": "6.0.0",
        }
    )

    # ------------------------------------------------------------- Account/Self
    lines.append(
        {
            "account": {
                "givenName": "Max Mustermann",
                "username": "max.99",
                # Geheimnis auf oberster Account-Ebene — landet nirgends in raw
                # (backups.raw existiert nicht), dient nur der Vollständigkeit.
                "accountEntropyPool": "sollte-nirgendwo-landen",
            }
        }
    )

    # ------------------------------------------------------------------ Recipients
    lines.append(
        {
            "recipient": {
                "id": "1",
                "self": {
                    "avatarColor": "A100",
                    # verschachteltes Geheimnis: svrPin liegt unter "pin", nicht auf
                    # oberster Ebene — testet die Rekursion von strip_secrets.
                    "pin": {"svrPin": "0000"},
                },
            }
        }
    )
    lines.append(
        {
            "recipient": {
                "id": "2",
                "contact": {
                    "systemGivenName": "Anna",
                    "systemFamilyName": "Musterfrau",
                    "profileGivenName": "Anna",
                    "nickname": {"given": "Anni"},
                    "e164": "491701234567",
                    "aci": "aci-anna-0002",
                    "registered": {},
                    # Geheimnis direkt unter contact — oberste Ebene.
                    "profileKey": "geheimer-profilschluessel-anna",
                    # Geheimnis verschachtelt unter einem Unterobjekt.
                    "security": {"identityKey": "geheimer-identitaetsschluessel-anna"},
                },
            }
        }
    )
    lines.append(
        {
            "recipient": {
                "id": "3",
                "contact": {
                    "systemGivenName": "Bernd",
                    "systemFamilyName": "Bauer",
                    "e164": "491709876543",
                    "aci": "aci-bernd-0003",
                    "registered": {},
                    "profileKey": "geheimer-profilschluessel-bernd",
                },
            }
        }
    )
    lines.append(
        {
            "recipient": {
                "id": "4",
                "group": {
                    "snapshot": {"title": {"title": "Team Chat"}},
                    "blocked": False,
                    "hidden": False,
                },
            }
        }
    )
    lines.append({"recipient": {"id": "5", "releaseNotes": {}}})

    # ------------------------------------------------------------------ Chats
    lines.append(
        {"chat": {"id": "100", "recipientId": "2", "archived": False, "pinnedOrder": 1}}
    )
    lines.append({"chat": {"id": "200", "recipientId": "3", "archived": False}})
    lines.append({"chat": {"id": "300", "recipientId": "4", "archived": False}})

    # -------------------------------------------------------------- ChatItems

    def std(chat_id: str, author: str, sent: int, body: str, **extra) -> dict:
        message: dict = {"text": {"body": body}}
        item: dict = {
            "chatId": chat_id,
            "authorId": author,
            "dateSent": str(sent),
            "standardMessage": {**message, **extra},
        }
        return item

    # M1: eingehend, von Anna
    t1 = _ms(2026, 1, 5, 10, 0, 0)
    m1 = std("100", "2", t1, "Hallo, wie geht es dir heute?")
    m1["incoming"] = {
        "dateReceived": str(t1 + 1000),
        "dateServerSent": str(t1 + 500),
        "read": True,
    }
    # Geheimnis, verschachtelt in einem sonst irrelevanten Zusatzfeld.
    m1["senderDevice"] = {"identityKey": "geheimer-identitaetsschluessel-nachricht"}
    lines.append({"chatItem": m1})

    # M2: ausgehend, mit sendStatus (delivered + read)
    t2 = t1 + 60_000
    m2 = std("100", "1", t2, "Mir geht es gut, danke!")
    m2["outgoing"] = {
        "sendStatus": [
            {"recipientId": "2", "delivered": {"sealedSender": True}, "timestamp": str(t2 + 2000)},
            {"recipientId": "2", "read": {}, "timestamp": str(t2 + 5000)},
        ]
    }
    lines.append({"chatItem": m2})

    # M3: directionless (z. B. Sync-Notiz ohne outgoing/incoming)
    t3 = _ms(2026, 1, 6, 8, 0, 0)
    m3 = std("100", "1", t3, "Notiz an mich selbst: Termin verschieben")
    lines.append({"chatItem": m3})

    # M4: eingehend mit Anhang (Datei vorhanden)
    t4 = _ms(2026, 1, 10, 14, 0, 0)
    m4 = std(
        "100",
        "2",
        t4,
        "Hier ist ein Bild vom Fahrradausflug",
        attachments=[
            {
                "pointer": {
                    "contentType": "image/png",
                    "fileName": "urlaub.png",
                    "width": 2,
                    "height": 2,
                    "locatorInfo": {"plaintextHash": photo_hash_b64, "size": len(photo_bytes)},
                },
                "wasDownloaded": True,
            }
        ],
    )
    m4["incoming"] = {"dateReceived": str(t4 + 1000), "dateServerSent": str(t4 + 500), "read": True}
    lines.append({"chatItem": m4})

    # M5: ausgehend mit Anhang, dessen Datei fehlt
    t5 = t4 + 5 * 60_000
    m5 = std(
        "100",
        "1",
        t5,
        "Schick dir gleich noch ein Foto",
        attachments=[
            {
                "pointer": {
                    "contentType": "image/jpeg",
                    "fileName": "verlorenes_foto.jpg",
                    "locatorInfo": {"plaintextHash": missing_hash_b64, "size": 12345},
                }
            }
        ],
    )
    m5["outgoing"] = {"sendStatus": [{"recipientId": "2", "sent": {}, "timestamp": str(t5 + 1000)}]}
    lines.append({"chatItem": m5})

    # M6: eingehend mit Reaktionen; enthält zugleich einen Teilstring, der die
    # FTS-Tokenisierung sprengt ("...sprächster..." mitten in "Gesprächstermin").
    t6 = _ms(2026, 1, 15, 9, 0, 0)
    m6 = std(
        "100",
        "3",
        t6,
        "Lass uns einen Gesprächstermin ausmachen",
        reactions=[
            {"authorId": "1", "emoji": "👍", "sentTimestamp": str(t6 + 1000)},
            {"authorId": "2", "emoji": "❤️", "sentTimestamp": str(t6 + 2000)},
        ],
    )
    m6["incoming"] = {"dateReceived": str(t6 + 500), "dateServerSent": str(t6 + 200), "read": False}
    lines.append({"chatItem": m6})

    # M7: ausgehend mit Zitat auf M1 (gleicher Chat, gleicher Autor, gleiche Zeit)
    t7 = _ms(2026, 1, 16, 9, 0, 0)
    m7 = std(
        "100",
        "1",
        t7,
        "Wie du weißt hab ich ja gesagt:",
        quote={
            "authorId": "2",
            "targetSentTimestamp": str(t1),
            "type": "NORMAL",
            "text": {"body": "Hallo, wie geht es dir heute?"},
        },
    )
    m7["outgoing"] = {"sendStatus": [{"recipientId": "2", "read": {}, "timestamp": str(t7 + 3000)}]}
    lines.append({"chatItem": m7})

    # M8: eingehend mit Linkvorschau (inkl. Vorschaubild, Datei vorhanden)
    t8 = _ms(2026, 2, 1, 12, 0, 0)
    m8 = std(
        "100",
        "2",
        t8,
        "Schau dir das mal an",
        linkPreview=[
            {
                "url": "https://example.org/ausflug",
                "title": "Ausflugsplanung",
                "description": "Alle Infos zum Wochenende",
                "date": str(t8),
                "image": {
                    "contentType": "text/plain",
                    "fileName": "fahrplan.txt",
                    "locatorInfo": {"plaintextHash": note_hash_b64, "size": len(note_bytes)},
                },
            }
        ],
    )
    m8["incoming"] = {"dateReceived": str(t8 + 1000), "dateServerSent": str(t8 + 500), "read": True}
    lines.append({"chatItem": m8})

    # M9: bearbeitete Nachricht (aktuelle Fassung + eine ältere Revision)
    t9_old = _ms(2026, 2, 10, 15, 30, 0)
    t9 = _ms(2026, 2, 10, 16, 0, 0)
    m9_old = std("100", "1", t9_old, "Ursprünglicher Text")
    m9_old["outgoing"] = {"sendStatus": []}
    m9 = std("100", "1", t9, "Aktueller Text (bearbeitet)")
    m9["outgoing"] = {"sendStatus": []}
    m9["revisions"] = [m9_old]
    lines.append({"chatItem": m9})

    # M10: remoteDeletedMessage
    t10 = _ms(2026, 2, 15, 11, 0, 0)
    m10 = {
        "chatId": "100",
        "authorId": "2",
        "dateSent": str(t10),
        "remoteDeletedMessage": {},
        "incoming": {"dateReceived": str(t10 + 500), "dateServerSent": str(t10 + 200), "read": True},
    }
    lines.append({"chatItem": m10})

    # M11: updateMessage.individualCall — angenommener Anruf
    t11 = _ms(2026, 3, 1, 10, 0, 0)
    m11 = {
        "chatId": "100",
        "authorId": "2",
        "dateSent": str(t11),
        "incoming": {"dateReceived": str(t11), "dateServerSent": str(t11)},
        "updateMessage": {
            "individualCall": {
                "type": "AUDIO_CALL",
                "direction": "INCOMING",
                "state": "ACCEPTED",
                "startedCallTimestamp": str(t11),
                "callId": "call-0001",
                "read": True,
            }
        },
    }
    lines.append({"chatItem": m11})

    # M12: updateMessage.individualCall — verpasster Anruf
    t12 = _ms(2026, 3, 2, 10, 0, 0)
    m12 = {
        "chatId": "100",
        "authorId": "2",
        "dateSent": str(t12),
        "incoming": {"dateReceived": str(t12), "dateServerSent": str(t12)},
        "updateMessage": {
            "individualCall": {
                "type": "VIDEO_CALL",
                "direction": "INCOMING",
                "state": "MISSED",
                "startedCallTimestamp": str(t12),
                "callId": "call-0002",
                "read": False,
            }
        },
    }
    lines.append({"chatItem": m12})

    # M13: updateMessage.simpleUpdate
    t13 = _ms(2026, 3, 3, 10, 0, 0)
    m13 = {
        "chatId": "100",
        "authorId": "2",
        "dateSent": str(t13),
        "incoming": {"dateReceived": str(t13), "dateServerSent": str(t13)},
        "updateMessage": {"simpleUpdate": {"type": "JOINED_SIGNAL"}},
    }
    lines.append({"chatItem": m13})

    # M14: normale eingehende Nachricht im März — sorgt dafür, dass timeline()
    # mit granularity="month" auch für den dritten Monat einen Bucket liefert
    # (M11-M13 sind kind=call/update und fließen dort nicht ein).
    t14 = _ms(2026, 3, 15, 17, 0, 0)
    m14 = std("100", "3", t14, "Bis bald im Café")
    m14["incoming"] = {"dateReceived": str(t14 + 500), "dateServerSent": str(t14 + 200), "read": True}
    lines.append({"chatItem": m14})

    with open(target_dir / "main.jsonl", "w", encoding="utf-8") as fh:
        for obj in lines:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")

    return target_dir


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Nutzung: {sys.argv[0]} <zielverzeichnis>", file=sys.stderr)
        sys.exit(1)
    out = build_fixture(Path(sys.argv[1]))
    print(f"Fixture geschrieben nach {out}")
