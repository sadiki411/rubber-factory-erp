import { createContext, useContext } from 'react'

// The server remains authoritative for writes; do not promise rejected actions.
export const ReadOnlyContext = createContext(false)
export const useReadOnly = () => useContext(ReadOnlyContext)
