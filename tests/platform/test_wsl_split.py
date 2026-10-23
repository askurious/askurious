"""A run of everything on Windows splits the test files between Windows and WSL (tests/helpers/wsl_deploy.py, 2026-10-22):
the halves end about together, files that need Node or a browser stay on Windows, and the output of the WSL part is
read back correctly."""
from tests.helpers import wsl_deploy
from tools import slow_tests


def test_the_halves_end_about_together_and_node_files_stay_here(tmp_path):
    files = {f"tests/a/test_{i}.py": "def test_x(): pass\n" for i in range(10)}
    files["tests/a/test_node.py"] = 'NODE = shutil.which("node")\n'
    files["tests/platform/test_audit8_b_deploy.py"] = "def test_x(): pass\n"
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, "utf-8")
    seconds = {f"tests/a/test_{i}.py": 100.0 * (i + 1) for i in range(10)}
    seconds["tests/a/test_node.py"] = 300.0
    seconds["tests/platform/test_audit8_b_deploy.py"] = 50.0
    (tmp_path / "tests" / "file_durations.txt").write_text(
        "# head\n" + "".join(f"{took} {name}\n" for name, took in seconds.items()), "utf-8")

    part = wsl_deploy.share(tmp_path)
    assert "tests/a/test_node.py" not in part and "tests/platform/test_audit8_b_deploy.py" not in part
    wsl = wsl_deploy.DEPLOY_SECONDS + sum(seconds[n] for n in part) * wsl_deploy.WSL_SPEED
    windows = sum(took for name, took in seconds.items() if name not in part)
    assert abs(wsl - windows) <= 1000 * wsl_deploy.WSL_SPEED  # within the biggest file
    assert part == wsl_deploy.share(tmp_path)  # the same split every time


def test_files_that_need_the_checkouts_git_stay_here(tmp_path):
    """The copy in WSL has no .git: tools/public_export.py reads `git archive HEAD` (first split run, 2026-10-22)."""
    for name, text in {"tests/test_export.py": "import public_export\n",
                       "tests/test_log.py": 'subprocess.run(["git", "log"])\n',
                       "tests/test_plain.py": "def test_x(): pass\n"}.items():
        (tmp_path / "tests").mkdir(exist_ok=True)
        (tmp_path / name).write_text(text, "utf-8")
    assert wsl_deploy.stays_here(tmp_path, "tests/test_export.py")
    assert wsl_deploy.stays_here(tmp_path, "tests/test_log.py")
    assert not wsl_deploy.stays_here(tmp_path, "tests/test_plain.py")


def test_a_file_without_a_measurement_counts_as_the_median(tmp_path):
    for i in range(4):
        (tmp_path / "tests").mkdir(exist_ok=True)
        (tmp_path / "tests" / f"test_{i}.py").write_text("def test_x(): pass\n", "utf-8")
    (tmp_path / "tests" / "file_durations.txt").write_text("10 tests/test_0.py\n10 tests/test_1.py\n", "utf-8")
    assert wsl_deploy.weights(tmp_path) == {"tests/test_0.py": 10.0, "tests/test_1.py": 10.0}
    assert set(wsl_deploy.share(tmp_path)) <= {f"tests/test_{i}.py" for i in range(4)}


def test_the_wsl_command_runs_the_deploy_tests_and_its_part_with_the_same_workers():
    line = wsl_deploy.command("/tmp/x", "/mnt/c/t.tar", 4)
    assert f"ASKURIOUS_WSL_PART={wsl_deploy.PART_FILE}" in line and "--full -n 4 tests" in line
    assert "TEST_SLOTS=0" in line and line.rstrip().endswith("exit $status")


def test_the_output_of_the_wsl_part_is_read_back():
    text = ("....s.. [100%]\nWSL-SKIPPED tests/a/test_x.py::test_y[1]\nWSL-SKIPPED tests/b/test_z.py::test_q\n"
            "1200 passed, 2 skipped in 300.00s (0:05:00)\n")
    assert wsl_deploy.skipped(text) == ["tests/a/test_x.py::test_y[1]", "tests/b/test_z.py::test_q"]
    assert wsl_deploy.counted(text, "passed") == 1200
    assert wsl_deploy.summary(text).startswith("1200 passed")
    assert wsl_deploy.counted("", "passed") == 0


def test_slow_tests_also_writes_the_seconds_per_file():
    log = ("3.10s call     tests/a/test_x.py::test_long\n0.20s setup    tests/a/test_x.py::test_long\n"
           "0.40s call     tests/a/test_y.py::test_short[p]\n")
    assert slow_tests.per_file(log) == {"tests/a/test_x.py": 3.3, "tests/a/test_y.py": 0.4}
