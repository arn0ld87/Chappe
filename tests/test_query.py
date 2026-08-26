"""Tests für chappe.query — Suche, Verlauf, Auswertungen."""

from __future__ import annotations

from datetime import datetime

import pytest

from chappe import query


# --------------------------------------------------------------------- Suche


def test_search_finds_known_word(db):
    conn, _ = db
    rows = query.search(conn, "Fahrradausflug")
    assert len(rows) == 1
    assert "Fahrradausflug" in rows[0]["body"]


def test_search_like_finds_substring_that_breaks_tokenization(db):
    conn, _ = db
    # "sprächste" liegt mitten in "Gesprächstermin" — kein vollständiges FTS-Token.
    rows = query.search_like(conn, "sprächste")
    assert any("Gesprächstermin" in r["body"] for r in rows)
    # Die reguläre Volltextsuche findet dasselbe Fragment nicht.
    assert query.search(conn, "sprächste") == []


# ---------------------------------------------------------------- parse_date


def test_parse_date_none_is_none():
    assert query.parse_date(None) is None


def test_parse_date_all_formats():
    assert query.parse_date("2026-03-14 18:30:05") == int(
        datetime(2026, 3, 14, 18, 30, 5).timestamp() * 1000
    )
    assert query.parse_date("2026-03-14 18:30") == int(
        datetime(2026, 3, 14, 18, 30).timestamp() * 1000
    )
    assert query.parse_date("2026-03-14") == int(datetime(2026, 3, 14).timestamp() * 1000)
    assert query.parse_date("2026-03") == int(datetime(2026, 3, 1).timestamp() * 1000)
    assert query.parse_date("2026") == int(datetime(2026, 1, 1).timestamp() * 1000)


def test_parse_date_unrecognized_raises():
    with pytest.raises(ValueError):
        query.parse_date("kein-datum")


def test_parse_date_end_boundaries():
    assert query.parse_date("2026-03-14 18:30:05", end=True) == int(
        datetime(2026, 3, 14, 18, 30, 6).timestamp() * 1000
    )
    assert query.parse_date("2026-03-14 18:30", end=True) == int(
        datetime(2026, 3, 14, 18, 31).timestamp() * 1000
    )
    assert query.parse_date("2026-03-14", end=True) == int(
        datetime(2026, 3, 15).timestamp() * 1000
    )
    assert query.parse_date("2026-03", end=True) == int(
        datetime(2026, 4, 1).timestamp() * 1000
    )
    assert query.parse_date("2026", end=True) == int(datetime(2027, 1, 1).timestamp() * 1000)


def test_parse_date_end_month_crosses_year_boundary():
    # Dezember + end=True muss ins Folgejahr springen, nicht Monat 13 ergeben.
    assert query.parse_date("2026-12", end=True) == int(
        datetime(2027, 1, 1).timestamp() * 1000
    )


# ---------------------------------------------------------------- transcript


def test_transcript_is_chronological_and_has_quote_text(db):
    conn, _ = db
    rows = query.transcript(conn)
    sent_times = [r["sent_at"] for r in rows]
    assert sent_times == sorted(sent_times)
    assert any(r["quote_text"] == "Hallo, wie geht es dir heute?" for r in rows)


def test_transcript_descending(db):
    conn, _ = db
    rows = query.transcript(conn, ascending=False)
    sent_times = [r["sent_at"] for r in rows]
    assert sent_times == sorted(sent_times, reverse=True)


# --------------------------------------------------------------------- stats


def test_stats_sum_per_author_matches_overall(db):
    conn, _ = db
    result = query.stats(conn)
    overall_n = result["overall"]["n"]
    by_author_n = sum(r["n"] for r in result["by_author"])
    assert overall_n == by_author_n
    assert overall_n == 10  # M1-M9 (standard) + M14 (standard) aus der Fixture


def test_stats_top_words_excludes_stopwords(db):
    conn, _ = db
    words = dict(query.stats(conn)["top_words"])
    # "heute" ist lang genug (min_len=4), steht aber auf der Stopwortliste.
    assert "heute" not in words
    assert "gesprächstermin" in words


# ------------------------------------------------------------------ timeline


def test_timeline_month_granularity_covers_all_three_months(db):
    conn, _ = db
    rows = query.timeline(conn, granularity="month")
    buckets = [r["bucket"] for r in rows]
    assert buckets == sorted(buckets)
    assert buckets == ["2026-01", "2026-02", "2026-03"]
