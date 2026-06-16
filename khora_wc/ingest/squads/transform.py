"""Pure transform: :class:`TeamSquad` -> :class:`RememberDoc`.

Renders a squad as readable prose grouped by position, goalkeepers first and
each group explicitly labelled, so the answer agent can directly field "who are
Bosnia's goalkeepers?" / "who is in Bosnia's squad?". No I/O lives here.

``source_type`` is ``"match"`` so the answer agent's ``source_type='match'``
filter surfaces squads alongside fixtures/standings; ``source_timestamp`` is the
2026-06-01 squad-submission deadline.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from khora_wc.contract import RememberDoc
from khora_wc.ingest.squads.wikipedia import Player, TeamSquad

SOURCE_NAME = "Wikipedia"
SQUADS_URL = "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_squads"

# Squad-submission deadline for World Cup 2026 (UTC) — the natural "as of" date.
SQUAD_DEADLINE = datetime(2026, 6, 1, tzinfo=UTC)

# Position abbreviation -> (singular-ish group label used in prose).
_POSITION_LABELS: dict[str, str] = {
    "GK": "Goalkeepers",
    "DF": "Defenders",
    "MF": "Midfielders",
    "FW": "Forwards",
}

# Render order: goalkeepers first, then back-to-front.
_POSITION_ORDER = ("GK", "DF", "MF", "FW")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def team_slug(team: str) -> str:
    """Slugify a team name for the external id, e.g. 'Bosnia and Herzegovina'
    -> 'bosnia-and-herzegovina'."""
    slug = _SLUG_RE.sub("-", team.lower()).strip("-")
    return slug or "team"


def _player_phrase(player: Player) -> str:
    """'Vedran Kjosevski (Slaven Belupo)' — name plus club when known."""
    if player.club:
        return f"{player.name} ({player.club})"
    return player.name


def _group_sentence(label: str, players: list[Player]) -> str:
    """'Goalkeepers: A (club), B (club).' for one position group."""
    listed = ", ".join(_player_phrase(p) for p in players)
    return f"{label}: {listed}."


def squad_to_doc(team: TeamSquad) -> RememberDoc:
    """Turn a parsed :class:`TeamSquad` into a ``RememberDoc``.

    The content opens with the team (and coach, if known), then lists each
    position group on its own labelled clause with goalkeepers first.
    """
    # Group players by position, preserving table (shirt-number) order.
    by_position: dict[str, list[Player]] = {pos: [] for pos in _POSITION_ORDER}
    for player in team.players:
        by_position.setdefault(player.position, []).append(player)

    coach_clause = f" (Coach: {team.coach})" if team.coach else ""
    lead = f"FIFA World Cup 2026 squad — {team.team}{coach_clause}."

    sentences: list[str] = [lead]
    for pos in _POSITION_ORDER:
        players = by_position.get(pos) or []
        if players:
            sentences.append(_group_sentence(_POSITION_LABELS[pos], players))

    content = " ".join(sentences)

    title = f"{team.team} — World Cup 2026 squad"

    metadata = {
        "team": team.team,
        "group": team.group,
        "coach": team.coach,
        "player_count": len(team.players),
        "players": [
            {
                "number": p.number,
                "position": p.position,
                "name": p.name,
                "club": p.club,
                "caps": p.caps,
            }
            for p in team.players
        ],
    }

    return RememberDoc(
        external_id=f"squad:{team_slug(team.team)}",
        content=content,
        title=title,
        source_type="match",
        source_name=SOURCE_NAME,
        source_url=SQUADS_URL,
        source_timestamp=SQUAD_DEADLINE,
        metadata=metadata,
    )
