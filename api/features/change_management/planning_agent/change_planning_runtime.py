"""
Change Planning Runtime (LLM / embeddings / Neo4j access)

Business capability: provide the integrations needed by change planning nodes.
Kept local to the change planning feature implementation.
"""

from __future__ import annotations

import os


def get_llm():
    """Get the configured LLM instance."""
    provider = os.getenv("LLM_PROVIDER", "openai")
    model = os.getenv("LLM_MODEL", "gpt-4o")

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, temperature=0)
    else:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, temperature=0)


def get_embeddings():
    """Get the embeddings model."""
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(model="text-embedding-3-small")


def get_neo4j_driver():
    """Get Neo4j driver."""
    from neo4j import GraphDatabase

    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "12345msaez")
    return GraphDatabase.driver(uri, auth=(user, password))


def get_neo4j_database() -> str | None:
    """Get target Neo4j database name (multi-database support)."""
    db = (os.getenv("NEO4J_DATABASE") or os.getenv("neo4j_database") or "").strip()
    return db or None


def neo4j_session(driver):
    """Create a session for the configured database (or default)."""
    db = get_neo4j_database()
    return driver.session(database=db) if db else driver.session()


