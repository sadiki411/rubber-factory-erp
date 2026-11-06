/* Manual read-only DevTools audit. Does not send requests or modify records.
 * Open the page, set a documented viewport, then paste this file in Console.
 * DOM dimensions are evidence for that browser/viewport, not Android camera QA.
 */
(() => {
  const box = (element) => {
    if (!element) return null
    const { x, y, width, height, bottom, right } = element.getBoundingClientRect()
    return { x, y, width, height, bottom, right }
  }
  const title = document.querySelector('.page-title-content h2')
  const tasks = [...document.querySelectorAll('.operations-task-row')]
  const result = {
    path: location.pathname + location.search,
    timestamp: new Date().toISOString(),
    viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio },
    pageOverflow: document.documentElement.scrollWidth > innerWidth + 1,
    title: box(title),
    primaryAction: box(document.querySelector('.page-title-extra .ant-btn-primary')),
    fullyVisibleTasks: tasks.filter((row) => {
      const r = row.getBoundingClientRect()
      return r.top >= 0 && r.bottom <= innerHeight
    }).length,
    // Only report dimensions and codes, not employee names or business data.
    locationCodeClipping: [...document.querySelectorAll('.inventory-location-tile strong')]
      .filter((item) => item.scrollWidth > item.clientWidth + 1 || item.scrollHeight > item.clientHeight + 1)
      .map((item) => item.textContent),
  }
  console.table(result)
  return result
})()
