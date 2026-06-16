"""Fetch + parse the Wikipedia "2026 FIFA World Cup squads" page.

The page lays each team out as an ``h3`` heading (the country name) under an
``h2`` group heading, optionally a ``Coach: <name>`` paragraph, then a sortable
``wikitable`` with columns: No. | Pos. | Player | Date of birth | Caps | Goals
| Club. The ``Pos.`` cell carries a hidden numeric sort-key span followed by a
``GK``/``DF``/``MF``/``FW`` link, so we read the position from the cell's link
text (falling back to the trailing token) rather than the raw cell text.

Parsing is deliberately defensive: a team whose table is missing or malformed
is skipped rather than aborting the whole page, and individual players with no
recognizable position are dropped so a layout change degrades gracefully.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx
from bs4 import BeautifulSoup, Tag

SQUADS_URL = "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_squads"

# A polite, identifying User-Agent (Wikipedia asks bots to identify themselves).
_USER_AGENT = "khora-world-cup/0.1 (squad ingest; +https://en.wikipedia.org)"

# The four valid position abbreviations, in canonical display order.
VALID_POSITIONS = ("GK", "DF", "MF", "FW")

# Header cells that mark a table as a squad table (must have a position column).
_SQUAD_HEADER_TOKENS = ("Pos", "Player")

# Matches a leading "Coach:" / "Head coach:" / "Manager:" label in a paragraph.
_COACH_RE = re.compile(r"^(?:head\s+)?(?:coach|manager)\s*:\s*(.+)$", re.IGNORECASE)


@dataclass
class Player:
    """One squad member."""

    number: int | None
    position: str  # one of VALID_POSITIONS
    name: str
    club: str = ""
    caps: int | None = None


@dataclass
class TeamSquad:
    """A national team's full World Cup squad."""

    team: str
    coach: str | None = None
    group: str | None = None
    players: list[Player] = field(default_factory=list)


def fetch_squads(*, url: str = SQUADS_URL, timeout: float = 30.0) -> list[TeamSquad]:
    """Fetch the squads page and parse it into per-team structured data.

    Performs a single GET (following redirects) then delegates to
    :func:`parse_squads`. Raises on a non-2xx response so a failed fetch is
    never silently treated as "no squads".
    """
    resp = httpx.get(
        url,
        headers={"User-Agent": _USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return parse_squads(resp.text)


def parse_squads(html: str) -> list[TeamSquad]:
    """Parse the squads-page HTML into a list of :class:`TeamSquad`.

    Walks the document in order: ``h2`` headings set the current group, each
    ``h3`` names a team whose squad is the first squad ``wikitable`` that
    follows it (before the next team/group heading). The optional ``Coach:``
    paragraph between the heading and that table supplies the coach name.
    """
    soup = BeautifulSoup(html, "html.parser")
    squads: list[TeamSquad] = []
    current_group: str | None = None

    # mw-headings wrap the actual h2/h3 in modern Wikipedia output, but the
    # heading tags themselves are still present and in document order.
    for heading in soup.find_all(["h2", "h3"]):
        text = heading.get_text(strip=True)
        if not text:
            continue

        if heading.name == "h2":
            # Group headings look like "Group A"; ignore page chrome such as
            # "Contents", "References", "See also", etc.
            if text.lower().startswith("group "):
                current_group = text
            else:
                current_group = None
            continue

        # h3 == a team name. Find the squad table that belongs to it.
        table = _squad_table_after(heading)
        if table is None:
            continue

        coach = _coach_between(heading, table)
        players = _parse_players(table)
        if not players:
            # No recognizable players — not a real squad table; skip.
            continue

        squads.append(
            TeamSquad(
                team=text,
                coach=coach,
                group=current_group,
                players=players,
            )
        )

    return squads


def _squad_table_after(heading: Tag) -> Tag | None:
    """Return the first squad ``wikitable`` after ``heading``.

    Stops at the next team/group heading so we never attach a table to the
    wrong team. A table qualifies only if its header row mentions both a
    position and a player column.
    """
    for el in heading.find_all_next():
        if isinstance(el, Tag) and el.name in ("h2", "h3"):
            # Reached the next team/group before finding a table.
            return None
        if isinstance(el, Tag) and el.name == "table" and _is_squad_table(el):
            return el
    return None


def _is_squad_table(table: Tag) -> bool:
    """True if ``table`` looks like a squad table (has the expected header)."""
    classes = table.get("class") or []
    if "wikitable" not in classes:
        return False
    first_row = table.find("tr")
    if first_row is None:
        return False
    header = first_row.get_text(" ", strip=True)
    return all(token in header for token in _SQUAD_HEADER_TOKENS)


def _coach_between(heading: Tag, table: Tag) -> str | None:
    """Find a ``Coach:`` paragraph located between the heading and the table."""
    for el in heading.find_all_next():
        if el is table:
            break
        if isinstance(el, Tag) and el.name in ("p", "li"):
            match = _COACH_RE.match(el.get_text(" ", strip=True))
            if match:
                coach = match.group(1).strip()
                return coach or None
    return None


def _parse_players(table: Tag) -> list[Player]:
    """Extract :class:`Player` rows from a squad table."""
    rows = table.find_all("tr")
    players: list[Player] = []
    for row in rows[1:]:  # row 0 is the header
        cells = row.find_all(["th", "td"])
        if len(cells) < 7:
            # Sub-header / spacer / unexpected row — skip.
            continue

        position = _cell_position(cells[1])
        if position not in VALID_POSITIONS:
            continue

        name = _cell_player_name(cells[2])
        if not name:
            continue

        players.append(
            Player(
                number=_cell_int(cells[0]),
                position=position,
                name=name,
                club=cells[6].get_text(" ", strip=True),
                caps=_cell_int(cells[4]),
            )
        )
    return players


def _cell_position(cell: Tag) -> str:
    """Read the position abbreviation from the ``Pos.`` cell.

    The cell is ``<span style="display:none">N</span><a ...>GK</a>``; prefer the
    link text, then fall back to the trailing whitespace-separated token (which
    drops the hidden numeric sort key).
    """
    link = cell.find("a")
    if link:
        token = link.get_text(strip=True).upper()
        if token in VALID_POSITIONS:
            return token
    tokens = cell.get_text(" ", strip=True).split()
    return tokens[-1].upper() if tokens else ""


def _cell_player_name(cell: Tag) -> str:
    """Player name, preferring the linked article title over raw text.

    Strips the captain ``(c)`` marker and any bracketed footnotes so the stored
    name is clean.
    """
    link = cell.find("a")
    name = link.get_text(" ", strip=True) if link else cell.get_text(" ", strip=True)
    # Drop "(c)" captain marker and [n] footnotes.
    name = re.sub(r"\(c\)", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\[[^\]]*\]", "", name)
    return name.strip()


def _cell_int(cell: Tag) -> int | None:
    """Parse the leading integer out of a numeric cell, tolerating noise."""
    match = re.search(r"\d+", cell.get_text(" ", strip=True))
    return int(match.group()) if match else None
