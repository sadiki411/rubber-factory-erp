const ERP_BASE_URL = 'https://erp.qvgro.com'

const MODULES = Object.freeze([
  { path: '/', title: 'ERP 工作台', shortTitle: '工作台', icon: '查', description: '快速查询模具与当前状态' },
  { path: '/molds', title: '模具台账', shortTitle: '模具', icon: '模', description: '查看型号、状态和流转历史' },
  { path: '/racks', title: '货架总览', shortTitle: '货架', icon: '架', description: '按正面图查看模具库位' },
  { path: '/orders', title: '订单管理', shortTitle: '订单', icon: '单', description: '核对订单、交期与发料' },
  { path: '/production', title: '前端生产', shortTitle: '生产', icon: '产', description: '查看计划、上机和实时看板' },
  { path: '/quality', title: '品检出货', shortTitle: '品检', icon: '检', description: '查看检验、出货和返工' },
  { path: '/analytics', title: '数据分析与绩效', shortTitle: '分析', icon: '析', description: '查看效率、成本和利润趋势' },
  { path: '/product-specifications', title: '产品规格资料', shortTitle: '规格', icon: '规', description: '查询产品与模具工艺参数' },
])

const ALLOWED_PATHS = Object.freeze(MODULES.map((item) => item.path))

module.exports = {
  ERP_BASE_URL,
  MODULES,
  ALLOWED_PATHS,
}
