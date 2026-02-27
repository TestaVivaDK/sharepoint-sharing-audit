# Unshare Error Reasons Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give users structured error reasons and actionable guidance when bulk unshare fails, via backend error classification and a frontend result dialog.

**Architecture:** Backend classifies Graph API errors into 5 reason codes (ACCESS_DENIED, NOT_FOUND, THROTTLED, VERIFICATION_FAILED, UNKNOWN), each with a user-facing message and suggested action. Frontend replaces the toast with an always-shown result dialog listing per-file outcomes. Failed files show reason chips and action text; a "Retry Failed" button re-runs throttled files.

**Tech Stack:** Python/FastAPI backend, React 19 + MUI 7 + TanStack Query frontend, pytest for backend tests, `tsc -b && vite build` for frontend verification.

---

### Task 1: Backend — Add `_classify_error()` and update `remove_all_permissions()`

**Files:**
- Modify: `src/webapp/graph_unshare.py`
- Test: `tests/webapp/test_graph_unshare.py`

**Step 1: Write failing tests for structured error format**

Add these tests to `tests/webapp/test_graph_unshare.py`. The existing `TestRemoveAllPermissions` tests need their assertions updated, and new tests added for error classification.

```python
# In TestRemoveAllPermissions, update test_deletes_non_inherited_permissions_and_verifies:
# Change assertion from:
#   assert result["failed"] == []
# To:
#   assert result["failed"] == []
# (no change for success case — failed is still empty list)

# Add new test:
class TestRemoveAllPermissions:
    # ... existing tests ...

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
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/webapp/test_graph_unshare.py -v`
Expected: New tests FAIL because `failed` entries don't have `reason`/`action` keys yet.

**Step 3: Implement `_classify_error()` and update `remove_all_permissions()`**

In `src/webapp/graph_unshare.py`, add after the `_is_removable` function:

```python
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
```

Update the DELETE loop in `remove_all_permissions()` — replace the `else` branch and `except` block:

```python
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
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/webapp/test_graph_unshare.py -v`
Expected: All tests PASS.

**Step 5: Commit**

```bash
git add src/webapp/graph_unshare.py tests/webapp/test_graph_unshare.py
git commit -m "feat(webapp): add _classify_error and structured errors to remove_all_permissions"
```

---

### Task 2: Backend — Update `bulk_unshare()` to propagate structured errors

**Files:**
- Modify: `src/webapp/graph_unshare.py`
- Test: `tests/webapp/test_graph_unshare.py`

**Step 1: Write failing test for structured bulk errors**

Update `TestBulkUnshare.test_neo4j_skipped_when_verification_fails` to assert the structured format:

```python
    @pytest.mark.asyncio
    async def test_neo4j_skipped_when_verification_fails(self):
        """Should NOT call neo4j cleanup when verification fails."""
        # ... (same setup as current) ...

        assert result["succeeded"] == []
        assert len(result["failed"]) == 1
        assert result["failed"][0]["reason"] == "VERIFICATION_FAILED"
        assert "action" in result["failed"][0]
        mock_neo4j.remove_shared_with.assert_not_called()
```

Add a new test for permission-level failure propagation:

```python
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
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/webapp/test_graph_unshare.py::TestBulkUnshare -v`
Expected: FAIL — `result["failed"][0]` has no `reason` key yet.

**Step 3: Update `bulk_unshare()` to propagate structured errors**

Replace the error-handling logic in `bulk_unshare()` (the `if result["failed"]` / `elif not result["verified"]` branches):

