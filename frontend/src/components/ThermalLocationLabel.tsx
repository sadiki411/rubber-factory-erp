import { QRCode } from 'antd'
import { Code128Barcode } from './Code128Barcode'

type Props = {
  code: string
  detailUrl: string
  kind: 'inventory' | 'mold-rack'
}

export function ThermalLocationLabel({ code, detailUrl, kind }: Props) {
  // Courier's fixed character advance is 0.6em. Fit the COMPLETE code into
  // 63mm, leaving 1.5mm on either side of the 66mm text area. SVG text avoids
  // inherited HTML max-width/overflow rules and prints as sharp vector text.
  const fontSize = Math.min(106, 630 / (Math.max(1, code.length) * 0.6))
  return <div className="thermal-label-row">
    <div className={`${kind}-label`}>
      <div className="thermal-label-content">
        <div className="thermal-label-artwork">
          <div className={`${kind}-label-codes`}>
            <Code128Barcode value={code} />
            <QRCode type="svg" value={detailUrl} bordered={false} />
          </div>
          <svg className="thermal-location-code" viewBox="0 0 660 120" role="img" aria-label={`库位 ${code}`}>
            <text x="330" y="60" textAnchor="middle" dominantBaseline="central" fontFamily="'Courier New', Courier, monospace" fontWeight="700" fontSize={fontSize} fill="#111">{code}</text>
          </svg>
        </div>
      </div>
    </div>
  </div>
}
