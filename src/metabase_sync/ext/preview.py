"""Branch previews: a copy of the scoped collection inside one user's personal collection, so only
that user and admins can see it. Metabase assigns its own entity ids on create, so preview items are
matched to the files by path: previewing again updates them in place, and exporting a preview maps
them back to the real ids. Nothing is written to the repo's state."""

import json
import re
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from metabase_sync.errors import ConfigError, ValidationFailed
from metabase_sync.ext._seams import personal
from metabase_sync.format import representations
from metabase_sync.format.representations import (
    CARD,
    COLLECTION,
    DASHBOARD,
    SUBSCRIPTION,
    Item,
)
from metabase_sync.format.representations.collection import Collection
from metabase_sync.format.representations.dashboard import Dashboard, Dashcard
from metabase_sync.ids import is_entity_id, new_entity_id
from metabase_sync.metabase.client import MetabaseClient
from metabase_sync.metabase.index import fetch_index, rows
from metabase_sync.store import tree
from metabase_sync.store.tree import CONFIG, Changes, Entity, LocalTree, read_tree
from metabase_sync.sync import commands as wf

Done = list[tuple[str, str, str]]


def ids_in(v: Any, out: set[str] | None = None) -> set[str]:
    out = set() if out is None else out
    if isinstance(v, dict):
        for k, w in v.items():
            if k == "entity_id" and is_entity_id(w):
                out.add(w)
            else:
                ids_in(w, out)
    elif isinstance(v, list):
        for w in v:
            ids_in(w, out)
    return out


def _content(t: LocalTree) -> list[Entity]:
    return [e for e in t.entities.values() if e.kind != SUBSCRIPTION]


def paths(t: LocalTree, top: str) -> dict[str, str]:
    """entity id -> canonical path relative to the top collection ('' for the top itself)."""
    laid = tree.layout(_content(t))
    base = laid[top].with_suffix("")
    return {
        eid: "" if eid == top else p.relative_to(base).as_posix()
        for eid, p in laid.items()
        if eid == top or p.is_relative_to(base)
    }


def _tab_position(dc: Dashcard, tabs: dict[str, int]) -> int | None:
    ref = dc.dashboard_tab_id
    return tabs.get(ref[-1]) if isinstance(ref, list) and ref else None


def _signature(data: Item, mapping: dict[str, str]) -> str:
    """Content with names, descriptions, and own ids left out, references mapped to b's ids."""
    d = representations.comparable(representations.swapped(data, mapping))
    if isinstance(data, Dashboard):
        d["tabs"] = sorted(t["name"] for t in d.get("tabs", []))
        d["dashcards"] = sorted(
            json.dumps(
                {
                    k: v
                    for k, v in dc.items()
                    if k not in ("entity_id", "dashboard_tab_id")
                },
                sort_keys=True,
            )
            for dc in d.get("dashcards", [])
        )
    for k in ("name", "description", "entity_id"):
        d.pop(k, None)
    return json.dumps(d, sort_keys=True, default=str)


def _by_content(a: LocalTree, b: LocalTree, found: dict[str, str]) -> None:
    """Pair still-unmatched items (renamed ones) whose content and parent are the same."""
    for _ in range(len(a.entities)):
        taken = set(found.values())
        left_b = [e for e in _content(b) if e.entity_id not in taken]
        added = False
        for ea in [e for e in _content(a) if e.entity_id not in found]:
            hits = [
                eb
                for eb in left_b
                if eb.kind == ea.kind
                and _signature(ea.data, found | {ea.entity_id: eb.entity_id})
                == _signature(eb.data, {})
            ]
            if len(hits) == 1 and hits[0].entity_id not in set(found.values()):
                found[ea.entity_id] = hits[0].entity_id
                added = True
        if not added:
            return


