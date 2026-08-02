const { MODULES } = require('../../utils/config')

Page({
  data: {
    modules: MODULES,
    online: true,
    networkText: '正在检查网络',
  },

  onLoad() {
    wx.hideShareMenu({ menus: ['shareAppMessage', 'shareTimeline'] })
    wx.getNetworkType({
      success: ({ networkType }) => this.updateNetwork(networkType !== 'none', networkType),
      fail: () => this.updateNetwork(false, 'unknown'),
    })
    this.networkListener = ({ isConnected, networkType }) => {
      this.updateNetwork(isConnected, networkType)
    }
    wx.onNetworkStatusChange(this.networkListener)
  },

  onUnload() {
    if (this.networkListener && wx.offNetworkStatusChange) {
      wx.offNetworkStatusChange(this.networkListener)
    }
  },

  updateNetwork(online, networkType) {
    const names = { wifi: 'Wi-Fi', '2g': '2G', '3g': '3G', '4g': '4G', '5g': '5G', unknown: '网络' }
    this.setData({
      online,
      networkText: online ? `${names[networkType] || '网络'}已连接` : '当前网络不可用',
    })
  },

  openModule(event) {
    const path = event.currentTarget.dataset.path
    if (!this.data.online) {
      wx.showToast({ title: '请先连接网络', icon: 'none' })
      return
    }
    if (!MODULES.some((item) => item.path === path)) {
      wx.showToast({ title: '无效的功能入口', icon: 'none' })
      return
    }
    wx.navigateTo({
      url: `/pages/webview/index?route=${encodeURIComponent(path)}`,
    })
  },
})
