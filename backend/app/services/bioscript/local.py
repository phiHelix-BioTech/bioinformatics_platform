"""
Local BioScript runner — executes user bash scripts in an isolated subprocess
with OS-level resource sandboxing.

Security controls applied to every script:
  - Separate temp working directory (removed on completion or failure)
  - CPU time limit: 2 hours (RLIMIT_CPU)
  - Virtual memory cap: 8 GB (RLIMIT_AS)
  - File size cap: 10 GB (RLIMIT_FSIZE)
  - Process count cap: 256 (RLIMIT_NPROC)
  - PATH restricted to known safe directories
  - No network isolation at this layer (use Docker for production isolation)

Set BIOSCRIPT_BACKEND=local to enable.
"""
import logging
import os
import resource
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

from app.services.bioscript.base import BioScriptRunner
from app.services.log_streamer import append_log

logger = logging.getLogger(__name__)

_PROC_TIMEOUT = 7_200  # 2 hours hard wall-clock timeout

# Resource limits
_RLIMIT_CPU_SEC  = 7_200          # 2 h CPU time
_RLIMIT_AS_BYTES = 8 * 1024 ** 3  # 8 GB virtual memory
_RLIMIT_FSIZE    = 10 * 1024 ** 3 # 10 GB per-file
_RLIMIT_NPROC    = 256            # child processes

_KEEP_EXTS = {
    "html", "txt", "csv", "tsv", "json",
    "gz", "bam", "bai", "vcf", "bed",
    "bigwig", "bw", "log", "sh",
}

_MIME_MAP = {
    "html": "text/html",
    "txt":  "text/plain",
    "csv":  "text/csv",
    "tsv":  "text/tab-separated-values",
    "json": "application/json",
    "gz":   "application/gzip",
    "bam":  "application/octet-stream",
    "bai":  "application/octet-stream",
    "vcf":  "text/plain",
    "bed":  "text/plain",
    "log":  "text/plain",
    "sh":   "text/plain",
}

_DEFAULT_SCRIPT = """\
#!/usr/bin/env bash
# Default BioScript — basic QC
set -euo pipefail

echo "INPUT_FILE:  $INPUT_FILE"
echo "OUTPUT_DIR:  $OUTPUT_DIR"

# Source bioplatform helper functions if available
if [ -f /usr/local/lib/bio_helpers.sh ]; then
    . /usr/local/lib/bio_helpers.sh
    bioplatform_qc "$INPUT_FILE" "$OUTPUT_DIR/qc"
else
    echo "bio_helpers.sh not found — running minimal pipeline"
    mkdir -p "$OUTPUT_DIR"
    echo "Input: $INPUT_FILE" > "$OUTPUT_DIR/summary.txt"
    echo "QC skipped (helpers not installed)" >> "$OUTPUT_DIR/summary.txt"
fi

echo "Done."
"""


def _apply_limits() -> None:
    """Called in the subprocess before exec — sets Unix resource limits."""
    try:
        resource.setrlimit(resource.RLIMIT_CPU,   (_RLIMIT_CPU_SEC,  _RLIMIT_CPU_SEC))
        resource.setrlimit(resource.RLIMIT_AS,    (_RLIMIT_AS_BYTES, _RLIMIT_AS_BYTES))
        resource.setrlimit(resource.RLIMIT_FSIZE, (_RLIMIT_FSIZE,    _RLIMIT_FSIZE))
        resource.setrlimit(resource.RLIMIT_NPROC, (_RLIMIT_NPROC,    _RLIMIT_NPROC))
    except Exception as exc:
        # Non-fatal — log but allow the script to continue without limits
        # (e.g., macOS has different rlimit behaviour)
        logger.warning("[bioscript/local] could not set resource limits: %s", exc)


def _drain(pipe, log_fn) -> None:
    try:
        for line in pipe:
            log_fn(line.rstrip())
    except Exception:
        pass


def _collect_results(output_dir: Path, runtime: int) -> dict:
    files = []
    for fpath in sorted(output_dir.rglob("*")):
        if not fpath.is_file():
            continue
        name = fpath.name
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in _KEEP_EXTS:
            continue
        try:
            size = fpath.stat().st_size
        except OSError:
            size = 0
        files.append({
            "name":       name,
            "path":       str(fpath),
            "size_bytes": size,
            "mime_type":  _MIME_MAP.get(ext, "application/octet-stream"),
            "description": "",
        })
    return {
        "type":            "files",
        "files":           files,
        "instance_type":   "local",
        "runtime_seconds": runtime,
    }


class LocalBioScriptRunner(BioScriptRunner):
    """Executes user bash scripts locally with OS resource sandboxing."""

    def run(
        self,
        storage_key: str,
        file_type: str,
        job_id: str = "",
        workflow_config: Optional[dict[str, Any]] = None,
    ) -> dict:
        start = time.time()

        def _log(msg: str) -> None:
            append_log(job_id, f"[bioscript/local] {msg}")
            logger.info("[bioscript/local][%s] %s", job_id, msg)

        script = (workflow_config or {}).get("script") or _DEFAULT_SCRIPT
        extra_env = (workflow_config or {}).get("env") or {}

        output_dir = Path("/outputs") / job_id / "bioscript"
        output_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix=f"bioscript-{job_id[:8]}-") as work_dir:
            script_path = Path(work_dir) / "user_script.sh"
            script_path.write_text(script)
            script_path.chmod(0o755)

            # Save a copy of the executed script to outputs
            script_copy = output_dir / "script.sh"
            script_copy.write_text(script)

            env = {
                **os.environ,
                "INPUT_FILE": storage_key,
                "OUTPUT_DIR": str(output_dir),
                "JOB_ID":     job_id,
                "TMPDIR":     work_dir,
                # Restrict PATH to known safe directories
                "PATH":       "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin",
            }
            for k, v in extra_env.items():
                env[k] = str(v)

            _log(f"Executing script in {work_dir} → output: {output_dir}")
            _log(f"Resource limits: CPU={_RLIMIT_CPU_SEC}s "
                 f"vmem={_RLIMIT_AS_BYTES//1024**3}GB "
                 f"fsize={_RLIMIT_FSIZE//1024**3}GB "
                 f"nproc={_RLIMIT_NPROC}")

            try:
                proc = subprocess.Popen(
                    ["/bin/bash", str(script_path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=work_dir,
                    env=env,
                    preexec_fn=_apply_limits,  # applies rlimits in child before exec
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    "[bioscript/local] /bin/bash not found."
                ) from exc

            log_path = output_dir / "script.log"
            log_lines: list[str] = []

            def _collect_log(line: str) -> None:
                _log(line)
                log_lines.append(line + "\n")

            drain_t = threading.Thread(
                target=_drain, args=(proc.stdout, _collect_log), daemon=True
            )
            drain_t.start()

            try:
                returncode = proc.wait(timeout=_PROC_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise RuntimeError(
                    f"[bioscript/local] Script timed out after {_PROC_TIMEOUT}s"
                )
            finally:
                drain_t.join(timeout=5)
                # Write captured log to output dir
                try:
                    log_path.write_text("".join(log_lines))
                except Exception:
                    pass

        runtime = int(time.time() - start)

        if returncode != 0:
            raise RuntimeError(
                f"[bioscript/local] Script exited with code {returncode} after {runtime}s"
            )

        _log(f"Script completed in {runtime}s")
        return _collect_results(output_dir, runtime)
