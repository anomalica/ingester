# Container-magic

What we've learned about using container-magic for this project.

## Mount system

Commands in cm.yaml can declare mounts with `mode: ro` or `mode: rw`. At runtime, provide values as `name=/path`:

```bash
cm run ingest input=/path/to/file.pdf output=/path/to/dir/
```

- Read-only mounts appear at `/mnt/<name>/<basename>`
- Read-write mounts appear at `/mnt/<name>/`

### Prefix bug (fixed in 4.0.0)

Mount prefixes like `prefix: "--input "` were concatenated into a single shell argument (`"--input /mnt/input/file.pdf"`) instead of being split into two (`--input` `/mnt/input/file.pdf`). This broke argparse-based scripts. Fixed by splitting the prefix on whitespace before shell joining.

Currently working around this by auto-detecting mount paths (`/mnt/input`, `/mnt/output`) in the Python script instead of using prefixes.

## Volume variable expansion (4.0.0)

`~` and `$HOME` in volume paths expand to the appropriate home directory on each side of the colon. `$WORKSPACE` expands to the workspace path.

## Command environment variables

Commands support per-command env vars:

```yaml
commands:
  ingest:
    env:
      PYTHONUNBUFFERED: "1"
```

## Running container warning

If a container is already running (e.g. from a previous `cm run`), mounts for a new command can't be added. Use `cm stop` first, or the warning appears. The command still runs but without the new mounts.

## Docker vs Podman

On this system, container-magic uses Docker. The `cm run` development mode mounts the workspace live for hot-reload of code changes.
