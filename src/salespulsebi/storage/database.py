"""SQLite metadata store for projects, sources, mappings, views, and sync history."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path

from salespulsebi.storage.models import (
    CleaningRules,
    ColumnProfile,
    GlobalSettings,
    GlobalSettingsUpdate,
    MappingState,
    ProjectCreate,
    ProjectSettings,
    ProjectSummary,
    ProjectUpdate,
    SavedViewCreate,
    SavedViewRecord,
    SourceRecord,
    SyncHistoryRecord,
)


def _apply_fx_to_amount(amount: float | int | None, fx_rate: Decimal) -> float | None:
    """Scale a persisted base monetary summary amount using exact Decimal arithmetic."""
    if amount is None:
        return None
    with localcontext() as ctx:
        ctx.prec = 28
        dec_amt = Decimal(str(amount))
        scaled = dec_amt if fx_rate == Decimal("1") else (dec_amt * fx_rate)
        return float(scaled.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def utcnow_iso() -> str:
    """Return current UTC ISO-8601 timestamp string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    is_favorite INTEGER NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    locale TEXT NOT NULL DEFAULT 'es',
    settings_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_synced_at TEXT,
    row_count INTEGER NOT NULL DEFAULT 0,
    file_count INTEGER NOT NULL DEFAULT 0,
    total_revenue REAL,
    health_score REAL
);

CREATE TABLE IF NOT EXISTS project_sources (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    path TEXT NOT NULL,
    recursive INTEGER NOT NULL DEFAULT 1,
    file_glob TEXT NOT NULL DEFAULT '*',
    file_hash TEXT,
    file_size INTEGER NOT NULL DEFAULT 0,
    row_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ready',
    error_message TEXT,
    last_seen_at TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE (project_id, source_type, path)
);

