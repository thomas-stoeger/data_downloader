"""
Browser UI — start with: dl serve
"""
import html as html_mod
import threading
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse

from .config import get_data_root
from .registry.loader import load_registry
from .versioning import manifest as manifest_mod
from .versioning.store import fetch_dataset

app = FastAPI()

# In-memory job state: {dataset_name: "running" | "done" | "error: <msg>"}
_jobs: dict[str, str] = {}
_jobs_lock = threading.Lock()


def _license_summary(config: dict, versions: list[dict]) -> str:
    """Short label describing the latest version's captured license, read from
    the manifest only (no network)."""
    latest = versions[-1].get("license") if versions else None
    if latest is None:
        return "—" if not config.get("license") else "not captured"
    if latest.get("error"):
        return "failed"
    if latest.get("changed_from_previous"):
        return "changed"
    return "recorded"


def _dataset_rows() -> list[dict]:
    registry = load_registry()
    data_root = get_data_root()
    rows = []
    for name, config in registry.items():
        dataset_root = data_root / name
        versions = manifest_mod.all_local_versions(dataset_root)
        with _jobs_lock:
            job = _jobs.get(name)
        rows.append(
            {
                "name": name,
                "description": config.get("description", ""),
                "downloaded": len(versions) > 0,
                "latest_version": versions[-1]["version"] if versions else None,
                "downloaded_at": versions[-1]["downloaded_at"] if versions else None,
                "version_count": len(versions),
                "license": _license_summary(config, versions),
                "job": job,
            }
        )
    return rows


def _run_fetch(name: str) -> None:
    try:
        fetch_dataset(name)
        with _jobs_lock:
            _jobs[name] = "done"
    except Exception as e:
        with _jobs_lock:
            _jobs[name] = f"error: {e}"


@app.post("/fetch/{name}")
def trigger_fetch(name: str):
    with _jobs_lock:
        if _jobs.get(name) == "running":
            return RedirectResponse("/", status_code=303)
        _jobs[name] = "running"
    threading.Thread(target=_run_fetch, args=(name,), daemon=True).start()
    return RedirectResponse("/", status_code=303)


@app.get("/", response_class=HTMLResponse)
def index():
    try:
        rows = _dataset_rows()
    except RuntimeError as e:
        return HTMLResponse(
            content=f"""<!DOCTYPE html>
<html><head><title>data_downloader</title>
<style>body{{font-family:sans-serif;padding:2rem;background:#f5f5f5}}
.box{{background:white;border-radius:8px;padding:1.5rem;max-width:600px;box-shadow:0 1px 4px rgba(0,0,0,0.08)}}
code{{background:#f0f0f0;padding:2px 6px;border-radius:4px}}</style></head>
<body><div class="box">
<h2>Configuration required</h2>
<p>{e}</p>
<p>Run once in your terminal:</p>
<pre><code>dl configure /path/to/your/data</code></pre>
</div></body></html>""",
            status_code=200,
        )

    any_running = any(r["job"] == "running" for r in rows)

    def row_html(r: dict) -> str:
        job = r["job"]

        if job == "running":
            status_badge = '<span class="badge running">downloading…</span>'
            button = '<button class="btn" disabled>downloading…</button>'
        elif job and job.startswith("error:"):
            status_badge = f'<span class="badge error" data-tooltip="{html_mod.escape(job, quote=True)}">error</span>'
            button = f'<form method="post" action="/fetch/{r["name"]}"><button class="btn retry">retry</button></form>'
        elif r["downloaded"]:
            status_badge = '<span class="badge downloaded">downloaded</span>'
            button = f'<form method="post" action="/fetch/{r["name"]}"><button class="btn update">update</button></form>'
        else:
            status_badge = '<span class="badge not-downloaded">not downloaded</span>'
            button = f'<form method="post" action="/fetch/{r["name"]}"><button class="btn download">download</button></form>'

        if r["downloaded"] and job != "running":
            version_cell = f'<td>{r["latest_version"]}</td><td>{r["downloaded_at"][:10]}</td>'
        else:
            version_cell = "<td>—</td><td>—</td>"

        return f"""
        <tr>
          <td><code>{r['name']}</code></td>
          <td>{r['description']}</td>
          <td>{status_badge}</td>
          {version_cell}
          <td>{r['version_count']}</td>
          <td>{r['license']}</td>
          <td>{button}</td>
        </tr>"""

    rows_html = "\n".join(row_html(r) for r in rows)
    downloaded = sum(1 for r in rows if r["downloaded"])
    total = len(rows)
    # Auto-refresh while any download is running
    refresh_tag = '<meta http-equiv="refresh" content="3">' if any_running else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {refresh_tag}
  <title>data_downloader</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f5f5f5;
      color: #222;
      margin: 0;
      padding: 2rem;
    }}
    h1 {{ font-size: 1.4rem; margin-bottom: 0.25rem; }}
    .subtitle {{ color: #666; font-size: 0.9rem; margin-bottom: 1.5rem; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: white;
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    }}
    th {{
      background: #f0f0f0;
      text-align: left;
      padding: 0.6rem 1rem;
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: #555;
    }}
    td {{
      padding: 0.7rem 1rem;
      font-size: 0.9rem;
      border-top: 1px solid #eee;
      vertical-align: middle;
    }}
    tr:hover td {{ background: #fafafa; }}
    code {{ font-size: 0.85rem; background: #f0f0f0; padding: 2px 5px; border-radius: 4px; }}
    .badge {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 12px;
      font-size: 0.78rem;
      font-weight: 500;
    }}
    .downloaded    {{ background: #d4edda; color: #155724; }}
    .not-downloaded{{ background: #f0f0f0; color: #666; }}
    .running       {{ background: #fff3cd; color: #856404; }}
    .error         {{ background: #f8d7da; color: #721c24; cursor: help; position: relative; }}
    .error::after  {{
      content: attr(data-tooltip);
      display: none;
      position: absolute;
      bottom: calc(100% + 6px);
      left: 50%;
      transform: translateX(-50%);
      background: #333;
      color: #fff;
      padding: 5px 8px;
      border-radius: 5px;
      font-size: 0.78rem;
      font-weight: 400;
      white-space: pre-wrap;
      word-break: break-all;
      max-width: 320px;
      width: max-content;
      z-index: 100;
      pointer-events: none;
    }}
    .error:hover::after {{ display: block; }}
    .btn {{
      padding: 4px 12px;
      border-radius: 6px;
      border: none;
      font-size: 0.82rem;
      cursor: pointer;
      font-weight: 500;
    }}
    .btn:disabled  {{ background: #f0f0f0; color: #999; cursor: default; }}
    .download      {{ background: #0d6efd; color: white; }}
    .download:hover{{ background: #0b5ed7; }}
    .update        {{ background: #f0f0f0; color: #333; }}
    .update:hover  {{ background: #e0e0e0; }}
    .retry         {{ background: #dc3545; color: white; }}
    .retry:hover   {{ background: #bb2d3b; }}
    form           {{ margin: 0; }}
  </style>
</head>
<body>
  <h1>data_downloader</h1>
  <p class="subtitle">{downloaded} of {total} datasets downloaded locally{" · refreshing…" if any_running else ""}</p>
  <table>
    <thead>
      <tr>
        <th>Name</th>
        <th>Description</th>
        <th>Status</th>
        <th>Latest version</th>
        <th>Downloaded on</th>
        <th>Versions</th>
        <th>License</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>
</body>
</html>"""
