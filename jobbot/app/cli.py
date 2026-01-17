from __future__ import annotations

import json
from dataclasses import asdict

import typer

from .config import load_config
from .pipeline import run_scrape

app = typer.Typer(add_completion=False)


@app.callback()
def root() -> None:
    """
    LinkedIn AI Jobs Scraper (jobbot).

    Use a subcommand like: jobbot run ...
    """
    # This callback forces Typer to behave like a multi-command CLI
    # even if we currently have only one command.
    pass


@app.command()
def run(
    config: str = typer.Option("config.yaml", "--config", "-c"),
    out: str = typer.Option("-", "--out", "-o"),
    print_urls: bool = typer.Option(True, "--no-print-urls"),
):
    """
    Run the LinkedIn scrape and output JSON (stdout by default).
    """
    cfg = load_config(config)
    jobs = run_scrape(cfg, print_urls=print_urls)

    payload = json.dumps([asdict(j) for j in jobs], ensure_ascii=False, indent=2)

    if out == "-":
        typer.echo(payload)
    else:
        with open(out, "w", encoding="utf-8") as f:
            f.write(payload)
        typer.echo(f"Wrote {len(jobs)} jobs to {out}")


def main() -> None:
    app(prog_name="jobbot")


if __name__ == "__main__":
    main()