```python
                result = await remove_all_permissions(client, drive_id, item_id)
                if result["failed"]:
                    # Pick most actionable reason from permission-level failures
                    priority = {
                        "ACCESS_DENIED": 0,
                        "THROTTLED": 1,
                        "NOT_FOUND": 2,
                        "UNKNOWN": 3,
                    }
                    best = min(
                        result["failed"],
                        key=lambda f: priority.get(f.get("reason", "UNKNOWN"), 99),
                    )
                    failed.append({
                        "id": file_id,
                        "reason": best.get("reason", "UNKNOWN"),
                        "message": best.get("message", "Permission removal failed"),
                        "action": best.get("action", "Check the file directly in SharePoint"),
                    })
                elif not result["verified"]:
                    failed.append({
                        "id": file_id,
                        "reason": "VERIFICATION_FAILED",
                        "message": "Permissions deleted but some reappeared",
                        "action": "Try again, or remove sharing manually in SharePoint",
                    })
                    logger.warning(f"Unshare not verified for {file_id}")
```

Also update the outer `except Exception as e` block:

```python
            except Exception as e:
                failed.append({
                    "id": file_id,
                    "reason": "UNKNOWN",
                    "message": f"Unexpected error: {e}",
                    "action": "Check the file directly in SharePoint",
                })
                logger.warning(f"Unshare failed for {file_id}: {e}")
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/webapp/test_graph_unshare.py -v`
Expected: All tests PASS.

**Step 5: Update `test_routes_unshare.py` mock return value**

The mock return value in `test_unshare_calls_bulk_unshare` still uses the old format — it returns `{"succeeded": [...], "failed": []}` which is still valid (empty failed array). No change needed there. But verify it still passes:

Run: `python -m pytest tests/webapp/test_routes_unshare.py -v`
Expected: All tests PASS.

**Step 6: Commit**

```bash
git add src/webapp/graph_unshare.py tests/webapp/test_graph_unshare.py
git commit -m "feat(webapp): propagate structured error reasons in bulk_unshare"
```

---

### Task 3: Frontend — Update types

**Files:**
- Modify: `frontend/src/api/types.ts`

**Step 1: Update `UnshareResponse` type**

Replace the `UnshareResponse` interface in `frontend/src/api/types.ts` (lines 31-34):

```typescript
export type UnshareReason = 'ACCESS_DENIED' | 'NOT_FOUND' | 'THROTTLED' | 'VERIFICATION_FAILED' | 'UNKNOWN'

export interface UnshareFailure {
  id: string
  reason: UnshareReason
  message: string
  action: string
}

export interface UnshareResponse {
  succeeded: string[]
  failed: UnshareFailure[]
}
```

**Step 2: Verify build**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: Should pass — `UnshareResponse.failed` is only read from API response data, and the old `.error` field is only accessed in `UnshareButton.tsx` which we'll update in Task 5. If there's a type error in `UnshareButton.tsx`, that's expected and will be fixed in Task 5.

**Step 3: Commit**

```bash
git add frontend/src/api/types.ts
git commit -m "feat(frontend): add UnshareFailure type with structured error fields"
```

---

### Task 4: Frontend — Create `UnshareResultDialog` component

**Files:**
- Create: `frontend/src/components/UnshareResultDialog.tsx`

**Step 1: Create the component**

Create `frontend/src/components/UnshareResultDialog.tsx`:

