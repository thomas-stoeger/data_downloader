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

45 datasets are registered in
[src/data_downloader/registry/datasets.toml](src/data_downloader/registry/datasets.toml),
which is the source of truth; `dl list` prints the same set with local version
info. The License column summarizes each dataset's `[<name>.license]` entry
("PD" is US Government public domain; "none stated" means upstream names no
license). Check the registry notes before reuse, since many aggregators pass
through the terms of their constituent sources.

### Genes, identifiers, and genomes

| Name | Contents | License |
|---|---|---|
| `ncbi_gene_info` | NCBI gene_info: gene symbols, names, chromosomal location, and identifiers | PD |
| `ncbi_gene2pubmed` | NCBI gene2pubmed: links between genes and PubMed articles | PD |
| `ncbi_gene2go` | NCBI gene2go: gene-to-GO term associations | PD |
| `ncbi_gene2accession` | NCBI gene2accession: gene-to-RefSeq/GenBank nucleotide and protein accessions | PD |
| `ncbi_gene2ensembl` | NCBI gene2ensembl: Entrez Gene to Ensembl gene, transcript, and protein IDs | PD |
| `ncbi_gene_history` | NCBI gene_history: discontinued and merged gene ID history | PD |
| `ncbi_generifs` | NCBI GeneRIFs: gene-to-literature functional annotation summaries | PD |
| `ncbi_taxdump` | NCBI Taxonomy dump: taxonomy nodes, names, and lineage | PD |
| `hgnc` | HGNC complete set: approved human gene symbols, names, locus types, and cross-references (monthly archive) | unrestricted |
| `ensembl_tsv` | Ensembl per-species TSV cross-references (Ensembl IDs to Entrez, RefSeq, UniProt, ENA) plus karyotype | unrestricted |
| `ensembl_gtf` | Ensembl per-species GTF gene annotation (gene/transcript/exon models) | unrestricted |
| `omim` | OMIM `mim2gene.txt`: MIM number to entry type, Entrez Gene, HGNC symbol, Ensembl gene (gated genemap2/morbidmap not fetched) | OMIM agreement |
| `alliancegenome` | Alliance of Genome Resources per-species TSV bulk files (orthology, disease, expression, interactions, gene descriptions, variants, cross-references) | CC-BY-4.0 |

### Proteins, domains, and expression

| Name | Contents | License |
|---|---|---|
| `uniprot_fasta` | UniProt reviewed Swiss-Prot protein sequences (FASTA) | CC-BY-4.0 |
| `uniprot_idmapping` | UniProt ID mapping: UniProtKB accession to external database IDs (`idmapping.dat.gz`) | CC-BY-4.0 |
| `interpro` | InterPro protein-to-entry matches (`protein2ipr.dat.gz`), entry hierarchy, entry metadata, and lookups | CC0-1.0 |
| `proteinatlas` | Human Protein Atlas per-gene summary of protein/RNA expression and localization | CC-BY-4.0 |
| `gtex` | GTEx open-access bulk RNA-seq gene-level matrices (median TPM, reads, TPM); release pinned by config (default v10) | GTEx terms |
| `unknome` | Unknome protein and cluster summaries ranking proteins by how little is known about them | CC-BY-4.0 |

### Interactions, pathways, and associations

| Name | Contents | License |
|---|---|---|
| `biogrid_interactions` | BioGRID protein, genetic, and chemical interactions (BIOGRID-ALL TAB3, Latest-Release); not BioGRID ORCS | MIT |
| `intact` | IntAct curated binary molecular interactions (PSI-MITAB) plus column README | CC-BY-4.0 |
| `reactome` | Reactome gene/protein (Entrez, UniProt, Ensembl) to pathway mappings, pathway names, and hierarchy | CC0-1.0 |
| `gwas_catalog` | NHGRI-EBI GWAS Catalog: EFO-annotated variant-trait associations, studies, ancestry, and trait mappings | EMBL-EBI terms |
| `opentargets` | Open Targets Platform release Parquet datasets plus `croissant.json` schema and per-dataset descriptions | CC0-1.0 (compilation) |
| `harmonizome` | Harmonizome distribution files for ~150 datasets (except similarity matrices) plus JSON metadata sidecars | CC-BY-NC-SA-4.0 |

