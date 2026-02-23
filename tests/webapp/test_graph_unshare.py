# tests/webapp/test_graph_unshare.py
import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock
from webapp.graph_unshare import remove_all_permissions


class TestRemoveAllPermissions:
    @pytest.mark.asyncio
    async def test_deletes_non_inherited_permissions(self):
        """Should fetch permissions, filter inherited, DELETE each remaining one."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)

        # GET permissions response
        perms_response = MagicMock()
        perms_response.status_code = 200
        perms_response.json.return_value = {
            "value": [
                {"id": "perm-1", "roles": ["read"]},
                {
                    "id": "perm-2",
                    "roles": ["write"],
                    "inheritedFrom": {"driveId": "d0"},
                },
                {"id": "perm-3", "roles": ["read"], "link": {"scope": "anonymous"}},
            ]
        }

        # DELETE responses
        delete_response = MagicMock()
        delete_response.status_code = 204

        mock_client.get.return_value = perms_response
        mock_client.delete.return_value = delete_response

        result = await remove_all_permissions(mock_client, "d1", "i1")
        assert result["succeeded"] == ["perm-1", "perm-3"]
        assert result["failed"] == []
        # perm-2 is inherited, should NOT be deleted
        assert mock_client.delete.call_count == 2
