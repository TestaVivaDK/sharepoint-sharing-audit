import { useState } from 'react'
import { Button, Dialog, DialogTitle, DialogContent, DialogActions, Typography, Alert, Snackbar } from '@mui/material'
import { useUnshare } from '../api/hooks'

interface Props {
  selectedIds: string[]
  onComplete: () => void
}

export function UnshareButton({ selectedIds, onComplete }: Props) {
  const [open, setOpen] = useState(false)
  const [toast, setToast] = useState<{ message: string; severity: 'success' | 'error' } | null>(null)
  const unshare = useUnshare()

  const handleConfirm = async () => {
    setOpen(false)
    try {
      const result = await unshare.mutateAsync(selectedIds)
      const msg = result.failed.length
        ? `${result.succeeded.length} succeeded, ${result.failed.length} failed`
        : `${result.succeeded.length} files unshared successfully`
      setToast({ message: msg, severity: result.failed.length ? 'error' : 'success' })
      onComplete()
    } catch (e) {
      setToast({ message: `Unshare failed: ${e}`, severity: 'error' })
    }
  }

  return (
    <>
      <Button
        variant="contained"
        color="error"
        disabled={selectedIds.length === 0 || unshare.isPending}
        onClick={() => setOpen(true)}
      >
        {unshare.isPending ? 'Removing...' : `Remove Sharing (${selectedIds.length})`}
      </Button>

      <Dialog open={open} onClose={() => setOpen(false)}>
        <DialogTitle>Remove All Sharing</DialogTitle>
        <DialogContent>
          <Typography>
            Remove all sharing from <strong>{selectedIds.length}</strong> file{selectedIds.length > 1 ? 's' : ''}?
            This cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button onClick={handleConfirm} color="error" variant="contained">Remove Sharing</Button>
        </DialogActions>
      </Dialog>

      <Snackbar open={!!toast} autoHideDuration={6000} onClose={() => setToast(null)}>
        {toast ? <Alert severity={toast.severity} onClose={() => setToast(null)}>{toast.message}</Alert> : undefined}
      </Snackbar>
    </>
  )
}
