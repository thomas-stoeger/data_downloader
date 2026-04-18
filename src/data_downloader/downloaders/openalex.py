"""
OpenAlex snapshot downloader.

Syncs the OpenAlex S3 public snapshot (s3://openalex) excluding legacy-data/.
Uses boto3 with unsigned requests — no AWS credentials required.

No config keys required.
"""
from pathlib import Path

import boto3
from boto3.s3.transfer import TransferConfig
from botocore import UNSIGNED
from botocore.config import Config
from tqdm import tqdm

from .base import BaseDownloader

_BUCKET = "openalex"
_RELEASE_NOTES = "RELEASE_NOTES.txt"


def _s3_client():
    return boto3.client("s3", config=Config(signature_version=UNSIGNED))


class OpenAlexDownloader(BaseDownloader):

    def latest_version(self, _: dict) -> str:
        """Return the LastModified date of RELEASE_NOTES.txt as YYYY-MM-DD."""
        s3 = _s3_client()
        head = s3.head_object(Bucket=_BUCKET, Key=_RELEASE_NOTES)
        return head["LastModified"].strftime("%Y-%m-%d")

    def fetch(self, _: dict, dest: Path) -> list[Path]:
        s3 = _s3_client()
        transfer_cfg = TransferConfig(max_concurrency=10)

        print("Listing OpenAlex S3 objects (excluding legacy-data/)...")
        paginator = s3.get_paginator("list_objects_v2")
        objects = [
            obj
            for page in paginator.paginate(Bucket=_BUCKET)
            for obj in page.get("Contents", [])
            if not obj["Key"].startswith("legacy-data/")
        ]
        print(f"Found {len(objects)} objects.")

        written: list[Path] = []
        for obj in tqdm(objects, unit="file", desc="openalex"):
            key = obj["Key"]
            remote_size = obj["Size"]
            local_path = dest / key

            if local_path.exists() and local_path.stat().st_size == remote_size:
                written.append(local_path)
                continue

            local_path.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(
                _BUCKET, key, str(local_path), Config=transfer_cfg
            )
            written.append(local_path)

        return written
