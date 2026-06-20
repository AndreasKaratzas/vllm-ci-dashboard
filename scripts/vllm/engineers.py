"""Engineer roster for ready-ticket assignment.

Maps the GitHub logins the team triages against to their display names so the
Ready Tickets tab can render a readable dropdown. The GitHub login is the
field that actually gets written to issue ``assignees``; the display name is
decorative.

PII model
---------
This roster is never serialized into a public artifact. ``ready_tickets.json``
is served on gh-pages — an earlier revision embedded ``{github_login,
display_name, email}`` there and leaked AMD corporate addresses + the team
association publicly. The current flow is:

    1. Admin runs ``scripts/vllm/encrypt_roster.py`` locally. It derives an
       AES-GCM key from the admin's password + per-user salt (same formula as
       ``docs/assets/js/token-vault.js``), encrypts ``to_dict()``, and writes
       the ciphertext to ``data/vllm/ci/engineers.enc.json``.
    2. ``docs/assets/js/ci-ready.js`` fetches that blob on admin render and
       decrypts it with the vault key — so only an authenticated admin whose
       browser vault is unlocked can read the roster.

Do NOT re-add an ``email`` field here, and do NOT reintroduce
``engineers`` as a key on the public plan. Contributor emails belong in the
mail provider's user directory, not in a static dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Engineer:
    github_login: str
    display_name: str


ENGINEERS: tuple[Engineer, ...] = (
    Engineer("Alexei-V-Ivanov-AMD", "Alexei Ivanov"),
    Engineer("AndreasKaratzas", "Andreas Karatzas"),
    Engineer("charlifu", "Charlie Fu"),
    Engineer("divakar-amd", "Divakar Verma"),
    Engineer("kenroche-amd", "Kenneth J Roche"),
    Engineer("mawong-amd", "Matt Wong"),
    Engineer("micah-williamson", "Micah Williamson"),
    Engineer("qli88", "Qiang Li"),
    Engineer("rasmith", "Randall Smith"),
    Engineer("ryanrock-amd", "Ryan Rock"),
    Engineer("yidawu-amd", "Yida Wu"),
)


def by_login(login: str) -> Engineer | None:
    needle = (login or "").lower()
    for e in ENGINEERS:
        if e.github_login.lower() == needle:
            return e
    return None


def to_dict() -> list[dict]:
    return [
        {"github_login": e.github_login, "display_name": e.display_name}
        for e in ENGINEERS
    ]
