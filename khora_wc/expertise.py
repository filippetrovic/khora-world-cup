"""Load the World Cup 2026 expertise config and expose its type lists.

The expertise is defined in ``config/worldcup_expertise.yaml`` and loaded into a
khora :class:`ExpertiseConfig`. Module-level singletons (``WC_EXPERTISE``,
``ENTITY_TYPES``, ``RELATIONSHIP_TYPES``) are imported by the remember worker so
every ingest call uses the same domain knowledge.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from khora import ExpertiseConfig

# Default location of the expertise YAML, relative to the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXPERTISE_PATH = REPO_ROOT / "config" / "worldcup_expertise.yaml"


def load_expertise(path: Path | None = None) -> ExpertiseConfig:
    """Load an ExpertiseConfig from a YAML file.

    Args:
        path: YAML file to load. Defaults to ``config/worldcup_expertise.yaml``.
    """
    yaml_path = Path(path) if path is not None else DEFAULT_EXPERTISE_PATH
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return ExpertiseConfig.from_dict(data)


WC_EXPERTISE: ExpertiseConfig = load_expertise()
ENTITY_TYPES: list[str] = WC_EXPERTISE.get_entity_type_names()
RELATIONSHIP_TYPES: list[str] = WC_EXPERTISE.get_relationship_type_names()
