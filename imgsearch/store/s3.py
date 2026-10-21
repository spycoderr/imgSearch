"""The boto3 ObjectStore: put, get and head on one versioned S3 bucket (plan D19, P1.6).

The IAM user behind the profile can only put, get and head in this bucket, so nothing
here deletes. Network and credential problems become StoreUnavailable, so callers can
say "offline" and stop cleanly.
"""

import base64
from collections.abc import Iterator
from contextlib import contextmanager

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    ProfileNotFound,
    ReadTimeoutError,
)

from imgsearch.config import S3Config
from imgsearch.interfaces import ObjectInfo, StoreUnavailable

_UNREACHABLE = (
    EndpointConnectionError,
    ConnectTimeoutError,
    ReadTimeoutError,
    ConnectionClosedError,
    NoCredentialsError,
    ProfileNotFound,
)


@contextmanager
def _reachable() -> Iterator[None]:
    try:
        yield
    except _UNREACHABLE as e:
        raise StoreUnavailable(f"S3 unreachable: {type(e).__name__}: {e}") from e


class S3ObjectStore:
    def __init__(self, s3: S3Config) -> None:
        with _reachable():
            session = boto3.Session(profile_name=s3.profile, region_name=s3.region)
        self._client = session.client("s3", config=BotoConfig(retries={"mode": "standard"}))
        self._bucket = s3.bucket
        self._storage_class = s3.storage_class

    def put(
        self, key: str, data: bytes, sha256: str, metadata: dict[str, str] | None = None
    ) -> str:
        # S3 recomputes the SHA-256 and rejects the upload if it differs.
        with _reachable():
            response = self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                StorageClass=self._storage_class,
                ChecksumAlgorithm="SHA256",
                ChecksumSHA256=base64.b64encode(bytes.fromhex(sha256)).decode(),
                Metadata=metadata or {},
            )
        if "VersionId" not in response:
            raise RuntimeError(f"no version id for {key}: is bucket versioning on?")
        return response["VersionId"]

    def get(self, key: str, version_id: str | None = None) -> bytes:
        with _reachable():
            response = self._client.get_object(Bucket=self._bucket, Key=key, **_version(version_id))
            return response["Body"].read()

    def head(self, key: str, version_id: str | None = None) -> ObjectInfo | None:
        with _reachable():
            try:
                response = self._client.head_object(
                    Bucket=self._bucket, Key=key, ChecksumMode="ENABLED", **_version(version_id)
                )
            except ClientError as e:
                if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NoSuchVersion"):
                    return None
                raise
        checksum = response.get("ChecksumSHA256")
        return ObjectInfo(
            size=response["ContentLength"],
            version_id=response.get("VersionId", ""),
            sha256=base64.b64decode(checksum).hex() if checksum else "",
            metadata=response.get("Metadata", {}),
        )


def _version(version_id: str | None) -> dict[str, str]:
    return {"VersionId": version_id} if version_id else {}
