"""
Cloudflare R2 Storage Adapter (S3-compatible) with fallback to local storage.
Zero egress bandwidth costs, 10 GB free storage.
"""
import os
import io
from typing import Optional, Tuple

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "").strip()
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME", "qrprint-storage").strip()
R2_PUBLIC_DOMAIN = os.environ.get("R2_PUBLIC_DOMAIN", "").strip().rstrip("/")

_s3_client = None

def is_r2_configured() -> bool:
    return bool(R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET_NAME)

def get_r2_client():
    global _s3_client
    if not is_r2_configured():
        return None
    if _s3_client is None:
        try:
            import boto3
            from botocore.config import Config
            endpoint = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
            _s3_client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=R2_ACCESS_KEY_ID,
                aws_secret_access_key=R2_SECRET_ACCESS_KEY,
                config=Config(signature_version="s3v4"),
                region_name="auto"
            )
        except Exception as e:
            print(f"[R2] Client initialization error: {e}")
            return None
    return _s3_client

def upload_file_to_r2(local_path: str, r2_key: str, content_type: Optional[str] = None) -> bool:
    client = get_r2_client()
    if not client:
        return False
    try:
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type
        client.upload_file(local_path, R2_BUCKET_NAME, r2_key, ExtraArgs=extra_args)
        return True
    except Exception as e:
        print(f"[R2] Upload failed for {r2_key}: {e}")
        return False

def generate_presigned_download_url(r2_key: str, expires_in: int = 3600) -> Optional[str]:
    """Generates direct direct download URL from Cloudflare R2 (0 bandwidth on Render)."""
    if R2_PUBLIC_DOMAIN:
        return f"{R2_PUBLIC_DOMAIN}/{r2_key}"
    client = get_r2_client()
    if not client:
        return None
    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": R2_BUCKET_NAME, "Key": r2_key},
            ExpiresIn=expires_in
        )
        return url
    except Exception as e:
        print(f"[R2] Presigned URL generation failed: {e}")
        return None

def delete_from_r2(r2_key: str) -> bool:
    client = get_r2_client()
    if not client:
        return False
    try:
        client.delete_object(Bucket=R2_BUCKET_NAME, Key=r2_key)
        return True
    except Exception as e:
        print(f"[R2] Delete failed for {r2_key}: {e}")
        return False
