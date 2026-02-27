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
