"""`tms-work-export` — the board as a file in the repository.

⛔ **This is not a convenience.** The board lives in a database inside the
corporate network, and the person who reads "check the board before starting
work" is usually outside it. Without a committed file that instruction is
aspirational, and an instruction nobody can follow is worse than none: it
looks like the question was answered.

The file it writes is generated, and says so at the top. Anything typed into
it by hand is lost on the next run - the board is where status is edited.

Python 3.9 compatible.
"""

import argparse
import io
import os
import sys
from typing import List, Optional

from tms.core.config import ConfigError, load_config

DEFAULT_CONFIG_PATH = "/etc/trino-management-service/config.yaml"
DEFAULT_OUTPUT = "docs/WORK_BOARD.md"


def run(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="작업 보드를 마크다운으로 내보낸다 (docs/WORK_BOARD.md).")
    parser.add_argument("--config", default=os.environ.get("TMS_CONFIG",
                                                           DEFAULT_CONFIG_PATH))
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help="쓸 파일. '-' 이면 표준출력")
    parser.add_argument("--seed", action="store_true",
                        help="문서 기반 항목을 처음 한 번 채운다 (이미 있는 키는 건드리지 않는다)")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except (ConfigError, FileNotFoundError) as exc:
        print("설정을 읽을 수 없다: {}".format(exc), file=sys.stderr)
        return 2

    from tms.work.service import render_markdown
    from tms.work.store import BoardUnavailable, PostgresBoardRepository

    try:
        repository = PostgresBoardRepository(config.database_url.reveal())
    except Exception as exc:  # noqa: BLE001
        print("보드 저장소를 열 수 없다: {}".format(exc), file=sys.stderr)
        return 2

    if args.seed:
        from tms.work.seed import seed

        try:
            added = seed(repository)
        except BoardUnavailable as exc:
            print("보드를 읽을 수 없다: {}".format(exc), file=sys.stderr)
            return 2
        print("{}건 추가 (이미 있던 항목은 그대로).".format(added), file=sys.stderr)

    try:
        text = render_markdown(repository.list_items())
    except BoardUnavailable as exc:
        print("보드를 읽을 수 없다: {}".format(exc), file=sys.stderr)
        return 2

    repository.close()

    if args.output == "-":
        sys.stdout.write(text)
        return 0

    # Written whole, then moved into place: a run interrupted halfway would
    # otherwise leave a truncated file that still looks like the board.
    temporary = args.output + ".tmp"
    with io.open(temporary, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, args.output)
    print("{} 갱신".format(args.output), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(run())
