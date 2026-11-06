"""First line of every native SQL card: `-- mb:repo=<repo> card=<path>`, so warehouse query
history shows which repo and card ran a query. Written into the files, so Metabase matches git."""

import copy
from pathlib import Path

from metabase_sync.format import representations
from metabase_sync.format.representations.card import Card
from metabase_sync.store import yamlio
from metabase_sync.store.tree import MAIN, read_tree

TAG = "-- mb:repo="


def tagged(sql: str, line: str) -> str:
    if sql.startswith(TAG):
        sql = sql.partition("\n")[2]
    return f"{line}\n{sql}"


def tag(root: Path, repo: str, check: bool = False) -> list[str]:
    changed = []
    for e in read_tree(root).entities.values():
        if not isinstance(e.data, Card) or e.path is None:
            continue
        query = e.data.dataset_query
        new = copy.deepcopy(query)
        line = f"{TAG}{repo} card={e.path.relative_to(MAIN).with_suffix('').as_posix()}"
        for stage in new.get("stages", []):
            if isinstance(stage, dict) and isinstance(stage.get("native"), str):
                stage["native"] = tagged(stage["native"], line)
        if new != query:
            changed.append(str(e.path))
            if not check:
                data = e.data.model_copy(update={"dataset_query": new})
                yamlio.write_text_atomic(
                    root / e.path, yamlio.dumps(representations.write(data))
                )
    return changed
