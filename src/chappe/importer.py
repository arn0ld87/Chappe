"""Import eines Signal-Backup-Verzeichnisses (main.jsonl + files/) in SQLite."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import model
from .model import b64_to_hex, to_int

SCHEMA = Path(__file__).with_name("schema.sql")


@dataclass
class ImportReport:
    label: str = ""
    backup_id: int = 0
    recipients: int = 0
    chats: int = 0
    messages: int = 0
    revisions: int = 0
    attachments: int = 0
    attachments_local: int = 0
    reactions: int = 0
    quotes: int = 0
    quotes_resolved: int = 0
    calls: int = 0
    media_files: int = 0
    media_bound: int = 0
    media_orphans: int = 0
    warnings: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        return [
            f"Backup        {self.label} (id {self.backup_id})",
            f"Kontakte      {self.recipients}",
            f"Chats         {self.chats}",
            f"Nachrichten   {self.messages}  (+{self.revisions} frühere Fassungen)",
            f"Anhänge       {self.attachments}  davon lokal vorhanden: {self.attachments_local}",
            f"Reaktionen    {self.reactions}",
            f"Zitate        {self.quotes}  davon aufgelöst: {self.quotes_resolved}",
            f"Anrufe        {self.calls}",
            f"Mediendateien {self.media_files}  zugeordnet: {self.media_bound}"
            f"  ohne Referenz: {self.media_orphans}",
        ]


def connect(db_path: str | os.PathLike) -> sqlite3.Connection:
    """Öffnet die Datenbank und legt das Schema an, falls es noch fehlt."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return conn


