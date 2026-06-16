"""Per-match lineup ingestion pipeline for FIFA World Cup 2026.

Fetches each fixture's starting XI (player names + grid positions + formation)
from API-Football (api-sports.io v3, league 1 / season 2026), transforms them
into readable ``RememberDoc`` prose that names every starter with their position
(goalkeeper made explicit), and writes only new fixtures into the inbox for the
remember worker to ingest.

Built so the agent's ``source_type='match'`` filter surfaces lineups alongside
the football-data.org match results.
"""
