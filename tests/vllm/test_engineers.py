"""Unit tests for ``scripts/vllm/engineers.py``.

The engineer roster feeds the admin-only assignee dropdown on the Ready
Tickets tab. The path is:

    * ``scripts/vllm/encrypt_roster.py`` — admin runs this locally when
      ``ENGINEERS`` changes. It serializes ``to_dict()``, encrypts it with
      AES-GCM using the admin's vault-derived key, and writes
      ``data/vllm/ci/engineers.enc.json``.
    * ``docs/assets/js/ci-ready.js`` — on every render, if the browser
      vault is unlocked, fetches that ciphertext and decrypts it via
      ``window.__tokenVault.decryptExternal``. Guests and locked sessions
      never see the roster.

The roster is intentionally NOT serialized into ``ready_tickets.json`` —
that file is served publicly on gh-pages, so any name/login/email there
leaks to the internet. These tests defend that invariant: no ``email``
field on the dataclass, no ``email`` key in ``to_dict`` output, unique
case-insensitive logins, and ``Engineer`` immutability so a renderer
can't mutate the roster by accident.
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from vllm.engineers import ENGINEERS, Engineer, by_login, to_dict


LOGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")


class TestRoster:
    def test_roster_nonempty(self):
        assert len(ENGINEERS) >= 5, "Roster must have at least a handful of engineers"

    def test_each_entry_is_engineer(self):
        for e in ENGINEERS:
            assert isinstance(e, Engineer), f"non-Engineer in roster: {e!r}"

    def test_logins_are_unique(self):
        logins = [e.github_login for e in ENGINEERS]
        assert len(logins) == len(set(logins)), (
            "Duplicate github_login in roster — would cause dashboard dropdown collisions"
        )

    def test_logins_lowercase_are_unique(self):
        # Case-insensitive uniqueness: GitHub login lookups fold case.
        lowered = [e.github_login.lower() for e in ENGINEERS]
        assert len(lowered) == len(set(lowered))

    def test_logins_match_github_format(self):
        for e in ENGINEERS:
            assert LOGIN_RE.match(e.github_login), (
                f"Invalid GitHub login format: {e.github_login!r}"
            )

    def test_display_names_are_nonempty(self):
        for e in ENGINEERS:
            assert e.display_name.strip(), (
                f"Engineer {e.github_login} has an empty display_name"
            )

    def test_admin_is_in_roster(self):
        # The dashboard admin must be findable in the roster so the assignment
        # UI can offer to assign issues to themselves.
        logins = {e.github_login for e in ENGINEERS}
        assert "AndreasKaratzas" in logins, "dashboard admin must appear in the roster"

    def test_engineer_has_no_email_field(self):
        # PII guarantee: the Engineer dataclass must not carry an email.
        # If this fails, a future contributor added email back and the
        # roster will re-leak AMD corporate addresses into the public
        # ready_tickets.json on gh-pages.
        fields = {f.name for f in dataclasses.fields(Engineer)}
        assert "email" not in fields, (
            "Engineer must not carry an email field — this roster is serialized "
            "into a publicly-hosted JSON file."
        )


class TestByLogin:
    def test_exact_match(self):
        e = by_login("AndreasKaratzas")
        assert e is not None and e.github_login == "AndreasKaratzas"

    def test_case_insensitive_match(self):
        # GitHub logins are not case-sensitive for lookups. Callers that type
        # "andreaskaratzas" must still resolve the real roster entry.
        lower = by_login("andreaskaratzas")
        upper = by_login("ANDREASKARATZAS")
        assert lower is not None and upper is not None
        assert lower.github_login == upper.github_login == "AndreasKaratzas"

    def test_unknown_login_returns_none(self):
        assert by_login("not-a-real-login-anywhere") is None

    def test_empty_login_returns_none(self):
        assert by_login("") is None
        assert by_login(None) is None  # type: ignore[arg-type]


class TestToDict:
    def test_shape(self):
        out = to_dict()
        assert isinstance(out, list)
        assert len(out) == len(ENGINEERS)
        for d in out:
            assert set(d.keys()) == {"github_login", "display_name"}

    def test_values_preserved(self):
        out = to_dict()
        expected = {(e.github_login, e.display_name) for e in ENGINEERS}
        got = {(d["github_login"], d["display_name"]) for d in out}
        assert got == expected

    def test_no_email_in_serialization(self):
        # Primary PII guarantee — a single stray "email" key in the dashboard
        # plan JSON exposes every AMD corporate address to the internet.
        out = to_dict()
        for d in out:
            assert "email" not in d, (
                f"Engineer serialization leaked an email field: {d!r}"
            )

    def test_output_is_json_safe(self):
        import json
        # The dashboard serializes this dict to ready_tickets.json. If it's
        # not JSON-round-trippable the tab breaks silently.
        rendered = json.dumps(to_dict())
        roundtripped = json.loads(rendered)
        assert isinstance(roundtripped, list)
        assert roundtripped == to_dict()


class TestImmutability:
    def test_engineer_is_frozen(self):
        e = ENGINEERS[0]
        with pytest.raises(dataclasses.FrozenInstanceError):
            e.github_login = "hijacked"  # type: ignore[misc]

    def test_to_dict_returns_fresh_list(self):
        a = to_dict()
        b = to_dict()
        assert a == b
        a.append({"github_login": "x", "display_name": "y"})
        # Mutating the returned list must not affect subsequent calls.
        assert b == to_dict()
