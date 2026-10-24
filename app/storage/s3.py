"""S3-compatible store. Objects stay private; HTML never gets a presigned URL."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import Settings
from app.storage.errors import StorageError
from app.storage.keys import media_type_for, safe_object_key

logger = logging.getLogger("family_album.storage")

_FORBIDDEN = {
    "AccessDenied",
    "AllAccessDisabled",
    "Forbidden",
    "InvalidAccessKeyId",
    "SignatureDoesNotMatch",
    "403",
    "NotAuthorized",
    "Unauthorized",
    "NoSuchBucket",
    "InvalidBucketName",
    "AccessControlListNotSupported",
}

_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
_VIDEO_TYPES = {"video/mp4", "video/quicktime"}
_KEEP_TYPES = _IMAGE_TYPES | _VIDEO_TYPES


def _missing_key(exc: Exception) -> bool:
    if not isinstance(exc, ClientError):
        return False
    response = exc.response if isinstance(exc.response, dict) else {}
    error = response.get("Error") or {}
    code = str(error.get("Code", ""))
    return code in {"NoSuchKey", "NoSuchKeyException"}


def map_s3_error(exc: Exception) -> StorageError:
    if isinstance(exc, ClientError):
        response = exc.response if isinstance(exc.response, dict) else {}
        error = response.get("Error") or {}
        code = str(error.get("Code", ""))
        meta = response.get("ResponseMetadata") or {}
        status = meta.get("HTTPStatusCode")
        if status in {401, 403} or code in _FORBIDDEN:
            return StorageError("S3_FORBIDDEN", "对象存储权限不对")
    return StorageError("S3_UNREACHABLE", "对象存储连不上")


class S3Store:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.settings = settings
        self._client_override = client

    def _client(self):
        if self._client_override is not None:
            return self._client_override
        settings = self.settings
        addressing = "path" if settings.s3_force_path_style else "virtual"
        kwargs: dict[str, Any] = {
            "region_name": settings.s3_region or "us-east-1",
            "aws_access_key_id": settings.s3_access_key or None,
            "aws_secret_access_key": settings.s3_secret_key or None,
            "config": Config(
                connect_timeout=2,
                read_timeout=3,
                retries={"max_attempts": 1},
                s3={"addressing_style": addressing},
            ),
        }
        if settings.s3_endpoint:
            kwargs["endpoint_url"] = settings.s3_endpoint
        return boto3.client("s3", **kwargs)

    def _require_config(self) -> None:
        settings = self.settings
        if not settings.s3_bucket or not settings.s3_access_key or not settings.s3_secret_key:
            raise StorageError("S3_FORBIDDEN", "对象存储权限不对")

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._require_config()
        name = safe_object_key(key)
        try:
            # No ACL: the bucket default stays private. Do not presign.
            self._client().put_object(
                Bucket=self.settings.s3_bucket,
                Key=name,
                Body=data,
                ContentType=content_type,
            )
        except StorageError:
            raise
        except Exception as exc:
            raise map_s3_error(exc) from exc

    def put_file(self, key: str, source: Path, content_type: str) -> None:
        """Upload a file from disk. Single PUT first; multipart only if that fails.

        The body is a file object, not a bytes blob.
        """
        self._require_config()
        name = safe_object_key(key)
        client = self._client()
        try:
            with Path(source).open("rb") as handle:
                client.put_object(
                    Bucket=self.settings.s3_bucket,
                    Key=name,
                    Body=handle,
                    ContentType=content_type,
                )
            return
        except StorageError:
            raise
        except Exception as exc:
            logger.warning("s3 single put failed error=%s", type(exc).__name__)
            first = exc
        try:
            client.upload_file(
                str(source),
                self.settings.s3_bucket,
                name,
                ExtraArgs={"ContentType": content_type},
            )
        except StorageError:
            raise
        except Exception as exc:
            raise map_s3_error(exc) from first

    def open(self, key: str) -> tuple[object, str]:
        self._require_config()
        name = safe_object_key(key)
        try:
            obj = self._client().get_object(Bucket=self.settings.s3_bucket, Key=name)
        except StorageError:
            raise
        except Exception as exc:
            if _missing_key(exc):
                raise FileNotFoundError(name) from exc
            raise map_s3_error(exc) from exc
        ctype = str(obj.get("ContentType") or "")
        if ctype not in _KEEP_TYPES:
            ctype = media_type_for(name)
        return obj["Body"], ctype

    def open_range(self, key: str, start: int, end: int | None = None) -> tuple[object, str]:
        self._require_config()
        name = safe_object_key(key)
        spec = f"bytes={start}-" if end is None else f"bytes={start}-{end}"
        try:
            obj = self._client().get_object(Bucket=self.settings.s3_bucket, Key=name, Range=spec)
        except StorageError:
            raise
        except Exception as exc:
            if _missing_key(exc):
                raise FileNotFoundError(name) from exc
            raise map_s3_error(exc) from exc
        ctype = str(obj.get("ContentType") or "")
        if ctype not in _KEEP_TYPES:
            ctype = media_type_for(name)
        return obj["Body"], ctype

    def delete(self, key: str) -> None:
        name = safe_object_key(key)
        try:
            self._client().delete_object(Bucket=self.settings.s3_bucket, Key=name)
        except Exception as exc:
            logger.warning("s3 delete failed key=%s error=%s", name, type(exc).__name__)

    def probe(self) -> None:
        self._require_config()
        # Slash prefix so this can never equal Photo.storage_path.
        key = f"_probe/{uuid.uuid4().hex}"
        client = self._client()
        try:
            client.put_object(
                Bucket=self.settings.s3_bucket,
                Key=key,
                Body=b"ok",
                ContentType="text/plain",
            )
        except StorageError:
            raise
        except Exception as exc:
            raise map_s3_error(exc) from exc
        try:
            client.delete_object(Bucket=self.settings.s3_bucket, Key=key)
        except Exception as exc:
            # Leave the object. Do not log the exception text; it can echo the key id.
            logger.warning("s3 probe delete failed key=%s error=%s", key, type(exc).__name__)
