"""Delegated Graph API calls for removing sharing permissions."""

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
MAX_RETRIES = 4


async def _request_with_retry(
    client: httpx.AsyncClient, method: str, url: str
) -> httpx.Response:
    """Make an HTTP request with retry on 429 and 5xx errors."""
    for attempt in range(MAX_RETRIES):
        resp = await client.request(method, url)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            logger.warning(f"Rate limited. Waiting {retry_after}s...")
            await asyncio.sleep(retry_after)
            continue
        if resp.status_code >= 500 and attempt < MAX_RETRIES - 1:
            wait = 2**attempt
            logger.warning(f"Server error {resp.status_code}. Retrying in {wait}s...")
            await asyncio.sleep(wait)
            continue
        return resp
    return resp


def _is_removable(perm: dict) -> bool:
    """Return True if a permission is non-inherited and non-owner (can be deleted)."""
    inherited = perm.get("inheritedFrom", {}).get("driveId") or perm.get(
        "inheritedFrom", {}
    ).get("path")
    owner = "owner" in perm.get("roles", [])
    return not inherited and not owner


def _classify_error(status_code: int, resp: httpx.Response | None = None) -> dict:
    """Classify an HTTP error into a structured error with reason, message, and action."""
    if status_code == 403:
        return {
            "reason": "ACCESS_DENIED",
            "message": "Insufficient permissions to modify sharing",
            "action": "Ask a site admin to remove sharing for this file",
        }
    if status_code == 404:
        return {
            "reason": "NOT_FOUND",
            "message": "File or permission no longer exists",
            "action": "It may have already been removed — refresh the page",
        }
    if status_code == 429:
        return {
            "reason": "THROTTLED",
            "message": "Microsoft rate limit exceeded",
            "action": "Wait a few minutes and try again",
        }
    detail = ""
    if resp is not None:
        try:
            detail = resp.json().get("error", {}).get("message", "")
        except Exception:
            pass
    msg = f"Unexpected error (HTTP {status_code})"
    if detail:
        msg += f": {detail}"
    return {
        "reason": "UNKNOWN",
        "message": msg,
        "action": "Check the file directly in SharePoint",
    }


async def remove_all_permissions(
    client: httpx.AsyncClient,
    drive_id: str,
    item_id: str,
) -> dict:
    """Remove all non-inherited permissions from a drive item.
    Returns {succeeded: [perm_ids], failed: [{id, error}], verified: bool}."""
    url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/permissions"
    resp = await _request_with_retry(client, "GET", url)
    resp.raise_for_status()
    permissions = resp.json().get("value", [])

    removable = [p for p in permissions if _is_removable(p)]

    succeeded = []
    failed = []

    for perm in removable:
        perm_id = perm["id"]
        try:
            del_resp = await _request_with_retry(
                client, "DELETE", f"{url}/{perm_id}"
            )
            if del_resp.status_code in (204, 200):
                succeeded.append(perm_id)
            else:
                err = _classify_error(del_resp.status_code, del_resp)
                failed.append({"id": perm_id, **err})
        except Exception as e:
            failed.append({
                "id": perm_id,
                "reason": "UNKNOWN",
                "message": f"Unexpected error: {e}",
                "action": "Check the file directly in SharePoint",
            })

    # Verification: re-fetch permissions and check none remain
    verified = False
    if not failed:
        try:
            verify_resp = await _request_with_retry(client, "GET", url)
            verify_resp.raise_for_status()
            remaining = verify_resp.json().get("value", [])
            remaining_removable = [p for p in remaining if _is_removable(p)]
            verified = len(remaining_removable) == 0
            if not verified:
                logger.warning(
                    f"Verification failed for {drive_id}:{item_id}: "
                    f"{len(remaining_removable)} permissions still present"
                )
        except Exception as e:
            logger.warning(f"Verification request failed for {drive_id}:{item_id}: {e}")

    return {"succeeded": succeeded, "failed": failed, "verified": verified}


async def bulk_unshare(
    graph_token: str,
    file_ids: list[str],
    neo4j_client=None,
) -> dict:
    """Remove all sharing from multiple files. file_ids are 'driveId:itemId' strings.
    Returns {succeeded: [file_ids], failed: [{id, error}]}."""
    succeeded = []
    failed = []

    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {graph_token}"},
        timeout=30.0,
    ) as client:
        for i, file_id in enumerate(file_ids):
            # Inter-file delay to avoid throttling (skip before first file)
            if i > 0:
                await asyncio.sleep(0.5)

            try:
                drive_id, item_id = file_id.split(":", 1)
                result = await remove_all_permissions(client, drive_id, item_id)
                if result["failed"]:
                    failed.append(
                        {
                            "id": file_id,
                            "error": f"{len(result['failed'])} permissions failed",
                        }
                    )
                elif not result["verified"]:
                    failed.append(
                        {"id": file_id, "error": "verification failed"}
                    )
                    logger.warning(f"Unshare not verified for {file_id}")
                else:
                    succeeded.append(file_id)
                    logger.info(
                        f"Unshared {file_id}: "
                        f"{len(result['succeeded'])} permissions removed (verified)"
                    )
                    if neo4j_client is not None:
                        try:
                            neo4j_client.remove_shared_with(drive_id, item_id)
                            logger.info(
                                f"Neo4j cleanup done for {file_id}"
                            )
                        except Exception as e:
                            logger.warning(
                                f"Neo4j cleanup failed for {file_id}: {e}"
                            )
            except Exception as e:
                failed.append({"id": file_id, "error": str(e)})
                logger.warning(f"Unshare failed for {file_id}: {e}")

    return {"succeeded": succeeded, "failed": failed}