def index_media(files_dir: Path) -> dict[str, tuple[str, int]]:
    """sha256(Dateiinhalt) -> (relativer Pfad, Größe).

    Der Dateiname im Backup ist ein Zufallswert aus Signal Desktop und trägt keine
    Information. Die Verbindung zur Nachricht läuft ausschließlich über den Hash,
    der im Backup als `locatorInfo.plaintextHash` steht.
    """
    index: dict[str, tuple[str, int]] = {}
    if not files_dir.is_dir():
        return index
    for root, _dirs, names in os.walk(files_dir):
        for name in names:
            if name.startswith("."):
                continue
            full = Path(root) / name
            digest = hashlib.sha256()
            with open(full, "rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(block)
            index[digest.hexdigest()] = (
                str(full.relative_to(files_dir.parent)),
                full.stat().st_size,
            )
    return index


def _preview(body: str | None, limit: int = 120) -> str | None:
    if not body:
        return None
    flat = " ".join(body.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


class Importer:
    def __init__(
        self,
        conn: sqlite3.Connection,
        backup_dir: Path,
        *,
        keep_secrets: bool = False,
        self_label: str = "Ich",
        progress=None,
    ):
        self.conn = conn
        self.dir = backup_dir
        self.keep_secrets = keep_secrets
        self.self_label = self_label
        self.progress = progress or (lambda _msg: None)
        self.report = ImportReport()
        self.rid_to_id: dict[str, int] = {}
        self.cid_to_id: dict[str, int] = {}
        self.media: dict[str, tuple[str, int]] = {}
        self.media_used: set[str] = set()
        # (chat_id, author_id, sent_at) -> message_id, für die Auflösung von Zitaten
        self.by_key: dict[tuple[int, int | None, int], int] = {}
        self.pending_quotes: list[tuple[int, int, int | None, int]] = []

    # ------------------------------------------------------------------ raw

    def _raw(self, obj) -> str:
        payload = obj if self.keep_secrets else model.strip_secrets(obj)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    # --------------------------------------------------------------- import

    def run(self, label: str | None = None, replace: bool = False) -> ImportReport:
        main = self.dir / "main.jsonl"
        if not main.is_file():
            raise FileNotFoundError(f"{main} nicht gefunden — ist das ein Signal-Export?")

        label = label or self.dir.name
        self.report.label = label

        existing = self.conn.execute(
            "SELECT id FROM backups WHERE label = ?", (label,)
        ).fetchone()
        if existing:
            if not replace:
                raise ValueError(
                    f"Backup '{label}' ist bereits importiert. "
                    f"Mit --replace neu einlesen oder --label anders benennen."
                )
            self.conn.execute("DELETE FROM backups WHERE id = ?", (existing["id"],))

        self.progress("Mediendateien werden indiziert …")
        self.media = index_media(self.dir / "files")
        self.report.media_files = len(self.media)

        self.progress("main.jsonl wird gelesen …")
        header: dict = {}
        account: dict = {}
        recipients: list[dict] = []
        chats: list[dict] = []
        items: list[dict] = []

        with open(main, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    self.report.warnings.append(f"Zeile {lineno} ist kein gültiges JSON: {exc}")
                    continue
                if "chatItem" in obj:
                    items.append(obj["chatItem"])
                elif "recipient" in obj:
                    recipients.append(obj["recipient"])
                elif "chat" in obj:
                    chats.append(obj["chat"])
                elif "account" in obj:
                    account = obj["account"]
                elif "version" in obj and "backupTimeMs" in obj:
                    header = obj
                # stickerPack und alles Weitere brauchen wir für die Auswertung nicht.

        self.report.backup_id = self._insert_backup(label, header, account)
        self._insert_recipients(recipients)
        self._insert_chats(chats)
        self._insert_items(items)
        self._resolve_quotes()
        self._finalize()
        self.conn.commit()
        return self.report

    # ---------------------------------------------------------------- parts

    def _insert_backup(self, label: str, header: dict, account: dict) -> int:
        cur = self.conn.execute(
            """INSERT INTO backups
               (label, source_path, imported_at, backup_time_ms, format_version,
                app_version, first_app_version, self_name)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                label,
                str(self.dir.resolve()),
                datetime.now().astimezone().isoformat(timespec="seconds"),
                to_int(header.get("backupTimeMs")),
                str(header.get("version")) if header.get("version") is not None else None,
                header.get("currentAppVersion"),
                header.get("firstAppVersion"),
                account.get("givenName") or account.get("username") or self.self_label,
            ),
        )
        return cur.lastrowid

    def _insert_recipients(self, recipients: list[dict]) -> None:
        bid = self.report.backup_id
        self_name = self.conn.execute(
            "SELECT self_name FROM backups WHERE id = ?", (bid,)
        ).fetchone()["self_name"]
        for rec in recipients:
            rid = str(rec.get("id"))
            fields = model.recipient_fields(rec, self_label=self_name or self.self_label)
            cur = self.conn.execute(
                """INSERT INTO recipients
                   (backup_id, rid, kind, display_name, given_name, family_name, nickname,
                    profile_name, system_name, username, e164, aci, pni, group_title,
                    avatar_color, registered, blocked, hidden, raw)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    bid,
                    rid,
                    fields["kind"],
                    fields["display_name"],
                    fields["given_name"],
                    fields["family_name"],
                    fields["nickname"],
                    fields["profile_name"],
                    fields["system_name"],
                    fields["username"],
                    fields["e164"],
                    fields["aci"],
                    fields["pni"],
                    fields["group_title"],
                    fields["avatar_color"],
                    fields["registered"],
                    fields["blocked"],
                    fields["hidden"],
                    self._raw(rec),
                ),
            )
            self.rid_to_id[rid] = cur.lastrowid
            if fields["kind"] == "self":
                self.conn.execute(
                    "UPDATE backups SET self_rid = ? WHERE id = ?", (rid, bid)
                )
        self.report.recipients = len(recipients)

    def _insert_chats(self, chats: list[dict]) -> None:
        bid = self.report.backup_id
        for chat in chats:
            cid = str(chat.get("id"))
            rid = str(chat.get("recipientId"))
            rec_id = self.rid_to_id.get(rid)
            name, kind = "Unbekannt", "other"
            if rec_id:
                row = self.conn.execute(
                    "SELECT display_name, kind FROM recipients WHERE id = ?", (rec_id,)
                ).fetchone()
                name = row["display_name"]
                kind = "group" if row["kind"] == "group" else (
                    "direct" if row["kind"] in ("contact", "self") else "other"
                )
            cur = self.conn.execute(
                """INSERT INTO chats
                   (backup_id, cid, recipient_id, name, kind, archived, pinned_order,
                    muted_until, raw)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    bid,
                    cid,
                    rec_id,
                    name,
                    kind,
                    1 if chat.get("archived") else 0,
                    to_int(chat.get("pinnedOrder")),
                    to_int(chat.get("muteUntilMs")),
                    self._raw(chat),
                ),
            )
            self.cid_to_id[cid] = cur.lastrowid
        self.report.chats = len(chats)

    def _insert_items(self, items: list[dict]) -> None:
        total = len(items)
        for n, item in enumerate(items, 1):
            if n % 2000 == 0:
                self.progress(f"  {n}/{total} Nachrichten …")
            self._insert_item(item, revision_of=None, revision_index=None)

    def _insert_item(
        self, item: dict, revision_of: int | None, revision_index: int | None
    ) -> int | None:
        chat_id = self.cid_to_id.get(str(item.get("chatId")))
        if chat_id is None:
            self.report.warnings.append(
                f"Nachricht verweist auf unbekannten Chat {item.get('chatId')}"
            )
            return None
        author_id = self.rid_to_id.get(str(item.get("authorId")))
        sent_at = to_int(item.get("dateSent")) or 0

        direction, received_at, server_sent_at, is_read = "directionless", None, None, None
        if "outgoing" in item:
            direction = "outgoing"
            received_at = to_int(item["outgoing"].get("dateReceived"))
        elif "incoming" in item:
            direction = "incoming"
            inc = item["incoming"]
            received_at = to_int(inc.get("dateReceived"))
            server_sent_at = to_int(inc.get("dateServerSent"))
            is_read = 1 if inc.get("read") else 0

        kind, payload = model.message_kind(item)
        subkind, body = None, None

        if kind == "standard":
            body = ((payload or {}).get("text") or {}).get("body")
        elif kind == "update":
            kind, subkind = model.update_subkind(payload or {})
            if kind == "call":
                call = (payload or {}).get("individualCall") or (payload or {}).get(
                    "groupCall"
                ) or {}
                body = model.describe_call(call)
                if call.get("read") is not None:
                    is_read = 1 if call.get("read") else 0
            else:
                body = model.UPDATE_TEXT.get(subkind or "", subkind or "Systemnachricht")
        elif kind == "deleted":
            body = "Diese Nachricht wurde gelöscht"
        elif kind == "sticker":
            sticker = (payload or {}).get("sticker") or {}
            body = sticker.get("emoji") or "Sticker"
        elif kind == "viewOnce":
            body = "Einmal ansehen"
        elif kind == "payment":
            body = (payload or {}).get("note") or "Zahlung"
        elif kind == "giftBadge":
            body = "Geschenk"

        cur = self.conn.execute(
            """INSERT INTO messages
               (backup_id, chat_id, author_id, sent_at, received_at, server_sent_at,
                direction, kind, subkind, body, body_preview, is_read, is_expiring,
                is_edited, revision_of, revision_index, raw)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                self.report.backup_id,
                chat_id,
                author_id,
                sent_at,
                received_at,
                server_sent_at,
                direction,
                kind,
                subkind,
                body,
                _preview(body),
                is_read,
                1 if item.get("expiresInMs") else 0,
                1 if item.get("revisions") else 0,
                revision_of,
                revision_index,
                self._raw(item),
            ),
        )
        message_id = cur.lastrowid

        if revision_of is None:
            self.report.messages += 1
            self.by_key[(chat_id, author_id, sent_at)] = message_id
        else:
            self.report.revisions += 1

        n_att = 0
        n_rea = 0
        has_quote = 0

        if kind == "call" and payload:
            call = payload.get("individualCall") or payload.get("groupCall") or {}
            self.conn.execute(
                """INSERT INTO calls (message_id, call_id, call_type, direction, state,
                                      started_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    message_id,
                    str(call.get("callId")) if call.get("callId") is not None else None,
                    call.get("type") or ("GROUP_CALL" if "groupCall" in payload else None),
                    call.get("direction"),
                    call.get("state"),
                    to_int(call.get("startedCallTimestamp")),
                ),
            )
            self.report.calls += 1

        std = payload if kind == "standard" else None
        if std:
            for i, att in enumerate(std.get("attachments") or []):
                n_att += self._insert_attachment(message_id, att, "body", i)

            for i, react in enumerate(std.get("reactions") or []):
                self.conn.execute(
                    """INSERT INTO reactions (message_id, author_id, emoji, sent_at, ordinal)
                       VALUES (?,?,?,?,?)""",
                    (
                        message_id,
                        self.rid_to_id.get(str(react.get("authorId"))),
                        react.get("emoji"),
                        to_int(react.get("sentTimestamp")),
                        i,
                    ),
                )
                n_rea += 1
            self.report.reactions += n_rea

            quote = std.get("quote")
            if quote:
                has_quote = 1
                q_author = self.rid_to_id.get(str(quote.get("authorId")))
                q_target = to_int(quote.get("targetSentTimestamp"))
                self.conn.execute(
                    """INSERT INTO quotes (message_id, author_id, target_sent_at,
                                           quote_type, text)
                       VALUES (?,?,?,?,?)""",
                    (
                        message_id,
                        q_author,
                        q_target,
                        quote.get("type"),
                        (quote.get("text") or {}).get("body"),
                    ),
                )
                self.report.quotes += 1
                if q_target:
                    self.pending_quotes.append((message_id, chat_id, q_author, q_target))
                for i, qatt in enumerate(quote.get("attachments") or []):
                    thumb = qatt.get("thumbnail")
                    if thumb:
                        self._insert_attachment(message_id, thumb, "quote_thumbnail", i)

            for i, lp in enumerate(std.get("linkPreview") or []):
                self.conn.execute(
                    """INSERT INTO link_previews (message_id, url, title, description, date)
                       VALUES (?,?,?,?,?)""",
                    (
                        message_id,
                        lp.get("url"),
                        lp.get("title"),
                        lp.get("description"),
                        to_int(lp.get("date")),
                    ),
                )
                if lp.get("image"):
                    self._insert_attachment(
                        message_id, {"pointer": lp["image"]}, "link_preview", i
                    )

        if kind == "sticker" and payload:
            sticker_data = (payload.get("sticker") or {}).get("data")
            if sticker_data:
                self._insert_attachment(
                    message_id, {"pointer": sticker_data}, "sticker", 0
                )

        for i, status in enumerate((item.get("outgoing") or {}).get("sendStatus") or []):
            state = next((s for s in model.SEND_STATES if s in status), None)
            detail = status.get(state) if isinstance(status.get(state), dict) else {}
            self.conn.execute(
                """INSERT INTO send_status (message_id, recipient_id, status, timestamp,
                                            sealed_sender)
                   VALUES (?,?,?,?,?)""",
                (
                    message_id,
                    self.rid_to_id.get(str(status.get("recipientId"))),
                    state,
                    to_int(status.get("timestamp")),
                    1 if (detail or {}).get("sealedSender") else 0,
                ),
            )

        self.conn.execute(
            """UPDATE messages SET n_attachments = ?, n_reactions = ?, has_quote = ?
               WHERE id = ?""",
            (n_att, n_rea, has_quote, message_id),
        )

        for i, rev in enumerate(item.get("revisions") or []):
            self._insert_item(rev, revision_of=message_id, revision_index=i)

        return message_id

    def _insert_attachment(self, message_id: int, att: dict, role: str, ordinal: int) -> int:
        pointer = att.get("pointer") or att
        locator = pointer.get("locatorInfo") or {}
        # Ältere Backups tragen die Angaben in backupLocator/attachmentLocator.
        fallback = (
            pointer.get("backupLocator")
            or pointer.get("attachmentLocator")
            or pointer.get("invalidAttachmentLocator")
            or {}
        )
        plaintext_hash = b64_to_hex(
            locator.get("plaintextHash") or fallback.get("digest") or fallback.get("plaintextHash")
        )
        local_path = None
        if plaintext_hash and plaintext_hash in self.media:
            local_path = self.media[plaintext_hash][0]
            self.media_used.add(plaintext_hash)
            self.report.attachments_local += 1

        content_type = pointer.get("contentType") or fallback.get("contentType")
        file_name = pointer.get("fileName") or fallback.get("fileName")

        self.conn.execute(
            """INSERT INTO attachments
               (message_id, role, ordinal, content_type, file_name, caption, size, width,
                height, blur_hash, flag, plaintext_hash, incremental_mac, downloaded,
                local_path)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                message_id,
                role,
                ordinal,
                content_type,
                file_name,
                pointer.get("caption") or None,
                to_int(locator.get("size") or fallback.get("size")),
                to_int(pointer.get("width")),
                to_int(pointer.get("height")),
                pointer.get("blurHash") or None,
                att.get("flag"),
                plaintext_hash,
                1 if pointer.get("incrementalMac") else 0,
                1 if att.get("wasDownloaded") else 0,
                local_path,
            ),
        )
        self.report.attachments += 1
        return 1 if role == "body" else 0

    def _resolve_quotes(self) -> None:
        """Zitat -> zitierte Nachricht, über (Chat, Autor, Sendezeit)."""
        for message_id, chat_id, author_id, target in self.pending_quotes:
            target_id = self.by_key.get((chat_id, author_id, target))
            if target_id is None:
                # Autor unbekannt oder abweichend: nur über Chat und Zeit suchen.
                row = self.conn.execute(
                    """SELECT id FROM messages
                       WHERE chat_id = ? AND sent_at = ? AND revision_of IS NULL LIMIT 1""",
                    (chat_id, target),
                ).fetchone()
                target_id = row["id"] if row else None
            if target_id:
                self.conn.execute(
                    "UPDATE quotes SET target_message_id = ? WHERE message_id = ?",
                    (target_id, message_id),
                )
                self.report.quotes_resolved += 1

    def _finalize(self) -> None:
        bid = self.report.backup_id
        for digest, (path, size) in self.media.items():
            self.conn.execute(
                """INSERT OR IGNORE INTO media_files (backup_id, plaintext_hash, path, size)
                   VALUES (?,?,?,?)""",
                (bid, digest, path, size),
            )
        self.report.media_bound = len(self.media_used)
        self.report.media_orphans = len(self.media) - len(self.media_used)
        self.conn.execute(
            "UPDATE backups SET media_files_total = ?, media_files_bound = ? WHERE id = ?",
            (self.report.media_files, self.report.media_bound, bid),
        )
        self.conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('optimize')")


def import_backup(
    conn: sqlite3.Connection,
    backup_dir: str | os.PathLike,
    *,
    label: str | None = None,
    replace: bool = False,
    keep_secrets: bool = False,
    progress=None,
) -> ImportReport:
    imp = Importer(conn, Path(backup_dir), keep_secrets=keep_secrets, progress=progress)
    return imp.run(label=label, replace=replace)
