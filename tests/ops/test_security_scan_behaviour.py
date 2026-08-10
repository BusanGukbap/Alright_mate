from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCAN = ROOT / "ops" / "security" / "scan.sh"


TIMEOUT_FAKE = """#!/bin/sh
set -eu
printf '%s\\n' "$*" >>"${FAKE_STATE_DIR}/timeout.log"
signal_option=$1
kill_option=$2
duration=$3
shift 3
[ "$signal_option" = "--signal=INT" ] || exit 91
[ "$kill_option" = "--kill-after=10s" ] || exit 92
if [ "$duration" = "120s" ]; then
  count=$(cat "${FAKE_STATE_DIR}/timeout-stage-count")
  count=$((count + 1))
  printf '%s\\n' "$count" >"${FAKE_STATE_DIR}/timeout-stage-count"
  if [ "${FAKE_TIMEOUT_FAIL_STAGE:-0}" = "$count" ]; then
    exit 124
  fi
elif [ "$duration" != "600s" ]; then
  exit 93
fi
"$@"
"""


ENV_FAKE = """#!/bin/sh
set -eu
printf '%s\\n' "$*" >>"${FAKE_STATE_DIR}/env.log"
while [ "$#" -gt 0 ]; do
  case "$1" in
    *=*) export "$1"; shift ;;
    *) break ;;
  esac
done
[ "$#" -gt 0 ] || exit 94
case "$1" in
  */scan.sh) exec sh "$@" ;;
  *) exec "$@" ;;
esac
"""


PYTHON_FAKE = """#!/bin/sh
set -eu
printf '%s\\n' "$*" >>"${FAKE_STATE_DIR}/python.log"
case "$*" in
  *validate_dependency_coverage.py*)
    printf '%s\\n' apps/api
    ;;
  *)
    printf '%s\\n' fake_validator=valid
    ;;
esac
"""


DOCKER_FAKE = """#!/bin/sh
set -eu
state="${FAKE_STATE_DIR}/containers"
printf '%s\\n' "$*" >>"${FAKE_STATE_DIR}/docker.log"
command_name=${1:-}
shift || true
case "$command_name" in
  ps)
    wanted=''
    for argument in "$@"; do
      case "$argument" in
        label=org.alright-mate.security-scan=*)
          wanted=${argument#label=org.alright-mate.security-scan=}
          ;;
      esac
    done
    if [ "${FAKE_PS_INCLUDE_ALL:-0}" = 1 ]; then
      awk -F '|' 'NF >= 2 { print $1 }' "$state"
    else
      awk -F '|' -v wanted="$wanted" '$2 == wanted { print $1 }' "$state"
    fi
    ;;
  inspect)
    identifier=''
    for argument in "$@"; do identifier=$argument; done
    label=$(awk -F '|' -v identifier="$identifier" '$1 == identifier { print $2; exit }' "$state")
    [ -n "$label" ] || exit 4
    printf '%s\\n' "$label"
    ;;
  rm)
    identifier=''
    for argument in "$@"; do identifier=$argument; done
    printf 'RM:%s\\n' "$identifier" >>"${FAKE_STATE_DIR}/docker-removals.log"
    [ "${FAKE_RM_FAIL:-0}" != 1 ] || exit 5
    awk -F '|' -v identifier="$identifier" '$1 != identifier' "$state" \
      >"${FAKE_STATE_DIR}/containers.next"
    mv "${FAKE_STATE_DIR}/containers.next" "$state"
    ;;
  run)
    count=$(cat "${FAKE_STATE_DIR}/docker-run-count")
    count=$((count + 1))
    printf '%s\\n' "$count" >"${FAKE_STATE_DIR}/docker-run-count"
    if [ "${FAKE_DOCKER_FAIL_STAGE:-0}" = "$count" ]; then
      label=''
      previous=''
      for argument in "$@"; do
        if [ "$previous" = '--label' ]; then
          label=${argument#org.alright-mate.security-scan=}
          break
        fi
        previous=$argument
      done
      printf 'abc%03d|%s\\n' "$count" "$label" >>"$state"
      exit 42
    fi
    ;;
  *)
    exit 95
    ;;
esac
"""


class SecurityScanBehaviourTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix=".task5c-scan-behaviour-", dir=ROOT
        )
        self.state = Path(self.temporary.name)
        self.fake_bin = self.state / "bin"
        self.fake_bin.mkdir()
        for name, source in {
            "timeout": TIMEOUT_FAKE,
            "env": ENV_FAKE,
            "python3": PYTHON_FAKE,
            "docker": DOCKER_FAKE,
        }.items():
            path = self.fake_bin / name
            path.write_bytes(source.encode("utf-8"))
            path.chmod(0o755)
        self.reset_state()

        if os.name == "nt":
            candidate = Path(r"C:\Program Files\Git\usr\bin\sh.exe")
            self.shell = str(candidate) if candidate.is_file() else shutil.which("bash")
        else:
            self.shell = shutil.which("sh")
        if not self.shell:
            self.fail("a POSIX shell is required for the scan behaviour contract")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def reset_state(self, containers: str = "") -> None:
        for name, value in {
            "containers": containers,
            "docker-run-count": "0\n",
            "timeout-stage-count": "0\n",
            "docker.log": "",
            "docker-removals.log": "",
            "timeout.log": "",
            "env.log": "",
            "python.log": "",
        }.items():
            (self.state / name).write_text(value, encoding="utf-8", newline="\n")

    @staticmethod
    def posix_path(path: Path) -> str:
        resolved = str(path.resolve())
        if os.name != "nt":
            return resolved
        drive, tail = os.path.splitdrive(resolved)
        return f"/{drive[0].lower()}{tail.replace(os.sep, '/')}"

    def run_scan(
        self,
        *,
        scan_id: str = "scan-test",
        extra_environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        inventory = json.loads(
            (ROOT / "ops" / "security" / "runtime-image-inventory.json").read_text(
                encoding="utf-8"
            )
        )
        environment = os.environ.copy()
        environment.update(
            {
                "TRIVY_IMAGES": inventory["public_images"][0],
                "TRIVY_SCAN_RUN_ID": scan_id,
                "TRIVY_CACHE_VOLUME": "fake-cache",
            }
        )
        environment.pop("TRIVY_BOUNDED_CHILD", None)
        if extra_environment:
            environment.update(extra_environment)
        shell_program = (
            'PATH="$1:/usr/bin:/bin"; export PATH; '
            'FAKE_STATE_DIR="$2"; export FAKE_STATE_DIR; '
            'exec sh "$3"'
        )
        return subprocess.run(
            [
                self.shell,
                "-c",
                shell_program,
                "scan-harness",
                self.posix_path(self.fake_bin),
                self.posix_path(self.state),
                self.posix_path(SCAN),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def read_lines(self, name: str) -> list[str]:
        return (self.state / name).read_text(encoding="utf-8").splitlines()

    def test_outer_and_each_stage_have_independent_exact_timeout_bounds(self) -> None:
        self.reset_state("abc123|scan-test\n")
        completed = self.run_scan()
        self.assertEqual(0, completed.returncode, completed.stderr)
        timeout_calls = [line.split() for line in self.read_lines("timeout.log")]
        outer = [call for call in timeout_calls if call[2] == "600s"]
        stages = [call for call in timeout_calls if call[2] == "120s"]
        self.assertEqual(1, len(outer))
        self.assertEqual("env", outer[0][3])
        self.assertIn("TRIVY_BOUNDED_CHILD=1", outer[0])
        self.assertEqual(8, len(stages))
        for call in timeout_calls:
            self.assertEqual(["--signal=INT", "--kill-after=10s"], call[:2])
        self.assertTrue(all(call[3:5] == ["docker", "run"] for call in stages))
        self.assertEqual(["RM:abc123"], self.read_lines("docker-removals.log"))
        self.assertEqual([], self.read_lines("containers"))

    def test_timed_out_and_failed_stages_propagate_nonzero_and_are_cleaned(self) -> None:
        timed_out = self.run_scan(extra_environment={"FAKE_TIMEOUT_FAIL_STAGE": "3"})
        self.assertNotEqual(0, timed_out.returncode)
        self.assertEqual(3, int((self.state / "timeout-stage-count").read_text()))
        self.assertEqual([], self.read_lines("containers"))

        self.reset_state()
        failed = self.run_scan(extra_environment={"FAKE_DOCKER_FAIL_STAGE": "2"})
        self.assertNotEqual(0, failed.returncode)
        self.assertIn("RM:abc002", self.read_lines("docker-removals.log"))
        self.assertEqual([], self.read_lines("containers"))

    def test_cleanup_removes_only_exact_label_and_refuses_foreign_or_malformed_ids(self) -> None:
        self.reset_state("abc123|scan-test\ndef456|foreign\nnot-hex|scan-test\n")
        foreign = self.run_scan(extra_environment={"FAKE_PS_INCLUDE_ALL": "1"})
        self.assertNotEqual(0, foreign.returncode)
        self.assertIn("another scan", foreign.stderr)
        self.assertIn("unexpected Trivy container identifier", foreign.stderr)
        self.assertEqual(["RM:abc123"], self.read_lines("docker-removals.log"))
        self.assertEqual(
            ["def456|foreign", "not-hex|scan-test"],
            self.read_lines("containers"),
        )

    def test_cleanup_failure_overrides_success(self) -> None:
        self.reset_state("abc123|scan-test\n")
        cleanup_failed = self.run_scan(extra_environment={"FAKE_RM_FAIL": "1"})
        self.assertNotEqual(0, cleanup_failed.returncode)
        self.assertEqual(["abc123|scan-test"], self.read_lines("containers"))

    def test_invalid_scan_id_fails_before_any_docker_or_timeout_invocation(self) -> None:
        completed = self.run_scan(scan_id="invalid/scan-id")
        self.assertEqual(2, completed.returncode)
        self.assertIn("unsupported characters", completed.stderr)
        self.assertEqual([], self.read_lines("docker.log"))
        self.assertEqual([], self.read_lines("timeout.log"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
