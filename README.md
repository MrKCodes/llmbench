# llmbench

Benchmark local LLM inference across machines and backends, then generate a
side-by-side Markdown report. Built to compare real hardware — e.g. an Apple
M4 Mac Mini (unified memory) against a discrete-GPU Windows/Linux laptop — on
the same models and prompts.

- **Backends:** [LM Studio](https://lmstudio.ai) (OpenAI-compatible server,
  enabled by default — its GGUF files copy cleanly between machines) and
  [Ollama](https://ollama.com).
- **Metrics:** generation throughput (tokens/sec), time-to-first-token (TTFT),
  total wall time, token counts — reported as median / mean / p95 / stdev over
  N runs (streaming, so TTFT is real).
- **Hardware aware:** auto-detects OS, CPU, RAM and GPU. No machine name to
  type — each box gets a stable folder from its own hardware, and re-runs on it
  collect there as sessions.
- **Config driven:** one YAML file describes the whole sweep.
- **Two outputs:** a Markdown `report` for a quick read or a PR, and an
  interactive offline HTML `dashboard` with charts, comparison tables, and a
  per-machine deep dive across re-runs.

## Why

Different architectures win at different sizes. Unified-memory Macs are smooth
up to their allocation cap; discrete-GPU machines are faster in VRAM and fall
back to system RAM for large models. This tool measures that instead of
guessing.

## Install

Requires Python 3.9+. The install splits by what you're doing:

```bash
git clone <your-repo-url> llmbench
cd llmbench
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate

# On a machine that will RUN benchmarks (pulls in requests + pyyaml):
pip install -e '.[run]'          # or: pip install -r requirements.txt

# To ONLY build reports/dashboards (e.g. CI / GitHub Pages): no deps at all
pip install -e .
```

`llmbench report` and `llmbench dashboard` read result JSON and render with the
standard library alone — **no third-party dependencies**. Only `llmbench run`
needs `requests` (backends) and `pyyaml` (config), which is why they live in the
`[run]` extra. This keeps a CI reporting job dependency-free and fast.

## Quick start

1. Start a backend on each machine:
   - **LM Studio:** load a model, open the **Local Server** tab, **Start
     Server** on port `1234`. The same GGUF file works on every machine.
   - **Ollama (optional):** install, `ollama pull qwen2.5-coder:7b`, server on
     `http://localhost:11434`. Enable it in the config to also sweep it.
2. Copy the sample config, then edit your copy (the sample stays pristine, and
   your working copy is git-ignored so sweeps never overwrite it):
   ```bash
   cp config/bench.sample.yaml config/bench.yaml
   ```
   Set the models you actually loaded in `config/bench.yaml`.
3. Run the sweep on each machine — no machine name needed:
   ```bash
   llmbench run
   # or without installing:  python -m llmbench run
   ```
   This writes `results/<hardware-slug>/session-<timestamp>.json`, e.g.
   `results/darwin-arm64-applem4-mac-mini/session-20261027-120000.json`. Run it
   again on the same box and a second `session-*.json` lands in the same folder.
   Pass `--machine "Studio Mac"` only if you want a friendlier label in the
   report; the folder is named from hardware either way.
4. Gather every machine's folder under one `results/` and build the outputs:
   ```bash
   llmbench report    --results results/ --out report.md        # Markdown
   llmbench dashboard --results results/ --out dashboard.html    # open in browser
   ```

## Multiple machines and re-runs

- **Different machines** each get their own folder automatically — just copy the
  folders together before reporting.
- **Re-runs on the same machine** accumulate as `session-*.json` in that
  machine's folder. The report and dashboard *pool* every run across a machine's
  sessions for the comparison numbers (more runs = bigger sample), and the
  deep dive shows each session separately so you can see run-to-run consistency.

## Model formats (GGUF vs MLX)

A cell is a `(backend, model, format, prompt)` combination. `format` is part of
the identity, so the same model benchmarked in two formats stays **two separate
cells that never pool together** — the report and dashboard show a `Format`
column and never average a GGUF run into an MLX one.

Give a model one id per backend (format defaults to `gguf`), or a list of
format variants to sweep several:

```yaml
models:
  - label: qwen2.5-coder-7b
    size: "7B"
    ollama: qwen2.5-coder:7b            # bare string -> format: gguf
    lmstudio:
      - { format: gguf, id: qwen2.5-coder-7b-instruct }
      - { format: mlx,  id: mlx-community/Qwen2.5-Coder-7B-Instruct-4bit }
```

On an Apple Silicon Mac, LM Studio serves both GGUF and **MLX** (Apple's
framework, typically 20–40% faster on Macs) on the same port — so you can
measure the Mac's true ceiling while still comparing GGUF against other
machines. MLX is Mac-only; other boxes simply skip the `mlx` variant.

Two comparisons, don't mix them:
- **Same format across machines** (gguf vs gguf) → honest hardware A/B.
- **Same machine across formats** (gguf vs mlx) → that Mac's format ceiling.

The shipped [`config/bench.sample.yaml`](config/bench.sample.yaml) is a
ready-made size ladder
(4B → 32B, plus a 30B MoE) with GGUF+MLX variants. Confirm each id against what
you actually downloaded — a mistyped id just produces an error row, it won't
crash the sweep.

## Remote machines

To benchmark a machine you only reach over the network (e.g. an old laptop used
as a thin client), run `llmbench run` *on that machine* against its own local
backend and copy its results folder back. Pointing a backend's `base_url` at a
remote host also works, but then TTFT and total time include network latency.

## Results JSON

One `session-*.json` per `llmbench run`, under a per-machine folder.
Self-describing: it carries the hardware profile (including the stable `slug`),
the config used, every raw run, and the aggregated stats. **This JSON is the
lightweight source of truth** — the Markdown report and HTML dashboard are just
rendered views of it, so you commit the JSON, not the HTML.

## Publishing to GitHub Pages (CI)

[`.github/workflows/pages.yml`](.github/workflows/pages.yml) consolidates every
committed session into one dashboard and publishes it. The flow:

```
run on each machine ─▶ commit results/<slug>/session-*.json ─▶ CI rebuilds
    the combined dashboard from ALL sessions ─▶ GitHub Pages
```

The generated `report.md` and `*.html` are git-ignored — only the small JSON is
committed, and CI regenerates the HTML on every push (stdlib-only, no deps).
To enable it once: repo **Settings → Pages → Source → GitHub Actions**. Then any
push that touches `results/**/*.json` republishes the site; you can also trigger
it manually from the Actions tab.

## Layout

```
.github/workflows/pages.yml  CI: consolidate sessions -> dashboard -> Pages
config/bench.sample.yaml  tracked template; copy to config/bench.yaml and edit
llmbench/
  cli.py               `run`, `report`, `dashboard` commands
  config.py            load + validate YAML
  hardware.py          OS / CPU / RAM / GPU detection + stable machine slug
  runner.py            warmup + measured runs, aggregation
  stats.py             median / mean / p95 / stdev
  loader.py            load sessions, group per machine, pool re-runs
  report.py            combined Markdown report
  dashboard.py         self-contained offline HTML dashboard (no CDN)
  backends/
    base.py            RunMetrics + backend protocol
    ollama.py          /api/generate (streaming)
    lmstudio.py        /v1/chat/completions (streaming)
results/
  <hardware-slug>/     one folder per machine
    session-*.json     one file per run; re-runs accumulate here
```

## License

MIT. See [LICENSE](LICENSE).
