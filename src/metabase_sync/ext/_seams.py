"""The only places ext reaches into upstream internals. Upstream changes here should fail tests/ext."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from metabase_sync.metabase import index


@contextmanager
def personal(roots: set[str]) -> Iterator[None]:
    """Let fetch_index list other users' personal collections and manage the subtrees of `roots`
    even when they sit inside a personal collection (upstream skips personal collections)."""
    manageable, listings = index.manageable, index.LISTINGS

    def patched(cols: list[index.RemoteItem]) -> dict[int, list[index.RemoteItem]]:
        chains, by_id = manageable(cols), {c.id: c for c in cols}
        for c in cols:
            chain = [by_id[a] for a in index.location_ids(c.location) if a in by_id] + [
                c
            ]
            if any(link.entity_id in roots for link in chain):
                chains[c.id] = chain
        return chains

    index.manageable = patched
    index.LISTINGS = {
        k: (
            p,
            {**q, "exclude_other_user_collections": "false"}
            if "exclude_other_user_collections" in q
            else q,
        )
        for k, (p, q) in listings.items()
    }
    try:
        yield
    finally:
        index.manageable, index.LISTINGS = manageable, listings
