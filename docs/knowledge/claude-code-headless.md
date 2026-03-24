# Claude Code Headless Mode

What we've learned about using `claude --print` for automated extraction.

## Key flags

- `--print` - headless mode, returns result and exits
- `--output-format json` - wraps response in an envelope with `result`, `structured_output`, `usage`, `total_cost_usd` etc.
- `--json-schema` - constrains structured output to match a schema (returned in `structured_output` field of the envelope)
- `--allowedTools Read` - limits which tools Claude Code can use
- `--add-dir <path>` - gives tool access to a directory
- `--no-session-persistence` - don't save session to disk
- `--model <name>` - select model (sonnet, opus, haiku)

## Envelope format

With `--output-format json`, stdout is a JSON object:

```json
{
  "type": "result",
  "subtype": "success",
  "result": "the text response",
  "structured_output": { ... },
  "duration_ms": 163301,
  "total_cost_usd": 0.33,
  "num_turns": 10,
  "usage": { "input_tokens": ..., "output_tokens": ... },
  "modelUsage": { ... },
  "permission_denials": [ ... ]
}
```

## Gotchas

- Without `--output-format json`, the `--json-schema` constraint works but the text response is prose, not JSON. The structured output only appears in the envelope.
- Claude Code tries Bash tools by default. If you only allow Read, it will attempt Bash several times (each denied), wasting turns and tokens. The prompt should explicitly say "use Read, do not use Bash."
- The `result` field contains a prose summary even when structured output is returned. This wastes tokens. Prompting "return ONLY the markdown" reduces but doesn't eliminate it.
- Claude sometimes wraps output in ` ```markdown ``` ` code fences. Strip these.
- Temp files in `/tmp` work as input if `--add-dir /tmp` is set.
- Running inside a Docker container requires mounting `~/.local/bin/claude` and `~/.claude` into the container.

## Container setup

```yaml
# cm.yaml volumes
volumes:
  - ~/.local/bin/claude:/usr/local/bin/claude:ro
  - ~/.claude:~/.claude
```

The `~` expansion requires container-magic 4.0.0+ with the volume variable expansion feature.

## Cost observations

Claude Code adds overhead per call due to multi-turn tool use. Even with optimised prompts, a simple 3-page PDF costs 6 turns. Direct Anthropic API calls would be 1 turn.
