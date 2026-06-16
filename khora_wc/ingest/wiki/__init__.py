"""Wikipedia ingestion stream — the third source alongside match data and news.

A bounded BFS crawl over the MediaWiki API, seeded at the "2026 FIFA World Cup"
article and following its article-namespace links (teams, groups, stadiums, host
cities, squads, key players, ...), turning each fetched page's plain-text extract
into a ``RememberDoc``.

The API is used instead of HTML scraping: it is keyless, polite, returns
structured JSON, and gives us redirect resolution and plain-text extracts for
free (no bs4/lxml/trafilatura needed for this stream).
"""
