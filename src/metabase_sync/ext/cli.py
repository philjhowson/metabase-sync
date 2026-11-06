"""`mbs`: the upstream CLI plus `preview` and `tag`."""

from typing import Annotated

import typer

from metabase_sync import cli as core
from metabase_sync.ext import preview as pv
from metabase_sync.ext import sqltag

app = core.app
preview = typer.Typer(
    help="Drafts in a user's personal collection: only they and admins see them.",
    no_args_is_help=True,
)
app.add_typer(preview, name="preview")

Owner = Annotated[
    str,
    typer.Option(
        "--owner",
        envvar="METABASE_PREVIEW_OWNER",
        help="Email of the user whose personal collection holds the preview.",
    ),
]
Name = Annotated[
    str,
    typer.Option(
        "--name",
        envvar="METABASE_PREVIEW_NAME",
        help="Preview collection name, e.g. '🚧 repo @ branch'.",
    ),
]


@preview.command("apply", help="Copy the files into the owner's preview collection.")
def preview_apply(
    owner: Owner,
    name: Name,
    create_owner: Annotated[
        bool, typer.Option(help="Create the Metabase user if missing.")
    ] = False,
    root: core.Root = None,
    verbose: core.Verbose = False,
    debug: core.Debug = False,
) -> None:
    root = core._start(root, verbose, debug)
    with core._client(root) as client:
        done = pv.apply(client, root, owner, name, create_owner)
    for action, kind, item in done:
        core.out.print(f"  {action} {kind} {item!r}", markup=False)
    core.out.print(f"Preview {name!r} for {owner}: {len(done)} changes.", markup=False)


@preview.command(
    "export",
    help="Pull the owner's preview back into the files, keeping real entity ids.",
)
def preview_export(
    owner: Owner,
    name: Name,
    root: core.Root = None,
    verbose: core.Verbose = False,
    debug: core.Debug = False,
) -> None:
    root = core._start(root, verbose, debug)
    with core._client(root) as client:
        changes = pv.export(client, root, owner, name)
    core._print_files(changes)


@preview.command("destroy", help="Archive the preview collection.")
def preview_destroy(
    name: Name,
    owner: Annotated[
        str | None,
        typer.Option(
            "--owner",
            envvar="METABASE_PREVIEW_OWNER",
            help="Only this owner's; default every owner's.",
        ),
    ] = None,
    root: core.Root = None,
    verbose: core.Verbose = False,
    debug: core.Debug = False,
) -> None:
    root = core._start(root, verbose, debug)
    with core._client(root) as client:
        gone = pv.destroy(client, name, owner)
    core.out.print(f"Archived {len(gone)} preview(s) named {name!r}.", markup=False)


@app.command(
    help="Set `-- mb:repo=REPO card=PATH` as the first line of every native SQL card."
)
def tag(
    repo: Annotated[str, typer.Option("--repo", envvar="METABASE_SYNC_REPO")],
    check: Annotated[
        bool, typer.Option(help="Write nothing; exit 2 if any card needs it.")
    ] = False,
    root: core.Root = None,
    verbose: core.Verbose = False,
    debug: core.Debug = False,
) -> None:
    root = core._start(root, verbose, debug)
    changed = sqltag.tag(root, repo, check)
    for path in changed:
        core.out.print(f"  {'needs tag' if check else 'tagged'} {path}", markup=False)
    if check and changed:
        raise typer.Exit(2)


main = core.main
