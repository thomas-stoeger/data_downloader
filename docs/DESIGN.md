# DESIGN

## Purpose

`data_downloader` fetches source scientific datasets from the web and stores
them locally as immutable, versioned snapshots. It is the ingestion stage that
sits upstream of a separate ETL pipeline (`lacuna-etl`): this repo's only job is
to produce verified, dated copies of upstream data so that downstream
processing is reproducible and can tell exactly when each input was captured and
whether a newer release exists.

The repo does not parse, transform, or interpret dataset contents. It downloads
bytes, verifies they are intact, and records what was downloaded and when.

## Inputs and outputs

**Inputs**

- A dataset registry: [src/data_downloader/registry/datasets.toml](../src/data_downloader/registry/datasets.toml).
  Each entry names a `downloader` type plus any keys that downloader needs
  (FTP host/path, Figshare collection id, file allowlists, etc.).
- A data root location, resolved by [config.py](../src/data_downloader/config.py)
  from `DL_DATA_ROOT` or `~/.config/data_downloader/config.toml`. This is the
  single output directory and the only location outside the repo that the tool
  writes to.
- User commands via the CLI (`dl`) or the local browser UI (`dl serve`).

**Outputs**

- Downloaded files under `<data-root>/<dataset>/<version>/`.
- A per-dataset `<data-root>/<dataset>/manifest.json` recording every version
  fetched, its `downloaded_at` UTC timestamp, and the list of files written.

The data root is treated as a shared artifact store. Nothing else in the system
writes there, and this tool writes nowhere else.

## Architecture

The flow is a small pipeline with one pluggable seam:

```
datasets.toml ──▶ registry/loader ──▶ versioning/store.fetch_dataset
                                          │
                                          ├─▶ downloaders/registry ──▶ <type> downloader
                                          ├─▶ versioning/integrity.verify_file
                                          └─▶ versioning/manifest.record_version
```

- **Registry** ([registry/loader.py](../src/data_downloader/registry/loader.py)):
  reads `datasets.toml` into per-dataset config dicts. Adding a dataset is a
  TOML edit, not a code change, as long as an existing downloader type fits.
- **Downloaders** ([downloaders/](../src/data_downloader/downloaders/)): all
  implement [BaseDownloader](../src/data_downloader/downloaders/base.py) with
  two methods, `fetch(config, dest)` and `latest_version(config)`. They are
  registered by string key in
  [downloaders/registry.py](../src/data_downloader/downloaders/registry.py).
  This is the extension point: a new source family means a new downloader class,
  not changes to the orchestration.
- **Store** ([versioning/store.py](../src/data_downloader/versioning/store.py)):
  orchestrates a fetch. It asks the downloader for the remote version, compares
  it to the local manifest, creates the version directory, runs the download,
  verifies every returned file, and records the version.
- **Integrity** ([versioning/integrity.py](../src/data_downloader/versioning/integrity.py)):
  rejects empty files and validates gzip/zip archives by reading them through.
- **Manifest** ([versioning/manifest.py](../src/data_downloader/versioning/manifest.py)):
  the source of truth for "what is downloaded." Append-only JSON.

The CLI ([cli.py](../src/data_downloader/cli.py)) and the web UI
([web.py](../src/data_downloader/web.py)) are thin front ends over `store` and
`manifest`. The web UI runs fetches in background threads and tracks job state
in memory only.

## Data sources

Each downloader type maps to a real upstream source and contact host(s):

| Downloader | Datasets | Source host(s) | Transport |
|---|---|---|---|
| `ftp` | NCBI gene_info, gene2pubmed, gene2go, gene_history, GeneRIFs, taxdump | `ftp.ncbi.nlm.nih.gov` | Anonymous FTP |
| `pubmed` | NCBI PubMed baseline + updatefiles | `ftp.ncbi.nlm.nih.gov` | Anonymous FTP |
| `pubtator3` | PubTator3 entity/relation annotation tables (optionally BioCXML archives) | `ftp.ncbi.nlm.nih.gov` | Anonymous FTP |
| `nlmcatalog` | NLM Catalog records matching a configurable Entrez search term (e.g. `reportedmedline`) | `eutils.ncbi.nlm.nih.gov` | HTTPS (E-utilities esearch + efetch) |
| `figshare` | iCite / NIH Open Citation Collection | `api.figshare.com` for metadata; file bytes from Figshare download URLs (`ndownloader.figshare.com`, may redirect to AWS S3) | HTTPS |
| `harmonizome` | Harmonizome (~150 datasets) | `maayanlab.cloud` for the dataset index and per-dataset JSON-LD; distribution files from the S3 `contentUrl` each page advertises | HTTPS |
| `nih_exporter` | NIH ExPORTER bulk data | `reporter.nih.gov` | HTTPS |
| `openalex` | OpenAlex full snapshot | `s3://openalex` (public bucket) | `aws s3 sync --no-sign-request` + boto3 unsigned `head_object` |

Note that `figshare` and `harmonizome` discover their actual byte-download URLs
at runtime from API responses and scraped HTML. Those URLs are upstream data,
not configuration (see Invariants).

## Companion documents

Each downloader (except `harmonizome`, which already writes a per-dataset
metadata sidecar, and `nih_exporter`, which has no documentation convention)
also pulls README-shaped files that sit alongside the data: `README*`,
`CHANGES`, `CHANGELOG`, `NOTES`, and `RELEASE_NOTES`, with or without a
text/markdown extension. The shared matcher is `is_doc_filename` in
[downloaders/base.py](../src/data_downloader/downloaders/base.py).

