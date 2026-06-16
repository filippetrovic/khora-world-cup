"""World Cup 2026 squad ingestion from Wikipedia.

Fetches and parses the "2026 FIFA World Cup squads" page into structured
per-team squads (:class:`~khora_wc.ingest.squads.wikipedia.TeamSquad`) and
turns each into a :class:`~khora_wc.contract.RememberDoc` of prose so the
answer agent can field questions like "who are Bosnia's goalkeepers?".
"""
