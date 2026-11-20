"""Command-line interface for SalesPulseBI (`salespulsebi`)."""

from __future__ import annotations

import argparse
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn
from rich.console import Console
from rich.panel import Panel

from salespulsebi import __app_name__, __version__
from salespulsebi.api.app import create_app
from salespulsebi.config import Settings
from salespulsebi.demo.generator import seed_demo_projects
from salespulsebi.ingestion.pipeline import ProjectIngestionPipeline
from salespulsebi.storage.database import MetadataDB
from salespulsebi.storage.models import ProjectCreate

console = Console()


def _open_browser_later(url: str, delay: float = 0.9) -> None:
    def _worker() -> None:
        time.sleep(delay)
        webbrowser.open(url)

    threading.Thread(target=_worker, daemon=True).start()


def _print_banner(host: str, port: int, data_dir: Path) -> None:
    url = f"http://{host}:{port}"
    console.print(
        Panel.fit(
            f"[bold #6366f1]{__app_name__}[/bold #6366f1] [dim]v{__version__}[/dim]\n"
            f"Local Web UI : [bold cyan]{url}[/bold cyan]\n"
            f"Data Folder  : [dim]{data_dir}[/dim]\n"
            f"Privacy Mode : [green]100% Local-First (Offline Ready)[/green]",
            border_style="#6366f1",
        )
    )


def _build_settings(args: argparse.Namespace) -> Settings:
    kwargs: dict[str, object] = {}
    if getattr(args, "data_dir", None):
        kwargs["data_dir"] = Path(args.data_dir).expanduser().resolve()
    if getattr(args, "host", None):
        kwargs["host"] = args.host
    if getattr(args, "port", None):
        kwargs["port"] = args.port
    return Settings(**kwargs)  # type: ignore[arg-type]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="salespulsebi",
        description="SalesPulseBI — Local-first Sales Intelligence Web App",
    )
    sub = parser.add_subparsers(dest="command")

    for name in ("serve", "demo"):
        p = sub.add_parser(name, help=f"{'Seed demos & ' if name == 'demo' else ''}Start local web app")
        p.add_argument("--host", default=None)
        p.add_argument("--port", type=int, default=None)
        p.add_argument("--data-dir", default=None)
        p.add_argument("--no-browser", action="store_true")
        if name == "serve":
            p.add_argument("--reload", action="store_true")

    p_open = sub.add_parser("open", help="Create/sync a project from a file or folder and launch dashboard")
    p_open.add_argument("path", help="Path to sales file or folder")
    p_open.add_argument("--name", default=None, help="Project display name")
    p_open.add_argument("--currency", default="USD", help="Currency code (default: USD)")
    p_open.add_argument("--host", default=None)
    p_open.add_argument("--port", type=int, default=None)
    p_open.add_argument("--data-dir", default=None)
    p_open.add_argument("--no-browser", action="store_true")

    p_info = sub.add_parser("info", help="Show local data directory and saved projects summary")
    p_info.add_argument("--data-dir", default=None)

    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--no-browser", action="store_true")

    args = parser.parse_args(argv)
    cmd = args.command or "serve"
    settings = _build_settings(args)
    settings.ensure_dirs()

    if cmd == "info":
        db = MetadataDB(settings.db_path)
        projects = db.list_projects()
        console.print(f"[bold]{__app_name__} v{__version__}[/bold] — Data dir: {settings.data_dir}")
        for proj in projects:
            console.print(f"  • {proj.name} ({proj.row_count:,} rows, {proj.file_count} files)")
        return

    if cmd == "demo":
        db = MetadataDB(settings.db_path)
        pipeline = ProjectIngestionPipeline(db, settings)
        seeded = seed_demo_projects(db, settings, pipeline)
        console.print(f"[green]Ready:[/green] {len(seeded)} demo project(s) available.")

    elif cmd == "open":
        target = Path(args.path).expanduser().resolve()
        if not target.exists():
            console.print(f"[red]Error:[/red] Path not found: {target}")
            raise SystemExit(1)
        db = MetadataDB(settings.db_path)
        pipeline = ProjectIngestionPipeline(db, settings)
        proj_name = args.name or target.name
        existing = db.find_project_by_name(proj_name)
        proj = existing or db.create_project(
            ProjectCreate(name=proj_name, currency=args.currency)
        )
        res = pipeline.link_local_path(proj.id, str(target), recursive=True)
        console.print(
            f"[green]Ingested[/green] {res.total_rows:,} rows from {res.total_files} file(s) into '{proj.name}'."
        )

    _print_banner(settings.host, settings.port, settings.data_dir)
    if not getattr(args, "no_browser", False):
        _open_browser_later(f"http://{settings.host}:{settings.port}")

    if cmd == "serve" and getattr(args, "reload", False):
        uvicorn.run(
            "salespulsebi.api.app:create_app",
            factory=True,
            host=settings.host,
            port=settings.port,
            reload=True,
        )
    else:
        app = create_app(settings)
        uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
