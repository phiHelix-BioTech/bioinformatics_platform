"""
Local Snakemake runner — runs workflows directly on the worker host.

Requires:
  - snakemake installed in the worker image (Dockerfile.worker)
  - conda/mamba for wrapper environments (--use-conda)
  - /outputs volume shared between backend and worker

Set SNAKEMAKE_BACKEND=local to enable.
"""
import logging
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

from app.config import settings
from app.services.snakemake.base import SnakemakeRunner
from app.services.log_streamer import append_log

logger = logging.getLogger(__name__)

_PROC_TIMEOUT = 13_500  # 3 h 45 m

_KEEP_EXTS = {
    "html", "txt", "csv", "tsv", "json",
    "gz", "bam", "bai", "vcf", "bed",
    "bigwig", "bw", "log",
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
}


def _generate_local_snakefile(
    workflow_config: Optional[dict[str, Any]],
    input_path: str,
    output_dir: str,
) -> str:
    """Generate a Snakefile with local filesystem paths."""
    wrappers: list[str] = []
    if workflow_config:
        wrappers = workflow_config.get("wrappers", [])
        workflows = workflow_config.get("workflows", [])
        if workflows:
            raise NotImplementedError(
                f"Community workflow '{workflows[0]}' is not yet supported in local mode. "
                "Use individual Snakemake wrappers instead."
            )

    if wrappers:
        rules = []
        prev_output = input_path
        for i, wrapper_id in enumerate(wrappers):
            rule_name = f"step_{i}_{wrapper_id.replace('/', '_').replace('-', '_')}"
            this_output = f"{output_dir}/{rule_name}/output"
            rules.append(f"""
rule {rule_name}:
    input: "{prev_output}"
    output: directory("{this_output}")
    wrapper:
        "{wrapper_id}"
""")
            prev_output = this_output

        all_rule = f'rule all:\n    input: "{prev_output}"\n'
        return all_rule + "\n".join(rules)

    # Default: generic QC pipeline using local paths
    return f"""
# Auto-generated generic Snakefile (local mode)
rule all:
    input:
        "{output_dir}/multiqc_report.html",
        "{output_dir}/fastp.json"

rule fastp_qc:
    input: "{input_path}"
    output:
        html="{output_dir}/multiqc_report.html",
        json="{output_dir}/fastp.json"
    wrapper:
        "v3.13.0/bio/fastp"
"""


def _drain(pipe, log_fn) -> None:
    try:
        for line in pipe:
            log_fn(line.rstrip())
    except Exception:
        pass


def _collect_results(output_dir: Path, runtime: int) -> dict:
    files = []
    if output_dir.exists():
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
    logger.info("[snakemake/local] collected %d output files", len(files))
    return {
        "type":            "files",
        "files":           files,
        "instance_type":   "local",
        "runtime_seconds": runtime,
    }


class LocalSnakemakeRunner(SnakemakeRunner):
    """Runs Snakemake workflows on the local worker host."""

    def run(
        self,
        pipeline_id: str,
        storage_key: str,
        file_type: str,
        job_id: str = "",
        workflow_config: Optional[dict[str, Any]] = None,
    ) -> dict:
        start = time.time()

        def _log(msg: str) -> None:
            append_log(job_id, f"[snakemake/local] {msg}")
            logger.info("[snakemake/local][%s] %s", job_id, msg)

        output_dir = Path("/outputs") / job_id / "snakemake"
        output_dir.mkdir(parents=True, exist_ok=True)

        snakefile_content = _generate_local_snakefile(
            workflow_config, storage_key, str(output_dir)
        )

        with tempfile.TemporaryDirectory() as run_dir:
            snakefile_path = Path(run_dir) / "Snakefile"
            snakefile_path.write_text(snakefile_content)
            _log(f"Snakefile written to {snakefile_path}")

            n_cores = min(os.cpu_count() or 4, 8)
            cmd = [
                "snakemake",
                "--snakefile",      str(snakefile_path),
                "--cores",          str(n_cores),
                "--use-conda",
                "--rerun-incomplete",
                "--nolock",
                "--directory",      str(output_dir),
            ]

            _log("Command: " + " ".join(cmd))

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=run_dir,
                    env={**os.environ},
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    "[snakemake/local] 'snakemake' not found in PATH. "
                    "Ensure snakemake is installed in the worker image."
                ) from exc

            drain_t = threading.Thread(
                target=_drain, args=(proc.stdout, _log), daemon=True
            )
            drain_t.start()

            try:
                returncode = proc.wait(timeout=_PROC_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise RuntimeError(
                    f"[snakemake/local] Snakemake timed out after {_PROC_TIMEOUT}s"
                )
            finally:
                drain_t.join(timeout=5)

        runtime = int(time.time() - start)

        if returncode != 0:
            raise RuntimeError(
                f"[snakemake/local] Snakemake exited with code {returncode} after {runtime}s"
            )

        _log(f"Snakemake completed in {runtime}s")
        return _collect_results(output_dir, runtime)
