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
