"""News ingestion pipeline for FIFA World Cup 2026.

Fetches World-Cup-relevant articles from RSS feeds, Google News search, and
NewsData.io, transforms them into ``RememberDoc`` payloads, and writes only
previously-unseen articles into the inbox for the remember worker to ingest.

Dedup is enforced *before* writing (every re-remember of an ``external_id``
costs a full downstream LLM re-extract): seen article ids are tracked in
``data/state/news_seen.json``, keyed by the sha1 of each article's canonical
URL. The same article is therefore never written twice.
"""
