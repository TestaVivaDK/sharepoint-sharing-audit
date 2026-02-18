import { AppBar, Toolbar, Typography, Button, Box } from '@mui/material'
import { useMsal } from '@azure/msal-react'

export function AppHeader() {
  const { instance, accounts } = useMsal()
  const name = accounts[0]?.name || accounts[0]?.username || ''

  return (
    <AppBar position="static" sx={{ bgcolor: '#1a3c6e' }}>
      <Toolbar>
        <Typography variant="h6" sx={{ flexGrow: 1 }}>Sharing Audit Dashboard</Typography>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <Typography variant="body2">{name}</Typography>
          <Button color="inherit" size="small" onClick={() => {
            fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
            instance.logoutRedirect()
          }}>
            Logout
          </Button>
        </Box>
      </Toolbar>
    </AppBar>
  )
}
