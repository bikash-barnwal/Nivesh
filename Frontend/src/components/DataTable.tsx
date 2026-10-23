import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from '@tanstack/react-table'
import { useState } from 'react'

/** Sortable table on TanStack Table. Click a header to sort; Shift-click adds a second sort key. */
export function DataTable<T>({
  data,
  columns,
  onRowClick,
  initialSort = [],
  getRowId,
  maxHeight,
  rowClass,
}: {
  data: T[]
  columns: ColumnDef<T, any>[] // eslint-disable-line @typescript-eslint/no-explicit-any
  onRowClick?: (row: T) => void
  initialSort?: SortingState
  getRowId?: (row: T, index: number) => string
  maxHeight?: string
  rowClass?: (row: T) => string
}) {
  const [sorting, setSorting] = useState<SortingState>(initialSort)
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getRowId,
  })

  return (
    <div className="overflow-auto rounded border border-line" style={{ maxHeight }}>
      <table className="w-full border-collapse text-left text-[13px]">
        <thead className="sticky top-0 z-10 bg-panel-2">
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => {
                const sorted = header.column.getIsSorted()
                return (
                  <th
                    key={header.id}
                    scope="col"
                    className={`whitespace-nowrap border-b border-line px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted ${
                      header.column.getCanSort() ? 'cursor-pointer select-none hover:text-text' : ''
                    }`}
                    onClick={header.column.getToggleSortingHandler()}
                    aria-sort={sorted === 'asc' ? 'ascending' : sorted === 'desc' ? 'descending' : 'none'}
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                    {sorted === 'asc' ? ' ▲' : sorted === 'desc' ? ' ▼' : ''}
                  </th>
                )
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr
              key={row.id}
              onClick={onRowClick ? () => onRowClick(row.original) : undefined}
              className={`border-b border-line/60 last:border-0 odd:bg-panel even:bg-panel/60 ${
                onRowClick ? 'cursor-pointer hover:bg-panel-2' : ''
              } ${rowClass?.(row.original) ?? ''}`}
            >
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} className="tabular whitespace-nowrap px-3 py-1.5">
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
