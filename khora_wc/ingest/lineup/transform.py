"""Pure transform: API-Football lineups JSON -> ``RememberDoc``.

No I/O. Turns a fixture + its ``/fixtures/lineups`` response into readable prose
that names every starter grouped by position, with the goalkeeper made explicit,
so downstream LLM extraction (and the agent's ``source_type='match'`` filter)
can answer questions like "who was Bosnia's goalkeeper vs Canada?".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from khora_wc.contract import RememberDoc

SOURCE_NAME = "API-Football"

# API-Football grid position codes -> (singular, plural) human labels.
_POSITION_LABELS: dict[str, tuple[str, str]] = {
    "G": ("Goalkeeper", "Goalkeeper"),
    "D": ("Defender", "Defenders"),
    "M": ("Midfielder", "Midfielders"),
    "F": ("Forward", "Forwards"),
}
# Render order so prose always reads back-to-front: keeper, defence, ... attack.
_POSITION_ORDER = ("G", "D", "M", "F")


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp; tolerate a trailing ``Z``."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _date_phrase(dt: datetime | None) -> str:
    if dt is None:
        return ""
    # e.g. "12 June 2026" — platform-independent day formatting.
    return f"{dt.day} {dt.strftime('%B %Y')}"


def _team_name(team: dict | None) -> str:
    if not team:
        return "TBD"
    name = team.get("name")
    return str(name) if name else "TBD"


def _grouped_players(start_xi: list[dict]) -> dict[str, list[str]]:
    """Group starter names by their position code (G/D/M/F).

    Each ``startXI`` entry is ``{"player": {"name", "pos", ...}}``. Players with
    an unknown/absent ``pos`` are collected under an empty-string key so they are
    never silently dropped from the prose.
    """
    grouped: dict[str, list[str]] = {code: [] for code in _POSITION_ORDER}
    for entry in start_xi:
        player = entry.get("player") or {}
        name = player.get("name") or "Unknown"
        pos = (player.get("pos") or "").upper()
        grouped.setdefault(pos, []).append(str(name))
    return grouped


def _team_clause(lineup: dict) -> str:
    """One team's clause, e.g. 'Bosnia (4-2-3-1): Goalkeeper X; Defenders A, B; ...'."""
    name = _team_name(lineup.get("team"))
    formation = lineup.get("formation")
    start_xi = lineup.get("startXI") or []
    grouped = _grouped_players(start_xi)

    segments: list[str] = []
    for code in _POSITION_ORDER:
        names = grouped.get(code) or []
        if not names:
            continue
        singular, plural = _POSITION_LABELS[code]
        label = singular if len(names) == 1 else plural
        segments.append(f"{label} {', '.join(names)}")

    # Any players with an unrecognized position code — append rather than drop.
    extras = grouped.get("", [])
    if extras:
        segments.append(f"Other {', '.join(extras)}")

    head = f"{name} ({formation})" if formation else name
    if not segments:
        return f"{head}: lineup not available"
    return f"{head}: " + "; ".join(segments)


def lineups_to_doc(fixture: dict, lineups: list[dict]) -> RememberDoc | None:
    """Turn a fixture + its lineups into a ``RememberDoc`` (or ``None``).

    Returns ``None`` when no lineups are present (not yet published) so the
    orchestrator can skip writing an empty doc and retry the fixture later.
    """
    if not lineups:
        return None

    fixture_node = fixture.get("fixture") or {}
    fixture_id = fixture_node.get("id")
    utc_dt = _parse_dt(fixture_node.get("date"))

    teams = fixture.get("teams") or {}
    home = _team_name(teams.get("home"))
    away = _team_name(teams.get("away"))
    # Fall back to the names carried on the lineup entries if the fixture node
    # lacked a teams block (e.g. when called with a bare fixture stub).
    if home == "TBD" and lineups:
        home = _team_name(lineups[0].get("team"))
    if away == "TBD" and len(lineups) > 1:
        away = _team_name(lineups[1].get("team"))

    date_phrase = _date_phrase(utc_dt)
    when = f" ({date_phrase})" if date_phrase else ""

    clauses = [_team_clause(lineup) for lineup in lineups]
    content = (
        f"Starting lineups — {home} vs {away}{when}. " + " ".join(f"{c}." for c in clauses)
    )

    title = f"Starting lineups: {home} vs {away}"

    metadata: dict[str, Any] = {
        "type": "lineup",
        "fixture_id": fixture_id,
        "home": home,
        "away": away,
        "formations": {
            _team_name(lineup.get("team")): lineup.get("formation")
            for lineup in lineups
        },
    }

    source_url = (
        f"https://www.api-football.com/fixtures/{fixture_id}"
        if fixture_id is not None
        else None
    )

    return RememberDoc(
        external_id=f"lineup:af:{fixture_id}",
        content=content,
        title=title,
        source_type="match",
        source_name=SOURCE_NAME,
        source_url=source_url,
        source_timestamp=utc_dt,
        metadata=metadata,
    )
