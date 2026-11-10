// Only known internal business routes can be used as a return destination.
export function safeBusinessReturn(value: string | null | undefined, fallback = '/operations') {
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.includes('\\') || [...value].some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) return fallback
  try {
    const url = new URL(value, 'https://erp.invalid')
    if (url.origin !== 'https://erp.invalid') return fallback
    const allowed = /^\/(?:operations|orders|analytics|production|quality|inventory)\/?$/.test(url.pathname)
      || url.pathname === '/previews/integration'
      || /^\/order-overview\/[1-9]\d*\/?$/.test(url.pathname)
      || /^\/production\/records\/[1-9]\d*\/?$/.test(url.pathname)
    return allowed ? url.pathname + url.search + url.hash : fallback
  } catch {
    return fallback
  }
}
