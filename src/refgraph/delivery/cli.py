from __future__ import annotations

import asyncio
import json
import sys

import typer

from ..adapters import default_sources, source_names
from ..domain.model import TraversalConfig
from ..domain.resolution import parse_input, resolve
from ..domain.traversal import build_graph
from ..infra.cache import ResponseCache

app = typer.Typer(
    name="refgraph",
    help="Build citation graphs (references + citations) around a seed paper.",
    no_args_is_help=True,
)


@app.command()
def build(
    input: str = typer.Argument(..., help="DOI, arXiv ID, or paper title."),
    ref_depth: int = typer.Option(1, "--ref-depth", min=0, help="Levels of references to follow."),
    cite_depth: int = typer.Option(1, "--cite-depth", min=0, help="Levels of citations to follow."),
    top_cites: int = typer.Option(
        30, "--top-cites", min=1, help="Max citing papers kept per paper (ranked by citations)."
    ),
    year_min: int | None = typer.Option(None, "--year-min", help="Ignore citers before this year."),
    year_max: int | None = typer.Option(None, "--year-max", help="Ignore citers after this year."),
    sources: str = typer.Option(
        "semantic_scholar,openalex",
        "--sources",
        help=f"Comma-separated sources: {', '.join(source_names())}.",
    ),
    output: str | None = typer.Option(
        None, "-o", "--output", help="Write the graph JSON here (default: stdout)."
    ),
    cache_path: str | None = typer.Option(
        ".refgraph-cache.sqlite3", "--cache", help="SQLite cache path."
    ),
    no_cache: bool = typer.Option(False, "--no-cache", help="Disable the response cache."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Non-interactive: accept the best resolution candidate."
    ),
) -> None:
    """Resolve INPUT to a paper, then build its citation graph document."""
    if ref_depth == 0 and cite_depth == 0:
        typer.secho("Nothing to do: both depths are 0.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    config = TraversalConfig(
        ref_depth=ref_depth,
        cite_depth=cite_depth,
        top_cites=top_cites,
        year_min=year_min,
        year_max=year_max,
        sources=[s.strip() for s in sources.split(",") if s.strip()],
    )

    async def run():
        cache = None if no_cache else ResponseCache(cache_path)
        try:
            srcs = default_sources(names=config.sources, cache=cache)
            try:
                paper = await _resolve_input(input, srcs, yes)
                typer.secho(
                    f"Seed: {paper.title} ({paper.year or 'year unknown'}) [{paper.id}]",
                    fg=typer.colors.BLUE,
                    err=True,
                )
                return await build_graph(paper, config=config, sources=srcs)
            finally:
                for src in srcs:
                    await src.aclose()
        finally:
            if cache is not None:
                cache.close()

    document = asyncio.run(run())
    payload = document.to_json()
    if output:
        with open(output, "w") as fh:
            fh.write(payload + "\n")
    else:
        print(payload)
    typer.secho(
        f"Graph complete: {len(document.nodes)} nodes, {len(document.edges)} edges, "
        f"{len(document.warnings)} warning(s).",
        fg=typer.colors.GREEN,
        err=True,
    )
    for warning in document.warnings:
        typer.secho(
            f"  warning [{warning.kind}]: {warning.detail}", fg=typer.colors.YELLOW, err=True
        )


@app.command("resolve")
def resolve_cmd(
    input: str = typer.Argument(..., help="DOI, arXiv ID, or paper title."),
    limit: int = typer.Option(10, "--limit", min=1, max=50, help="Max candidates."),
    sources: str = typer.Option(
        "semantic_scholar,openalex",
        "--sources",
        help=f"Comma-separated sources: {', '.join(source_names())}.",
    ),
    cache_path: str | None = typer.Option(
        ".refgraph-cache.sqlite3", "--cache", help="SQLite cache path."
    ),
    no_cache: bool = typer.Option(False, "--no-cache", help="Disable the response cache."),
) -> None:
    """Show resolution candidates for INPUT as JSON."""

    async def run() -> None:
        cache = None if no_cache else ResponseCache(cache_path)
        try:
            names = [s.strip() for s in sources.split(",") if s.strip()]
            srcs = default_sources(names=names, cache=cache)
            try:
                candidates = await resolve(input, sources=srcs, limit=limit)
            finally:
                for src in srcs:
                    await src.aclose()
            print(json.dumps([c.model_dump(mode="json") for c in candidates], indent=2))
        finally:
            if cache is not None:
                cache.close()

    asyncio.run(run())


async def _resolve_input(input: str, srcs, yes: bool):
    doi, arxiv, _ = parse_input(input)
    candidates = await resolve(input, sources=srcs)
    if not candidates:
        typer.secho(f"No paper found for: {input!r}", fg=typer.colors.RED, err=True)
        if "semantic_scholar" in (s.name for s in srcs):
            typer.secho(
                "Hint: Semantic Scholar's unauthenticated pool is often saturated; "
                "setting S2_API_KEY (free) improves reliability.",
                fg=typer.colors.YELLOW,
                err=True,
            )
        raise typer.Exit(code=1)
    if doi or arxiv:
        return candidates[0].paper  # exact identifier: unambiguous
    if len(candidates) == 1:
        return candidates[0].paper
    if yes:
        return candidates[0].paper
    if not sys.stdin.isatty():
        typer.secho(
            f"Ambiguous input ({len(candidates)} candidates) and stdin is not a TTY; "
            "rerun with --yes to accept the best match.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    typer.secho("Multiple candidates found:", fg=typer.colors.BLUE, err=True)
    for i, cand in enumerate(candidates, start=1):
        paper = cand.paper
        meta = f"{paper.year or '????'}"
        if paper.venue:
            meta += f" — {paper.venue}"
        cites = f", {paper.citation_count} citations" if paper.citation_count is not None else ""
        typer.secho(
            f"  [{i}] {paper.title} ({meta}){cites} "
            f"[match {cand.match_confidence:.2f}; {'/'.join(cand.source)}]",
            err=True,
        )
    choice = typer.prompt("Select paper", type=int, default=1, show_default=True, err=True)
    if not 1 <= choice <= len(candidates):
        typer.secho("Invalid choice.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    return candidates[choice - 1].paper


if __name__ == "__main__":
    app()
