from __future__ import annotations

"""
Neo4j connectivity shared across business capabilities.

This module intentionally centralizes:
- dotenv loading
- Neo4j connection configuration
- driver lifecycle
- session creation

So feature modules can focus on their domain behavior and Cypher, without
re-implementing connection plumbing.
"""

import os
import time
from typing import Optional

from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j import Driver

from api.platform.observability.smart_logger import SmartLogger

# Load environment variables once for the whole process.
load_dotenv()

# Neo4j Configuration
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "12345msaez")
NEO4J_DATABASE = (os.getenv("NEO4J_DATABASE") or os.getenv("neo4j_database") or "").strip() or None

_driver: Optional[Driver] = None


def init_neo4j_driver(*, log: bool = True) -> Driver:
    """
    Initialize a singleton Neo4j driver if needed.
    Safe to call multiple times.
    """
    global _driver
    if _driver is not None:
        return _driver

    t0 = time.perf_counter()
    _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    if log:
        SmartLogger.log(
            "INFO",
            "Neo4j driver created.",
            category="platform.neo4j.driver.init",
            params={
                "neo4j_uri": NEO4J_URI,
                "neo4j_user": NEO4J_USER,
                "neo4j_database": NEO4J_DATABASE,
                "duration_ms": int((time.perf_counter() - t0) * 1000),
            },
        )
    return _driver


def close_neo4j_driver(*, log: bool = True) -> None:
    """Close and reset the singleton Neo4j driver."""
    global _driver
    if _driver is None:
        return
    try:
        _driver.close()
    finally:
        _driver = None
        if log:
            SmartLogger.log(
                "INFO",
                "Neo4j driver closed.",
                category="platform.neo4j.driver.close",
                params={"neo4j_uri": NEO4J_URI},
            )


def get_driver() -> Driver:
    """Get the singleton Neo4j driver, initializing lazily if needed."""
    return init_neo4j_driver(log=False)


def get_session():
    """Get a Neo4j session (optionally bound to configured database)."""
    if NEO4J_DATABASE:
        return get_driver().session(database=NEO4J_DATABASE)
    return get_driver().session()


