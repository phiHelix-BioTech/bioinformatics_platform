import boto3
from botocore.exceptions import ClientError

from app.config import settings
from app.services.storage.base import StorageBackend


def _client():
    kwargs: dict = {
        "region_name":           settings.AWS_REGION,
        "aws_access_key_id":     settings.AWS_ACCESS_KEY_ID or None,
        "aws_secret_access_key": settings.AWS_SECRET_ACCESS_KEY or None,
    }
    # S3_ENDPOINT_URL enables Turkish-cloud S3-compatible storage
    # (Huawei OBS: https://obs.tr-west-1.myhuaweicloud.com,
    #  Turkcell nDepo: provided at account creation)
    if settings.S3_ENDPOINT_URL:
        kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL
    return boto3.client("s3", **kwargs)


class S3StorageBackend(StorageBackend):
    def generate_upload_url(self, storage_key: str) -> str:
        """Return a presigned PUT URL so the browser uploads directly to S3."""
        try:
            url = _client().generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": settings.S3_BUCKET,
                    "Key": storage_key,
                    "ContentType": "application/octet-stream",
                },
                ExpiresIn=settings.S3_PRESIGN_EXPIRY,
            )
            return url
        except ClientError as e:
            raise RuntimeError(f"Failed to generate S3 presigned URL: {e}") from e

    def file_path(self, storage_key: str) -> str:
        """Return the S3 URI used by pipeline runners to access the file."""
        return f"s3://{settings.S3_BUCKET}/{storage_key}"

    def generate_download_url(self, path: str, expiry: int = 3600) -> str | None:
        """Generate a presigned GET URL for an S3 URI or bare object key."""
        # Normalise: strip s3://bucket-name/ prefix if present
        if path.startswith("s3://"):
            # s3://bucket/key  →  key
            without_scheme = path[5:]  # strip "s3://"
            slash = without_scheme.find("/")
            key = without_scheme[slash + 1:] if slash >= 0 else without_scheme
        else:
            key = path

        try:
            url = _client().generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.S3_BUCKET, "Key": key},
                ExpiresIn=expiry,
            )
            return url
        except ClientError as e:
            raise RuntimeError(f"Failed to generate S3 download URL: {e}") from e

    def delete(self, storage_key: str) -> None:
        """Delete an object from S3 by its storage key."""
        key = storage_key
        if storage_key.startswith("s3://"):
            without_scheme = storage_key[5:]
            slash = without_scheme.find("/")
            key = without_scheme[slash + 1:] if slash >= 0 else without_scheme
        try:
            _client().delete_object(Bucket=settings.S3_BUCKET, Key=key)
        except ClientError as e:
            raise RuntimeError(f"Failed to delete S3 object: {e}") from e
