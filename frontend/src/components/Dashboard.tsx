import { useState, useCallback } from 'react'
import { Box, TextField, FormControl, InputLabel, Select, MenuItem, Container } from '@mui/material'
import type { GridRowSelectionModel } from '@mui/x-data-grid-pro'
import { useFiles } from '../api/hooks'
import { AppHeader } from './AppHeader'
import { SummaryCards } from './SummaryCards'
import { FileDataGrid } from './FileDataGrid'
import { UnshareButton } from './UnshareButton'

const emptySelection: GridRowSelectionModel = { type: 'include', ids: new Set() }

export function Dashboard() {
  const [search, setSearch] = useState('')
  const [riskFilter, setRiskFilter] = useState('')
  const [sourceFilter, setSourceFilter] = useState('')
  const [selectedIds, setSelectedIds] = useState<GridRowSelectionModel>(emptySelection)

  const { data, isLoading } = useFiles({
    search: search || undefined,
    risk_level: riskFilter || undefined,
    source: sourceFilter || undefined,
  })

  const selectedIdStrings = Array.from(selectedIds.ids).map(String)

  const handleClearSelection = useCallback(() => {
    setSelectedIds(emptySelection)
  }, [])

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <AppHeader />
      <Container maxWidth={false} sx={{ flex: 1, py: 2 }}>
        <SummaryCards />

        <Box sx={{ display: 'flex', gap: 2, mb: 2, alignItems: 'center', flexWrap: 'wrap' }}>
          <TextField
            size="small" placeholder="Search file paths..."
            value={search} onChange={e => setSearch(e.target.value)}
            sx={{ minWidth: 250 }}
          />
          <FormControl size="small" sx={{ minWidth: 130 }}>
            <InputLabel>Risk Level</InputLabel>
            <Select value={riskFilter} label="Risk Level" onChange={e => setRiskFilter(e.target.value)}>
              <MenuItem value="">All</MenuItem>
              <MenuItem value="HIGH">HIGH</MenuItem>
              <MenuItem value="MEDIUM">MEDIUM</MenuItem>
              <MenuItem value="LOW">LOW</MenuItem>
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 130 }}>
            <InputLabel>Source</InputLabel>
            <Select value={sourceFilter} label="Source" onChange={e => setSourceFilter(e.target.value)}>
              <MenuItem value="">All</MenuItem>
              <MenuItem value="OneDrive">OneDrive</MenuItem>
              <MenuItem value="SharePoint">SharePoint</MenuItem>
              <MenuItem value="Teams">Teams</MenuItem>
            </Select>
          </FormControl>
          <Box sx={{ flexGrow: 1 }} />
          <UnshareButton
            selectedIds={selectedIdStrings}
            onComplete={handleClearSelection}
          />
        </Box>

        <FileDataGrid
          files={data?.files || []}
          loading={isLoading}
          selectedIds={selectedIds}
          onSelectionChange={setSelectedIds}
        />
      </Container>
    </Box>
  )
}
