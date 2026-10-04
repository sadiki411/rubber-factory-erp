import '@testing-library/jest-dom/vitest'
import { configure } from '@testing-library/react'

// CI runners can take longer to paint Ant Design tables than the library's
// one-second default, which made data-backed action buttons look missing.
configure({ asyncUtilTimeout: 5_000 })
