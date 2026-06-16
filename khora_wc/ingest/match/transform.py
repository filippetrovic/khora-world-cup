"""Pure transforms: football-data.org JSON -> ``RememberDoc``.

These functions contain no I/O. They turn match/standings/scorers API payloads
into natural-language prose that reads well for downstream LLM entity
extraction — full sentences with explicit team names, scores, stage, venue and
date rather than terse score lines.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from khora_wc.contract import RememberDoc

SOURCE_NAME = "football-data.org"
COMPETITION_URL = "https://www.football-data.org/competition/2000/overview"

# Human-readable stage labels keyed by the API's STAGE enum.
_STAGE_LABELS = {
    "GROUP_STAGE": "Group Stage",
    "LAST_32": "Round of 32",
    "LAST_16": "Round of 16",
    "QUARTER_FINALS": "Quarter-final",
    "SEMI_FINALS": "Semi-final",
    "THIRD_PLACE": "Third-place play-off",
    "FINAL": "Final",
}


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp; tolerate a trailing ``Z``."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _team_name(team: dict | None) -> str:
    """Best display name for a team, falling back through name/shortName/tla."""
    if not team:
        return "TBD"
    for key in ("name", "shortName", "tla"):
        val = team.get(key)
        if val:
            return str(val)
    return "TBD"


def _stage_label(stage: str | None) -> str:
    if not stage:
        return ""
    return _STAGE_LABELS.get(stage, stage.replace("_", " ").title())


def _group_label(group: str | None) -> str:
    """``GROUP_A`` -> ``Group A``; empty for non-group stages."""
    if not group:
        return ""
    return group.replace("_", " ").title()


def _stage_group_phrase(stage: str | None, group: str | None) -> str:
    """A phrase like 'Group Stage, Group A' or 'Quarter-final' for prose."""
    parts = [p for p in (_stage_label(stage), _group_label(group)) if p]
    # Avoid the redundant "Group Stage, Group A" -> keep both, it reads fine,
    # but drop a duplicate when group already implies the stage.
    return ", ".join(parts)


def _venue_phrase(venue: str | None) -> str:
    return f" at {venue}" if venue else ""


def _date_phrase(dt: datetime | None) -> str:
    if dt is None:
        return ""
    # e.g. "14 June 2026" — platform-independent day formatting.
    return f"{dt.day} {dt.strftime('%B %Y')}"


def _referee_name(referees: list[dict] | None) -> str | None:
    """Return the main referee's name if present."""
    if not referees:
        return None
    # Prefer an explicit REFEREE type; otherwise take the first entry.
    for ref in referees:
        if ref.get("type") in ("REFEREE", "MAIN_REFEREE") and ref.get("name"):
            return str(ref["name"])
    first = referees[0]
    return str(first["name"]) if first.get("name") else None


def _score_pair(node: dict | None) -> tuple[int | None, int | None]:
    """Extract ``(home, away)`` from a score sub-node, tolerating nulls."""
    if not node:
        return (None, None)
    return (node.get("home"), node.get("away"))


def _winner_phrase(home: str, away: str, winner: str | None) -> str:
    """A sentence fragment naming the victor (or the draw)."""
    if winner == "HOME_TEAM":
        return f"{home} beat {away}."
    if winner == "AWAY_TEAM":
        return f"{away} beat {home}."
    if winner == "DRAW":
        return f"{home} and {away} drew."
    return ""