```tsx
import {
  Dialog, DialogTitle, DialogContent, DialogActions,
  Button, List, ListItem, ListItemIcon, ListItemText,
  Chip, Typography, Box, Collapse,
} from '@mui/material'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import ErrorIcon from '@mui/icons-material/Error'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import { useState } from 'react'
import type { UnshareResponse, UnshareReason, SharedFile, FilesResponse } from '../api/types'
import { useQueryClient } from '@tanstack/react-query'

const reasonColor: Record<UnshareReason, 'error' | 'warning' | 'info' | 'default'> = {
  ACCESS_DENIED: 'error',
  THROTTLED: 'warning',
  VERIFICATION_FAILED: 'warning',
  NOT_FOUND: 'info',
  UNKNOWN: 'default',
}

const reasonLabel: Record<UnshareReason, string> = {
  ACCESS_DENIED: 'Access Denied',
  THROTTLED: 'Rate Limited',
  VERIFICATION_FAILED: 'Not Verified',
  NOT_FOUND: 'Not Found',
  UNKNOWN: 'Error',
}

interface Props {
  open: boolean
  result: UnshareResponse
  onClose: () => void
  onRetry: (fileIds: string[]) => void
}

export function UnshareResultDialog({ open, result, onClose, onRetry }: Props) {
  const [showSucceeded, setShowSucceeded] = useState(result.failed.length === 0)
  const queryClient = useQueryClient()

  // Build a lookup of file paths from cached query data
  const fileMap = new Map<string, SharedFile>()
  const allCached = queryClient.getQueriesData<FilesResponse>({ queryKey: ['files'] })
  for (const [, cached] of allCached) {
    if (!cached) continue
    for (const f of cached.files) {
      fileMap.set(f.id, f)
    }
  }

  const retryableIds = result.failed
    .filter((f) => f.reason === 'THROTTLED')
    .map((f) => f.id)

  const allSucceeded = result.failed.length === 0
  const allFailed = result.succeeded.length === 0

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        Unshare Results
        <Box component="span" sx={{ ml: 1 }}>
          {allSucceeded && (
            <Chip label={`${result.succeeded.length} succeeded`} color="success" size="small" />
          )}
          {allFailed && (
            <Chip label={`${result.failed.length} failed`} color="error" size="small" />
          )}
          {!allSucceeded && !allFailed && (
            <>
              <Chip label={`${result.succeeded.length} succeeded`} color="success" size="small" sx={{ mr: 0.5 }} />
              <Chip label={`${result.failed.length} failed`} color="error" size="small" />
            </>
          )}
        </Box>
      </DialogTitle>
      <DialogContent dividers sx={{ maxHeight: 400 }}>
        {result.succeeded.length > 0 && (
          <>
            <Box
              sx={{ display: 'flex', alignItems: 'center', cursor: 'pointer', mb: 1 }}
              onClick={() => setShowSucceeded(!showSucceeded)}
            >
              <CheckCircleIcon color="success" sx={{ mr: 1 }} />
              <Typography variant="subtitle2">
                {result.succeeded.length} file{result.succeeded.length > 1 ? 's' : ''} unshared
              </Typography>
              {result.failed.length > 0 && (showSucceeded ? <ExpandLessIcon /> : <ExpandMoreIcon />)}
            </Box>
            <Collapse in={showSucceeded}>
              <List dense disablePadding>
                {result.succeeded.map((id) => (
                  <ListItem key={id} sx={{ pl: 4 }}>
                    <ListItemText
                      primary={fileMap.get(id)?.item_path ?? id}
                      primaryTypographyProps={{ variant: 'body2', noWrap: true }}
                    />
                  </ListItem>
                ))}
              </List>
            </Collapse>
          </>
        )}

        {result.failed.length > 0 && (
          <>
            <Box sx={{ display: 'flex', alignItems: 'center', mt: result.succeeded.length > 0 ? 2 : 0, mb: 1 }}>
              <ErrorIcon color="error" sx={{ mr: 1 }} />
              <Typography variant="subtitle2">
                {result.failed.length} file{result.failed.length > 1 ? 's' : ''} failed
              </Typography>
            </Box>
            <List dense disablePadding>
              {result.failed.map((f) => (
                <ListItem key={f.id} sx={{ pl: 4, alignItems: 'flex-start' }}>
                  <ListItemIcon sx={{ minWidth: 32, mt: 0.5 }}>
                    <Chip
                      label={reasonLabel[f.reason]}
                      color={reasonColor[f.reason]}
                      size="small"
                      sx={{ fontSize: '0.7rem' }}
                    />
                  </ListItemIcon>
                  <ListItemText
                    primary={fileMap.get(f.id)?.item_path ?? f.id}
                    secondary={f.action}
                    primaryTypographyProps={{ variant: 'body2', noWrap: true }}
                    secondaryTypographyProps={{ variant: 'caption' }}
                  />
                </ListItem>
              ))}
            </List>
          </>
        )}
      </DialogContent>
      <DialogActions>
        {retryableIds.length > 0 && (
          <Button onClick={() => { onClose(); onRetry(retryableIds) }} color="primary">
            Retry Failed ({retryableIds.length})
          </Button>
        )}
        <Button onClick={onClose} variant="contained">Close</Button>
      </DialogActions>
    </Dialog>
  )
}
```

