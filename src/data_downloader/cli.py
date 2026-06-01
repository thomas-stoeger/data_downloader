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
        latest_license = versions[-1].get("license") if versions else None
        if latest_license is None:
            lic = "not configured" if not config.get("license") else "not captured"
        elif latest_license.get("error"):
            lic = "capture failed"
        elif latest_license.get("changed_from_previous"):
            lic = "recorded (CHANGED)"
        else:
            lic = "recorded"
        typer.echo(f"  {name:<30} {config['description']}")
        typer.echo(f"    {'local versions:':<18} {n}  latest: {latest}  license: {lic}")


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
        lic = r.get("license_status", "none")
        marker = "  *** LICENSE CHANGED ***" if lic == "changed" else ""
        typer.echo(
            f"  {r['name']:<30} local={r['local_version']}  remote={r['remote_version']}"
            f"  [{status}]  license={lic}{marker}"
        )


@app.command()
def license(
    name: str = typer.Argument(..., help="Dataset name (see 'dl list')"),
    all_versions: bool = typer.Option(
        False, "--all", "-a", help="Show every captured version, not just the latest"
    ),
):
    """Show the license captured for a dataset: where it was declared upstream
    and the stored copy (path and hash) for each version."""
    config = load_registry().get(name)
    if config is None:
        raise typer.Exit(code=1)

    declared = config.get("license", {}).get("declared_at") if config.get("license") else None
    typer.echo(f"{name}")
    if config.get("license", {}).get("spdx"):
        typer.echo(f"  configured SPDX: {config['license']['spdx']}")
    if declared:
        for d in [declared] if isinstance(declared, str) else declared:
            typer.echo(f"  declared at:     {d}")

    dataset_root = get_data_root() / name
    versions = manifest_mod.all_local_versions(dataset_root)
    if not versions:
        typer.echo("  no local versions captured yet.")
        return
    shown = versions if all_versions else versions[-1:]
    for entry in shown:
        lic = entry.get("license")
        typer.echo(f"  version {entry['version']} (captured {entry['downloaded_at'][:10]}):")
        if not lic:
            typer.echo("    no license recorded")
            continue
        if lic.get("spdx"):
            typer.echo(f"    spdx:        {lic['spdx']}")
        if lic.get("note"):
            typer.echo(f"    note:        {lic['note']}")
        for d in lic.get("declared_at", []):
            typer.echo(f"    declared at: {d}")
        if lic.get("changed_from_previous"):
            typer.echo("    *** changed from previous version ***")
        if lic.get("error"):
            typer.echo(f"    capture error: {lic['error']}")
        for doc in lic.get("documents", []):
            typer.echo(f"    copy:        {doc['file']}")
            typer.echo(f"      url:       {doc['url']}")
            typer.echo(f"      sha256:    {doc['sha256']}  ({doc['bytes']} bytes)")


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
