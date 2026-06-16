"""Match ingestion pipeline for FIFA World Cup 2026.

Fetches match results, group standings, and tournament scorers from
football-data.org (competition ``WC``), transforms them into ``RememberDoc``
payloads, and writes only changed/new docs into the inbox for the remember
worker to ingest.
"""
