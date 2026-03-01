"""PostClassifier and raw query helpers - audience database."""
from typing import Optional, Dict, Any

from psycopg2.extras import RealDictCursor

from app.database.pool import get_audience_connection
from app.database.models import PostClassifier


def find_post_classifier_by_id(classifier_id: str) -> Optional[PostClassifier]:
    with get_audience_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                'SELECT id, "userId", name, description, prompt, labels, examples, "createdAt", "updatedAt" FROM "PostClassifier" WHERE id = %s',
                (classifier_id,)
            )
            row = cur.fetchone()
            return PostClassifier(row) if row else None


def query_first(sql: str, *args) -> Optional[Dict[str, Any]]:
    with get_audience_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, args)
            return cur.fetchone()