### Ontologies and vocabularies

| Name | Contents | License |
|---|---|---|
| `geneontology_basic` | Gene Ontology `go-basic.obo` (filtered, cycle-free) | CC-BY-4.0 |
| `disease_ontology` | Human Disease Ontology `doid.obo` | CC0-1.0 |
| `ols` | Ontology Lookup Service snapshot of all loaded ontologies as linked JSON (~150 GB uncompressed) | per ontology |
| `mesh` | MeSH XML: descriptors (with tree numbers), qualifiers, pharmacological actions, supplementary concept records | PD (NLM terms) |

### Literature and full text

| Name | Contents | License |
|---|---|---|
| `ncbi_pubmed` | PubMed XML annual baseline plus updatefiles, MD5-verified (~45 GB) | NLM terms |
| `ncbi_pubtator3` | PubTator3 entity and relation annotation tables plus BioCXML archives (~225 GB) | PD |
| `pmc_open_access` | PMC Open Access Subset (`oa_bulk`): full-text plain text and JATS XML, baseline plus daily incrementals | per article |
| `pmc_author_manuscripts` | PMC Author Manuscript Dataset: full-text plain text and JATS XML, baseline plus daily incrementals | per article |
| `pmc_historical_ocr` | PMC Historical OCR collection: OCR text of historical medical journals (~95 titles) | per article |
| `ncbi_nlmcatalog_reportedmedline` | NLM Catalog journals reporting MEDLINE indexing status (full XML via E-utilities) | NLM terms |
| `icite` | iCite NIH Open Citation Collection (monthly snapshots) | CC-BY-4.0 |
| `openalex` | OpenAlex snapshot, all entity types under `data/` (~600 GB, requires AWS CLI) | CC0-1.0 |

The three `pmc_*` datasets read legacy FTP paths that NLM plans to remove in
August 2026.

### Research integrity

| Name | Contents | License |
|---|---|---|
| `retractionwatch_retractiondatabase` | Retraction Watch retraction database: Crossref's daily `retraction_watch.csv` plus README | none stated |
| `retractionwatch_hijackedjournals` | Retraction Watch Hijacked Journal Checker (Google Sheet exported as CSV) | none stated |
| `predatory_publishers` | Predatory publishers list (Google Sheet as CSV; versioned by capture date plus content hash) | none stated |
| `predatory_journals` | Predatory journals list (Google Sheet as CSV; versioned by capture date plus content hash) | none stated |

### Researchers, organizations, and funding

| Name | Contents | License |
|---|---|---|
| `orcid` | ORCID Public Data File: annual record summaries (identity, affiliations, employment, works) | CC0-1.0 |
| `ror` | Research Organization Registry data dump (JSON + CSV, schema v1 and v2) | CC0-1.0 |
| `nih_exporter` | NIH ExPORTER bulk data: projects, abstracts, publications, link tables, patents, clinical studies | PD |
| `nsf_awards` | NSF Award Search per-fiscal-year award archives plus the pre-1976 Historical set | PD |

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
      achilles__gene_attribute_edges.txt.gz
      achilles__gene_set_library_up_crisp.gmt.gz
      achilles__metadata.json
      ...
  openalex/
    2026-03-31/
      LICENSE.txt
      data/works/...
      data/authors/...
      ...
  ncbi_pubmed/
    2026/
      baseline/
        pubmed26n0001.xml.gz
        pubmed26n0001.xml.gz.md5
        ...
      updatefiles/
        pubmed26n1335.xml.gz
        pubmed26n1335.xml.gz.md5
        ...
```
