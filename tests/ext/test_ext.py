from __future__ import annotations

from pathlib import Path

from metabase_sync.ext import _seams, sqltag
from metabase_sync.ext import preview as pv
from metabase_sync.format.representations.card import Card
from metabase_sync.format.representations.collection import Collection
from metabase_sync.format.representations.dashboard import Dashboard
from metabase_sync.ids import is_entity_id
from metabase_sync.metabase import index
from metabase_sync.metabase.index import RemoteItem
from metabase_sync.store import tree, yamlio
from metabase_sync.store.tree import LocalTree, read_tree

TOP, CHILD, CARD, DASH, TAB, DC, PERSONAL = (c * 21 for c in "TKQDBXP")
REAL = (TOP, CHILD, CARD, DASH, TAB, DC)


def repo(root: Path) -> Path:
    main = root / "collections" / "main"
    yamlio.write(root / tree.CONFIG, {"collections": [TOP], "removal": "archive"})
    yamlio.write(
        main / "repo.yaml",
        {"name": "repo", "entity_id": TOP, "serdes/meta": [{"model": "Collection"}]},
    )
    yamlio.write(
        main / "repo" / "health.yaml",
        {
            "name": "health",
            "entity_id": CHILD,
            "parent_id": TOP,
            "serdes/meta": [{"model": "Collection"}],
        },
    )
    yamlio.write(
        main / "repo" / "health" / "runs.yaml",
        {
            "name": "runs",
            "entity_id": CARD,
            "collection_id": CHILD,
            "display": "table",
            "visualization_settings": {},
            "dataset_query": {
                "database": "db",
                "lib/type": "mbql/query",
                "stages": [{"lib/type": "mbql.stage/native", "native": "select 1"}],
            },
            "serdes/meta": [{"model": "Card"}],
        },
    )
    yamlio.write(
        main / "repo" / "board.yaml",
        {
            "name": "board",
            "entity_id": DASH,
            "collection_id": TOP,
            "tabs": [{"entity_id": TAB, "name": "One", "position": 0}],
            "dashcards": [
                {
                    "entity_id": DC,
                    "card_id": CARD,
                    "dashboard_tab_id": [DASH, TAB],
                    "row": 0,
                    "col": 0,
                    "size_x": 4,
                    "size_y": 4,
                }
            ],
            "serdes/meta": [{"model": "Dashboard"}],
        },
    )
    return root


def preview_of(local: LocalTree, at: Path, name: str = "🚧 repo @ b") -> LocalTree:
    """What Metabase would hold after a first preview: same layout, all-new ids."""
    top = local.entities[TOP].data
    assert isinstance(top, Collection)
    ptop = "p" * 21
    data = top.model_copy(
        update={"entity_id": ptop, "name": name, "parent_id": PERSONAL}
    )
    content, _ = pv.translate(local, TOP, {TOP: ptop}, data)
    yamlio.write(at / tree.CONFIG, {"collections": [ptop]})
    for p, text in tree.render(content).items():
        (at / p).parent.mkdir(parents=True, exist_ok=True)
        (at / p).write_text(text)
    return read_tree(at)


def test_a_first_preview_has_no_real_ids_and_moves_the_top_under_the_owner(
    tmp_path,
):
    local = read_tree(repo(tmp_path / "repo"))
    prev = preview_of(local, tmp_path / "prev")
    text = "".join(p.read_text() for p in (tmp_path / "prev").rglob("*.yaml"))
    assert not any(i in text for i in REAL)
    top = prev.entities["p" * 21].data
    assert isinstance(top, Collection) and top.parent_id == PERSONAL
    dash = next(e.data for e in prev.entities.values() if isinstance(e.data, Dashboard))
    card = next(e.entity_id for e in prev.entities.values() if e.kind == "card")
    assert dash.dashcards[0].card_id == card
    assert dash.dashcards[0].dashboard_tab_id == [
        dash.entity_id,
        dash.tabs[0].entity_id,
    ]


def test_previewing_again_matches_every_item_tab_and_dashcard_by_path(tmp_path):
    local = read_tree(repo(tmp_path / "repo"))
    prev = preview_of(local, tmp_path / "prev")
    matched = pv.match(local, TOP, prev, "p" * 21)
    assert set(matched) == set(REAL)
    assert len(set(matched.values())) == len(REAL)
    assert not set(matched.values()) & set(REAL)


def test_exporting_a_preview_restores_real_ids_and_gives_new_items_fresh_ones(
    tmp_path,
):
    local = read_tree(repo(tmp_path / "repo"))
    prev_root = tmp_path / "prev"
    prev = preview_of(local, prev_root)
    runs = next(prev_root.rglob("runs.yaml"))
    yamlio.write(
        runs.with_name("new.yaml"),
        {**yamlio.read(runs), "name": "new", "entity_id": "N" * 21},
    )
    prev = read_tree(prev_root)
    content, _ = pv.translate(
        prev, "p" * 21, pv.match(prev, "p" * 21, local, TOP), local.entities[TOP].data
    )
    by_name = {e.name: e.data for e in content}
    assert by_name["repo"] == local.entities[TOP].data
    assert {n: by_name[n] for n in ("health", "runs", "board")} == {
        e.name: e.data for e in local.entities.values() if e.entity_id != TOP
    }
    new = by_name["new"]
    assert isinstance(new, Card) and new.collection_id == CHILD
    assert is_entity_id(new.entity_id) and new.entity_id != "N" * 21
    assert set(tree.render(content)) >= {e.path for e in local.entities.values()}


def test_tag_sets_the_first_line_once(tmp_path):
    root = repo(tmp_path)
    assert sqltag.tag(root, "repo", check=True) == [
        "collections/main/repo/health/runs.yaml"
    ]
    sqltag.tag(root, "repo")
    card = read_tree(root).entities[CARD].data
    assert isinstance(card, Card)
    sql = card.dataset_query["stages"][0]["native"]
    assert sql == "-- mb:repo=repo card=repo/health/runs\nselect 1"
    assert sqltag.tag(root, "repo", check=True) == []


def test_seam_still_matches_upstream_and_restores_it():
    personal = RemoteItem(id=1, entity_id=PERSONAL, personal_owner_id=7, location="/")
    top = RemoteItem(id=2, entity_id=TOP, location="/1/", is_personal=True)
    other = RemoteItem(id=3, entity_id=CHILD, location="/1/", is_personal=True)
    before = (index.manageable, index.LISTINGS)
    assert index.manageable([personal, top, other]) == {}
    with _seams.personal({TOP}):
        assert set(index.manageable([personal, top, other])) == {2}
        assert (
            index.LISTINGS["collections"][1]["exclude_other_user_collections"]
            == "false"
        )
    assert (index.manageable, index.LISTINGS) == before


def test_items_renamed_on_either_side_keep_their_ids(tmp_path):
    local = read_tree(repo(tmp_path / "repo"))
    prev_root = tmp_path / "prev"
    preview_of(local, prev_root)
    for f in ("runs.yaml", "board.yaml"):
        old = next(prev_root.rglob(f))
        yamlio.write(old.with_name("x" + f), {**yamlio.read(old), "name": "x" + f[:-5]})
        old.unlink()
    prev = read_tree(prev_root)
    matched = pv.match(prev, "p" * 21, local, TOP)
    assert set(matched.values()) == set(REAL)
    content, _ = pv.translate(prev, "p" * 21, matched, local.entities[TOP].data)
    names = {e.entity_id: e.name for e in content}
    assert (names[CARD], names[DASH]) == ("xruns", "xboard")
