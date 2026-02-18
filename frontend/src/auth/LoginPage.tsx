import { useMsal } from '@azure/msal-react'
import { loginScopes } from './msalConfig'
import { Button, Box, Typography } from '@mui/material'

export function LoginPage() {
  const { instance } = useMsal()

  const handleLogin = () => {
    instance.loginRedirect({ scopes: loginScopes })
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', mt: 10 }}>
      <Typography variant="h4" gutterBottom>Sharing Audit Dashboard</Typography>
      <Typography variant="body1" color="text.secondary" gutterBottom>
        Log in with your organization account to view and manage your shared files.
      </Typography>
      <Button variant="contained" size="large" onClick={handleLogin} sx={{ mt: 3 }}>
        Sign in with Microsoft
      </Button>
    </Box>
  )
}
