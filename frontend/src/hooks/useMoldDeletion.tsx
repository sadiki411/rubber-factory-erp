import { App, Typography } from 'antd'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, moldApi } from '../api/client'
import type { MoldAsset } from '../types'
import { moldCode, moldLocation } from '../types'

interface DeleteOptions {
  onSuccess?: () => void | Promise<void>
}

export function useMoldDeletion() {
  const queryClient = useQueryClient()
  const { message, modal } = App.useApp()
  const mutation = useMutation({
    mutationFn: ({ mold, confirmWarnings }: { mold: MoldAsset; confirmWarnings: boolean }) => moldApi.remove(mold.id, confirmWarnings),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['molds'] }),
        queryClient.invalidateQueries({ queryKey: ['mold'] }),
        queryClient.invalidateQueries({ queryKey: ['racks'] }),
        queryClient.invalidateQueries({ queryKey: ['slots'] }),
        queryClient.invalidateQueries({ queryKey: ['machines'] }),
        queryClient.invalidateQueries({ queryKey: ['production'] }),
      ])
      message.success('误录模具已删除，原库位已释放')
    },
  })

  const deleteMold = async (mold: MoldAsset, options: DeleteOptions, confirmWarnings = false) => {
    try {
      await mutation.mutateAsync({ mold, confirmWarnings })
      await options.onSuccess?.()
    } catch (error) {
      const warnings = error instanceof ApiError ? error.data?.warnings : undefined
      if (!confirmWarnings && error instanceof ApiError && error.status === 409 && Array.isArray(warnings) && warnings.length) {
        modal.confirm({
          title: '删除前需要确认叠放风险',
          content: <div>{warnings.map((warning: string) => <Typography.Paragraph key={warning}>{warning}</Typography.Paragraph>)}</div>,
          okText: '已检查叠放风险，继续删除',
          cancelText: '返回检查',
          okButtonProps: { danger: true },
          onOk: () => deleteMold(mold, options, true),
        })
      } else {
        message.error((error as Error).message)
      }
    }
  }

  const confirmDelete = (mold: MoldAsset, options: DeleteOptions = {}) => {
    modal.confirm({
      title: `删除误录模具 ${moldCode(mold)}？`,
      content: (
        <div>
          <Typography.Paragraph>当前位置：{moldLocation(mold)}</Typography.Paragraph>
          <Typography.Text type="danger">该记录会从活动台账中删除；如果模具在库，当前库位会立即变为空闲。此操作仅用于纠正误录。</Typography.Text>
        </div>
      ),
      okText: '确认删除误录记录',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: () => deleteMold(mold, options),
    })
  }

  return { confirmDelete, deleting: mutation.isPending }
}
