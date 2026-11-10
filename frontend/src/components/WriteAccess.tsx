import { Alert, Button, Drawer } from 'antd'
import type { ReactNode } from 'react'
import { useReadOnly } from '../access'

/** Do not mount write-only forms for a read-only account, even via a deep entry. */
export function WriteAccess({ open, onClose, children }: { open: boolean; onClose: () => void; children: ReactNode }) {
  const readOnly = useReadOnly()
  return readOnly
    ? <Drawer open={open} onClose={onClose} title="只读账号" footer={<Button onClick={onClose}>关闭</Button>}><Alert type="info" showIcon title="此账号只能查看记录，不能新增、修改或取消。" /></Drawer>
    : children
}
