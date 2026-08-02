const { ERP_BASE_URL, MODULES, ALLOWED_PATHS } = require('../../utils/config')

function safeDecode(value) {
  try {
    return decodeURIComponent(value || '/')
  } catch (_error) {
    return '/'
  }
}

function buildErpUrl(path, retryToken) {
  const suffix = retryToken ? `&retry=${retryToken}` : ''
  return `${ERP_BASE_URL}${path}?entry=wxmini${suffix}`
}

Page({
  data: {
    src: '',
    loadError: false,
  },

  onLoad(options) {
    wx.hideShareMenu({ menus: ['shareAppMessage', 'shareTimeline'] })
    const requestedPath = safeDecode(options.route)
    this.erpPath = ALLOWED_PATHS.includes(requestedPath) ? requestedPath : '/'
    const currentModule = MODULES.find((item) => item.path === this.erpPath)
    wx.setNavigationBarTitle({ title: currentModule ? currentModule.shortTitle : 'ERP 工作台' })
    this.loadErp()
  },

  onUnload() {
    this.clearLoadTimer()
    wx.hideLoading()
  },

  loadErp(retryToken) {
    this.clearLoadTimer()
    wx.showLoading({ title: '正在连接 ERP', mask: true })
    this.setData({
      loadError: false,
      src: buildErpUrl(this.erpPath || '/', retryToken),
    })
    this.loadTimer = setTimeout(() => {
      wx.hideLoading()
      this.setData({ src: '', loadError: true })
    }, 20000)
  },

  clearLoadTimer() {
    if (this.loadTimer) {
      clearTimeout(this.loadTimer)
      this.loadTimer = null
    }
  },

  handleLoad() {
    this.clearLoadTimer()
    wx.hideLoading()
  },

  handleError() {
    this.clearLoadTimer()
    wx.hideLoading()
    this.setData({ src: '', loadError: true })
  },

  retry() {
    this.loadErp(Date.now())
  },

  backHome() {
    wx.navigateBack({
      delta: 1,
      fail: () => wx.reLaunch({ url: '/pages/home/index' }),
    })
  },

  copyAddress() {
    wx.setClipboardData({ data: `${ERP_BASE_URL}${this.erpPath || '/'}` })
  },
})
