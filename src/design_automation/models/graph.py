"""Application models for structured graph evidence."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GraphEntity:
    """A named enterprise object represented in the graph."""

    name: str
    entity_type: str
    description: str = ""
    owner: str | None = None


@dataclass(frozen=True)
class GraphRelationship:
    """A directed relationship between two enterprise objects."""

    source_name: str
    source_type: str
    relationship_type: str
    target_name: str
    target_type: str

    @property
    def identity(self) -> tuple[str, str, str]:
        """Return the fields that uniquely identify the relationship."""

        return self.source_name, self.relationship_type, self.target_name


@dataclass(frozen=True)
class GraphContext:
    """Structured relationship evidence collected for known seed entities."""

    seed_entities: list[GraphEntity] = field(default_factory=list)
    related_entities: list[GraphEntity] = field(default_factory=list)
    relationships: list[GraphRelationship] = field(default_factory=list)
    traversal_depth: int = 1
