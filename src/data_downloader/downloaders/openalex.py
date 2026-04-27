"""
OpenAlex snapshot downloader.

Syncs the OpenAlex S3 public snapshot (s3://openalex) excluding legacy-data/.
Delegates to `aws s3 sync --no-sign-request` — no AWS credentials required.
Resumable: re-running after a Ctrl-C or error skips already-complete files.

No config keys required.
"""
import subprocess
from pathlib import Path

import boto3
from botocore import UNSIGNED
from botocore.config import Config

from .base import BaseDownloader

_BUCKET = "openalex"
_RELEASE_NOTES = "RELEASE_NOTES.txt"


class OpenAlexDownloader(BaseDownloader):

    def latest_version(self, _: dict) -> str:
        """Return the LastModified date of RELEASE_NOTES.txt as YYYY-MM-DD."""
        s3 = boto3.client("s3", config=Config(signature_version=UNSIGNED))
        head = s3.head_object(Bucket=_BUCKET, Key=_RELEASE_NOTES)
        return head["LastModified"].strftime("%Y-%m-%d")

    def fetch(self, _: dict, dest: Path) -> list[Path]:
        cmd = [
            "aws", "s3", "sync",
            f"s3://{_BUCKET}", str(dest),
            "--no-sign-request",
            "--exclude", "legacy-data/*",
        ]
        print(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            raise RuntimeError(f"aws s3 sync exited with code {result.returncode}")

        return [p for p in dest.rglob("*") if p.is_file()]