def match(a: LocalTree, a_top: str, b: LocalTree, b_top: str) -> dict[str, str]:
    """a's entity ids -> b's, for items at the same path, plus their dashboards' tabs and cards."""
    b_at = {p: eid for eid, p in paths(b, b_top).items()}
    found = {
        ea: eb
        for ea, p in paths(a, a_top).items()
        if (eb := b_at.get(p)) and a.entities[ea].kind == b.entities[eb].kind
    }
    _by_content(a, b, found)
    for ea, eb in list(found.items()):
        da, db = a.entities[ea].data, b.entities[eb].data
        if not (isinstance(da, Dashboard) and isinstance(db, Dashboard)):
            continue
        b_tabs = {t.position: t.entity_id for t in db.tabs}
        found |= {
            t.entity_id: b_tabs[t.position] for t in da.tabs if t.position in b_tabs
        }
        pa = {t.entity_id: t.position for t in da.tabs}
        pb = {t.entity_id: t.position for t in db.tabs}
        b_cards = {
            (_tab_position(dc, pb), dc.card_id, dc.row, dc.col): dc.entity_id
            for dc in db.dashcards
        }
        for dc in da.dashcards:
            card = found.get(dc.card_id, dc.card_id) if dc.card_id else None
            if hit := b_cards.get((_tab_position(dc, pa), card, dc.row, dc.col)):
                found[dc.entity_id] = hit
    return found


def translate(
    t: LocalTree, top: str, matched: dict[str, str], top_data: Item
) -> tuple[list[Entity], dict[str, str]]:
    """t's content with matched ids swapped, every other id fresh, and the top replaced."""
    content = _content(t)
    ids = {i for e in content for i in ids_in(e.data.model_dump())}
    mapping = {i: matched.get(i) or new_entity_id() for i in ids}
    out = []
    for e in content:
        data = (
            top_data if e.entity_id == top else representations.swapped(e.data, mapping)
        )
        out.append(replace(e, data=data, path=None, provisional=[]))
    return out, mapping


def _scope(root: Path) -> str:
    cols = wf.load_config(root).collections
    if not cols or len(cols) != 1:
        raise ConfigError(
            f"preview needs exactly one collection under `collections:` in {CONFIG}"
        )
    return next(iter(cols))


def _local(root: Path) -> tuple[LocalTree, str]:
    top, local = _scope(root), read_tree(root)
    if local.problems:
        raise ValidationFailed(local.problems)
    if top not in local.entities:
        raise ConfigError(
            f"collection {top} has no file", hint="Run `metabase-sync export` first"
        )
    return local, top


def _collections(client: MetabaseClient) -> list[dict[str, Any]]:
    return rows(
        client.get(
            "/api/collection", archived="false", exclude_other_user_collections="false"
        )
    )


def owner_collection(
    client: MetabaseClient, email: str, create: bool = False
) -> dict[str, Any]:
    users = rows(client.get("/api/user", status="all"))
    user = next(
        (u for u in users if str(u.get("email", "")).lower() == email.lower()), None
    )
    if user is None:
        if not create:
            raise ConfigError(
                f"no Metabase user {email}", hint="Pass --create-owner to create it"
            )
        first, *rest = re.split(r"[._+]", email.split("@")[0])
        user = client.post(
            "/api/user",
            {
                "first_name": first.title(),
                "last_name": " ".join(p.replace("-", " ").title() for p in rest)
                or first.title(),
                "email": email,
            },
        )
    elif not user.get("is_active", True):
        raise ConfigError(f"Metabase user {email} is deactivated")
    found = next(
        (c for c in _collections(client) if c.get("personal_owner_id") == user["id"]),
        None,
    )
    if found is None or not found.get("entity_id"):
        raise ConfigError(
            f"{email} has no personal collection yet",
            hint="Log in to Metabase once, then preview again",
        )
    return found


def _in(c: dict[str, Any], parents: set[int]) -> bool:
    loc = str(c.get("location", "")).strip("/")
    return loc.isdigit() and int(loc) in parents


def find(client: MetabaseClient, name: str, parent: int) -> dict[str, Any] | None:
    return next(
        (c for c in _collections(client) if c.get("name") == name and _in(c, {parent})),
        None,
    )


