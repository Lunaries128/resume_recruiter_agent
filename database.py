import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from config import DATABASE_PATH


@contextmanager
def get_connection():
    connection = sqlite3.connect(
        DATABASE_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    try:
        yield connection
        connection.commit()

    finally:
        connection.close()


def init_database():
    with get_connection() as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS candidates (
            candidate_code TEXT PRIMARY KEY,
            source_filename TEXT NOT NULL,
            education_json TEXT NOT NULL,
            skills_json TEXT NOT NULL,
            work_years REAL NOT NULL,
            job_titles_json TEXT NOT NULL,
            companies_json TEXT NOT NULL,
            projects_json TEXT NOT NULL,
            certificates_json TEXT NOT NULL,
            summary TEXT NOT NULL,
            missing_fields_json TEXT NOT NULL,
            redacted_text TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_code TEXT NOT NULL,
            jd_hash TEXT NOT NULL,
            total_score REAL NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS deletion_requests (
            token TEXT PRIMARY KEY,
            candidate_code TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        """)


def upsert_candidate(
    profile: dict,
    filename: str,
    redacted_text: str,
):
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO candidates (
                candidate_code,
                source_filename,
                education_json,
                skills_json,
                work_years,
                job_titles_json,
                companies_json,
                projects_json,
                certificates_json,
                summary,
                missing_fields_json,
                redacted_text,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_code)
            DO UPDATE SET
                source_filename = excluded.source_filename,
                education_json = excluded.education_json,
                skills_json = excluded.skills_json,
                work_years = excluded.work_years,
                job_titles_json = excluded.job_titles_json,
                companies_json = excluded.companies_json,
                projects_json = excluded.projects_json,
                certificates_json = excluded.certificates_json,
                summary = excluded.summary,
                missing_fields_json = excluded.missing_fields_json,
                redacted_text = excluded.redacted_text
            """,
            (
                profile["candidate_code"],
                filename,
                json.dumps(
                    profile["education"],
                    ensure_ascii=False,
                ),
                json.dumps(
                    profile["skills"],
                    ensure_ascii=False,
                ),
                profile["work_years"],
                json.dumps(
                    profile["job_titles"],
                    ensure_ascii=False,
                ),
                json.dumps(
                    profile["companies"],
                    ensure_ascii=False,
                ),
                json.dumps(
                    profile["projects"],
                    ensure_ascii=False,
                ),
                json.dumps(
                    profile["certificates"],
                    ensure_ascii=False,
                ),
                profile["summary"],
                json.dumps(
                    profile["missing_fields"],
                    ensure_ascii=False,
                ),
                redacted_text,
                datetime.now(
                    timezone.utc
                ).isoformat(),
            ),
        )


def get_candidate(
    candidate_code: str,
):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM candidates
            WHERE candidate_code = ?
            """,
            (candidate_code,),
        ).fetchone()

    return dict(row) if row else None


def list_candidates():
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                candidate_code,
                source_filename,
                skills_json,
                work_years,
                summary,
                missing_fields_json,
                created_at
            FROM candidates
            ORDER BY created_at DESC
            """
        ).fetchall()

    results = []

    for row in rows:
        item = dict(row)
        item["skills"] = json.loads(
            item.pop("skills_json")
        )

        item["missing_fields"] = (
            json.loads(
                item.pop(
                    "missing_fields_json"
                )
            )
        )

        results.append(item)

    return results


def save_score(
    candidate_code: str,
    jd_hash: str,
    result: dict,
):
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO scores (
                candidate_code,
                jd_hash,
                total_score,
                result_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                candidate_code,
                jd_hash,
                result["total_score"],
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
                datetime.now(
                    timezone.utc
                ).isoformat(),
            ),
        )


def delete_candidate(
    candidate_code: str,
):
    with get_connection() as connection:
        connection.execute(
            """
            DELETE FROM scores
            WHERE candidate_code = ?
            """,
            (candidate_code,),
        )

        cursor = connection.execute(
            """
            DELETE FROM candidates
            WHERE candidate_code = ?
            """,
            (candidate_code,),
        )

    return cursor.rowcount > 0