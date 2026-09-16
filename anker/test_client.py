"""CLI smoke tests for the AnkerGames client."""

from __future__ import annotations

import sys


def _client():
    from anker.anker_api import AnkerGamesClient

    return AnkerGamesClient()


def cmd_browse() -> None:
    games = _client().browse(page=1)
    print(f"{len(games)} games")
    for game in games[:8]:
        print(f"  {game.game_id:40} {game.title}")


def cmd_search(query: str) -> None:
    games = _client().search(query)
    print(f"{len(games)} results for {query!r}")
    for game in games[:8]:
        print(f"  {game.game_id:40} {game.title}")


def cmd_download(slug: str) -> None:
    url = _client().get_download_url(slug, title=slug)
    print(url)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv[1:])
    if not argv:
        print("Usage: python -m anker.test_client browse|search <q>|download <slug>")
        return 1

    command = argv[0].lower()
    if command == "browse":
        cmd_browse()
    elif command == "search" and len(argv) >= 2:
        cmd_search(" ".join(argv[1:]))
    elif command == "download" and len(argv) >= 2:
        cmd_download(argv[1])
    else:
        print("Usage: python -m anker.test_client browse|search <q>|download <slug>")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