def _scratch(root: Path, scope: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / CONFIG).write_text(
        f"collections:\n  - {scope}\nremoval: archive\n", encoding="utf-8"
    )
    return root


def _pull(client: MetabaseClient, root: Path, scope: str) -> LocalTree:
    wf.export(client, _scratch(root, scope), force=True)
    return read_tree(root)


def apply(
    client: MetabaseClient,
    root: Path,
    owner: str,
    name: str,
    create_owner: bool = False,
) -> Done:
    local, top = _local(root)
    parent = owner_collection(client, owner, create_owner)
    existing = find(client, name, parent["id"])
    roots: set[str] = set()
    done: Done = []
    with tempfile.TemporaryDirectory() as t, personal(roots):
        matched: dict[str, str] = {}
        if existing:
            roots.add(existing["entity_id"])
            current = _pull(client, Path(t) / "current", existing["entity_id"])
            matched = match(local, top, current, existing["entity_id"])
        real_top = local.entities[top].data
        assert isinstance(real_top, Collection)
        top_id = matched.get(top) or new_entity_id()
        top_data = real_top.model_copy(
            update={
                "entity_id": top_id,
                "name": name,
                "parent_id": parent["entity_id"],
                "description": f"Preview, not live. Remove: mbs preview destroy --name '{name}'",
            }
        )
        content, _ = translate(local, top, matched | {top: top_id}, top_data)
        roots.add(top_id)
        scratch = _scratch(Path(t) / "preview", top_id)
        for p, text in tree.render(content).items():
            (scratch / p).parent.mkdir(parents=True, exist_ok=True)
            (scratch / p).write_text(text, encoding="utf-8")
        prepared, plan = wf.plan_apply(client, scratch, None, force=True)
        wf.apply(
            client,
            scratch,
            prepared,
            plan,
            lambda op, _: done.append((op.action, op.kind, op.name)),
        )
        after = read_tree(scratch)
        final = next(
            e.entity_id
            for e in after.entities.values()
            if isinstance(e.data, Collection)
            and e.data.parent_id == parent["entity_id"]
        )
        roots.add(final)
        keep = set(after.entities)
        idx = fetch_index(client, {final})
    for kind in (CARD, DASHBOARD, COLLECTION):
        for item in idx.live(kind):
            if item.entity_id and item.entity_id not in keep:
                client.put(f"/api/{kind}/{item.id}", {"archived": True})
                done.append(("archive", kind, item.name))
    return done


def export(client: MetabaseClient, root: Path, owner: str, name: str) -> Changes:
    local, top = _local(root)
    existing = find(client, name, owner_collection(client, owner)["id"])
    if existing is None:
        raise ConfigError(
            f"no preview {name!r} for {owner}", hint="Run `mbs preview apply` first"
        )
    scope = existing["entity_id"]
    with tempfile.TemporaryDirectory() as t, personal({scope}):
        got = _pull(client, Path(t), scope)
    content, _ = translate(
        got, scope, match(got, scope, local, top), local.entities[top].data
    )
    content += [e for e in local.entities.values() if e.kind == SUBSCRIPTION]
    laid = tree.layout(content)
    moves = {
        old.path: laid[eid]
        for eid, old in local.entities.items()
        if old.path and eid in laid
    }
    rendered, before = tree.render(content), tree.current_files(root)
    tree.write_tree(root, rendered, local.state, moves)
    return tree.changes(before, rendered, moves)


def destroy(client: MetabaseClient, name: str, owner: str | None = None) -> list[str]:
    """Archive the preview called `name`: the owner's, or every owner's when owner is None."""
    cols = _collections(client)
    if owner:
        parents = {owner_collection(client, owner)["id"]}
    else:
        parents = {c["id"] for c in cols if c.get("personal_owner_id")}
    hits = [c for c in cols if c.get("name") == name and _in(c, parents)]
    for c in hits:
        client.put(f"/api/collection/{c['id']}", {"archived": True})
    return [str(c["name"]) for c in hits]
