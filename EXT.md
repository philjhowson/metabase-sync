# Fork additions

Built on [metabase-sync](https://github.com/novucs/metabase-sync) by [novucs](https://github.com/novucs)
(MIT, see `LICENSE`). Everything upstream is his work; this fork adds `src/metabase_sync/ext/`.
Core files are untouched except one line in `pyproject.toml` (the `mbs` script).

`mbs` = `metabase-sync` + `preview apply|export|destroy` + `tag`.

| File | What |
|---|---|
| `ext/preview.py` | Previews in the owner's personal collection (owner and admins only); items matched to the files by path |
| `ext/sqltag.py` | `-- mb:repo=<repo> card=<path>` first line on native SQL cards |
| `ext/_seams.py` | The only reach into upstream internals (personal-collection visibility) |

## Taking an upstream release

```
git fetch upstream --tags
git rebase vX.Y.Z main
uv sync
uv run pytest tests/ext
uv run pytest -m integration -n 0 tests/integration/test_ext_preview.py
git push --force-with-lease origin main
git tag vX.Y.Z-ext.1 && git push origin vX.Y.Z-ext.1
```

## Testing a Metabase upgrade

```
METABASE_VERSION=v0.65.3 uv run pytest -m integration -n 0 tests/integration/test_ext_preview.py
```
