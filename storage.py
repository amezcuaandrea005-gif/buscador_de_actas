import hashlib
import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PDF_DIR = DATA_DIR / "pdfs"
DB_PATH = DATA_DIR / "actas.db"
SOURCES_PATH = DATA_DIR / "sources.json"


def init_db(db_path: Path = DB_PATH) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_url TEXT UNIQUE NOT NULL,
                page_url TEXT,
                link_text TEXT,
                local_path TEXT,
                sha256 TEXT,
                status TEXT NOT NULL DEFAULT 'downloaded',
                error_message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                raw_text TEXT NOT NULL,
                normalized_text TEXT NOT NULL,
                FOREIGN KEY (document_id) REFERENCES documents (id)
            )
        """)
        conn.commit()


def load_seed_urls(sources_path: Path = SOURCES_PATH) -> list[str]:
    if not sources_path.exists():
        return []
    with sources_path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    urls = data.get("seed_urls", [])
    return [u.strip() for u in urls if isinstance(u, str) and u.strip()]


def sync_sources_from_config(db_path: Path = DB_PATH, sources_path: Path = SOURCES_PATH) -> list[str]:
    urls = load_seed_urls(sources_path)
    with sqlite3.connect(db_path) as conn:
        for url in urls:
            conn.execute(
                "INSERT OR IGNORE INTO sources (url, active) VALUES (?, 1)",
                (url,),
            )
        rows = conn.execute("SELECT url FROM sources WHERE active = 1").fetchall()
        conn.commit()
    return [r[0] for r in rows]


def get_document_by_source_url(source_url: str, db_path: Path = DB_PATH):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM documents WHERE source_url = ?",
            (source_url,),
        ).fetchone()


def get_document_by_sha256(sha256: str, db_path: Path = DB_PATH):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM documents WHERE sha256 = ?",
            (sha256,),
        ).fetchone()


def compute_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def record_document(
    source_url: str,
    page_url: str,
    link_text: str,
    local_path: str | None,
    sha256: str | None,
    status: str = "downloaded",
    error_message: str | None = None,
    db_path: Path = DB_PATH,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO documents (
                source_url, page_url, link_text, local_path, sha256, status, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_url) DO UPDATE SET
                page_url = COALESCE(excluded.page_url, documents.page_url),
                link_text = COALESCE(excluded.link_text, documents.link_text),
                local_path = COALESCE(excluded.local_path, documents.local_path),
                sha256 = COALESCE(excluded.sha256, documents.sha256),
                status = excluded.status,
                error_message = excluded.error_message
            """,
            (source_url, page_url, link_text, local_path, sha256, status, error_message),
        )
        conn.commit()