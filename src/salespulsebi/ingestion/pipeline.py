"""End-to-end project ingestion pipeline: uploads, folder linking, sync, and Parquet rebuild."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

import pandas as pd

from salespulsebi.config import Settings
from salespulsebi.ingestion.cleaner import (
    CleaningStats,
    deduplicate_combined,
    normalize_dataframe,
)
from salespulsebi.ingestion.readers import read_tabular_file
from salespulsebi.ingestion.scanner import ScannedFile, compute_sync_diff, scan_path
from salespulsebi.ingestion.schema_detector import detect_schema_and_profile
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import (
    CleaningRules,
    ColumnProfile,
    SyncResult,
)
from salespulsebi.storage.parquet_store import ParquetStore, sanitize_json


def _safe_rel_path(raw_name: str) -> Path:
    parts = [p for p in Path(raw_name).parts if p not in ("", ".", "..", "/", "\\")]
    if not parts:
        return Path("uploaded_file.csv")
    return Path(*parts)


class ProjectIngestionPipeline:
    """Orchestrates file/folder ingestion, heterogeneous schema merging, and Parquet builds."""

    def __init__(self, db: MetadataDB, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def ingest_uploaded_files(
        self,
        project_id: str,
        files: list[tuple[str, bytes]],
    ) -> SyncResult:
        """Persist uploaded files (preserving relative folder paths) and rebuild project."""
        raw_dir = self.settings.project_raw_dir(project_id)
        for rel_name, content in files:
            rel_path = _safe_rel_path(rel_name)
            dest = (raw_dir / rel_path).resolve()
            if not str(dest).startswith(str(raw_dir.resolve())):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)

        return self.sync_project(project_id)

    def link_local_path(
        self,
        project_id: str,
        path_str: str,
        *,
        recursive: bool = True,
        file_glob: str = "*",
    ) -> SyncResult:
        """Link a local folder or file path to a project and run initial sync."""
        target = Path(path_str).expanduser().resolve()
        if not target.exists():
            raise FileNotFoundError(f"Path does not exist: {target}")

        source_type = "linked_folder" if target.is_dir() else "linked_file"
        self.db.upsert_source(
            project_id=project_id,
            source_type=source_type,
            path=str(target),
            recursive=recursive,
            file_glob=file_glob,
            status="linked",
        )
        return self.sync_project(project_id)

    def remove_source(self, project_id: str, source_id: str) -> SyncResult:
        """Remove a source (and its copied raw file if uploaded) and rebuild dataset."""
        source = self.db.get_source(project_id, source_id)
        if not source:
            raise FileNotFoundError(f"Source {source_id} not found")
        if source.source_type == "uploaded_file":
            p = Path(source.path)
            raw_dir = self.settings.project_raw_dir(project_id).resolve()
            if p.exists() and str(p.resolve()).startswith(str(raw_dir)):
                p.unlink()
        self.db.delete_source(project_id, source_id)
        return self.sync_project(project_id)

    def rebuild_with_mapping(
        self,
        project_id: str,
        role_map: dict[str, str],
        cleaning_rules: CleaningRules | None = None,
    ) -> SyncResult:
        """Persist updated manual role mappings / cleaning rules and rebuild Parquet."""
        current = self.db.get_mapping(project_id)
        rules = cleaning_rules or current.cleaning_rules
        self.db.save_mapping(
            project_id,
            role_map=role_map,
            detected_schema=current.detected_schema,
            cleaning_rules=rules,
        )
        return self.sync_project(project_id, manual_overrides=role_map)

    def _collect_all_scanned_files(
        self, project_id: str, warnings: list[str]
    ) -> tuple[list[tuple[ScannedFile, str]], dict[str, str]]:
        """Collect all readable files from uploaded raw dir and linked folder/file sources."""
        all_files: list[tuple[ScannedFile, str]] = []
        seen_abs: set[str] = set()

        raw_dir = self.settings.project_raw_dir(project_id)
        for sf in scan_path(raw_dir, recursive=True, file_glob="*"):
            key = str(sf.abs_path)
            if key not in seen_abs:
                seen_abs.add(key)
                all_files.append((sf, "uploaded_file"))

        sources = self.db.list_sources(project_id)
        prev_uploaded_hashes: dict[str, str] = {}
        for src in sources:
            if src.source_type == "uploaded_file" and src.file_hash:
                prev_uploaded_hashes[src.path] = src.file_hash
            elif src.source_type in ("linked_folder", "linked_file"):
                root = Path(src.path)
                if not root.exists():
                    warnings.append(f"Linked path not found: {src.path}")
                    self.db.upsert_source(
                        project_id=project_id,
                        source_type=src.source_type,
                        path=src.path,
                        recursive=src.recursive,
                        file_glob=src.file_glob,
                        status="missing",
                        error_message="Path does not exist",
                    )
                    continue
                scanned = scan_path(root, recursive=src.recursive, file_glob=src.file_glob)
                if not scanned:
                    warnings.append(f"No supported sales files found in: {src.path}")
                self.db.upsert_source(
                    project_id=project_id,
                    source_type=src.source_type,
                    path=src.path,
                    recursive=src.recursive,
                    file_glob=src.file_glob,
                    file_hash=scanned[0].file_hash if (len(scanned) == 1 and root.is_file()) else None,
                    file_size=sum(f.file_size for f in scanned),
                    row_count=src.row_count,
                    status="ready",
                    error_message=None,
                )
                for sf in scanned:
                    key = str(sf.abs_path)
                    if key not in seen_abs:
                        seen_abs.add(key)
                        all_files.append((sf, "linked_discovered"))

        return all_files, prev_uploaded_hashes

    def sync_project(
        self,
        project_id: str,
        manual_overrides: dict[str, str] | None = None,
    ) -> SyncResult:
        """Scan all sources, unify heterogeneous schemas, clean, and write dataset.parquet."""
        t0 = time.perf_counter()
        project = self.db.get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        mapping_state = self.db.get_mapping(project_id)
        overrides = manual_overrides if manual_overrides is not None else mapping_state.role_map
        cleaning_rules = mapping_state.cleaning_rules

        warnings: list[str] = []
        all_files, prev_hashes = self._collect_all_scanned_files(project_id, warnings)
        scanned_only = [sf for sf, _ in all_files]
        diff = compute_sync_diff(prev_hashes, scanned_only)

        normalized_frames: list[pd.DataFrame] = []
        raw_preview_frames: list[pd.DataFrame] = []
        merged_role_map: dict[str, str] = {}
        schema_variants: list[dict[str, Any]] = []
        agg_stats = CleaningStats()
        total_rows_by_linked_root: dict[str, int] = {}

        for sf, kind in all_files:
            try:
                sheets = read_tabular_file(sf.abs_path)
            except Exception as exc:
                warnings.append(f"Failed reading {sf.rel_path}: {exc}")
                if kind == "uploaded_file":
                    self.db.upsert_source(
                        project_id=project_id,
                        source_type="uploaded_file",
                        path=str(sf.abs_path),
                        file_hash=sf.file_hash,
                        file_size=sf.file_size,
                        row_count=0,
                        status="error",
                        error_message=str(exc),
                    )
                continue

            file_rows = 0
            for sheet_name, raw_df in sheets:
                if raw_df.empty:
                    continue
                raw_preview_frames.append(raw_df.head(500))
                file_role_map, _ = detect_schema_and_profile(
                    raw_df,
                    manual_overrides=overrides,
                )
                for role, col_name in file_role_map.items():
                    merged_role_map.setdefault(role, col_name)

                schema_variants.append(
                    {
                        "file": sf.rel_path,
                        "sheet": sheet_name,
                        "columns": list(raw_df.columns),
                        "detected_roles": file_role_map,
                        "rows": len(raw_df),
                    }
                )

                norm_df, st = normalize_dataframe(
                    raw_df,
                    file_role_map,
                    cleaning_rules,
                    source_file=sf.abs_path.name,
                    source_relpath=sf.rel_path,
                    source_folder=sf.folder_label,
                    source_sheet=sheet_name,
                )
                agg_stats.rows_raw += st.rows_raw
                agg_stats.invalid_dates += st.invalid_dates
                agg_stats.invalid_amounts += st.invalid_amounts
                if st.derived_revenue_used:
                    agg_stats.derived_revenue_used = True
                if not norm_df.empty:
                    normalized_frames.append(norm_df)
                    file_rows += len(norm_df)

            if kind == "uploaded_file":
                self.db.upsert_source(
                    project_id=project_id,
                    source_type="uploaded_file",
                    path=str(sf.abs_path),
                    file_hash=sf.file_hash,
                    file_size=sf.file_size,
                    row_count=file_rows,
                    status="ready",
                    error_message=None,
                )
            else:
                # Accumulate row counts per linked folder/file parent
                for src in self.db.list_sources(project_id):
                    if src.source_type in ("linked_folder", "linked_file"):
                        src_path = Path(src.path).resolve()
                        if sf.abs_path == src_path or src_path in sf.abs_path.parents:
                            total_rows_by_linked_root[src.id] = (
                                total_rows_by_linked_root.get(src.id, 0) + file_rows
                            )

        # Update linked source row counts
        for src in self.db.list_sources(project_id):
            if src.id in total_rows_by_linked_root:
                self.db.upsert_source(
                    project_id=project_id,
                    source_type=src.source_type,
                    path=src.path,
                    recursive=src.recursive,
                    file_glob=src.file_glob,
                    file_hash=src.file_hash,
                    file_size=src.file_size,
                    row_count=total_rows_by_linked_root[src.id],
                    status="ready",
                    error_message=None,
                )

        parquet_path = self.settings.project_parquet_path(project_id)
        if normalized_frames:
            combined = pd.concat(normalized_frames, ignore_index=True, sort=False)
            combined, dup_removed = deduplicate_combined(combined, cleaning_rules)
            agg_stats.duplicates_removed = dup_removed
            agg_stats.rows_clean = len(combined)
            total_rows = ParquetStore.write_parquet(combined, parquet_path)

            # Update mapping schema profiles using unified raw preview + final role_map
            preview_df = pd.concat(raw_preview_frames, ignore_index=True, sort=False)
            final_role_map = {**merged_role_map, **{k: v for k, v in overrides.items() if v}}
            _, profiles = detect_schema_and_profile(preview_df, manual_overrides=final_role_map)
            self.db.save_mapping(
                project_id,
                role_map=final_role_map,
                detected_schema=profiles,
                cleaning_rules=cleaning_rules,
            )

            # Summary KPIs for Project Hub card
            total_rev: float | None = None
            if "_revenue" in combined.columns:
                valid = (
                    combined.loc[~combined["_is_excluded_status"], "_revenue"]
                    if "_is_excluded_status" in combined.columns
                    else combined["_revenue"]
                )
                rev_sum = float(valid.dropna().sum())
                total_rev = round(rev_sum, 2)

            health_score = _quick_health_score(combined, agg_stats, schema_variants)
            quality_payload = sanitize_json(
                {
                    "health_score": health_score,
                    "stats": {
                        "rows_raw": agg_stats.rows_raw,
                        "rows_clean": agg_stats.rows_clean,
                        "duplicates_removed": agg_stats.duplicates_removed,
                        "invalid_dates": agg_stats.invalid_dates,
                        "invalid_amounts": agg_stats.invalid_amounts,
                        "derived_revenue_used": agg_stats.derived_revenue_used,
                    },
                    "schema_variants": schema_variants,
                    "warnings": warnings,
                }
            )
            self.settings.project_quality_path(project_id).write_text(
                json.dumps(quality_payload, indent=2), encoding="utf-8"
            )
        else:
            total_rows = 0
            total_rev = None
            health_score = None
            if parquet_path.exists():
                parquet_path.unlink()
            self.db.save_mapping(
                project_id,
                role_map=overrides,
                detected_schema=[],
                cleaning_rules=cleaning_rules,
            )

        self.db.update_project_stats(
            project_id,
            row_count=total_rows,
            file_count=len(all_files),
            total_revenue=total_rev,
            health_score=health_score,
            synced=True,
        )

        duration_ms = int((time.perf_counter() - t0) * 1000)
        self.db.record_sync(
            project_id=project_id,
            files_added=len(diff.added),
            files_updated=len(diff.updated),
            files_removed=len(diff.removed_paths),
            total_rows=total_rows,
            duration_ms=duration_ms,
            warnings=warnings,
        )

        return SyncResult(
            project_id=project_id,
            files_added=len(diff.added),
            files_updated=len(diff.updated),
            files_removed=len(diff.removed_paths),
            total_files=len(all_files),
            total_rows=total_rows,
            duration_ms=duration_ms,
            warnings=warnings,
        )

    def delete_project_files(self, project_id: str) -> None:
        """Remove local project directory from disk."""
        pdir = self.settings.projects_root / project_id
        if pdir.exists():
            shutil.rmtree(pdir, ignore_errors=True)


def _quick_health_score(
    df: pd.DataFrame,
    stats: CleaningStats,
    schema_variants: list[dict[str, Any]],
) -> float:
    score = 100.0
    total = max(len(df), 1)
    if stats.invalid_dates > 0:
        score -= min(20.0, (stats.invalid_dates / total) * 100.0)
    if stats.invalid_amounts > 0:
        score -= min(20.0, (stats.invalid_amounts / total) * 100.0)
    core_cols = [c for c in ("_date", "_revenue", "_product", "_customer") if c in df.columns]
    if core_cols:
        avg_null = float(df[core_cols].isna().mean().mean())
        score -= min(25.0, avg_null * 80.0)
    if "_revenue" in df.columns:
        neg_pct = float((df["_revenue"] < 0).mean())
        score -= min(10.0, neg_pct * 40.0)
    if len(schema_variants) > 1:
        col_sets = {tuple(sorted(v["columns"])) for v in schema_variants}
        if len(col_sets) > 1:
            score -= 3.0  # minor info deduction for heterogeneous headers resolved automatically
    return round(max(0.0, min(100.0, score)), 1)


def profiles_to_dicts(profiles: list[ColumnProfile]) -> list[dict[str, Any]]:
    return [p.model_dump() for p in profiles]
