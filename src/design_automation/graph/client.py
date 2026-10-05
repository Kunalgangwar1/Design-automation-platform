"""Small lifecycle wrapper around the official Neo4j driver."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from neo4j import GraphDatabase, unit_of_work

from design_automation.core import (
    NEO4J_CONNECTION_TIMEOUT_SECONDS,
    NEO4J_PASSWORD,
    NEO4J_QUERY_TIMEOUT_SECONDS,
    NEO4J_URI,
    NEO4J_USER,
)


class GraphConfigurationError(RuntimeError):
    """Raised when Neo4j connection settings are incomplete."""


class Neo4jClient:
    """Own the driver and expose clean dictionary-based transactions."""

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        driver: Any | None = None,
        connection_timeout_seconds: float = NEO4J_CONNECTION_TIMEOUT_SECONDS,
        query_timeout_seconds: float = NEO4J_QUERY_TIMEOUT_SECONDS,
    ) -> None:
        if not uri or not user or not password:
            raise GraphConfigurationError(
                "NEO4J_URI, NEO4J_USER, and NEO4J_PASSWORD must all be configured."
            )
        if connection_timeout_seconds <= 0 or query_timeout_seconds <= 0:
            raise GraphConfigurationError("Neo4j timeouts must be positive")
        self._query_timeout_seconds = query_timeout_seconds
        self._driver = driver or GraphDatabase.driver(
            uri,
            auth=(user, password),
            connection_timeout=connection_timeout_seconds,
        )

    @classmethod
    def from_environment(cls) -> Neo4jClient:
        """Create a client from environment-backed project configuration."""

        return cls(
            uri=NEO4J_URI or "",
            user=NEO4J_USER or "",
            password=NEO4J_PASSWORD or "",
        )

    def verify_connectivity(self) -> None:
        self._driver.verify_connectivity()

    def execute_read(
        self,
        query: str,
        parameters: Mapping[str, object] | None = None,
    ) -> list[dict[str, Any]]:
        return self._execute("read", query, parameters)

    def execute_write(
        self,
        query: str,
        parameters: Mapping[str, object] | None = None,
    ) -> list[dict[str, Any]]:
        return self._execute("write", query, parameters)

    def _execute(
        self,
        mode: str,
        query: str,
        parameters: Mapping[str, object] | None,
    ) -> list[dict[str, Any]]:
        values = dict(parameters or {})

        def run_query(transaction: Any) -> list[dict[str, Any]]:
            return [record.data() for record in transaction.run(query, values)]

        timed_query = unit_of_work(timeout=self._query_timeout_seconds)(run_query)

        transaction_method: Callable[..., list[dict[str, Any]]]
        with self._driver.session() as session:
            transaction_method = (
                session.execute_read if mode == "read" else session.execute_write
            )
            return transaction_method(timed_query)

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> Neo4jClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
