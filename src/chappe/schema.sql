-- chappe: normalisiertes Schema für Signal-Backups (Backup-Format v1/v2, JSONL)
-- Mehrere Backups koexistieren in einer DB; `backups.id` trennt sie.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS backups (
    id                INTEGER PRIMARY KEY,
    label             TEXT NOT NULL UNIQUE,      -- Name des Backup-Verzeichnisses
    source_path       TEXT NOT NULL,
    imported_at       TEXT NOT NULL,             -- ISO-8601, lokale Zeit
    backup_time_ms    INTEGER,                   -- Zeitpunkt der Backup-Erstellung
    format_version    TEXT,
    app_version       TEXT,
    first_app_version TEXT,
    self_rid          TEXT,                      -- recipient-id des eigenen Accounts
    self_name         TEXT,
    media_files_total INTEGER DEFAULT 0,
    media_files_bound INTEGER DEFAULT 0          -- davon einer Nachricht zugeordnet
);

CREATE TABLE IF NOT EXISTS recipients (
    id           INTEGER PRIMARY KEY,
    backup_id    INTEGER NOT NULL REFERENCES backups(id) ON DELETE CASCADE,
    rid          TEXT NOT NULL,                  -- recipient.id aus dem Backup
    kind         TEXT NOT NULL,                  -- contact | self | group | distributionList | releaseNotes | callLink
    display_name TEXT,
    given_name   TEXT,
    family_name  TEXT,
    nickname     TEXT,
    profile_name TEXT,
    system_name  TEXT,
    username     TEXT,
    e164         TEXT,
    aci          TEXT,
    pni          TEXT,
    group_title  TEXT,
    avatar_color TEXT,
    registered   INTEGER,                        -- 1 registriert, 0 nicht, NULL unbekannt
    blocked      INTEGER,
    hidden       INTEGER,
    raw          TEXT NOT NULL,                  -- vollständiges Original-JSON
    UNIQUE (backup_id, rid)
);

CREATE TABLE IF NOT EXISTS chats (
    id            INTEGER PRIMARY KEY,
    backup_id     INTEGER NOT NULL REFERENCES backups(id) ON DELETE CASCADE,
    cid           TEXT NOT NULL,                 -- chat.id aus dem Backup
    recipient_id  INTEGER REFERENCES recipients(id),
    name          TEXT,                          -- aufgelöster Anzeigename
    kind          TEXT,                          -- direct | group | other
    archived      INTEGER,
    pinned_order  INTEGER,
    muted_until   INTEGER,
    raw           TEXT NOT NULL,
    UNIQUE (backup_id, cid)
);

CREATE TABLE IF NOT EXISTS messages (
    id             INTEGER PRIMARY KEY,
    backup_id      INTEGER NOT NULL REFERENCES backups(id) ON DELETE CASCADE,
    chat_id        INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    author_id      INTEGER REFERENCES recipients(id),
    sent_at        INTEGER NOT NULL,             -- ms seit Epoch
    received_at    INTEGER,
    server_sent_at INTEGER,
    direction      TEXT NOT NULL,                -- outgoing | incoming | directionless
    kind           TEXT NOT NULL,                -- standard | call | update | deleted | sticker | contact | payment | giftBadge | viewOnce | other
    subkind        TEXT,                         -- z. B. AUDIO_CALL, MESSAGE_REQUEST_ACCEPTED, GROUP_UPDATE
    body           TEXT,
    body_preview   TEXT,                         -- gekürzte, einzeilige Fassung für Listen
    is_read        INTEGER,
    is_expiring    INTEGER,
    is_edited      INTEGER DEFAULT 0,            -- Nachricht wurde nachträglich bearbeitet
    revision_of    INTEGER REFERENCES messages(id) ON DELETE CASCADE,
    revision_index INTEGER,                      -- 0 = älteste Fassung
    n_attachments  INTEGER DEFAULT 0,
    n_reactions    INTEGER DEFAULT 0,
    has_quote      INTEGER DEFAULT 0,
    raw            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
    id             INTEGER PRIMARY KEY,
    message_id     INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    role           TEXT NOT NULL,                -- body | quote_thumbnail | link_preview | sticker | long_text
    ordinal        INTEGER NOT NULL DEFAULT 0,
    content_type   TEXT,
    file_name      TEXT,
    caption        TEXT,
    size           INTEGER,
    width          INTEGER,
    height         INTEGER,
    blur_hash      TEXT,
    flag           TEXT,                         -- VOICE_MESSAGE | BORDERLESS | GIF
    plaintext_hash TEXT,                         -- hex(sha256(Klartext)) — Schlüssel zur Datei
    incremental_mac INTEGER,
    downloaded     INTEGER DEFAULT 0,            -- Pointer sagt: lag lokal vor
    local_path     TEXT,                         -- Pfad im Backup-Verzeichnis, falls gefunden
    export_name    TEXT                          -- sprechender Name für Medien-Export
);

