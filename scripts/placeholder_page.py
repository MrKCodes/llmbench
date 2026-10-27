"""Write a self-explaining placeholder page for GitHub Pages.

Used by CI when no benchmark sessions are committed yet, so the site exists and
tells the reader how to populate it instead of 404-ing. Standard library only.

Usage: python scripts/placeholder_page.py <output.html>
"""

import sys

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>llmbench</title>
<style>
  body { font: 16px/1.6 system-ui, -apple-system, sans-serif;
         max-width: 42rem; margin: 4rem auto; padding: 0 1rem; color: #1a1a1a; }
  code, pre { background: #f0f0f0; border-radius: 4px; }
  code { padding: .1em .35em; }
  pre { padding: 1rem; overflow-x: auto; }
  h1 { margin-bottom: .2rem; }
  .muted { color: #666; }
</style>
</head>
<body>
  <h1>llmbench</h1>
  <p class="muted">Local LLM inference benchmarks across machines and backends.</p>
  <p>No benchmark results are published yet. On a machine with a backend
     running, run a sweep and commit its session JSON:</p>
  <pre><code>llmbench run
git add results/ &amp;&amp; git commit -m "benchmark run" &amp;&amp; git push</code></pre>
  <p>This page rebuilds automatically once a result lands under
     <code>results/</code>.</p>
</body>
</html>
"""


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: placeholder_page.py <output.html>", file=sys.stderr)
        return 2
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        fh.write(HTML)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
