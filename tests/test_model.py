"""Tests für chappe.model — reine Funktionen ohne DB-Abhängigkeit."""

from __future__ import annotations

import base64
import hashlib

from chappe import model


# ------------------------------------------------------------------- to_int


def test_to_int_from_string():
    assert model.to_int("42") == 42


def test_to_int_from_number():
    assert model.to_int(42) == 42
    assert model.to_int(42.0) == 42


def test_to_int_none_and_empty():
    assert model.to_int(None) is None
    assert model.to_int("") is None


def test_to_int_garbage():
    assert model.to_int("kein-datum") is None
    assert model.to_int([1, 2, 3]) is None


# --------------------------------------------------------------- b64_to_hex


def test_b64_to_hex_roundtrip():
    data = b"hallo welt"
    digest = hashlib.sha256(data).digest()
    b64 = base64.b64encode(digest).decode()
    assert model.b64_to_hex(b64) == hashlib.sha256(data).hexdigest()


def test_b64_to_hex_none_and_empty():
    assert model.b64_to_hex(None) is None
    assert model.b64_to_hex("") is None


def test_b64_to_hex_garbage():
    # "abc" hat eine Länge, die kein gültiges Base64-Padding erlaubt.
    assert model.b64_to_hex("abc") is None


# -------------------------------------------------------------- extension_for


def test_extension_for_filename_wins_over_content_type():
    # Dateiname schlägt content_type, auch wenn beide vorhanden sind.
    assert model.extension_for("video/mp4", "urlaub.PNG") == ".png"


def test_extension_for_uses_content_type_without_filename():
    assert model.extension_for("image/jpeg", None) == ".jpg"


def test_extension_for_unknown_type_falls_back_to_bin():
    assert model.extension_for("application/x-nichts-bekanntes", None) == ".bin"
    assert model.extension_for(None, None) == ".bin"


# -------------------------------------------------------------- safe_filename


def test_safe_filename_removes_path_separators():
    result = model.safe_filename("../../etc/passwd")
    assert "/" not in result
    assert "\\" not in result


def test_safe_filename_removes_control_characters():
    result = model.safe_filename("a\x00\x01\x1fb")
    assert all(ord(c) >= 32 for c in result)


def test_safe_filename_limits_length():
    result = model.safe_filename("x" * 500, limit=10)
    assert len(result) <= 10


def test_safe_filename_keeps_readable_name():
    assert model.safe_filename("Urlaubsfoto 2026.png") == "Urlaubsfoto 2026.png"


# ----------------------------------------------------------- recipient_fields


def _contact(**overrides) -> dict:
    base = {
        "nickname": {"given": "Nick"},
        "systemGivenName": "Sys",
        "systemFamilyName": "Tem",
        "profileGivenName": "Prof",
        "profileFamilyName": "il",
        "username": "user1",
        "e164": "491701112222",
    }
    base.update(overrides)
    return {"contact": base}


def test_recipient_fields_nickname_wins():
    fields = model.recipient_fields(_contact())
    assert fields["display_name"] == "Nick"


def test_recipient_fields_system_name_next():
    fields = model.recipient_fields(_contact(nickname=None))
    assert fields["display_name"] == "Sys Tem"


def test_recipient_fields_profile_name_next():
    fields = model.recipient_fields(
        _contact(nickname=None, systemGivenName=None, systemFamilyName=None)
    )
    assert fields["display_name"] == "Prof il"


def test_recipient_fields_username_next():
    fields = model.recipient_fields(
        _contact(
            nickname=None,
            systemGivenName=None,
            systemFamilyName=None,
            profileGivenName=None,
            profileFamilyName=None,
        )
    )
    assert fields["display_name"] == "user1"


def test_recipient_fields_e164_last():
    fields = model.recipient_fields(
        _contact(
            nickname=None,
            systemGivenName=None,
            systemFamilyName=None,
            profileGivenName=None,
            profileFamilyName=None,
            username=None,
        )
    )
    assert fields["display_name"] == "+491701112222"


def test_recipient_fields_self():
    fields = model.recipient_fields({"self": {"avatarColor": "A100"}}, self_label="Ich selbst")
    assert fields["display_name"] == "Ich selbst"
    assert fields["kind"] == "self"


# ---------------------------------------------------------------- strip_secrets


def test_strip_secrets_removes_top_level_and_nested():
    data = {
        "a": 1,
        "profileKey": "geheim",
        "nested": {"identityKey": "auch-geheim", "keep": "ja"},
        "list": [{"svrPin": "1234", "ok": "ja"}],
    }
    cleaned = model.strip_secrets(data)
    assert cleaned["a"] == 1
    assert "profileKey" not in cleaned
    assert "identityKey" not in cleaned["nested"]
    assert cleaned["nested"]["keep"] == "ja"
    assert "svrPin" not in cleaned["list"][0]
    assert cleaned["list"][0]["ok"] == "ja"


# ------------------------------------------------------------------ describe_call


def test_describe_call_known_combo():
    call = {"type": "AUDIO_CALL", "direction": "INCOMING", "state": "MISSED"}
    assert model.describe_call(call) == "Verpasster Sprachanruf"


def test_describe_call_unknown_combo_falls_back():
    call = {"type": "SCREEN_SHARE", "direction": "OUTGOING", "state": "UNKNOWN"}
    text = model.describe_call(call)
    assert text.startswith("Anruf")
    assert "SCREEN_SHARE" in text
