"""
S3-compatible object storage client for presigned URL generation.

Supports MinIO, AWS S3, Timeweb Object Storage, Yandex Cloud, Selectel,
or any other S3-compatible provider.

The orchestrator uses this to generate temporary presigned PUT URLs
that workers use to upload PDFs directly to S3 without the bytes
ever passing through the orchestrator.
"""
import os
from datetime import timedelta
from typing import Optional, Dict, List

from minio import Minio
from minio.error import S3Error

from src.utils.logger import get_logger

logger = get_logger(__name__)


class S3Client:
    """
    S3 client wrapper for presigned URL generation.

    Environment:
        S3_ENDPOINT      — e.g., "s3.timeweb.cloud" or "storage.yandexcloud.net"
        S3_ACCESS_KEY    — access key
        S3_SECRET_KEY    — secret key
        S3_BUCKET        — bucket name (default: "arbitr-pdfs")
        S3_REGION        — region (default: "ru-1")
        S3_SECURE        — use HTTPS (default: true)
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        bucket: Optional[str] = None,
        region: Optional[str] = None,
        secure: Optional[bool] = None,
    ):
        self.endpoint = endpoint or os.environ.get("S3_ENDPOINT", "")
        self.access_key = access_key or os.environ.get("S3_ACCESS_KEY", "")
        self.secret_key = secret_key or os.environ.get("S3_SECRET_KEY", "")
        self.bucket = bucket or os.environ.get("S3_BUCKET", "arbitr-pdfs")
        self.region = region or os.environ.get("S3_REGION", "ru-1")
        self.secure = secure if secure is not None else (
            os.environ.get("S3_SECURE", "true").lower() == "true"
        )

        self._client: Optional[Minio] = None

    def _get_client(self) -> Minio:
        if self._client is None:
            if not self.endpoint or not self.access_key or not self.secret_key:
                raise RuntimeError(
                    "S3 not configured. Set S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY."
                )
            self._client = Minio(
                self.endpoint,
                access_key=self.access_key,
                secret_key=self.secret_key,
                region=self.region,
                secure=self.secure,
            )
            # Ensure bucket exists
            if not self._client.bucket_exists(self.bucket):
                self._client.make_bucket(self.bucket)
                logger.info("Created S3 bucket: %s", self.bucket)
        return self._client

    def generate_presigned_upload_url(
        self,
        case_id: str,
        doc_id: str,
        filename: str,
        expiry_seconds: int = 900,  # 15 minutes
    ) -> str:
        """
        Generate a presigned PUT URL for a worker to upload a PDF directly.

        Returns:
            A presigned URL that the worker can PUT PDF bytes to.
        """
        # Build object key: pdfs/{case_id}/{doc_id}_{filename}
        safe_name = filename.replace(" ", "_").replace("/", "_")
        object_name = f"pdfs/{case_id}/{doc_id}_{safe_name}"

        client = self._get_client()
        url = client.presigned_put_object(
            bucket_name=self.bucket,
            object_name=object_name,
            expires=timedelta(seconds=expiry_seconds),
        )
        return url

    def generate_presigned_upload_urls(
        self,
        documents: List[Dict[str, str]],
        expiry_seconds: int = 900,
    ) -> Dict[str, Dict[str, str]]:
        """
        Generate presigned URLs for multiple documents.

        Args:
            documents: List of dicts with keys: case_id, doc_id, filename

        Returns:
            {case_id: {doc_id: presigned_url}}
        """
        urls: Dict[str, Dict[str, str]] = {}
        for doc in documents:
            case_id = doc["case_id"]
            doc_id = doc["doc_id"]
            filename = doc["filename"]

            url = self.generate_presigned_upload_url(
                case_id=case_id,
                doc_id=doc_id,
                filename=filename,
                expiry_seconds=expiry_seconds,
            )

            urls.setdefault(case_id, {})[doc_id] = url

        return urls

    def get_storage_key(self, case_id: str, doc_id: str, filename: str) -> str:
        """Get the S3 object key for a document."""
        safe_name = filename.replace(" ", "_").replace("/", "_")
        return f"pdfs/{case_id}/{doc_id}_{safe_name}"

    def is_configured(self) -> bool:
        """Check if S3 is properly configured."""
        return bool(self.endpoint and self.access_key and self.secret_key)