CREATE TABLE IF NOT EXISTS reactions (
    id         INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    author_id  INTEGER REFERENCES recipients(id),
    emoji      TEXT NOT NULL,
    sent_at    INTEGER,
    ordinal    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS quotes (
    message_id       INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    author_id        INTEGER REFERENCES recipients(id),
    target_sent_at   INTEGER,
    target_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    quote_type       TEXT,
    text             TEXT
);

CREATE TABLE IF NOT EXISTS link_previews (
    id          INTEGER PRIMARY KEY,
    message_id  INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    url         TEXT,
    title       TEXT,
    description TEXT,
    date        INTEGER
);

CREATE TABLE IF NOT EXISTS calls (
    message_id  INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    call_id     TEXT,
    call_type   TEXT,                            -- AUDIO_CALL | VIDEO_CALL | GROUP_CALL
    direction   TEXT,                            -- INCOMING | OUTGOING
    state       TEXT,                            -- ACCEPTED | MISSED | DECLINED | NOT_ACCEPTED
    started_at  INTEGER
);

CREATE TABLE IF NOT EXISTS send_status (
    id           INTEGER PRIMARY KEY,
    message_id   INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    recipient_id INTEGER REFERENCES recipients(id),
    status       TEXT,                           -- pending | sent | delivered | read | viewed | failed | skipped
    timestamp    INTEGER,
    sealed_sender INTEGER
);

CREATE TABLE IF NOT EXISTS media_files (
    id             INTEGER PRIMARY KEY,
    backup_id      INTEGER NOT NULL REFERENCES backups(id) ON DELETE CASCADE,
    plaintext_hash TEXT NOT NULL,                -- hex(sha256(Dateiinhalt))
    path           TEXT NOT NULL,                -- relativ zum Backup-Verzeichnis
    size           INTEGER,
    UNIQUE (backup_id, plaintext_hash)
);

CREATE INDEX IF NOT EXISTS ix_msg_chat_time  ON messages (chat_id, sent_at);
CREATE INDEX IF NOT EXISTS ix_msg_author     ON messages (author_id);
CREATE INDEX IF NOT EXISTS ix_msg_kind       ON messages (kind);
CREATE INDEX IF NOT EXISTS ix_msg_sent       ON messages (sent_at);
CREATE INDEX IF NOT EXISTS ix_msg_revision   ON messages (revision_of);
CREATE INDEX IF NOT EXISTS ix_att_msg        ON attachments (message_id);
CREATE INDEX IF NOT EXISTS ix_att_hash       ON attachments (plaintext_hash);
CREATE INDEX IF NOT EXISTS ix_att_type       ON attachments (content_type);
CREATE INDEX IF NOT EXISTS ix_rea_msg        ON reactions (message_id);
CREATE INDEX IF NOT EXISTS ix_quo_target     ON quotes (target_sent_at);
CREATE INDEX IF NOT EXISTS ix_snd_msg        ON send_status (message_id);

-- Volltextsuche über die Nachrichtentexte (external content, bleibt schlank)
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    body,
    content='messages',
    content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages
    WHEN new.body IS NOT NULL BEGIN
        INSERT INTO messages_fts(rowid, body) VALUES (new.id, new.body);
    END;
CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, body) VALUES ('delete', old.id, old.body);
    END;
CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE OF body ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, body) VALUES ('delete', old.id, old.body);
        INSERT INTO messages_fts(rowid, body) SELECT new.id, new.body WHERE new.body IS NOT NULL;
    END;

-- Bequeme Sicht für SQL-Auswertungen: alles Wichtige an einem Nachrichtensatz.
CREATE VIEW IF NOT EXISTS v_messages AS
SELECT
    m.id,
    b.label                                            AS backup,
    c.name                                             AS chat,
    c.id                                               AS chat_id,
    COALESCE(r.display_name, '?')                      AS author,
    m.direction,
    m.kind,
    m.subkind,
    datetime(m.sent_at / 1000, 'unixepoch', 'localtime') AS sent_local,
    m.sent_at,
    m.body,
    m.n_attachments,
    m.n_reactions,
    m.has_quote,
    m.is_edited
FROM messages m
JOIN chats      c ON c.id = m.chat_id
JOIN backups    b ON b.id = m.backup_id
LEFT JOIN recipients r ON r.id = m.author_id
WHERE m.revision_of IS NULL;

CREATE VIEW IF NOT EXISTS v_chat_overview AS
SELECT
    c.id                                                  AS chat_id,
    b.label                                               AS backup,
    c.name                                                AS chat,
    c.kind,
    COUNT(m.id)                                           AS messages,
    SUM(m.direction = 'outgoing')                         AS sent,
    SUM(m.direction = 'incoming')                         AS received,
    SUM(m.n_attachments)                                  AS attachments,
    datetime(MIN(m.sent_at) / 1000, 'unixepoch', 'localtime') AS first_message,
    datetime(MAX(m.sent_at) / 1000, 'unixepoch', 'localtime') AS last_message
FROM chats c
JOIN backups b ON b.id = c.backup_id
LEFT JOIN messages m ON m.chat_id = c.id AND m.revision_of IS NULL
GROUP BY c.id;
