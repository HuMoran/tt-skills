# tt-skills

Personal Claude Code skill collection by [@HuMoran](https://github.com/HuMoran).

A curated set of broadly useful skills I extract from real engineering work and reuse across projects. Distributed as a Claude Code plugin marketplace.

## Skills

| Name | Description |
|---|---|
| [`oscilloscope`](skills/oscilloscope/SKILL.md) | Drive Keysight DSO5000-series and RIGOL DS1000Z-series benchtop scopes over VISA / USBTMC. Auto-detects vendor by `*IDN?`; bundles a 800-line self-contained `scope.py` with screenshots, CSV capture, triggering, deep memory, mask test, FFT, USB pipe-stall recovery. |

More to come.

## Install

### Option A — Claude Code plugin (recommended for others)

```text
/plugin marketplace add HuMoran/tt-skills
/plugin install tt-skills@tt-skills
```

Claude Code clones the repo into its plugin cache and registers all skills under the `tt-skills:` namespace (e.g. `tt-skills:oscilloscope`).

### Option B — Manual clone + symlink (recommended for the author / dev workflow)

```bash
# Clone the source-of-truth
git clone https://github.com/HuMoran/tt-skills.git ~/Claude/tt-skills

# Expose each skill to Claude Code via symlink
ln -s ~/Claude/tt-skills/skills/oscilloscope ~/.claude/skills/oscilloscope
```

Changes to files in `~/Claude/tt-skills/` are picked up by Claude Code immediately — no plugin update needed.

## Per-skill bootstrap

Some skills (notably `oscilloscope`) ship a `setup.sh` for runtime deps. After install:

```bash
bash ~/.claude/skills/oscilloscope/scripts/setup.sh    # creates .venv/
```

The `.venv/` is gitignored — every clone re-creates it.

## License

[MIT](LICENSE)
