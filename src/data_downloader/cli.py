import typer
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from .config import get_data_root, set_data_root
from .registry.loader import load_registry
from .versioning import manifest as manifest_mod
from .versioning.store import check_dataset, fetch_dataset

app = typer.Typer(help="Download and manage versioned datasets.")


@app.command()
def configure(root: Path = typer.Argument(..., help="Path to the data root directory")):
    """Set the data root directory (persisted to ~/.config/data_downloader/config.toml)."""
    set_data_root(root.resolve())
    typer.echo(f"Data root set to: {root.resolve()}")


@app.command()
def fetch(
    name: str = typer.Argument(..., help="Dataset name (see 'dl list')"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-download even if up-to-date"),
):
    """Download the latest version of a dataset."""
    fetch_dataset(name, force=force)


@app.command("list")
def list_datasets():
    """List all known datasets and their locally downloaded versions."""
    registry = load_registry()
    data_root = get_data_root()

    for name, config in registry.items():
        dataset_root = data_root / name
        versions = manifest_mod.all_local_versions(dataset_root)
        latest = versions[-1]["version"] if versions else "—"
        n = len(versions)
        typer.echo(f"  {name:<30} {config['description']}")
        typer.echo(f"    {'local versions:':<18} {n}  latest: {latest}")


@app.command()
def check(
    name: str = typer.Option(None, "--name", "-n", help="Check a single dataset"),
    workers: int = typer.Option(4, "--workers", "-w", help="Parallel check threads"),
):
    """Check all (or one) dataset(s) for newer remote versions."""
    registry = load_registry()
    names = [name] if name else list(registry)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(check_dataset, names))

    for r in results:
        status = "up-to-date" if r["up_to_date"] else "UPDATE AVAILABLE"
        typer.echo(
            f"  {r['name']:<30} local={r['local_version']}  remote={r['remote_version']}  [{status}]"
        )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(8000, "--port", "-p", help="Bind port"),
):
    """Start the browser UI."""
    import uvicorn
    typer.echo(f"Starting browser UI at http://{host}:{port}")
    uvicorn.run("data_downloader.web:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
