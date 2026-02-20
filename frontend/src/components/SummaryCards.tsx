import { Alert, Box, Paper, Typography } from '@mui/material'
import { useStats } from '../api/hooks'

export function SummaryCards() {
  const { data } = useStats()
  if (!data) return null

  const cards = [
    { label: 'HIGH', count: data.high, color: '#dc3545' },
    { label: 'MEDIUM', count: data.medium, color: '#f0ad4e' },
    { label: 'LOW', count: data.low, color: '#5cb85c' },
  ]

  const scanRunning = data.scan_status === 'running'

  return (
    <>
      {scanRunning && (
        <Alert severity="info" sx={{ my: 1 }}>
          A scan is currently in progress — data shown may be incomplete.
        </Alert>
      )}
      <Box sx={{ display: 'flex', gap: 2, my: 2, flexWrap: 'wrap', alignItems: 'center' }}>
        {cards.map(c => (
          <Paper key={c.label} sx={{ px: 3, py: 1.5, bgcolor: c.color, color: c.label === 'MEDIUM' ? '#333' : '#fff' }}>
            <Typography variant="subtitle2">{c.label}: {c.count}</Typography>
          </Paper>
        ))}
        <Typography variant="body2" color="text.secondary" sx={{ ml: 2 }}>
          {data.total} shared items | Last scan: {scanRunning ? 'Scan in progress...' : data.last_scan ? new Date(data.last_scan).toLocaleString() : 'never'}
        </Typography>
      </Box>
    </>
  )
}
