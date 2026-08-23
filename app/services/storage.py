from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path

import boto3
from botocore.client import Config

from app.config import get_settings


def _r2_client():
    s = get_settings()
    if not s.r2_endpoint or not s.r2_access_key_id:
        raise RuntimeError("R2 not configured - set R2_ENDPOINT etc in .env")
    return boto3.client(
        "s3",
        endpoint_url=s.r2_endpoint,
        aws_access_key_id=s.r2_access_key_id,
        aws_secret_access_key=s.r2_secret_access_key,
        config=Config(signature_version="s3v4", region_name="auto"),
    )


async def upload_to_r2(filepath: Path) -> str:
    """Upload file to R2/S3 and return presigned URL. Runs boto3 in thread."""
    s = get_settings()
    if not s.r2_bucket:
        raise RuntimeError("R2_BUCKET not set")

    key = f"tmp/{filepath.name}"
    ctype = mimetypes.guess_type(filepath.name)[0] or "application/octet-stream"

    def _upload():
        client = _r2_client()
        client.upload_file(
            str(filepath),
            s.r2_bucket,
            key,
            ExtraArgs={"ContentType": ctype},
        )
        # Generate presigned URL
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": s.r2_bucket, "Key": key},
            ExpiresIn=s.r2_expire_seconds,
        )
        # If R2_PUBLIC_URL is set and bucket is public, prefer that (faster, no signing)
        # But presigned is safer for private buckets
        return url

    return await asyncio.to_thread(_upload)
