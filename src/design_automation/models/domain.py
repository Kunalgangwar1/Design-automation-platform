"""Small domain vocabulary used to describe the fictional OMS."""

from dataclasses import dataclass
from enum import StrEnum


class ComponentKind(StrEnum):
    """Kinds of components represented in the sample architecture."""

    SERVICE = "service"
    DATABASE = "database"
    EXTERNAL_SYSTEM = "external_system"


@dataclass(frozen=True)
class Component:
    """A system component and the business capability it owns."""

    name: str
    kind: ComponentKind
    responsibility: str


@dataclass(frozen=True)
class Connection:
    """A directed interaction between two system components."""

    source: str
    target: str
    interaction: str

