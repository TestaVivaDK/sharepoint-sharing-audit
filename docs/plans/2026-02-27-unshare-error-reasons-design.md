# Structured Unshare Error Reasons

## Problem

When bulk unshare fails for some files, users see only generic messages like "2 succeeded, 1 failed" with no explanation of why or what to do. The backend returns opaque error strings (`"2 permissions failed"`, `"verification failed"`) that the frontend can't act on.

## Design

### Error Categories

Five reason codes classify all failure modes:

| Reason | Trigger | User-Facing Message | Suggested Action |
|---|---|---|---|
| `ACCESS_DENIED` | HTTP 403 from Graph API | Insufficient permissions to modify sharing | Ask a site admin to remove sharing for this file |
| `NOT_FOUND` | HTTP 404 from Graph API | File or permission no longer exists | It may have already been removed — refresh the page |
| `THROTTLED` | 429 after all retries exhausted | Microsoft rate limit exceeded | Wait a few minutes and try again |
| `VERIFICATION_FAILED` | Removable permissions remain after deletion | Permissions deleted but some reappeared | Try again, or remove sharing manually in SharePoint |
| `UNKNOWN` | Any other error | Unexpected error: {detail} | Check the file directly in SharePoint |

### Backend Changes

**API response shape** (extends existing, no breaking change to `succeeded`):

```json
{
  "succeeded": ["d1:i1"],
  "failed": [
    {
      "id": "d2:i2",
      "reason": "ACCESS_DENIED",
      "message": "Insufficient permissions to modify sharing",
      "action": "Ask a site admin to remove sharing for this file"
    }
  ]
}
```

**`graph_unshare.py`**:

- `_classify_error(status_code, response_body, context)` — maps HTTP status and Graph error codes to `(reason, message, action)` tuples.
- `remove_all_permissions()` — parses Graph API error response body on non-204 DELETE. Each `failed` entry becomes `{id, reason, message, action}`. Verification failure produces a file-level `VERIFICATION_FAILED` entry.
- `bulk_unshare()` — propagates structured failed entries. When multiple permissions fail for one file, picks the most actionable reason by priority: ACCESS_DENIED > THROTTLED > NOT_FOUND > UNKNOWN.

### Frontend Changes

**`types.ts`** — new `UnshareFailure` interface:

```typescript
export interface UnshareFailure {
  id: string
  reason: 'ACCESS_DENIED' | 'NOT_FOUND' | 'THROTTLED' | 'VERIFICATION_FAILED' | 'UNKNOWN'
  message: string
  action: string
}

export interface UnshareResponse {
  succeeded: string[]
  failed: UnshareFailure[]
}
```

**New `UnshareResultDialog` component** — always shown after the operation completes (replaces the snackbar toast):

- Title: "Unshare Results"
- Summary chip: "N succeeded, M failed" or "N files unshared successfully"
- **Succeeded section**: Green checkmark with count. Collapsed by default if there are failures.
- **Failed section**: MUI `List` with each failed file showing:
  - File path (looked up from React Query cache)
  - Color-coded `Chip` for the reason code
  - `action` text as secondary line
- **Footer**: "Close" button. If any files are `THROTTLED`, also a "Retry Failed" button that re-invokes unshare with just the failed IDs.

**`UnshareButton.tsx`** — replace toast state with result dialog state. On operation complete, open `UnshareResultDialog` instead of showing a snackbar.

### Files to Modify

1. `src/webapp/graph_unshare.py` — add `_classify_error()`, update `remove_all_permissions()` and `bulk_unshare()` to return structured errors
2. `frontend/src/api/types.ts` — add `UnshareFailure`, update `UnshareResponse`
3. `frontend/src/components/UnshareResultDialog.tsx` — new component
4. `frontend/src/components/UnshareButton.tsx` — replace toast with result dialog, add retry support
5. `tests/webapp/test_graph_unshare.py` — update for structured error format
6. `tests/webapp/test_routes_unshare.py` — update mock return values

### What Does NOT Change

- `routes_unshare.py` — pass-through, no changes needed
- `neo4j_client.py` — no changes
- `hooks.ts` — `useUnshare` mutation and cache logic unchanged (still keyed on `succeeded`)
- API endpoint URL and method — same `POST /api/unshare`
