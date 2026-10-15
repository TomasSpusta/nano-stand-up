from pathlib import Path

import pytest

from nano_standup.run_lock import AlreadyRunning, run_lock


def test_a_second_lock_is_refused_while_the_first_is_held(tmp_path: Path) -> None:
    path = tmp_path / "job.lock"
    with run_lock(path), pytest.raises(AlreadyRunning, match=f"{path} is locked"), run_lock(path):
        pass


def test_the_lock_is_free_again_after_the_run(tmp_path: Path) -> None:
    path = tmp_path / "job.lock"
    with run_lock(path):
        pass

    with run_lock(path):
        pass
