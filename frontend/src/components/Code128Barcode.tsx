const PATTERNS = [
  '212222', '222122', '222221', '121223', '121322', '131222', '122213', '122312', '132212', '221213',
  '221312', '231212', '112232', '122132', '122231', '113222', '123122', '123221', '223211', '221132',
  '221231', '213212', '223112', '312131', '311222', '321122', '321221', '312212', '322112', '322211',
  '212123', '212321', '232121', '111323', '131123', '131321', '112313', '132113', '132311', '211313',
  '231113', '231311', '112133', '112331', '132131', '113123', '113321', '133121', '313121', '211331',
  '231131', '213113', '213311', '213131', '311123', '311321', '331121', '312113', '312311', '332111',
  '314111', '221411', '431111', '111224', '111422', '121124', '121421', '141122', '141221', '112214',
  '112412', '122114', '122411', '142112', '142211', '241211', '221114', '413111', '241112', '134111',
  '111242', '121142', '121241', '114212', '124112', '124211', '411212', '421112', '421211', '212141',
  '214121', '412121', '111143', '111341', '131141', '114113', '114311', '411113', '411311', '113141',
  '114131', '311141', '411131', '211412', '211214', '211232', '2331112',
]

function code128Symbols(value: string) {
  const normalized = value.toUpperCase()
  if ([...normalized].some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) > 126)) {
    throw new Error('Code 128仅支持可打印ASCII字符。')
  }
  const data = [...normalized].map((character) => character.charCodeAt(0) - 32)
  const checksum = (104 + data.reduce((total, code, index) => total + code * (index + 1), 0)) % 103
  return [104, ...data, checksum, 106]
}

export function Code128Barcode({ value }: { value: string }) {
  const patterns = code128Symbols(value).map((symbol) => PATTERNS[symbol])
  const quietZone = 10
  const symbolWidth = patterns.reduce((total, pattern) => total + [...pattern].reduce((sum, width) => sum + Number(width), 0), 0)
  const bars: Array<{ x: number; width: number }> = []
  let x = quietZone
  patterns.forEach((pattern) => {
    Array.from(pattern).forEach((widthText, index) => {
      const width = Number(widthText)
      if (index % 2 === 0) bars.push({ x, width })
      x += width
    })
  })

  return <svg
    className="inventory-code128"
    role="img"
    aria-label={`条形码 ${value}`}
    viewBox={`0 0 ${symbolWidth + quietZone * 2} 42`}
    preserveAspectRatio="none"
  >
    <rect width="100%" height="100%" fill="#fff" />
    {bars.map((bar, index) => <rect key={`${bar.x}-${index}`} x={bar.x} y="1" width={bar.width} height="40" fill="#111" />)}
  </svg>
}
