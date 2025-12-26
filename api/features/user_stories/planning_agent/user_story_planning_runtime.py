from __future__ import annotations

import os
import uuid

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()


def get_llm():
    provider = os.getenv("LLM_PROVIDER", "openai")
    model = os.getenv("LLM_MODEL", "gpt-4o")

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, temperature=0)

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=model, temperature=0)


def get_neo4j_driver():
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "12345msaez")
    return GraphDatabase.driver(uri, auth=(user, password))


def get_neo4j_session(driver):
    db = (os.getenv("NEO4J_DATABASE") or os.getenv("neo4j_database") or "").strip() or None
    if db:
        return driver.session(database=db)
    return driver.session()


def generate_id(prefix: str) -> str:
    return f"{prefix}-{str(uuid.uuid4())[:8].upper()}"


