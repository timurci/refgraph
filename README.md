# refgraph

Build a citation graph from a seed paper: its **references** (papers it cites) and
**citations** (papers citing it), expanded 1–2 levels in each direction.

Data comes from [Semantic Scholar](https://api.semanticscholar.org) and
[OpenAlex](https://api.openalex.org), merged into a single normalized graph document.

## Install

```bash
uv sync --group dev       # or: pip install -e .
```

## CLI

```bash
refgraph build "attention is all you need" \
  --ref-depth 1 --cite-depth 2 \
  --top-cites 30 --year-min 2018 \
  -o graph.json
```

Input can be a DOI (`10.18653/v1/N18-3011`), an arXiv ID (`arXiv:1706.03762`),
or a free-text title. Ambiguous title searches prompt for candidate confirmation.

Options:

| Flag | Meaning | Default |
|---|---|---|
| `--ref-depth` | How many levels of references to follow (refs of refs) | 1 |
| `--cite-depth` | How many levels of citations to follow (citers of citers) | 1 |
| `--top-cites` | Max citers kept per paper (ranked by citation count) | 30 |
| `--year-min` / `--year-max` | Optional year filter on citing papers | — |
| `--sources` | Data sources to use (`semantic_scholar`, `openalex`) | both |
| `--cache` | SQLite cache path | `.refgraph-cache.sqlite3` |
| `--no-cache` | Disable the cache | — |
| `-o` | Output file (defaults to stdout) | — |

Environment variables:

- `S2_API_KEY` — optional Semantic Scholar API key (raises rate limits).
- `OPENALEX_MAILTO` — contact email for the OpenAlex polite pool.

## Library

```python
import asyncio
from refgraph import build_graph, resolve

candidates = asyncio.run(resolve("attention is all you need"))
paper = candidates[0]  # delivery layer picks; see CLI for an interactive prompt

doc = asyncio.run(build_graph(paper, ref_depth=1, cite_depth=1, top_cites=30))
print(doc.model_dump_json(indent=2))
```

### Graph document

```jsonc
{
  "schema_version": 1,
  "seed": { /* Paper */ },
  "config": { /* traversal config used */ },
  "nodes": [
    {
      "paper": { /* id, title, year, venue, citation_count, external_ids */ },
      "provenance": {
        "directions": ["ref"],          // how the node was discovered
        "min_depth": 1,
        "paths": [["seed-id", "node-id"]]  // discoverer paths from the seed
      }
    }
  ],
  "edges": [ { "source": "...", "target": "...", "relation": "cites" } ],
  "warnings": [ { "kind": "...", "detail": "..." } ]
}
```

- **Pure-direction traversal:** ref expansion only follows references, cite
  expansion only follows citations. `ref_depth` and `cite_depth` are independent.
- **Determinism:** the same seed and config produce a byte-identical document.
  Citers are ranked by citation count (tiebreak: paper ID) after optional year
  filtering, then capped at `top_cites`.
- **Best-effort:** fetch failures are retried (3×, backoff), then recorded in
  `warnings`; the document always completes.
- **Identity:** nodes carry `external_ids` (DOI, arXiv, S2, OpenAlex, PMID);
  the same paper from both sources is merged (DOI > arXiv > title+year).

## Tests

```bash
pytest
```
