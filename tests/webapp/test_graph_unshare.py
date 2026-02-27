# tests/webapp/test_graph_unshare.py
import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from webapp.graph_unshare import remove_all_permissions, bulk_unshare


def _make_response(status_code=200, json_data=None, headers=None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.headers = headers or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    return resp


class TestRemoveAllPermissions:
    @pytest.mark.asyncio
    async def test_deletes_non_inherited_permissions_and_verifies(self):
        """Should fetch permissions, filter inherited/owner, DELETE each remaining, then verify."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)

        perms_response = _make_response(
            json_data={
                "value": [
                    {"id": "perm-1", "roles": ["read"]},
                    {
                        "id": "perm-2",
                        "roles": ["write"],
                        "inheritedFrom": {"driveId": "d0"},
                    },
                    {"id": "perm-3", "roles": ["read"], "link": {"scope": "anonymous"}},
                    {"id": "perm-owner", "roles": ["owner"]},
                ]
            }
        )

        # Verification response: only inherited + owner remain
        verify_response = _make_response(
            json_data={
                "value": [
                    {
                        "id": "perm-2",
                        "roles": ["write"],
                        "inheritedFrom": {"driveId": "d0"},
                    },
                    {"id": "perm-owner", "roles": ["owner"]},
                ]
            }
        )

        delete_response = _make_response(status_code=204)

        # GET(permissions), DELETE(perm-1), DELETE(perm-3), GET(verify)
        mock_client.request.side_effect = [
            perms_response,
            delete_response,
            delete_response,
            verify_response,
        ]

        result = await remove_all_permissions(mock_client, "d1", "i1")
        assert result["succeeded"] == ["perm-1", "perm-3"]
        assert result["failed"] == []
        assert result["verified"] is True
        assert mock_client.request.call_count == 4

    @pytest.mark.asyncio
    async def test_verification_fails_when_permissions_remain(self):
        """Verification should fail if removable permissions still present after deletion."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)

        perms_response = _make_response(
            json_data={"value": [{"id": "perm-1", "roles": ["read"]}]}
        )
        delete_response = _make_response(status_code=204)
        # Verification shows perm is still there
        verify_response = _make_response(
            json_data={"value": [{"id": "perm-1", "roles": ["read"]}]}
        )

        mock_client.request.side_effect = [
            perms_response,
            delete_response,
            verify_response,
        ]

        result = await remove_all_permissions(mock_client, "d1", "i1")
        assert result["succeeded"] == ["perm-1"]
        assert result["failed"] == []
        assert result["verified"] is False

    @pytest.mark.asyncio
    async def test_classifies_403_as_access_denied(self):
        """HTTP 403 on DELETE should produce ACCESS_DENIED structured error."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)

        perms_response = _make_response(
            json_data={"value": [{"id": "perm-1", "roles": ["read"]}]}
        )
        forbidden_response = _make_response(
            status_code=403,
            json_data={"error": {"code": "accessDenied", "message": "Access denied"}},
        )

        mock_client.request.side_effect = [perms_response, forbidden_response]

        result = await remove_all_permissions(mock_client, "d1", "i1")
        assert len(result["failed"]) == 1
        assert result["failed"][0]["reason"] == "ACCESS_DENIED"
        assert "action" in result["failed"][0]

    @pytest.mark.asyncio
    async def test_classifies_404_as_not_found(self):
        """HTTP 404 on DELETE should produce NOT_FOUND structured error."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)

        perms_response = _make_response(
            json_data={"value": [{"id": "perm-1", "roles": ["read"]}]}
        )
        not_found_response = _make_response(status_code=404)

        mock_client.request.side_effect = [perms_response, not_found_response]

        result = await remove_all_permissions(mock_client, "d1", "i1")
        assert len(result["failed"]) == 1
        assert result["failed"][0]["reason"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_retry_on_429(self):
        """Should retry after 429 with Retry-After header."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)

        throttled = _make_response(status_code=429, headers={"Retry-After": "1"})
        perms_response = _make_response(json_data={"value": []})
        verify_response = _make_response(json_data={"value": []})

        mock_client.request.side_effect = [throttled, perms_response, verify_response]

        with patch("webapp.graph_unshare.asyncio.sleep", new_callable=AsyncMock):
            result = await remove_all_permissions(mock_client, "d1", "i1")

        assert result["succeeded"] == []
        assert result["failed"] == []
        assert result["verified"] is True


class TestBulkUnshare:
    @pytest.mark.asyncio
    async def test_neo4j_cleanup_on_verified_success(self):
        """Should call neo4j_client.remove_shared_with for verified files."""
        mock_neo4j = MagicMock()

        perms_resp = _make_response(
            json_data={"value": [{"id": "p1", "roles": ["read"]}]}
        )
        del_resp = _make_response(status_code=204)
        verify_resp = _make_response(json_data={"value": []})

        with patch("webapp.graph_unshare.httpx.AsyncClient") as MockClient:
            ctx = AsyncMock()
            ctx.request.side_effect = [perms_resp, del_resp, verify_resp]
            MockClient.return_value.__aenter__ = AsyncMock(return_value=ctx)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await bulk_unshare(
                "token", ["d1:i1"], neo4j_client=mock_neo4j
            )

        assert result["succeeded"] == ["d1:i1"]
        assert result["failed"] == []
        mock_neo4j.remove_shared_with.assert_called_once_with("d1", "i1")

    @pytest.mark.asyncio
    async def test_neo4j_skipped_when_verification_fails(self):
        """Should NOT call neo4j cleanup when verification fails."""
        mock_neo4j = MagicMock()

        perms_resp = _make_response(
            json_data={"value": [{"id": "p1", "roles": ["read"]}]}
        )
        del_resp = _make_response(status_code=204)
        # Verification shows permission still present
        verify_resp = _make_response(
            json_data={"value": [{"id": "p1", "roles": ["read"]}]}
        )

        with patch("webapp.graph_unshare.httpx.AsyncClient") as MockClient:
            ctx = AsyncMock()
            ctx.request.side_effect = [perms_resp, del_resp, verify_resp]
            MockClient.return_value.__aenter__ = AsyncMock(return_value=ctx)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await bulk_unshare(
                "token", ["d1:i1"], neo4j_client=mock_neo4j
            )

        assert result["succeeded"] == []
        assert len(result["failed"]) == 1
        assert result["failed"][0]["reason"] == "VERIFICATION_FAILED"
        assert "action" in result["failed"][0]
        mock_neo4j.remove_shared_with.assert_not_called()

    @pytest.mark.asyncio
    async def test_neo4j_failure_does_not_demote_succeeded(self):
        """Neo4j cleanup failure should NOT move file from succeeded to failed."""
        mock_neo4j = MagicMock()
        mock_neo4j.remove_shared_with.side_effect = Exception("Neo4j down")

        perms_resp = _make_response(
            json_data={"value": [{"id": "p1", "roles": ["read"]}]}
        )
        del_resp = _make_response(status_code=204)
        verify_resp = _make_response(json_data={"value": []})

        with patch("webapp.graph_unshare.httpx.AsyncClient") as MockClient:
            ctx = AsyncMock()
            ctx.request.side_effect = [perms_resp, del_resp, verify_resp]
            MockClient.return_value.__aenter__ = AsyncMock(return_value=ctx)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await bulk_unshare(
                "token", ["d1:i1"], neo4j_client=mock_neo4j
            )

        # File should still be in succeeded despite Neo4j failure
        assert result["succeeded"] == ["d1:i1"]
        assert result["failed"] == []

    @pytest.mark.asyncio
    async def test_bulk_unshare_without_neo4j_client(self):
        """Should work fine when neo4j_client is None."""
        perms_resp = _make_response(
            json_data={"value": [{"id": "p1", "roles": ["read"]}]}
        )
        del_resp = _make_response(status_code=204)
        verify_resp = _make_response(json_data={"value": []})

        with patch("webapp.graph_unshare.httpx.AsyncClient") as MockClient:
            ctx = AsyncMock()
            ctx.request.side_effect = [perms_resp, del_resp, verify_resp]
            MockClient.return_value.__aenter__ = AsyncMock(return_value=ctx)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await bulk_unshare("token", ["d1:i1"])

        assert result["succeeded"] == ["d1:i1"]
        assert result["failed"] == []

    @pytest.mark.asyncio
    async def test_structured_error_propagated_from_permission_failure(self):
        """Permission-level structured errors should propagate to file-level."""
        perms_resp = _make_response(
            json_data={"value": [{"id": "p1", "roles": ["read"]}]}
        )
        forbidden_resp = _make_response(status_code=403)

        with patch("webapp.graph_unshare.httpx.AsyncClient") as MockClient:
            ctx = AsyncMock()
            ctx.request.side_effect = [perms_resp, forbidden_resp]
            MockClient.return_value.__aenter__ = AsyncMock(return_value=ctx)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await bulk_unshare("token", ["d1:i1"])

        assert result["succeeded"] == []
        assert len(result["failed"]) == 1
        assert result["failed"][0]["id"] == "d1:i1"
        assert result["failed"][0]["reason"] == "ACCESS_DENIED"
        assert "action" in result["failed"][0]
