"""
Database connection and schema initialization for IW Solo PIXI dashboard.
"""
import os
import psycopg2

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://pixi:pixipass@localhost:5434/pixi_test"
)


def get_connection(timeout: int = 5, database_url: str | None = None):
    """Get a new database connection."""
    return psycopg2.connect(database_url or DATABASE_URL, connect_timeout=timeout)


def init_schema(database_url: str | None = None):
    """
    Initialize the database schema from schema.sql.
    Idempotent: safe to call on every startup.
    Searches multiple paths for schema.sql (Docker vs local dev).
    """
    search_paths = [
        "/app/schema.sql",                              # Docker container
        os.path.join(os.path.dirname(__file__), "..", "schema.sql"),  # Local dev
        os.path.join(os.path.dirname(__file__), "schema.sql"),
    ]

    schema_path = None
    for path in search_paths:
        resolved = os.path.abspath(path)
        if os.path.exists(resolved):
            schema_path = resolved
            break

    if schema_path is None:
        print("WARNING: schema.sql not found, skipping schema initialization.")
        return

    with open(schema_path, "r", encoding="utf-8") as f:
        sql = f.read()

    conn = get_connection(database_url=database_url)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
        cur.close()
        print(f"Schema initialized from {schema_path}")
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def check_health() -> dict:
    """Check database connectivity and return status dict."""
    try:
        conn = get_connection(timeout=3)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM test_results")
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return {"status": "ok", "db": "connected", "test_results_count": count}
    except Exception as e:
        return {"status": "error", "db": str(e)}
