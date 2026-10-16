# imozuru provenance and license

The source-chasing method and this guidance are adapted from
[MaryCache/imozuru](https://github.com/MaryCache/imozuru), inspected at commit
`613f260d8f26c582ef5e9a0af981e80adfc285eb` (2026-10-15).
Reviewed: README.md; skills/imozuru/SKILL.md; references/theory.md;
scripts/chain.py, fetch.py, report.py and watch.py; LICENSE.

Kiokuko implements its own bounded Hermes orchestrator and search middleware.
Upstream scripts are not vendored or executed. Independent search routes,
question-based coverage, grounded citation/author/term chasing and exact excerpt
checks are retained. Upstream's 70% saturation heuristic is replaced by an
explicit no-new-grounded-work stop, plus hard budgets. Monitoring and report HTML
are outside this integration. No automatic user-memory storage is added.

The following upstream license applies to adapted portions in this Skill and
`research_trail.py`:

MIT License

Copyright (c) 2026 MaryCache

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
