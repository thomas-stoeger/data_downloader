"""
Retraction Watch downloader.

Crossref publishes the Retraction Watch database as a single CSV file in a
public GitLab repository (crossref/retraction-watch-data) that a bot rebuilds
every working day. This downloader grabs that CSV plus the repository's
README, which documents the column layout.

The version string is the date of the latest commit on the tracked branch,
read cheaply from the GitLab commits API. The bot's commit titles look like
"Retraction Watch data for 2026-05-29", and each commit's committed_date is
the day that snapshot was built, so the date answers "is this current?"
without downloading the ~64 MB CSV.

Optional config keys (defaults shown):
    project  - GitLab project path        ("crossref/retraction-watch-data")
    branch   - branch to track            ("main")
    file     - data file within the repo  ("retraction_watch.csv")
"""
import json
import ssl
import urllib.parse
import urllib.request
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries, is_doc_filename

_HOST = "https://gitlab.com"
_API = f"{_HOST}/api/v4"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())

_DEFAULT_PROJECT = "crossref/retraction-watch-data"
_DEFAULT_BRANCH = "main"
_DEFAULT_FILE = "retraction_watch.csv"


def _project_id(project: str) -> str:
    """URL-encode a GitLab project path for use in API paths."""
    return urllib.parse.quote(project, safe="")


def _get_json(url: str) -> dict | list:
    with urllib.request.urlopen(url, context=_SSL) as r:
        return json.loads(r.read())


def _latest_commit_date(project: str, branch: str) -> str:
    """Return the committed_date of the latest commit on `branch` as YYYY-MM-DD."""
    qs = urllib.parse.urlencode({"ref_name": branch, "per_page": 1})
    url = f"{_API}/projects/{_project_id(project)}/repository/commits?{qs}"
    commits = _get_json(url)
    if not commits:
        raise RuntimeError(
            f"No commits found on branch {branch!r} of GitLab project {project!r}"
        )
    committed_date = commits[0].get("committed_date")
    if not committed_date:
        raise RuntimeError(
            f"GitLab commit for {project!r} missing committed_date: {commits[0]!r}"
        )
    # committed_date is ISO 8601, e.g. "2026-05-29T23:00:08.000+00:00".
    return committed_date[:10]


def _repo_files(project: str, branch: str) -> list[str]:
    """List blob paths at the repository root for `branch`."""
    qs = urllib.parse.urlencode({"ref": branch, "per_page": 100})
    url = f"{_API}/projects/{_project_id(project)}/repository/tree?{qs}"
    tree = _get_json(url)
    return [entry["path"] for entry in tree if entry.get("type") == "blob"]


def _raw_url(project: str, branch: str, path: str) -> str:
    return f"{_HOST}/{project}/-/raw/{branch}/{urllib.parse.quote(path)}"


def _download(url: str, name: str, dest: Path) -> Path:
    local_path = dest / name
    with urllib.request.urlopen(url, context=_SSL) as response:
        expected = int(response.headers.get("Content-Length", 0)) or None
        bytes_written = 0
        with (
            open(local_path, "wb") as f,
            tqdm(total=expected, unit="B", unit_scale=True, desc=name) as bar,
        ):
            while chunk := response.read(_CHUNK):
                f.write(chunk)
                bytes_written += len(chunk)
                bar.update(len(chunk))
    if expected is not None and bytes_written != expected:
        local_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"{name}: size mismatch — expected {expected} bytes, got {bytes_written}"
        )
    return local_path


class RetractionWatchDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        project = config.get("project", _DEFAULT_PROJECT)
        branch = config.get("branch", _DEFAULT_BRANCH)
        return _latest_commit_date(project, branch)

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        project = config.get("project", _DEFAULT_PROJECT)
        branch = config.get("branch", _DEFAULT_BRANCH)
        data_file = config.get("file", _DEFAULT_FILE)

        repo_files = _repo_files(project, branch)
        if data_file not in repo_files:
            raise RuntimeError(
                f"Data file {data_file!r} not found in {project!r}@{branch}. "
                f"Available: {repo_files}"
            )

        # The data file plus any README-shaped companions that document it.
        wanted = [data_file] + [
            p for p in repo_files if p != data_file and is_doc_filename(Path(p).name)
        ]

        written = []
        for path in wanted:
            url = _raw_url(project, branch, path)
            name = Path(path).name
            written.append(
                _with_retries(lambda u=url, n=name, d=dest: _download(u, n, d))
            )
        return written
