import { DataGridPro } from '@mui/x-data-grid-pro'
import type { GridColDef, GridRowSelectionModel } from '@mui/x-data-grid-pro'
import { Chip, Link } from '@mui/material'
import type { SharedFile } from '../api/types'

const riskColor = { HIGH: 'error', MEDIUM: 'warning', LOW: 'success' } as const

const columns: GridColDef<SharedFile>[] = [
  {
    field: 'risk_score', headerName: 'Score', width: 70, type: 'number',
    renderCell: (p) => <strong>{p.value}</strong>,
  },
  {
    field: 'risk_level', headerName: 'Risk', width: 90,
    renderCell: (p) => (
      <Chip label={p.value} size="small" color={riskColor[p.value as keyof typeof riskColor] || 'default'} />
    ),
  },
  { field: 'source', headerName: 'Source', width: 100 },
  { field: 'item_type', headerName: 'Type', width: 70 },
  { field: 'item_path', headerName: 'File / Folder Path', flex: 1, minWidth: 250 },
  {
    field: 'item_web_url', headerName: 'Link', width: 70,
    renderCell: (p) => p.value ? <Link href={p.value} target="_blank" rel="noopener">Open</Link> : '-',
  },
  { field: 'sharing_type', headerName: 'Sharing Type', width: 150 },
  { field: 'shared_with', headerName: 'Shared With', flex: 1, minWidth: 200 },
  { field: 'shared_with_type', headerName: 'Audience', width: 100 },
]

interface Props {
  files: SharedFile[]
  loading: boolean
  selectedIds: GridRowSelectionModel
  onSelectionChange: (ids: GridRowSelectionModel) => void
}

export function FileDataGrid({ files, loading, selectedIds, onSelectionChange }: Props) {
  return (
    <DataGridPro
      rows={files}
      columns={columns}
      loading={loading}
      checkboxSelection
      rowSelectionModel={selectedIds}
      onRowSelectionModelChange={onSelectionChange}
      disableRowSelectionOnClick
      initialState={{
        sorting: { sortModel: [{ field: 'risk_score', sort: 'desc' }] },
      }}
      getRowClassName={(params) => `risk-${params.row.risk_level.toLowerCase()}`}
      sx={{
        '& .risk-high': { bgcolor: '#fce4e4' },
        '& .risk-medium': { bgcolor: '#fff8e6' },
        '& .risk-low': { bgcolor: '#eaf6ea' },
        height: 'calc(100vh - 250px)',
      }}
      pagination
      pageSizeOptions={[25, 50, 100]}
    />
  )
}