**Step 2: Verify it compiles**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: May fail if MUI icons package isn't installed yet. If so, install it first:

Run: `cd frontend && npm install @mui/icons-material`

Then re-run tsc.

**Step 3: Commit**

```bash
git add frontend/src/components/UnshareResultDialog.tsx frontend/package.json frontend/package-lock.json
git commit -m "feat(frontend): add UnshareResultDialog component with per-file error details"
```

---

### Task 5: Frontend — Replace toast with result dialog in `UnshareButton`

**Files:**
- Modify: `frontend/src/components/UnshareButton.tsx`

**Step 1: Rewrite `UnshareButton.tsx`**

Replace the entire file content:

```tsx
import { useState } from 'react'
import { Button, Dialog, DialogTitle, DialogContent, DialogActions, Typography } from '@mui/material'
import { useUnshare } from '../api/hooks'
import { UnshareResultDialog } from './UnshareResultDialog'
import type { UnshareResponse } from '../api/types'

interface Props {
  selectedIds: string[]
  onComplete: () => void
}

export function UnshareButton({ selectedIds, onComplete }: Props) {
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [result, setResult] = useState<UnshareResponse | null>(null)
  const unshare = useUnshare()

  const handleConfirm = async () => {
    setConfirmOpen(false)
    try {
      const res = await unshare.mutateAsync(selectedIds)
      setResult(res)
      onComplete()
    } catch {
      setResult({
        succeeded: [],
        failed: selectedIds.map((id) => ({
          id,
          reason: 'UNKNOWN' as const,
          message: 'Request failed',
          action: 'Check your connection and try again',
        })),
      })
    }
  }

  const handleRetry = async (fileIds: string[]) => {
    try {
      const res = await unshare.mutateAsync(fileIds)
      setResult(res)
    } catch {
      setResult({
        succeeded: [],
        failed: fileIds.map((id) => ({
          id,
          reason: 'UNKNOWN' as const,
          message: 'Request failed',
          action: 'Check your connection and try again',
        })),
      })
    }
  }

  return (
    <>
      <Button
        variant="contained"
        color="error"
        disabled={selectedIds.length === 0 || unshare.isPending}
        onClick={() => setConfirmOpen(true)}
      >
        {unshare.isPending ? 'Removing...' : `Remove Sharing (${selectedIds.length})`}
      </Button>

      <Dialog open={confirmOpen} onClose={() => setConfirmOpen(false)}>
        <DialogTitle>Remove All Sharing</DialogTitle>
        <DialogContent>
          <Typography>
            Remove all sharing from <strong>{selectedIds.length}</strong> file{selectedIds.length > 1 ? 's' : ''}?
            This cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmOpen(false)}>Cancel</Button>
          <Button onClick={handleConfirm} color="error" variant="contained">Remove Sharing</Button>
        </DialogActions>
      </Dialog>

      {result && (
        <UnshareResultDialog
          open={!!result}
          result={result}
          onClose={() => setResult(null)}
          onRetry={handleRetry}
        />
      )}
    </>
  )
}
```

**Step 2: Verify build**

Run: `cd frontend && npx tsc -b --noEmit && npx vite build`
Expected: PASS — no type errors, clean build.

**Step 3: Commit**

```bash
git add frontend/src/components/UnshareButton.tsx
git commit -m "feat(frontend): replace unshare toast with result dialog showing per-file errors"
```

---

### Task 6: Final verification

**Step 1: Run all backend tests**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All tests PASS.

**Step 2: Run frontend build**

Run: `cd frontend && npx vite build`
Expected: Clean build, no errors.

**Step 3: Commit if any fixups needed, then push**

```bash
git push
```
