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
            if v is not None or k in ("default_base_currency", "default_date_dayfirst"):
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
