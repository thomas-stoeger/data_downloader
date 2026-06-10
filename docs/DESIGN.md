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
| `ftp` | NCBI gene_info, gene2pubmed, gene2go, gene2accession, gene2ensembl, gene_history, GeneRIFs, taxdump | `ftp.ncbi.nlm.nih.gov` | Anonymous FTP |
| `pubmed` | NCBI PubMed baseline + updatefiles | `ftp.ncbi.nlm.nih.gov` | Anonymous FTP |
| `pubtator3` | PubTator3 entity/relation annotation tables (optionally BioCXML archives) | `ftp.ncbi.nlm.nih.gov` | Anonymous FTP |
| `nlmcatalog` | NLM Catalog records matching a configurable Entrez search term (e.g. `reportedmedline`) | `eutils.ncbi.nlm.nih.gov` | HTTPS (E-utilities esearch + efetch) |
| `mesh` | MeSH (Medical Subject Headings) XML record sets: descriptors (carry the tree numbers for parent/ancestor lookups), qualifiers, pharmacological actions, supplementary concept records | `nlmpubs.nlm.nih.gov` | HTTPS |
| `biogrid` | BioGRID interaction data — `biogrid_interactions` (the stable Latest-Release TAB3 archive, default BIOGRID-ALL; the interaction database, not BioGRID ORCS) | `downloads.thebiogrid.org` for bytes; `thebiogrid.org` home page for the release number | HTTPS |
| `omim` | OMIM `mim2gene.txt` cross-reference (the only openly served OMIM file) | `omim.org` | HTTPS |
| `nsf_awards` | NSF per-fiscal-year bulk award archives (and the Historical set) via the Award Search list-files API and its pre-signed S3 URLs | `www.research.gov` for the listing; `dis-prod-awardsearch.s3.amazonaws.com` for bytes | HTTPS |
| `hgnc` | HGNC complete set, newest monthly archive snapshot (TSV) from the `public-download-files` GCS bucket | `storage.googleapis.com` | HTTPS |
| `reactome` | Reactome release files (gene/protein-to-pathway mappings, pathway list, hierarchy relations) from `download/current/` | `reactome.org` | HTTPS |
| `zenodo` | Research Organization Registry (ROR) data dump (latest version of a Zenodo concept record) | `zenodo.org` | HTTPS |
| `figshare` | iCite / NIH Open Citation Collection; ORCID Public Data File | `api.figshare.com` for metadata; file bytes from Figshare download URLs (`ndownloader.figshare.com`, may redirect to AWS S3) | HTTPS |
| `harmonizome` | Harmonizome (~150 datasets) | `maayanlab.cloud` for the dataset index and per-dataset JSON-LD; distribution files from the S3 `contentUrl` each page advertises | HTTPS |
| `nih_exporter` | NIH ExPORTER bulk data | `reporter.nih.gov` | HTTPS |
| `openalex` | OpenAlex full snapshot | `s3://openalex` (public bucket) | `aws s3 sync --no-sign-request` + boto3 unsigned `head_object` |
| `retractionwatch` | Retraction Watch database (Crossref's daily CSV) | `gitlab.com` (`crossref/retraction-watch-data`): `/api/v4` commits+tree for versioning, `/-/raw/` for bytes | HTTPS |
| `google_sheet` | Public Google Sheet tabs exported as CSV (e.g. Retraction Watch Hijacked Journal Checker) | `docs.google.com` for the gviz version cell and CSV `export`; bytes from the `googleusercontent.com` host it redirects to | HTTPS |
| `google_sheet_hashed` | Public Google Sheet tabs with no version marker, exported as CSV (predatory publishers, predatory journals) | `docs.google.com` CSV `export`; bytes from the `googleusercontent.com` host it redirects to | HTTPS |
| `opentargets` | Open Targets Platform release datasets (Parquet), plus the field-level schema (`croissant.json`) and the per-dataset Downloads-page descriptions (`downloads.json`) | `ftp.ebi.ac.uk` for data, schema, checksums; `api.platform.opentargets.org` for the descriptions | HTTPS |
| `gtex` | GTEx open-access bulk RNA-seq gene-level expression matrices from the `adult-gtex` GCS bucket (release pinned by config) | `storage.googleapis.com` | HTTPS |
| `gwas_catalog` | NHGRI-EBI GWAS Catalog release files (EFO-annotated associations, studies, ancestry, trait-ontology mappings) from `releases/latest/` | `ftp.ebi.ac.uk` | HTTPS |
| `intact` | IntAct molecular interactions (full PSI-MITAB `intact.zip` plus its README) from `current/psimitab/` | `ftp.ebi.ac.uk` | HTTPS |
| `ols` | Ontology Lookup Service (OLS) dated snapshot of all loaded ontologies as linked JSON (`ontology_jsons_linked.tgz`); other snapshot archives selectable | `ftp.ebi.ac.uk` | HTTPS |
| `unknome` | Unknome (MRC LMB) per-release compressed protein summary and cluster summary TSVs (the full SQLite database is selectable but off by default) | `unknome.mrc-lmb.cam.ac.uk` | HTTPS |
| `proteinatlas` | Human Protein Atlas bulk files (the per-gene summary `proteinatlas.tsv.zip` by default; the JSON and comprehensive XML are selectable) | `www.proteinatlas.org` | HTTPS |
| `uniprot` | UniProt knowledgebase files from a configured subdirectory: protein sequences (`complete/`, e.g. `uniprot_sprot.fasta.gz`) and ID cross-references (`idmapping/`, e.g. `idmapping.dat.gz`) | `ftp.uniprot.org` | HTTPS |
| `interpro` | InterPro release files: per-protein integrated matches (`protein2ipr.dat.gz`), the entry hierarchy (`ParentChildTreeFile.txt`), entry metadata/schema (`interpro.xml.gz`, `interpro.dtd`), and lookups (`entry.list`, `names.dat`, `interpro2go`) | `ftp.ebi.ac.uk` | HTTPS |
| `obo` | OBO-format ontology files (Gene Ontology `go-basic.obo`, Disease Ontology `doid.obo`) | `purl.obolibrary.org` (PURL) redirecting to the release host for the bytes (`current.geneontology.org` for GO; `raw.githubusercontent.com` for DOID) | HTTPS |
| `ensembl_tsv` | Ensembl per-species TSV cross-reference tables (Entrez, RefSeq, UniProt, ENA, karyotype) under `current_tsv`, plus `CHECKSUMS` and `README_*` | `ftp.ensembl.org` | HTTPS |
| `ensembl_gtf` | Ensembl per-species GTF gene-annotation files (the full `.gtf.gz` gene set plus selectable `chr`, `chr_patch_hapl_scaff`, and `abinitio` flavors) under `release-<N>/gtf/`, plus `CHECKSUMS` and `README` | `ftp.ensembl.org` | HTTPS |
| `alliancegenome` | Alliance of Genome Resources per-species TSV bulk files (orthology, disease, expression, interactions, gene descriptions, variant-allele, cross-references) from a release snapshot | `fms.alliancegenome.org` for the release version and snapshot; file bytes from `download.alliancegenome.org` | HTTPS |

Note that `figshare` and `harmonizome` discover their actual byte-download URLs
at runtime from API responses and scraped HTML, `opentargets`, `ensembl_tsv`,
and `ensembl_gtf` read the FTP autoindex to learn the per-folder file names,
`alliancegenome`
reads the FMS release snapshot to learn each file's download URL, path, and MD5,
`mesh` reads the autoindex to learn the production year and the available
per-record-set file names, `ols` reads the autoindex to learn the newest
snapshot timestamp, `unknome` parses its download page for the release
identifiers and takes each file's name from the `Content-Disposition` header,
`proteinatlas` reads the release number and the available file names off
its download page, `uniprot` reads the autoindex to validate file names and
parses `RELEASE.metalink` for per-file MD5s, and `interpro` reads the autoindex
to validate file names and reads each file's `.md5` sidecar where present.
Those URLs and names are upstream data, not configuration (see Invariants).

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

`opentargets` additionally captures, per release, `croissant.json` (the
field-level schema for every dataset), `release_data_integrity.sha1` (the
checksums it verifies Parquet files against), `manifest.json` (provenance), and
`downloads.json` (the per-dataset text descriptions shown on the platform's
Downloads page). These cover the schema and description for every dataset and
are fetched regardless of which datasets' Parquet bytes are selected.

## Licenses

Each dataset may declare its license in the registry under a nested
`[<name>.license]` table. License capture is generic and orchestrated in the
store (not per-downloader), so adding a license is a TOML edit and it applies
uniformly to every dataset. Two things are recorded so the question "what is
this dataset's license and where did it come from" can be answered later:

- `declared_at` — a provenance reference (URL or list) to where the license is
  stated upstream. Recorded verbatim, never fetched.
- `url` — the license document(s) (URL or list) downloaded into the version's
  `<version>/_license/` directory and hashed (SHA-256). This is a byte-exact
  copy of the terms that governed that snapshot.

Optional `spdx` and `note` are recorded as documentation. The handling lives in
[versioning/license.py](../src/data_downloader/versioning/license.py); the
record is stored in each version's manifest entry under a `license` key.

The license is captured **per version**: it is re-fetched into each snapshot
when the data is fetched, mirroring the immutable-snapshot model. Capture is
**best-effort** — a license download failure is recorded as an `error` and
warned about, but never discards an already-verified data version (the one
sanctioned exception to "any failure wipes the version").

**Drift detection** runs at two points. At `fetch`, the freshly captured
license is compared to the previous version's recorded hashes; a difference
sets `changed_from_previous` and prints a prominent `LICENSE CHANGED` warning.
At `check`, the tiny license document is fetched into memory and hashed (no
disk writes) and compared to the last recorded hashes, so a license change is
caught even when the data version is unchanged. `dl check` reports a
`license=<status>` column (`none` / `new` / `ok` / `changed` / `error`), and
`dl license <name>` prints the recorded provenance and the stored copy's path
and hash for the latest (or all) versions.

License document hosts are part of the allowed-domain list in
[CLAUDE.md](../CLAUDE.md) and are added there as datasets gain license URLs.

## Versioning

A version string comes from the remote source, never from the local clock where
avoidable, so that "is this current?" can be answered by comparison:

- `ftp`: file modification time (`MDTM`) as `YYYY-MM-DD`.
- `pubmed`: the four-digit baseline year (e.g. `2026`).
- `pubtator3`: the max `MDTM` across the selected files as `YYYY-MM-DD`
  (NCBI refreshes the snapshot in lockstep each month).
- `gtex`: the configured `version` (default `v10`). GTEx releases are pinned,
  not auto-detected: the `adult-gtex` bucket keeps several version prefixes and
  newer ones can be placeholders without the matrices, so `check` compares the
  configured version to the local one. Files are verified against the MD5 GCS
  returns in the download response (`x-goog-hash: md5=`).
- `nsf_awards`: the export build date (e.g. `2026-06-04`), the `lastModified`
  date of the `timestamp.txt` entry in the Award Search list-files response,
  read without downloading any data file. NSF rebuilds daily, so like
  `nih_exporter` this can report a new version each day.
- `omim`: the `# Generated: YYYY-MM-DD` date in the `mim2gene.txt` header, read
  from the first few kilobytes (a ranged GET) without downloading the whole
  file.
- `biogrid`: the BioGRID release number (e.g. `5.0.257`), parsed from the
  BioGRID home page without downloading any data file. The data is fetched from
  the stable `Latest-Release` file names, which are served chunked without a
  Content-Length, so the size check is skipped and the `.zip` is validated by
  the archive read-through.
- `reactome`: the Reactome release number (e.g. `96`), read from the
  ContentService `data/database/version` endpoint without downloading any data
  file.
- `hgnc`: the date embedded in the newest monthly complete-set file name (e.g.
  `2026-06-02`), found by listing the archive prefix via the GCS JSON API
  without downloading any data file. Files are verified against the bucket's
  published MD5 (the listing's base64 `md5Hash`).
- `zenodo`: the `metadata.version` tag of the latest version of a concept
  record (e.g. ROR's `v2.8`), read from the record JSON (the concept id
  redirects to the newest version) without downloading any file.
- `figshare`: `YYYY-MM` parsed from the latest article title (iCite); a bare
  `YYYY` where the title carries only a year (ORCID's "ORCID Public Data File
  2025"). "Latest" is the article with the newest `published_date` in the
  collection.
- `harmonizome`: the max `Last-Modified` date across datasets' gene-attribute
  matrices, used as a cheap freshness proxy for the whole collection.
- `openalex`: `LastModified` of `RELEASE_NOTES.txt`.
- `opentargets`: the release tag (`YY.MM`, e.g. `26.03`), the newest such
  directory in the FTP listing, read without downloading anything. A `version`
  config key can pin an older release. The Parquet files are verified against
  the release's `release_data_integrity.sha1` checksums when available.
- `ensembl_tsv`: the Ensembl release number (e.g. `115`), read from the
  single-integer `/pub/VERSION` file without downloading any data. `current_tsv`
  is a symlink to that release's `tsv` tree. Files are verified against each
  folder's `CHECKSUMS` (the classic Unix `sum` BSD checksum and block count)
  where present, in addition to the size and gzip checks.
- `ensembl_gtf`: the Ensembl release number (e.g. `116`), read from the same
  `/pub/VERSION` file without downloading any data. GTF has no `current_gtf`
  symlink, so the release-numbered path (`release-<N>/gtf/<species>/`) is built
  from that number. Files are verified against each species folder's
  `CHECKSUMS` (the same BSD `sum` checksum and block count) where present, plus
  the size and gzip checks.
- `alliancegenome`: the Alliance release version (e.g. `9.0.0`), read from the
  FMS `releaseversion/current` endpoint without downloading any data. Each TSV
  file is verified against the `md5Sum` the release snapshot publishes for it,
  in addition to the size and gzip checks.
- `ols`: the newest OLS snapshot timestamp (e.g. `2026_05_22__06_33_19`), the
  max timestamped directory name in the listing, read without downloading
  anything. OLS publishes no checksum sidecars, so the `.tgz` is verified by the
  size check plus the gzip read-through (`.tgz` is recognized as gzip by
  `verify_file`).
- `gwas_catalog`: the release date (e.g. `2026-06-01`), the `Last-Modified`
  date of a stable file in `releases/latest/`, read with a HEAD request without
  downloading any data. `latest/` is a symlink to the newest dated release, so
  this tracks whatever it currently points to (mirrors `ftp` MDTM versioning).
- `intact`: the release date (e.g. `2026-01-14`), the `Last-Modified` date of
  `intact.zip` in `current/psimitab/`, read with a HEAD request without
  downloading any data (mirrors `ftp` MDTM versioning).
- `interpro`: the InterPro release number (e.g. `108.0`), read from
  `release_notes.txt` without downloading any data file. Files that ship a
  `<name>.md5` sidecar are verified against it, plus the size and gzip checks.
- `uniprot`: the UniProt release tag (e.g. `2026_01`), read from
  `complete/reldate.txt` without downloading any data file. The same tag
  versions both the sequence and idmapping datasets (UniProt rebuilds a release
  together). Files are verified against the per-file MD5 in the subdirectory's
  `RELEASE.metalink` where present, plus the size and gzip checks.
- `proteinatlas`: the HPA release number (e.g. `25.1`), parsed from the
  download page without downloading any data file (the bulk URLs are stable and
  unversioned). No checksum sidecars are published, so each file gets the size
  check plus the archive read-through (zip or gzip).
- `unknome`: the newest release date identifier (e.g. `18_Mar_2026`), parsed
  from the download page and selected by parsing each `DD_Mon_YYYY` to a date,
  without downloading any data. No checksum sidecars are published, so each
  gzipped TSV gets the size check plus the gzip read-through.
- `mesh`: the MeSH production year (e.g. `2026`), read from the `descYYYY`
  file names in the `xmlmesh` directory listing without downloading any data.
  MeSH publishes no checksum sidecars, so files get the size check plus the
  gzip archive check (for the gzipped descriptor and SCR files).
- `obo`: the ontology's own `data-version` header, read from the first few
  kilobytes of the file. An embedded `YYYY-MM-DD` is extracted when present, so
  both GO's `releases/2026-05-19` and DOID's `releases/2026-05-30/doid.obo`
  resolve to a date (`2026-05-19`, `2026-05-30`); otherwise the last path
  segment is used. No full download is needed to learn the version.
- `retractionwatch`: `committed_date` of the latest commit on the tracked
  branch as `YYYY-MM-DD` (Crossref's bot commits one rebuilt CSV per working
  day), read from the GitLab commits API without downloading the file.
- `google_sheet`: the last `Month D, YYYY` date in a designated cell (default
  A1, e.g. "...last updated May 17, 2026") as `YYYY-MM-DD`, read via the gviz
  API without exporting the sheet.
- `google_sheet_hashed`: `YYYY-MM-DD (hash)`, where `hash` is the first 12 hex
  characters of the SHA-256 of the exported CSV and the date is the day the
  content was first captured. These sheets (predatory publishers, predatory
  journals) carry no version marker, so this is a sanctioned exception to
  "version comes cheaply from the remote": `latest_version` downloads the CSV
  to hash it, and the date is local. The hash, not the date, is the identity:
  when the hash matches a snapshot already in the manifest, `latest_version`
  returns that existing version string (preserving its capture date) so an
  unchanged sheet reads up-to-date rather than new every day; a changed hash
  yields a fresh version dated today. To read the manifest, the store injects
  the dataset name into the config as `_dataset_name`.
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
