"""Keyword taxonomy shared by the memory providers and the review engine.

Topics connect what the code does (signals in a diff) to what the team has taught
(rules in memory). They are used for ranking/citing, never to invent knowledge.
"""
from __future__ import annotations

import re

CATEGORIES = ["Architecture", "Database", "Security", "Logging", "Testing", "API", "Performance", "Other"]

TOPIC_PATTERNS: dict[str, re.Pattern[str]] = {
    "database_access": re.compile(r"repository|db\.py|database access|data access|direct sql|direct database|direct db|read-only sql|\borm\b", re.I),
    "sql_safety": re.compile(r"\bsql\b|parameteriz|injection|statement timeout", re.I),
    "api_thin": re.compile(r"\bthin\b|route handler|api route|controller", re.I),
    "api_contract": re.compile(r"response model|versioned|/api/v\d|pydantic response", re.I),
    "logging": re.compile(r"logging|logger|structlog|print\(|console\.log", re.I),
    "sensitive_logging": re.compile(r"never log|do not log|don't log|must not log|not be logged", re.I),
    "auth": re.compile(r"get_current_user|\bjwt\b|authenticat|protected endpoint|permission|\brbac\b", re.I),
    "typing": re.compile(r"type annotation|type hint|\btyping\b|mypy", re.I),
    "error_handling": re.compile(r"try/except|try/catch|error handling|apperror|exception", re.I),
    "testing": re.compile(r"\bpytest\b|\btests?\b|coverage", re.I),
    "validation": re.compile(r"validat|pydantic", re.I),
    "secrets": re.compile(r"hardcod|secret|api key|credential", re.I),
    "performance": re.compile(r"performance|n\+1|latency|\bcache\b|\bindex(es)?\b", re.I),
}

TOPIC_PHRASES = {
    "database_access": "database access and repository layer",
    "sql_safety": "raw SQL safety and parameterized queries",
    "api_thin": "API route handlers and controller design",
    "api_contract": "API versioning and response models",
    "logging": "logging practices",
    "sensitive_logging": "not logging secrets or tokens",
    "auth": "authentication and authorization",
    "typing": "python type annotations",
    "error_handling": "error handling for async calls",
    "testing": "testing requirements",
    "validation": "input validation",
    "secrets": "secret management",
    "performance": "performance",
}

MODE_TOPICS = {
    "security": {"sql_safety", "database_access", "sensitive_logging", "auth", "secrets", "validation"},
    "architecture": {"database_access", "api_thin", "api_contract", "error_handling", "logging"},
    "performance": {"performance", "sql_safety", "database_access"},
}

CATEGORY_HINTS = [
    ("Database", re.compile(r"database|repository|\bsql\b|db\.py|query", re.I)),
    ("Logging", re.compile(r"logging|logger|print\(|console\.log", re.I)),
    ("Security", re.compile(r"secur|secret|token|password|\bauth|\bjwt\b|injection|credential", re.I)),
    ("Testing", re.compile(r"pytest|\btests?\b", re.I)),
    ("API", re.compile(r"endpoint|/api|route|response model", re.I)),
    ("Performance", re.compile(r"performance|latency|n\+1|\bcache\b|\bindex", re.I)),
    ("Architecture", re.compile(r"architect|\blayer\b|\bthin\b|service|handler|exception", re.I)),
]

TECH_VOCAB = {
    "FastAPI": r"fastapi",
    "PostgreSQL": r"postgres",
    "Pydantic": r"pydantic",
    "JWT": r"\bjwt\b",
    "pytest": r"pytest",
    "structlog": r"structlog",
    "mypy": r"mypy",
    "Repository pattern": r"repository",
    "SQLAlchemy": r"sqlalchemy",
    "Redis": r"redis",
}


def memory_topics(text: str) -> list[str]:
    return [t for t, pat in TOPIC_PATTERNS.items() if pat.search(text)]


def classify_category(text: str) -> str:
    for cat, pat in CATEGORY_HINTS:
        if pat.search(text):
            return cat
    return "Other"


def detect_tech(text: str) -> list[str]:
    return [name for name, pat in TECH_VOCAB.items() if re.search(pat, text, re.I)]
