"""Delegated Graph API calls for removing sharing permissions."""

import logging
import httpx

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


async def remove_all_permissions(
    client: httpx.AsyncClient,
    drive_id: str,
    item_id: str,
) -> dict:
    """Remove all non-inherited permissions from a drive item.
    Returns {succeeded: [perm_ids], failed: [{id, error}]}."""
    url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/permissions"
    resp = await client.get(url)
    resp.raise_for_status()
    permissions = resp.json().get("value", [])

    # Filter out inherited permissions
    direct = [
        p
        for p in permissions
        if not (
            p.get("inheritedFrom", {}).get("driveId")
            or p.get("inheritedFrom", {}).get("path")
        )
    ]

    # Also skip "owner" role — can't remove the owner
    removable = [p for p in direct if "owner" not in p.get("roles", [])]

    succeeded = []
    failed = []

    for perm in removable:
        perm_id = perm["id"]
        try:
            del_resp = await client.delete(f"{url}/{perm_id}")
            if del_resp.status_code in (204, 200):
                succeeded.append(perm_id)
            else:
                failed.append({"id": perm_id, "error": f"HTTP {del_resp.status_code}"})
        except Exception as e:
            failed.append({"id": perm_id, "error": str(e)})

    return {"succeeded": succeeded, "failed": failed}


async def bulk_unshare(
    graph_token: str,
    file_ids: list[str],
) -> dict:
    """Remove all sharing from multiple files. file_ids are 'driveId:itemId' strings.
    Returns {succeeded: [file_ids], failed: [{id, error}]}."""
    succeeded = []
    failed = []

    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {graph_token}"},
        timeout=30.0,
    ) as client:
        for file_id in file_ids:
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
                else:
                    succeeded.append(file_id)
                    logger.info(
                        f"Unshared {file_id}: {len(result['succeeded'])} permissions removed"
                    )
            except Exception as e:
                failed.append({"id": file_id, "error": str(e)})
                logger.warning(f"Unshare failed for {file_id}: {e}")

    return {"succeeded": succeeded, "failed": failed}
