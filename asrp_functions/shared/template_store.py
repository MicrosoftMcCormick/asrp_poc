# Implement `TemplateStore` for the HSBC ASRP Phase 1 POC.
# Responsibilities:
# - Read the HSBC CSR PowerPoint template and supporting assets (icons,
#   pictograms) from Azure Blob Storage using azure.storage.blob and
#   DefaultAzureCredential.
# - Container and blob name come from shared.config (environment-driven).
#   Default template name: "hsbc_csr_template_brand_reviewed.pptx".
# - Public methods:
#     get_template_bytes() -> bytes
#     get_asset_bytes(asset_path: str) -> bytes
#     write_deck_bytes(customer_id: str, run_id: str, data: bytes) -> str
#       (returns blob URL — SAS URL when DECK_DELIVERY_MODE == "sas")
# - All operations stream where possible; do not read entire blobs into
#   memory unless required by python-pptx.
# - Log boundary-safe events only — no payload bodies.

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import (
    BlobClient,
    BlobSasPermissions,
    BlobServiceClient,
    ContainerClient,
    UserDelegationKey,
    generate_blob_sas,
)

from shared.auth import get_credential
from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger(__name__)

_BX_EVENT = "BX_BlobStorage"
_DECK_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
_SAS_TTL = timedelta(hours=1)
_SAS_CLOCK_SKEW = timedelta(minutes=5)
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_\-./]+$")


class TemplateStoreError(RuntimeError):
    """Raised for unrecoverable template/asset/deck I/O failures."""


class TemplateStore:
    """Blob-backed store for the HSBC CSR template, assets and decks."""

    def __init__(self, service_client: BlobServiceClient | None = None) -> None:
        self._settings = get_settings()
        self._service: BlobServiceClient = service_client or BlobServiceClient(
            account_url=str(self._settings.BLOB_ACCOUNT_URL),
            credential=get_credential(),
        )

    # ------------------------------------------------------------------ public

    def get_template_bytes(self) -> bytes:
        """Return the CSR template bytes for python-pptx consumption."""
        container = self._settings.BLOB_TEMPLATE_CONTAINER
        blob_name = self._settings.BLOB_TEMPLATE_NAME
        return self._download(container=container, blob=blob_name, kind="template")

    def get_asset_bytes(self, asset_path: str) -> bytes:
        """Return bytes for a deck asset (icon, pictogram, etc.).

        ``asset_path`` is interpreted as a blob path within the configured
        assets container. Path traversal and absolute paths are rejected
        for defence-in-depth.
        """
        if not asset_path:
            raise ValueError("asset_path must be a non-empty string")
        if asset_path.startswith("/") or ".." in asset_path.split("/"):
            raise ValueError(f"Invalid asset_path: {asset_path!r}")
        if not _SAFE_PATH_RE.match(asset_path):
            raise ValueError(f"Invalid asset_path characters: {asset_path!r}")

        return self._download(
            container=self._settings.BLOB_ASSETS_CONTAINER,
            blob=asset_path,
            kind="asset",
        )

    def write_deck_bytes(
        self, customer_id: str, run_id: str, data: bytes
    ) -> str:
        """Upload the assembled deck and return a caller-usable URL.

        When ``DECK_DELIVERY_MODE == "sas"`` returns a short-lived
        user-delegation SAS URL; otherwise returns the bare blob URL
        (the orchestrator handles Dataverse write-through).
        """
        self._require_id("customer_id", customer_id)
        self._require_id("run_id", run_id)

        container = self._settings.BLOB_DECK_CONTAINER
        blob_name = f"{customer_id}/{run_id}.pptx"
        size_bytes = len(data)

        try:
            container_client: ContainerClient = self._service.get_container_client(
                container
            )
            blob_client: BlobClient = container_client.get_blob_client(blob_name)
            blob_client.upload_blob(
                data,
                overwrite=True,
                length=size_bytes,
                content_type=_DECK_CONTENT_TYPE,
            )
        except Exception as exc:  # noqa: BLE001 - boundary handler
            logger.error(
                _BX_EVENT,
                extra={
                    "phase": "write_deck.error",
                    "container": container,
                    "blob_present": True,
                    "error_type": type(exc).__name__,
                },
            )
            raise TemplateStoreError(
                f"Failed to write deck blob to {container}"
            ) from exc

        logger.info(
            _BX_EVENT,
            extra={
                "phase": "write_deck.ok",
                "container": container,
                "size_bytes": size_bytes,
            },
        )

        if self._settings.DECK_DELIVERY_MODE == "sas":
            return self._sas_url(blob_client)
        return blob_client.url

    # ----------------------------------------------------------------- private

    @staticmethod
    def _require_id(name: str, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
        if "/" in value or ".." in value.split("/"):
            raise ValueError(f"{name} must not contain path separators")

    def _download(self, *, container: str, blob: str, kind: str) -> bytes:
        try:
            container_client = self._service.get_container_client(container)
            blob_client = container_client.get_blob_client(blob)
            stream = blob_client.download_blob()
            payload = stream.readall()
        except ResourceNotFoundError as exc:
            logger.error(
                _BX_EVENT,
                extra={
                    "phase": f"{kind}.not_found",
                    "container": container,
                },
            )
            raise TemplateStoreError(
                f"{kind} blob not found in container {container!r}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - boundary handler
            logger.error(
                _BX_EVENT,
                extra={
                    "phase": f"{kind}.error",
                    "container": container,
                    "error_type": type(exc).__name__,
                },
            )
            raise TemplateStoreError(
                f"Failed to download {kind} from container {container!r}"
            ) from exc

        logger.info(
            _BX_EVENT,
            extra={
                "phase": f"{kind}.ok",
                "container": container,
                "size_bytes": len(payload),
            },
        )
        return payload

    def _sas_url(self, blob_client: BlobClient) -> str:
        now = datetime.now(timezone.utc)
        expiry = now + _SAS_TTL
        start = now - _SAS_CLOCK_SKEW

        try:
            udk: UserDelegationKey = self._service.get_user_delegation_key(
                key_start_time=start, key_expiry_time=expiry
            )
        except Exception as exc:  # noqa: BLE001 - boundary handler
            logger.error(
                _BX_EVENT,
                extra={
                    "phase": "sas.udk_error",
                    "error_type": type(exc).__name__,
                },
            )
            raise TemplateStoreError(
                "Failed to acquire user delegation key for SAS issuance"
            ) from exc

        account_name = blob_client.account_name
        if not account_name:
            raise TemplateStoreError(
                "Blob account name unavailable for SAS issuance"
            )

        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=blob_client.container_name,
            blob_name=blob_client.blob_name,
            user_delegation_key=udk,
            permission=BlobSasPermissions(read=True),
            expiry=expiry,
            start=start,
        )
        logger.info(
            _BX_EVENT,
            extra={
                "phase": "sas.issued",
                "ttl_seconds": int(_SAS_TTL.total_seconds()),
            },
        )
        return f"{blob_client.url}?{sas_token}"


__all__ = ["TemplateStore", "TemplateStoreError"]
