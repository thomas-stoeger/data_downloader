# data_downloader

A tool for downloading and managing versioned scientific datasets. Datasets are tracked locally with version history so you can tell when data was fetched and whether a newer version is available upstream.

## Setup

**1. Install prerequisites:**

- [uv](https://docs.astral.sh/uv/) — Python package manager
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) — required for the `openalex` dataset only

On macOS both are available via Homebrew:

```bash
brew install uv awscli
```

**2. Create a Python 3.13 virtual environment and install:**

```bash
uv venv --python 3.13 .venv
uv pip install -e .
```

**3. Set the download location:**

```bash
.venv/bin/dl configure /path/to/your/data
```

This is saved to `~/.config/data_downloader/config.toml` and persists across sessions. You can also set `DL_DATA_ROOT` as an environment variable instead.

## Usage

### Browser UI

```bash
.venv/bin/dl serve
```

Opens at http://127.0.0.1:8000 — shows all datasets, their download status, and lets you trigger downloads with a button.

### Command line

```bash
# List all datasets and local version info
.venv/bin/dl list

# Check whether newer versions are available (no download)
.venv/bin/dl check

# Download a dataset
.venv/bin/dl fetch ncbi_gene2pubmed

# Re-download even if already up-to-date
.venv/bin/dl fetch ncbi_gene2pubmed --force
```

## Available datasets

| Name | Description |
|---|---|
| `ncbi_gene_info` | NCBI gene_info — gene symbols, names, chromosomal location, and identifiers |
| `ncbi_gene2pubmed` | NCBI gene2pubmed — links between genes and PubMed articles |
| `ncbi_gene2go` | NCBI gene2go — gene-to-GO term associations |
| `ncbi_gene2refseq` | NCBI gene2refseq — gene-to-RefSeq accession mappings |
| `ncbi_gene_history` | NCBI gene_history — discontinued and merged gene ID history |
| `ncbi_gene2ensembl` | NCBI gene2ensembl — gene-to-Ensembl ID mappings |
| `ncbi_gene2accession` | NCBI gene2accession — gene-to-nucleotide/protein accession mappings |
| `ncbi_generifs` | NCBI GeneRIFs — gene functional annotation summaries |
| `ncbi_taxdump` | NCBI Taxonomy dump — full taxonomy nodes, names, and lineage |
| `icite` | iCite NIH Open Citation Collection (monthly snapshots) |
| `harmonizome` | Harmonizome gene-to-attribute matrices (~150 datasets) |
| `openalex` | OpenAlex full snapshot — all entity types, excludes legacy-data (~600 GB, requires AWS CLI) |
| `nih_exporter` | NIH ExPORTER bulk data — projects, abstracts, publications, link tables, patents, clinical studies |

## Resuming interrupted downloads

Large downloads (e.g. `openalex`) can be interrupted with Ctrl-C and resumed by re-running the same `dl fetch` command. Already-downloaded files are skipped automatically.

```bash
# Start (or resume) openalex
.venv/bin/dl fetch openalex
```

For `openalex` specifically this delegates to `aws s3 sync`, which compares local and remote file sizes and only transfers what is missing.

## Data layout

Each dataset is stored under `<data-root>/<dataset-name>/<version>/`. Version strings are derived from the remote source (modification date, release date, etc.).

```
/path/to/your/data/
  ncbi_gene2pubmed/
    2025-11-01/
      gene2pubmed.gz
  icite/
    2026-03/
      icite_metadata.zip
      open_citation_collection.zip
  harmonizome/
    2020-08-03/
      achilles__gene_attribute_matrix.txt.gz
      ...
  openalex/
    2026-03-31/
      LICENSE.txt
      data/works/...
      data/authors/...
      ...
```