These docs are downloaded into the version directory but never participate in
the version string, so an upstream README touch does not look like a new
snapshot. `openalex` already gets them through `aws s3 sync`.

## Versioning

A version string comes from the remote source, never from the local clock where
avoidable, so that "is this current?" can be answered by comparison:

- `ftp`: file modification time (`MDTM`) as `YYYY-MM-DD`.
- `pubmed`: the four-digit baseline year (e.g. `2026`).
- `pubtator3`: the max `MDTM` across the selected files as `YYYY-MM-DD`
  (NCBI refreshes the snapshot in lockstep each month).
- `figshare`: `YYYY-MM` parsed from the latest article title.
- `harmonizome`: the max `Last-Modified` date across datasets' gene-attribute
  matrices, used as a cheap freshness proxy for the whole collection.
- `openalex`: `LastModified` of `RELEASE_NOTES.txt`.
- `nih_exporter`: today's date. This source has no single release marker, so
  this downloader cannot detect "no change"; `check` always reports an update
  available and `fetch` re-downloads if run on a new day. This is a known
  trade-off, accepted because ExPORTER is refreshed on a rolling basis.

`fetch_dataset` skips the download when `local_version == remote_version`
unless `--force` is passed. `check_dataset` performs the same comparison
without downloading.

## Fetch cadence

There is no scheduler in this repo. Fetching is manual and on demand, via the
CLI or the UI. The intended operating model is: run `dl check` to see which
datasets have newer upstream releases, then `dl fetch <name>` the ones you want.

Upstream release cadences vary widely (iCite and OpenAlex roughly monthly,
PubMed an annual baseline plus daily update files, NCBI gene files refreshed
frequently, Harmonizome effectively static since 2020). Encoding those
schedules is deliberately out of scope; `check` exposes the current state and a
human or an external scheduler decides when to act.

## Rate limiting, politeness, and concurrency

The tool assumes these are bulk/public endpoints intended for bulk download and
keeps load modest rather than implementing formal rate limiting:

- **No throttling or inter-request delays.** There are no sleeps and no backoff
  between retries; retries fire immediately. This is a deliberate simplicity
  choice for a low-frequency, single-operator tool, not a guarantee of polite
  pacing. If a source begins rejecting requests, add backoff here rather than
  assuming it exists.
- **Bounded concurrency.** `dl check` fans out over datasets with a 4-thread
  pool. Harmonizome discovery and freshness checks use 6-thread pools for page
  parsing and HEAD requests. Within a single dataset, files are downloaded
  sequentially (one connection at a time), except `openalex`, which hands
  parallelism to `aws s3 sync`.
- **FTP** uses anonymous login and one connection per file.
- **TLS** verification always uses the `certifi` CA bundle.

## Retry and failure behavior

Integrity is enforced at multiple layers and partial state is cleaned up:

- **Size checks** on every HTTP/FTP download; a byte-count mismatch deletes the
  partial file and raises.
- **Checksums where the source publishes them**: Figshare `computed_md5`,
  PubMed `.md5` sidecars. PubMed retries up to 3 times on a hash mismatch.
- **Archive validation** after download: gzip and zip files are read through to
  confirm they decompress (`verify_file`).
- **Retry policy**: the shared helper retries 2 extra times (3 attempts total)
  for `figshare` and `nih_exporter`; `pubmed` and `harmonizome` each retry up to
  3 times per file with their own logic.
- **Atomic-ish versions**: `store.fetch_dataset` wraps download + verify in a
  try/except that `rmtree`s the entire version directory on any failure, so a
  failed fetch never leaves a half-written version that the manifest would
  later trust. The version is recorded in the manifest only after every file
  verifies.
- **Harmonizome is the deliberate exception**: with ~150 datasets, one bad
  distribution file should not discard the whole snapshot. It verifies per
  file, quarantines persistent failures to `<version>/failed/`, records both
  successes and failures in each dataset's `{slug}__metadata.json` sidecar, and
  still completes the version. It also skips distributions that HEAD as
  missing or zero-length (some upstream files are intentionally empty), so they
  do not trip the gzip check and trigger a wipe.
- **Resumability**: large fetches can be interrupted and re-run. `openalex`
  resumes through `aws s3 sync` size comparison; `pubmed` skips files already
  present with a verified `.md5` sidecar.

## Invariants and assumptions

- A version directory that has a corresponding manifest entry is complete and
  verified. Incomplete fetches are removed, not recorded (Harmonizome excepted,
  where completeness is recorded per file in the sidecar).
- `manifest.json` is the authority for local state. `latest_local_version`
  returns the **last appended** entry, not a semantic max. The code assumes
  versions are fetched in chronological order; fetching an older version after a
  newer one would make the older one appear "latest."
- Version strings must be filesystem-safe; they become directory names.
- Filenames are taken from the remote (FTP path basename, Figshare/Harmonizome
  file names). Downloaded URLs and file names from API/HTML responses are
  **untrusted data**: they are used only to fetch bytes into the version
  directory, never executed or interpreted as instructions.
- A data root must be configured; with none set, every command fails fast with
  guidance.
- `openalex` requires the AWS CLI on `PATH`; all other downloaders use only the
  standard library plus the declared dependencies.
- The contacted hosts are fixed and small (see Data sources). Network egress is
  expected only to those domains.
- Targets Python 3.13.
