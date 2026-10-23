import { useNavigate, useSearch } from '@tanstack/react-router'

/** `as_of` lives in the URL so the browser back button is time travel and every view is linkable. */
export function useAsOf(): string | undefined {
  const search = useSearch({ strict: false }) as { as_of?: string }
  return search.as_of
}

export function useSetAsOf(): (date?: string) => void {
  const navigate = useNavigate()
  return (date?: string) => {
    void navigate({
      to: '.',
      search: ((prev: Record<string, unknown>) => ({ ...prev, as_of: date })) as never,
    })
  }
}
