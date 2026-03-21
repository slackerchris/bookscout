"""BookScout CLI — thin typer wrapper around the service internals.

Usage:
    python cli.py scan --author-id 42
    python cli.py scan --all
    python cli.py migrate --sqlite /data/bookscout.db
    python cli.py migrate --sqlite /data/bookscout.db --dry-run
"""
from __future__ import annotations

import asyncio
import sys
from typing import Optional

import typer

app = typer.Typer(
    name="bookscout",
    help="BookScout command-line interface.",
    no_args_is_help=True,
)


@app.command()
def scan(
    author_id: Optional[int] = typer.Option(
        None, "--author-id", "-a", help="Scan a specific author by database ID."
    ),
    all_authors: bool = typer.Option(
        False, "--all", help="Scan all active watchlisted authors."
    ),
) -> None:
    """Run scans in-process (bypasses the arq job queue)."""
    if not author_id and not all_authors:
        typer.echo("Pass --author-id <id> or --all", err=True)
        raise typer.Exit(1)

    async def _run() -> None:
        from config import load_config
        from db.session import AsyncSessionFactory
        from core.scan import scan_author_by_id

        config = load_config()

        if author_id:
            async with AsyncSessionFactory() as session:
                result = await scan_author_by_id(session, author_id, config=config)
            typer.echo(
                f"Done: {result['new_books']} new, "
                f"{result['updated_books']} updated  —  {result['author_name']}"
            )
        else:
            from sqlalchemy import select
            from db.models import Author, Watchlist

            async with AsyncSessionFactory() as session:
                q = await session.execute(
                    select(Watchlist)
                    .join(Author, Watchlist.author_id == Author.id)
                    .where(Author.active.is_(True), Watchlist.scan_enabled.is_(True))
                )
                author_ids = [e.author_id for e in q.scalars().all()]

            typer.echo(f"Scanning {len(author_ids)} authors…")
            for aid in author_ids:
                async with AsyncSessionFactory() as session:
                    r = await scan_author_by_id(session, aid, config=config)
                typer.echo(
                    f"  {r['author_name']}: {r['new_books']} new, "
                    f"{r['updated_books']} updated"
                )

    asyncio.run(_run())


@app.command()
def migrate(
    sqlite_path: str = typer.Option(..., "--sqlite", help="Path to legacy bookscout.db."),
    postgres_url: Optional[str] = typer.Option(
        None,
        "--postgres",
        help="PostgreSQL DSN (defaults to DATABASE_URL env var).",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing."),
) -> None:
    """Migrate data from the SQLite database to PostgreSQL."""
    import subprocess

    args = [sys.executable, "scripts/migrate_sqlite.py", "--sqlite", sqlite_path]
    if postgres_url:
        args += ["--postgres", postgres_url]
    if dry_run:
        args.append("--dry-run")

    result = subprocess.run(args, check=False)
    sys.exit(result.returncode)


if __name__ == "__main__":
    app()