CREATE TABLE IF NOT EXISTS project_mappings (
    project_id TEXT PRIMARY KEY,
    role_map_json TEXT NOT NULL DEFAULT '{}',
    detected_schema_json TEXT NOT NULL DEFAULT '[]',
    cleaning_rules_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS saved_views (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    filters_json TEXT NOT NULL DEFAULT '{}',
    active_tab TEXT NOT NULL DEFAULT 'overview',
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sync_history (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    triggered_at TEXT NOT NULL,
    files_added INTEGER NOT NULL DEFAULT 0,
    files_updated INTEGER NOT NULL DEFAULT 0,
    files_removed INTEGER NOT NULL DEFAULT 0,
    total_rows INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    settings_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_ml_models (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    target_metric TEXT NOT NULL,
    granularity TEXT NOT NULL,
    winner_model TEXT NOT NULL,
    candidates_json TEXT NOT NULL DEFAULT '[]',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    feature_importance_json TEXT NOT NULL DEFAULT '[]',
    data_hash TEXT NOT NULL DEFAULT '',
    artifact_path TEXT,
    trained_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE (project_id, target_metric, granularity)
);

CREATE TABLE IF NOT EXISTS project_documents (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    doc_type TEXT NOT NULL DEFAULT 'other',
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL DEFAULT '',
    page_count INTEGER NOT NULL DEFAULT 0,
    as_of_date TEXT,
    period_start TEXT,
    period_end TEXT,
    currency TEXT,
    status TEXT NOT NULL DEFAULT 'uploaded',
    extracted_json TEXT,
    validation_errors_json TEXT NOT NULL DEFAULT '[]',
    ingested_to_dataset INTEGER NOT NULL DEFAULT 0,
    indexed_in_rag INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai_telemetry_spans (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    project_id TEXT,
    feature_name TEXT NOT NULL,
    prompt_version TEXT NOT NULL DEFAULT 'v1',
    provider TEXT NOT NULL,
    model_id TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd TEXT NOT NULL DEFAULT '0.000000',
    latency_ms INTEGER NOT NULL DEFAULT 0,
    ttft_ms INTEGER NOT NULL DEFAULT 0,
    tool_calls_count INTEGER NOT NULL DEFAULT 0,
    validation_retries INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_response_cache (
    cache_key TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model_id TEXT NOT NULL,
    data_snapshot_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS market_news_cache (
    id TEXT PRIMARY KEY,
    query_key TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    link TEXT NOT NULL,
    published_at TEXT,
    summary TEXT NOT NULL DEFAULT '',
    sentiment_label TEXT NOT NULL DEFAULT 'neutral',
    sentiment_score REAL NOT NULL DEFAULT 0.0,
    impact_tags_json TEXT NOT NULL DEFAULT '[]',
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_alerts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    metric_value TEXT,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);
"""


class MetadataDB:
    """Thread-safe SQLite metadata persistence manager."""

    def __init__(self, db_path: Path, default_currency: str = "USD") -> None:
        self.db_path = db_path
        self.default_currency = default_currency.strip().upper() if default_currency else "USD"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            row = conn.execute("SELECT id FROM app_settings WHERE id = 1").fetchone()
            if not row:
                now = utcnow_iso()
                initial = GlobalSettings(default_currency=self.default_currency, updated_at=now)
                conn.execute(
                    "INSERT INTO app_settings (id, settings_json, updated_at) VALUES (1, ?, ?)",
                    (initial.model_dump_json(exclude={"updated_at"}), now),
                )

    def get_global_settings(self) -> GlobalSettings:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT settings_json, updated_at FROM app_settings WHERE id = 1"
            ).fetchone()
        if not row:
            return GlobalSettings(default_currency=self.default_currency, updated_at=utcnow_iso())
        try:
            raw = json.loads(row["settings_json"] or "{}")
            return GlobalSettings.model_validate({**raw, "updated_at": row["updated_at"]})
        except Exception:
            return GlobalSettings(default_currency=self.default_currency, updated_at=row["updated_at"])

    def update_global_settings(self, payload: GlobalSettingsUpdate) -> GlobalSettings:
        current = self.get_global_settings()
        merged = current.model_dump(exclude={"updated_at"})
        patch_data = payload.model_dump(exclude_unset=True, exclude={"apply_currency_to_existing"})
        for k, v in patch_data.items():
            if v is not None or k in ("default_base_currency", "default_date_dayfirst", "ai_base_url"):
                merged[k] = v
        now = utcnow_iso()
        validated = GlobalSettings.model_validate({**merged, "updated_at": now})
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (id, settings_json, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    settings_json = excluded.settings_json,
                    updated_at = excluded.updated_at
                """,
                (validated.model_dump_json(exclude={"updated_at"}), now),
            )
            if payload.apply_currency_to_existing:
                rows = conn.execute("SELECT id, settings_json FROM projects").fetchall()
                for r in rows:
                    try:
                        p_settings = ProjectSettings.model_validate(
                            json.loads(r["settings_json"] or "{}")
                        )
                    except Exception:
                        p_settings = ProjectSettings()
                    p_settings.base_currency = validated.default_base_currency
                    p_settings.exchange_rate = validated.default_exchange_rate
                    conn.execute(
                        "UPDATE projects SET currency = ?, settings_json = ?, updated_at = ? WHERE id = ?",
                        (validated.default_currency, p_settings.model_dump_json(), now, r["id"]),
                    )
        return validated

    def reset_global_settings(self) -> GlobalSettings:
        now = utcnow_iso()
        fresh = GlobalSettings(default_currency=self.default_currency, updated_at=now)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (id, settings_json, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    settings_json = excluded.settings_json,
                    updated_at = excluded.updated_at
                """,
                (fresh.model_dump_json(exclude={"updated_at"}), now),
            )
        return fresh

    @staticmethod
    def _row_to_project_summary(row: sqlite3.Row) -> ProjectSummary:
        raw_settings_str = row["settings_json"] if "settings_json" in row.keys() else "{}"
        try:
            settings = ProjectSettings.model_validate(json.loads(raw_settings_str or "{}"))
        except Exception:
            settings = ProjectSettings()
        return ProjectSummary(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            tags=json.loads(row["tags_json"] or "[]"),
            is_favorite=bool(row["is_favorite"]),
            currency=row["currency"],
            locale=row["locale"],
            settings=settings,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_synced_at=row["last_synced_at"],
            row_count=row["row_count"],
            file_count=row["file_count"],
            total_revenue=_apply_fx_to_amount(row["total_revenue"], settings.exchange_rate),
            health_score=row["health_score"],
        )

    def create_project(self, payload: ProjectCreate, project_id: str | None = None) -> ProjectSummary:
        pid = project_id or str(uuid.uuid4())
        now = utcnow_iso()
        g_settings = self.get_global_settings()
        resolved_currency = (payload.currency or g_settings.default_currency).upper()
        resolved_locale = payload.locale or g_settings.locale
        if payload.settings is not None:
            resolved_settings = payload.settings
        else:
            resolved_settings = ProjectSettings(
                base_currency=g_settings.default_base_currency,
                exchange_rate=g_settings.default_exchange_rate,
            )
        default_cleaning = CleaningRules(
            deduplicate=g_settings.default_deduplicate,
            dedup_mode=g_settings.default_dedup_mode,
            exclude_negative_revenue=g_settings.default_exclude_negative_revenue,
            date_dayfirst=g_settings.default_date_dayfirst,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO projects (
                    id, name, description, tags_json, is_favorite,
                    currency, locale, settings_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pid,
                    payload.name.strip(),
                    payload.description.strip(),
                    json.dumps(payload.tags),
                    int(payload.is_favorite),
                    resolved_currency,
                    resolved_locale,
                    resolved_settings.model_dump_json(),
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO project_mappings (
                    project_id, role_map_json, detected_schema_json, cleaning_rules_json, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    pid,
                    "{}",
                    "[]",
                    default_cleaning.model_dump_json(),
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
        assert row is not None
        return self._row_to_project_summary(row)

    def list_projects(self) -> list[ProjectSummary]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM projects ORDER BY is_favorite DESC, updated_at DESC"
            ).fetchall()
        return [self._row_to_project_summary(r) for r in rows]

    def get_project(self, project_id: str) -> ProjectSummary | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return self._row_to_project_summary(row) if row else None

    def find_project_by_name(self, name: str) -> ProjectSummary | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()
        return self._row_to_project_summary(row) if row else None

    def update_project(self, project_id: str, payload: ProjectUpdate) -> ProjectSummary | None:
        existing = self.get_project(project_id)
        if not existing:
            return None
        updates: dict[str, object] = {"updated_at": utcnow_iso()}
        if payload.name is not None:
            updates["name"] = payload.name.strip()
        if payload.description is not None:
            updates["description"] = payload.description.strip()
        if payload.tags is not None:
            updates["tags_json"] = json.dumps(payload.tags)
        if payload.currency is not None:
            updates["currency"] = payload.currency.upper()
        if payload.locale is not None:
            updates["locale"] = payload.locale
        if payload.is_favorite is not None:
            updates["is_favorite"] = int(payload.is_favorite)
        if payload.settings is not None:
            updates["settings_json"] = payload.settings.model_dump_json()

        cols = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [project_id]
        with self.connect() as conn:
            conn.execute(f"UPDATE projects SET {cols} WHERE id = ?", params)
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return self._row_to_project_summary(row) if row else None

    def update_project_stats(
        self,
        project_id: str,
        *,
        row_count: int,
        file_count: int,
        total_revenue: float | None,
        health_score: float | None,
        synced: bool = True,
    ) -> None:
        now = utcnow_iso()
        with self.connect() as conn:
            if synced:
                conn.execute(
                    """
                    UPDATE projects
                    SET row_count = ?, file_count = ?, total_revenue = ?,
                        health_score = ?, updated_at = ?, last_synced_at = ?
                    WHERE id = ?
                    """,
                    (row_count, file_count, total_revenue, health_score, now, now, project_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE projects
                    SET row_count = ?, file_count = ?, total_revenue = ?,
                        health_score = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (row_count, file_count, total_revenue, health_score, now, project_id),
                )

    def delete_project(self, project_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            return cur.rowcount > 0

    def duplicate_project(self, source_id: str, new_name: str | None = None) -> ProjectSummary | None:
        src = self.get_project(source_id)
        if not src:
            return None
        with self.connect() as conn:
            raw_row = conn.execute(
                "SELECT total_revenue FROM projects WHERE id = ?", (source_id,)
            ).fetchone()
        raw_total_revenue = raw_row["total_revenue"] if raw_row else None
        new_proj = self.create_project(
            ProjectCreate(
                name=new_name or f"{src.name} (Copy)",
                description=src.description,
                tags=list(src.tags),
                currency=src.currency,
                locale=src.locale,
                is_favorite=False,
                settings=src.settings.model_copy(deep=True),
            )
        )
        mapping = self.get_mapping(source_id)
        self.save_mapping(
            new_proj.id,
            role_map=mapping.role_map,
            detected_schema=mapping.detected_schema,
            cleaning_rules=mapping.cleaning_rules,
        )
        for source in self.list_sources(source_id):
            self.upsert_source(
                project_id=new_proj.id,
                source_type=source.source_type,
                path=source.path,
                recursive=source.recursive,
                file_glob=source.file_glob,
                file_hash=source.file_hash,
                file_size=source.file_size,
                row_count=source.row_count,
                status=source.status,
                error_message=source.error_message,
            )
        for view in self.list_saved_views(source_id):
            self.create_saved_view(
                new_proj.id,
                SavedViewCreate(name=view.name, filters=view.filters, active_tab=view.active_tab),
            )
        self.update_project_stats(
            new_proj.id,
            row_count=src.row_count,
            file_count=src.file_count,
            total_revenue=raw_total_revenue,
            health_score=src.health_score,
            synced=False,
        )
        return self.get_project(new_proj.id)

    def upsert_source(
        self,
        *,
        project_id: str,
        source_type: str,
        path: str,
        recursive: bool = True,
        file_glob: str = "*",
        file_hash: str | None = None,
        file_size: int = 0,
        row_count: int = 0,
        status: str = "ready",
        error_message: str | None = None,
    ) -> SourceRecord:
        now = utcnow_iso()
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT id FROM project_sources
                WHERE project_id = ? AND source_type = ? AND path = ?
                """,
                (project_id, source_type, path),
            ).fetchone()
            sid = existing["id"] if existing else str(uuid.uuid4())
            if existing:
                conn.execute(
                    """
                    UPDATE project_sources
                    SET recursive = ?, file_glob = ?, file_hash = ?, file_size = ?,
                        row_count = ?, status = ?, error_message = ?, last_seen_at = ?
                    WHERE id = ?
                    """,
                    (
                        int(recursive),
                        file_glob,
                        file_hash,
                        file_size,
                        row_count,
                        status,
                        error_message,
                        now,
                        sid,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO project_sources (
                        id, project_id, source_type, path, recursive, file_glob,
                        file_hash, file_size, row_count, status, error_message, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sid,
                        project_id,
                        source_type,
                        path,
                        int(recursive),
                        file_glob,
                        file_hash,
                        file_size,
                        row_count,
                        status,
                        error_message,
                        now,
                    ),
                )
            row = conn.execute("SELECT * FROM project_sources WHERE id = ?", (sid,)).fetchone()
        assert row is not None
        return self._row_to_source(row)

    @staticmethod
    def _row_to_source(row: sqlite3.Row) -> SourceRecord:
        return SourceRecord(
            id=row["id"],
            project_id=row["project_id"],
            source_type=row["source_type"],
            path=row["path"],
            recursive=bool(row["recursive"]),
            file_glob=row["file_glob"],
            file_hash=row["file_hash"],
            file_size=row["file_size"],
            row_count=row["row_count"],
            status=row["status"],
            error_message=row["error_message"],
            last_seen_at=row["last_seen_at"],
        )

    def list_sources(self, project_id: str) -> list[SourceRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM project_sources WHERE project_id = ? ORDER BY source_type, path",
                (project_id,),
            ).fetchall()
        return [self._row_to_source(r) for r in rows]

    def get_source(self, project_id: str, source_id: str) -> SourceRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM project_sources WHERE project_id = ? AND id = ?",
                (project_id, source_id),
            ).fetchone()
        return self._row_to_source(row) if row else None

    def delete_source(self, project_id: str, source_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "DELETE FROM project_sources WHERE project_id = ? AND id = ?",
                (project_id, source_id),
            )
            return cur.rowcount > 0

    def delete_sources_by_type(self, project_id: str, source_type: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM project_sources WHERE project_id = ? AND source_type = ?",
                (project_id, source_type),
            )

    def get_mapping(self, project_id: str) -> MappingState:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM project_mappings WHERE project_id = ?", (project_id,)
            ).fetchone()
        if not row:
            return MappingState()
        role_map = json.loads(row["role_map_json"] or "{}")
        raw_schema = json.loads(row["detected_schema_json"] or "[]")
        detected_schema = [ColumnProfile.model_validate(item) for item in raw_schema]
        raw_rules = json.loads(row["cleaning_rules_json"] or "{}")
        cleaning_rules = CleaningRules.model_validate(raw_rules)
        return MappingState(
            role_map=role_map,
            detected_schema=detected_schema,
            cleaning_rules=cleaning_rules,
            updated_at=row["updated_at"],
        )

    def save_mapping(
        self,
        project_id: str,
        *,
        role_map: dict[str, str],
        detected_schema: list[ColumnProfile] | None = None,
        cleaning_rules: CleaningRules | None = None,
    ) -> MappingState:
        current = self.get_mapping(project_id)
        schema_to_save = detected_schema if detected_schema is not None else current.detected_schema
        rules_to_save = cleaning_rules if cleaning_rules is not None else current.cleaning_rules
        now = utcnow_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO project_mappings (
                    project_id, role_map_json, detected_schema_json, cleaning_rules_json, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    role_map_json = excluded.role_map_json,
                    detected_schema_json = excluded.detected_schema_json,
                    cleaning_rules_json = excluded.cleaning_rules_json,
                    updated_at = excluded.updated_at
                """,
                (
                    project_id,
                    json.dumps(role_map),
                    json.dumps([c.model_dump() for c in schema_to_save]),
                    rules_to_save.model_dump_json(),
                    now,
                ),
            )
        return self.get_mapping(project_id)

    def create_saved_view(self, project_id: str, payload: SavedViewCreate) -> SavedViewRecord:
        vid = str(uuid.uuid4())
        now = utcnow_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO saved_views (id, project_id, name, filters_json, active_tab, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (vid, project_id, payload.name.strip(), json.dumps(payload.filters), payload.active_tab, now),
            )
            row = conn.execute("SELECT * FROM saved_views WHERE id = ?", (vid,)).fetchone()
        assert row is not None
        return SavedViewRecord(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            filters=json.loads(row["filters_json"] or "{}"),
            active_tab=row["active_tab"],
            created_at=row["created_at"],
        )

    def list_saved_views(self, project_id: str) -> list[SavedViewRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM saved_views WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [
            SavedViewRecord(
                id=r["id"],
                project_id=r["project_id"],
                name=r["name"],
                filters=json.loads(r["filters_json"] or "{}"),
                active_tab=r["active_tab"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
    def delete_saved_view(self, project_id: str, view_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "DELETE FROM saved_views WHERE project_id = ? AND id = ?",
                (project_id, view_id),
            )
            return cur.rowcount > 0

    def record_sync(
        self,
        *,
        project_id: str,
        files_added: int,
        files_updated: int,
        files_removed: int,
        total_rows: int,
        duration_ms: int,
        warnings: list[str],
    ) -> SyncHistoryRecord:
        sid = str(uuid.uuid4())
        now = utcnow_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_history (
                    id, project_id, triggered_at, files_added, files_updated,
                    files_removed, total_rows, duration_ms, warnings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sid,
                    project_id,
                    now,
                    files_added,
                    files_updated,
                    files_removed,
                    total_rows,
                    duration_ms,
                    json.dumps(warnings),
                ),
            )
            row = conn.execute("SELECT * FROM sync_history WHERE id = ?", (sid,)).fetchone()
        assert row is not None
        return SyncHistoryRecord(
            id=row["id"],
            project_id=row["project_id"],
            triggered_at=row["triggered_at"],
            files_added=row["files_added"],
            files_updated=row["files_updated"],
            files_removed=row["files_removed"],
            total_rows=row["total_rows"],
            duration_ms=row["duration_ms"],
            warnings=json.loads(row["warnings_json"] or "[]"),
        )

    def list_sync_history(self, project_id: str, limit: int = 20) -> list[SyncHistoryRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sync_history
                WHERE project_id = ?
                ORDER BY triggered_at DESC
                LIMIT ?
                """,
                (project_id, limit),
            ).fetchall()
        return [
            SyncHistoryRecord(
                id=r["id"],
                project_id=r["project_id"],
                triggered_at=r["triggered_at"],
                files_added=r["files_added"],
                files_updated=r["files_updated"],
                files_removed=r["files_removed"],
                total_rows=r["total_rows"],
                duration_ms=r["duration_ms"],
                warnings=json.loads(r["warnings_json"] or "[]"),
            )
            for r in rows
        ]

    # ── ML Model Registry ──────────────────────────────────────────────────

    def upsert_ml_model(
        self,
        *,
        project_id: str,
        target_metric: str,
        granularity: str,
        winner_model: str,
        candidates: list[dict[str, object]],
        metrics: dict[str, object],
        feature_importance: list[dict[str, object]],
        data_hash: str,
        artifact_path: str | None = None,
    ) -> dict[str, object]:
        mid = str(uuid.uuid4())
        now = utcnow_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO project_ml_models (
                    id, project_id, target_metric, granularity, winner_model,
                    candidates_json, metrics_json, feature_importance_json,
                    data_hash, artifact_path, trained_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, target_metric, granularity) DO UPDATE SET
                    winner_model = excluded.winner_model,
                    candidates_json = excluded.candidates_json,
                    metrics_json = excluded.metrics_json,
                    feature_importance_json = excluded.feature_importance_json,
                    data_hash = excluded.data_hash,
                    artifact_path = excluded.artifact_path,
                    trained_at = excluded.trained_at
                """,
                (
                    mid,
                    project_id,
                    target_metric,
                    granularity,
                    winner_model,
                    json.dumps(candidates),
                    json.dumps(metrics),
                    json.dumps(feature_importance),
                    data_hash,
                    artifact_path,
                    now,
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM project_ml_models
                WHERE project_id = ? AND target_metric = ? AND granularity = ?
                """,
                (project_id, target_metric, granularity),
            ).fetchone()
        assert row is not None
        return self._row_to_ml_model(row)

    @staticmethod
    def _row_to_ml_model(row: sqlite3.Row) -> dict[str, object]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "target_metric": row["target_metric"],
            "granularity": row["granularity"],
            "winner_model": row["winner_model"],
            "candidates": json.loads(row["candidates_json"] or "[]"),
            "metrics": json.loads(row["metrics_json"] or "{}"),
            "feature_importance": json.loads(row["feature_importance_json"] or "[]"),
            "data_hash": row["data_hash"],
            "artifact_path": row["artifact_path"],
            "trained_at": row["trained_at"],
        }

    def get_ml_model(
        self, project_id: str, target_metric: str, granularity: str
    ) -> dict[str, object] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM project_ml_models
                WHERE project_id = ? AND target_metric = ? AND granularity = ?
                """,
                (project_id, target_metric, granularity),
            ).fetchone()
        return self._row_to_ml_model(row) if row else None

    def list_ml_models(self, project_id: str) -> list[dict[str, object]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM project_ml_models WHERE project_id = ? ORDER BY trained_at DESC",
                (project_id,),
            ).fetchall()
        return [self._row_to_ml_model(r) for r in rows]

    def invalidate_project_ai_caches(self, project_id: str) -> None:
        """Clear cached AI responses and stale ML model pointers when project data changes."""
        with self.connect() as conn:
            conn.execute("DELETE FROM ai_response_cache WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM project_ml_models WHERE project_id = ?", (project_id,))

    # ── AI Telemetry & Cache ───────────────────────────────────────────────

    def record_ai_span(
        self,
        *,
        trace_id: str,
        project_id: str | None,
        feature_name: str,
        prompt_version: str,
        provider: str,
        model_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        estimated_cost_usd: Decimal = Decimal("0"),
        latency_ms: int = 0,
        ttft_ms: int = 0,
        tool_calls_count: int = 0,
        validation_retries: int = 0,
        status: str = "ok",
    ) -> str:
        sid = str(uuid.uuid4())
        now = utcnow_iso()
        with localcontext() as ctx:
            ctx.prec = 28
            cost_str = format(
                estimated_cost_usd.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN), "f"
            )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO ai_telemetry_spans (
                    id, trace_id, project_id, feature_name, prompt_version, provider, model_id,
                    input_tokens, output_tokens, cached_tokens, estimated_cost_usd,
                    latency_ms, ttft_ms, tool_calls_count, validation_retries, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sid,
                    trace_id,
                    project_id,
                    feature_name,
                    prompt_version,
                    provider,
                    model_id,
                    int(input_tokens),
                    int(output_tokens),
                    int(cached_tokens),
                    cost_str,
                    int(latency_ms),
                    int(ttft_ms),
                    int(tool_calls_count),
                    int(validation_retries),
                    status,
                    now,
                ),
            )
        return sid

    def get_ai_telemetry_summary(
        self, project_id: str | None = None, limit: int = 50
    ) -> dict[str, object]:
        with self.connect() as conn:
            if project_id:
                rows = conn.execute(
                    """
                    SELECT * FROM ai_telemetry_spans
                    WHERE project_id = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (project_id, limit),
                ).fetchall()
                all_rows = conn.execute(
                    "SELECT input_tokens, output_tokens, cached_tokens, estimated_cost_usd, latency_ms, status FROM ai_telemetry_spans WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM ai_telemetry_spans ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                all_rows = conn.execute(
                    "SELECT input_tokens, output_tokens, cached_tokens, estimated_cost_usd, latency_ms, status FROM ai_telemetry_spans"
                ).fetchall()
        total_cost = Decimal("0")
        total_in = 0
        total_out = 0
        total_cached = 0
        latencies: list[int] = []
        ok_count = 0
        for r in all_rows:
            total_in += int(r["input_tokens"] or 0)
            total_out += int(r["output_tokens"] or 0)
            total_cached += int(r["cached_tokens"] or 0)
            try:
                total_cost += Decimal(str(r["estimated_cost_usd"] or "0"))
            except Exception:
                pass
            latencies.append(int(r["latency_ms"] or 0))
            if r["status"] == "ok":
                ok_count += 1
        avg_lat = int(sum(latencies) / len(latencies)) if latencies else 0
        return {
            "total_calls": len(all_rows),
            "success_rate_pct": round((ok_count / len(all_rows)) * 100.0, 2) if all_rows else 100.0,
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "total_cached_tokens": total_cached,
            "total_estimated_cost_usd": format(
                total_cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN), "f"
            ),
            "avg_latency_ms": avg_lat,
            "recent_spans": [dict(r) for r in rows],
        }

    def get_ai_cache(self, cache_key: str, data_snapshot_hash: str) -> dict[str, object] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT response_json FROM ai_response_cache
                WHERE cache_key = ? AND data_snapshot_hash = ?
                """,
                (cache_key, data_snapshot_hash),
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["response_json"])
        except Exception:
            return None

    def set_ai_cache(
        self,
        *,
        cache_key: str,
        project_id: str,
        prompt_version: str,
        model_id: str,
        data_snapshot_hash: str,
        response: dict[str, object],
    ) -> None:
        now = utcnow_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO ai_response_cache (
                    cache_key, project_id, prompt_version, model_id,
                    data_snapshot_hash, response_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    data_snapshot_hash = excluded.data_snapshot_hash,
                    response_json = excluded.response_json,
                    created_at = excluded.created_at
                """,
                (
                    cache_key,
                    project_id,
                    prompt_version,
                    model_id,
                    data_snapshot_hash,
                    json.dumps(response),
                    now,
                ),
            )

    # ── Project Documents (OCR + RAG) ──────────────────────────────────────

    def upsert_document(
        self,
        *,
        doc_id: str | None = None,
        project_id: str,
        filename: str,
        doc_type: str,
        file_path: str,
        file_hash: str,
        page_count: int = 0,
        as_of_date: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        currency: str | None = None,
        status: str = "uploaded",
        extracted: dict[str, object] | None = None,
        validation_errors: list[str] | None = None,
        ingested_to_dataset: bool = False,
        indexed_in_rag: bool = False,
    ) -> dict[str, object]:
        did = doc_id or str(uuid.uuid4())
        now = utcnow_iso()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id, created_at FROM project_documents WHERE id = ?", (did,)
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO project_documents (
                    id, project_id, filename, doc_type, file_path, file_hash, page_count,
                    as_of_date, period_start, period_end, currency, status, extracted_json,
                    validation_errors_json, ingested_to_dataset, indexed_in_rag, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    doc_type = excluded.doc_type,
                    page_count = excluded.page_count,
                    as_of_date = excluded.as_of_date,
                    period_start = excluded.period_start,
                    period_end = excluded.period_end,
                    currency = excluded.currency,
                    status = excluded.status,
                    extracted_json = excluded.extracted_json,
                    validation_errors_json = excluded.validation_errors_json,
                    ingested_to_dataset = excluded.ingested_to_dataset,
                    indexed_in_rag = excluded.indexed_in_rag,
                    updated_at = excluded.updated_at
                """,
                (
                    did,
                    project_id,
                    filename,
                    doc_type,
                    file_path,
                    file_hash,
                    page_count,
                    as_of_date,
                    period_start,
                    period_end,
                    currency,
                    status,
                    json.dumps(extracted) if extracted is not None else None,
                    json.dumps(validation_errors or []),
                    int(ingested_to_dataset),
                    int(indexed_in_rag),
                    created_at,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM project_documents WHERE id = ?", (did,)).fetchone()
        assert row is not None
        return self._row_to_document(row)

    @staticmethod
    def _row_to_document(row: sqlite3.Row) -> dict[str, object]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "filename": row["filename"],
            "doc_type": row["doc_type"],
            "file_path": row["file_path"],
            "file_hash": row["file_hash"],
            "page_count": row["page_count"],
            "as_of_date": row["as_of_date"],
            "period_start": row["period_start"],
            "period_end": row["period_end"],
            "currency": row["currency"],
            "status": row["status"],
            "extracted": json.loads(row["extracted_json"]) if row["extracted_json"] else None,
            "validation_errors": json.loads(row["validation_errors_json"] or "[]"),
            "ingested_to_dataset": bool(row["ingested_to_dataset"]),
            "indexed_in_rag": bool(row["indexed_in_rag"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_documents(self, project_id: str) -> list[dict[str, object]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM project_documents WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        return [self._row_to_document(r) for r in rows]

    def get_document(self, project_id: str, doc_id: str) -> dict[str, object] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM project_documents WHERE project_id = ? AND id = ?",
                (project_id, doc_id),
            ).fetchone()
        return self._row_to_document(row) if row else None

    def delete_document(self, project_id: str, doc_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "DELETE FROM project_documents WHERE project_id = ? AND id = ?",
                (project_id, doc_id),
            )
            return cur.rowcount > 0

    # ── Market News Cache & Project Alerts ─────────────────────────────────

    def replace_market_news(self, query_key: str, items: list[dict[str, object]]) -> None:
        now = utcnow_iso()
        with self.connect() as conn:
            conn.execute("DELETE FROM market_news_cache WHERE query_key = ?", (query_key,))
            for it in items:
                conn.execute(
                    """
                    INSERT INTO market_news_cache (
                        id, query_key, source, title, link, published_at, summary,
                        sentiment_label, sentiment_score, impact_tags_json, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(it.get("id") or uuid.uuid4()),
                        query_key,
                        str(it.get("source") or "RSS"),
                        str(it.get("title") or ""),
                        str(it.get("link") or ""),
                        it.get("published_at"),
                        str(it.get("summary") or ""),
                        str(it.get("sentiment_label") or "neutral"),
                        float(it.get("sentiment_score") or 0.0),
                        json.dumps(it.get("impact_tags") or []),
                        now,
                    ),
                )

    def get_market_news(self, query_key: str) -> list[dict[str, object]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM market_news_cache WHERE query_key = ? ORDER BY published_at DESC",
                (query_key,),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "query_key": r["query_key"],
                "source": r["source"],
                "title": r["title"],
                "link": r["link"],
                "published_at": r["published_at"],
                "summary": r["summary"],
                "sentiment_label": r["sentiment_label"],
                "sentiment_score": r["sentiment_score"],
                "impact_tags": json.loads(r["impact_tags_json"] or "[]"),
                "fetched_at": r["fetched_at"],
            }
            for r in rows
        ]

    def replace_project_alerts(
        self, project_id: str, alerts: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        now = utcnow_iso()
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM project_alerts WHERE project_id = ? AND acknowledged = 0",
                (project_id,),
            )
            for al in alerts:
                conn.execute(
                    """
                    INSERT INTO project_alerts (
                        id, project_id, alert_type, severity, title, body,
                        metric_value, acknowledged, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        str(al.get("id") or uuid.uuid4()),
                        project_id,
                        str(al.get("alert_type") or "info"),
                        str(al.get("severity") or "info"),
                        str(al.get("title") or ""),
                        str(al.get("body") or ""),
                        al.get("metric_value"),
                        now,
                    ),
                )
        return self.list_project_alerts(project_id)

    def list_project_alerts(self, project_id: str, limit: int = 30) -> list[dict[str, object]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM project_alerts
                WHERE project_id = ?
                ORDER BY acknowledged ASC, created_at DESC
                LIMIT ?
                """,
                (project_id, limit),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "project_id": r["project_id"],
                "alert_type": r["alert_type"],
                "severity": r["severity"],
                "title": r["title"],
                "body": r["body"],
                "metric_value": r["metric_value"],
                "acknowledged": bool(r["acknowledged"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def acknowledge_alert(self, project_id: str, alert_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE project_alerts SET acknowledged = 1 WHERE project_id = ? AND id = ?",
                (project_id, alert_id),
            )
            return cur.rowcount > 0
