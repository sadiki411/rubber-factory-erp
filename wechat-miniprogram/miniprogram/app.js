App({
  onLaunch() {
    if (!wx.getUpdateManager) return
    const updateManager = wx.getUpdateManager()
    updateManager.onUpdateReady(() => {
      wx.showModal({
        title: '发现新版本',
        content: '新版本已经准备好，是否立即重启更新？',
        success: ({ confirm }) => {
          if (confirm) updateManager.applyUpdate()
        },
      })
    })
  },
})