def match_to_doc(match: dict) -> RememberDoc:
    """Turn a football-data match object into a ``RememberDoc``.

    The prose adapts to the match ``status``: upcoming fixtures, live scores,
    finished results (with half-time and, where present, extra time/penalties),
    and the postponed/cancelled/suspended states all get their own phrasing.
    """
    match_id = match.get("id")
    home_team = match.get("homeTeam") or {}
    away_team = match.get("awayTeam") or {}
    home = _team_name(home_team)
    away = _team_name(away_team)

    stage = match.get("stage")
    group = match.get("group")
    status = match.get("status") or "SCHEDULED"
    venue = match.get("venue")
    utc_dt = _parse_dt(match.get("utcDate"))

    score = match.get("score") or {}
    winner = score.get("winner")
    ft_home, ft_away = _score_pair(score.get("fullTime"))
    ht_home, ht_away = _score_pair(score.get("halfTime"))
    et_home, et_away = _score_pair(score.get("extraTime"))
    pen_home, pen_away = _score_pair(score.get("penalties"))

    stage_phrase = _stage_group_phrase(stage, group)
    venue_phrase = _venue_phrase(venue)
    date_phrase = _date_phrase(utc_dt)
    referee = _referee_name(match.get("referees"))

    # --- Build the prose by status ------------------------------------------
    lead = "FIFA World Cup 2026"
    if stage_phrase:
        lead = f"{lead}, {stage_phrase}"
    sentences: list[str] = [f"{lead}."]

    if status in ("SCHEDULED", "TIMED"):
        when = f" on {date_phrase}" if date_phrase else ""
        sentences.append(
            f"Upcoming fixture: {home} versus {away}, to be played{when}{venue_phrase}."
        )
    elif status in ("IN_PLAY", "PAUSED", "EXTRA_TIME"):
        h = ft_home if ft_home is not None else 0
        a = ft_away if ft_away is not None else 0
        live = "at half-time" if status == "PAUSED" else "in progress"
        sentences.append(
            f"Match {live}: {home} {h}, {away} {a}{venue_phrase}."
        )
        if date_phrase:
            sentences.append(f"Being played on {date_phrase}.")
    elif status == "PENALTY_SHOOTOUT":
        h = ft_home if ft_home is not None else 0
        a = ft_away if ft_away is not None else 0
        sentences.append(
            f"{home} and {away} are level at {h}-{a} and the match has gone to a penalty shootout{venue_phrase}."
        )
    elif status == "FINISHED":
        sentences.append(_finished_prose(
            home, away, ft_home, ft_away, ht_home, ht_away,
            et_home, et_away, pen_home, pen_away, winner,
            date_phrase, venue_phrase,
        ))
    elif status in ("POSTPONED", "SUSPENDED", "CANCELLED"):
        word = {
            "POSTPONED": "postponed",
            "SUSPENDED": "suspended",
            "CANCELLED": "cancelled",
        }[status]
        when = f" originally scheduled for {date_phrase}" if date_phrase else ""
        sentences.append(
            f"The match between {home} and {away}{when}{venue_phrase} has been {word}."
        )
    elif status == "AWARDED":
        result = ""
        if ft_home is not None and ft_away is not None:
            result = f" The result was awarded as {home} {ft_home}, {away} {ft_away}."
        sentences.append(
            f"The match between {home} and {away}{venue_phrase} was awarded.{result}"
        )
    else:
        # Unknown status — keep a safe, generic line.
        sentences.append(f"{home} versus {away}{venue_phrase} (status: {status}).")

    if referee:
        sentences.append(f"Referee: {referee}.")

    content = " ".join(s for s in sentences if s).strip()

    # --- Title ---------------------------------------------------------------
    title = f"{home} vs {away}"
    if stage_phrase:
        title = f"{title} — {stage_phrase}"

    # --- Metadata ------------------------------------------------------------
    metadata: dict[str, Any] = {
        "stage": stage,
        "group": group,
        "status": status,
        "home": home,
        "away": away,
        "score": score,
        "venue": venue,
        "lastUpdated": match.get("lastUpdated"),
    }

    source_url = (
        f"https://www.football-data.org/match/{match_id}" if match_id is not None else None
    )

    return RememberDoc(
        external_id=f"match:fd:{match_id}",
        content=content,
        title=title,
        source_type="match",
        source_name=SOURCE_NAME,
        source_url=source_url,
        source_timestamp=utc_dt,
        metadata=metadata,
    )


