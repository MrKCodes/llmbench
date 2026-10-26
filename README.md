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

```bash
git clone <your-repo-url> llmbench
cd llmbench
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# optional, gives you the `llmbench` command:
pip install -e .
```

Requires Python 3.9+. Only two dependencies: `requests` and `pyyaml`.

## Quick start

1. Start a backend on each machine:
   - **LM Studio:** load a model, open the **Local Server** tab, **Start
     Server** on port `1234`. The same GGUF file works on every machine.
   - **Ollama (optional):** install, `ollama pull qwen2.5-coder:7b`, server on
     `http://localhost:11434`. Enable it in the config to also sweep it.
2. Edit [`config/bench.yaml`](config/bench.yaml) — set the models you loaded.
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

## Remote machines

To benchmark a machine you only reach over the network (e.g. an old laptop used
as a thin client), run `llmbench run` *on that machine* against its own local
backend and copy its results folder back. Pointing a backend's `base_url` at a
remote host also works, but then TTFT and total time include network latency.

## Results JSON

One `session-*.json` per `llmbench run`, under a per-machine folder.
Self-describing: it carries the hardware profile (including the stable `slug`),
the config used, every raw run, and the aggregated stats. Safe to commit so the
report and dashboard can be regenerated later.

## Layout

```
config/bench.yaml      the sweep definition
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
