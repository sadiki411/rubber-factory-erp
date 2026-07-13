import type { ReactNode } from 'react'
import { Typography } from 'antd'

export function PageTitle({ title, description, extra }: { title: string; description?: string; extra?: ReactNode }) {
  return (
    <div className="page-title-row">
      <div>
        <Typography.Title level={2}>{title}</Typography.Title>
        {description && <Typography.Text type="secondary">{description}</Typography.Text>}
      </div>
      {extra && <div className="page-title-extra">{extra}</div>}
    </div>
  )
}