def _finished_prose(
    home: str,
    away: str,
    ft_home: int | None,
    ft_away: int | None,
    ht_home: int | None,
    ht_away: int | None,
    et_home: int | None,
    et_away: int | None,
    pen_home: int | None,
    pen_away: int | None,
    winner: str | None,
    date_phrase: str,
    venue_phrase: str,
) -> str:
    """Compose the full-time result sentence for a finished match."""
    h = ft_home if ft_home is not None else 0
    a = ft_away if ft_away is not None else 0
    parts = [f"Full time: {home} {h}, {away} {a}"]

    if ht_home is not None and ht_away is not None:
        parts.append(f" (half-time {ht_home}–{ht_away})")
    parts.append(".")

    extras: list[str] = []
    if et_home is not None and et_away is not None:
        extras.append(f"After extra time it was {home} {et_home}, {away} {et_away}.")
    if pen_home is not None and pen_away is not None:
        # Penalties decide the winner; state it explicitly.
        pen_winner = home if pen_home > pen_away else away
        extras.append(
            f"{home} and {away} went to a penalty shootout, which {pen_winner} won {pen_home}–{pen_away}."
        )

    win = "" if (pen_home is not None) else _winner_phrase(home, away, winner)

    when = f" Played on {date_phrase}{venue_phrase}." if date_phrase else (
        f" Played{venue_phrase}." if venue_phrase else ""
    )

    sentence = "".join(parts)
    tail = " ".join([win, *extras]).strip()
    if tail:
        sentence = f"{sentence} {tail}"
    return f"{sentence}{when}".strip()


def standings_to_docs(standings: dict) -> list[RememberDoc]:
    """One doc per group table.

    Each entry of the API's ``standings`` list is a ``{group, table}`` block;
    ``table`` rows carry ``position``, ``team``, ``playedGames``, ``won``,
    ``draw``, ``lost``, ``goalDifference`` and ``points``. We render each as a
    prose ranking that names every team.
    """
    docs: list[RememberDoc] = []
    now = _now()

    blocks = standings.get("standings") or []
    for block in blocks:
        # Only the overall (TOTAL) table per group, to avoid home/away dupes.
        if block.get("type") not in (None, "TOTAL"):
            continue
        group = block.get("group")
        table = block.get("table") or []
        if not group or not table:
            continue

        group_label = _group_label(group)
        rows: list[str] = []
        max_played = 0
        for row in table:
            team = _team_name(row.get("team"))
            pos = row.get("position", "?")
            pts = row.get("points", 0)
            won = row.get("won", 0)
            draw = row.get("draw", 0)
            lost = row.get("lost", 0)
            gd = row.get("goalDifference", 0)
            played = row.get("playedGames", 0)
            max_played = max(max_played, played)
            gd_str = f"+{gd}" if gd > 0 else str(gd)
            rows.append(
                f"{pos}. {team} {pts} pts ({won}W {draw}D {lost}L, GD {gd_str})"
            )

        header = f"{group_label} standings after matchday {max_played}: "
        content = header + "; ".join(rows) + "."

        docs.append(
            RememberDoc(
                external_id=f"standings:wc:{group}",
                content=content,
                title=f"{group_label} standings",
                source_type="match",
                source_name=SOURCE_NAME,
                source_url=COMPETITION_URL,
                source_timestamp=now,
                metadata={"group": group, "matchday": max_played, "type": "standings"},
            )
        )

    return docs


def scorers_to_doc(scorers: dict) -> RememberDoc:
    """Single doc listing the tournament's top scorers.

    Each ``scorers`` entry has ``player`` ({name}), ``team`` ({name}) and a
    goal count (``goals`` or, on some payloads, ``numberOfGoals``).
    """
    now = _now()
    entries = scorers.get("scorers") or []

    lines: list[str] = []
    for idx, entry in enumerate(entries, start=1):
        player = (entry.get("player") or {}).get("name") or "Unknown"
        team = (entry.get("team") or {}).get("name") or "Unknown"
        goals = entry.get("goals")
        if goals is None:
            goals = entry.get("numberOfGoals", 0)
        goal_word = "goal" if goals == 1 else "goals"
        lines.append(f"{idx}. {player} ({team}) — {goals} {goal_word}")

    if lines:
        content = "World Cup 2026 top scorers: " + "; ".join(lines) + "."
    else:
        content = "World Cup 2026 top scorers: no goals have been recorded yet."

    return RememberDoc(
        external_id="scorers:wc",
        content=content,
        title="World Cup 2026 top scorers",
        source_type="match",
        source_name=SOURCE_NAME,
        source_url=COMPETITION_URL,
        source_timestamp=now,
        metadata={"type": "scorers", "count": len(entries)},
    )
