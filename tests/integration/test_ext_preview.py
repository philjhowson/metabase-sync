from __future__ import annotations

import pytest

from metabase_sync.ext import preview as pv
from metabase_sync.store.tree import read_tree
from metabase_sync.sync import commands as workflow

from ._content import build_rich, unique
from .test_roundtrip import scope

pytestmark = pytest.mark.integration


def test_preview_lives_in_the_owners_personal_collection_and_round_trips(
    client, mb, tmp_path
):
    rich = build_rich(mb)
    root = scope(tmp_path / "root", rich.collection["entity_id"])
    workflow.export(client, root)
    owner, name = mb.admin_user()["email"], unique("🚧 rich @ branch")
    personal = pv.owner_collection(client, owner)

    done = pv.apply(client, root, owner, name)
    assert ("create", "collection", name) in done
    top = pv.find(client, name, personal["id"])
    assert top is not None and top["is_personal"]
    assert workflow.plan(client, root)[1].empty

    again = pv.apply(client, root, owner, name)
    assert not [d for d in again if d[0] in ("create", "archive")]

    cols = mb.get("/api/collection?exclude_other_user_collections=false")
    inside = {top["id"]} | {
        c["id"] for c in cols if f"/{top['id']}/" in str(c.get("location"))
    }
    copy = next(
        c
        for c in mb.get("/api/card?f=all")
        if c["name"] == rich.native["name"] and c["collection_id"] in inside
    )
    mb.update_card(copy["id"], {"name": "Edited in preview"})
    pv.export(client, root, owner, name)
    assert (
        read_tree(root).entities[rich.native["entity_id"]].name == "Edited in preview"
    )
    assert mb.get_card(rich.native["id"])["name"] == rich.native["name"]
    ops = workflow.plan(client, root)[1].operations
    assert [(op.action, op.entity_id) for op in ops] == [
        ("update", rich.native["entity_id"])
    ]

    assert pv.destroy(client, name) == [name]
    assert mb.get(f"/api/collection/{top['id']}")["archived"]
