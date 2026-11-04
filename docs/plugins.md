# Plugin modes

A plugin adds your own command to Bartmoss: a prompt, plus checks that send problems back to the model the way lint errors are sent back in `/debug`. Drop a directory into `~/.config/bartmoss/modes/` and it appears as `/name` in the REPL and `bartmoss name` on the command line.

```
~/.config/bartmoss/modes/review/
    prompt.md    required: the mode's system prompt
    mode.json    optional: options (below)
    check.py     optional: your own check
```

The directory name is the command name: lowercase letters, digits, `-` and `_`, and not a built-in command. `bartmoss plugins` lists what loaded; a plugin that fails to load is skipped with a warning saying why.

Two examples are in [`examples/plugins/`](../examples/plugins/): `lint-clean` (rewrite a file so it lints without warnings, checked with Verilator or NVC) and `review` (a Markdown code review whose issues must each cite a real line). Try them with:

```bash
BARTMOSS_MODES_DIR=examples/plugins bartmoss review rtl/fifo.sv
```

## prompt.md

The mode's instructions. Bartmoss prepends the same safety rules every built-in mode uses and a `MODE: <name>` line. With `output_tag` set, it appends "Reply with the result inside <tag> tags." The user's file is sent as `<file name="...">` and context files as `<file ... role="context">`.

## mode.json

| Key | Default | Meaning |
|---|---|---|
| `description` | `""` | shown in `/help` and `bartmoss plugins` |
| `input` | `"file"` | `"file"`: one workspace file plus context files (`/name file ctx...`, `bartmoss name file --with ctx`). `"text"`: free text, like `/ask` |
| `output_tag` | none | extract the result from this tag; without it the whole reply is the result |
| `output_suffix` | none | write the result to `<output dir>/<stem><suffix>`; `{ext}` is the input file's extension (`.clean{ext}`). Without it the result is printed |
| `lint` | `false` | the result is HDL: refuse unsafe constructs (`$system`, file I/O, DPI), then lint it with the input's context files and send errors back |
| `sections` | `[]` | `# Heading` sections the result must contain |
| `model`, `max_tokens`, `effort`, `max_retries` | from `default_model`, 8000, none, 1 | the same per-mode settings the built-in modes take; `modes.<name>` in `bartmoss.json` overrides them |

## check.py

```python
def check(output: str, source: str) -> list:
    """`output` is the extracted result, `source` the input file (or text). Return problems; [] passes."""
```

Problems are sent back to the model and it tries again, up to `max_retries` times. Checks run in order (sections, lint, then `check.py`), and each runs only if the previous ones passed. If `check.py` raises, the run stops with an error rather than retrying.

## Trust

Plugins are loaded only from your own config directory (or `$BARTMOSS_MODES_DIR`), never from the workspace, so cloning a repository can't add code that runs. `check.py` is ordinary Python running with your permissions, like any script you install. The built-in `lint` check goes through the same tool sandbox as the rest of Bartmoss. If your checker runs tools, call them through `bartmoss.sim.tools` to get the same sandbox.
