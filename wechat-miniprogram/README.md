# 橡胶工厂 ERP 微信小程序

本目录是一个不重复保存业务数据的微信小程序入口。原生首页提供模具、货架、订单、生产、品检和绩效快捷入口；进入功能后由微信 `web-view` 打开 `https://erp.qvgro.com` 的移动端页面，因此网站和小程序始终使用同一套 Django API、SQLite 数据库与共享账号。

## 正式发布前的必要条件

1. 使用非个人主体的小程序账号，并取得该小程序的真实 AppID。个人类型小程序和小游戏不支持 `web-view`。
2. 在微信公众平台把开发微信加入项目成员并授予开发权限；提交审核和正式发布还需要管理员或具备“开发管理”权限的成员操作。
3. 确认 `qvgro.com` 已完成 ICP 备案，`https://erp.qvgro.com` 使用有效 HTTPS 证书；新完成备案的域名按平台规则可能需要等待24小时后才能配置。
4. 在“开发管理 → 开发设置 → 业务域名”添加 `https://erp.qvgro.com`。
5. 如果微信要求放置域名校验文件，下载平台提供的原始文件，不要修改文件名或内容，将它放到 `frontend/public/`，重新构建并部署 Web 镜像；确认 `https://erp.qvgro.com/校验文件名.txt` 返回原始纯文本后再完成后台校验。
6. 提交审核前按微信公众平台提示补齐小程序名称、图标、服务类目、版本截图、隐私保护指引及用户协议等资料；具体必填项以提交时后台显示为准。

微信官方说明：

- [web-view 组件](https://developers.weixin.qq.com/miniprogram/dev/component/web-view.html)
- [业务域名](https://developers.weixin.qq.com/miniprogram/dev/framework/ability/domain.html)
- [HTTPS与网络域名](https://developers.weixin.qq.com/miniprogram/dev/framework/ability/network.html)
- [项目配置](https://developers.weixin.qq.com/miniprogram/dev/devtools/projectconfig.html)
- [上传、审核与发布](https://developers.weixin.qq.com/miniprogram/dev/framework/quickstart/release.html)

## 微信开发者工具

1. 安装并登录微信开发者工具。
2. 导入本目录 `wechat-miniprogram/`，项目类型选择“小程序”。
3. 仓库中的 `project.config.json` 使用公开占位值 `touristappid`，它只用于导入项目和静态检查，不能用于正式真机验证、上传或审核。复制 `project.private.config.example.json` 为被Git忽略的 `project.private.config.json`，填写真实 AppID；若当前开发者工具版本仍要求写入 `project.config.json`，只在本机修改并确认不要把该改动提交。不要提交 AppSecret、上传私钥、Access Token、ERP密码或Cookie。
4. 尚未配置业务域名时，只能在开发工具/真机调试模式临时勾选“不校验合法域名、web-view（业务域名）、TLS版本以及HTTPS证书”。正式测试和发布前必须重新开启校验。
5. 依次测试快捷入口、ERP登录、返回、会话过期、弱网以及iOS/Android真机显示。
6. 使用开发者工具上传代码，在微信公众平台提交审核，审核通过后发布。

## 安全边界

- 小程序原生入口只生成固定的 `erp.qvgro.com` 域名和允许路径，不能通过入口参数跳转到任意网址；进入网页后的链接、重定向和iframe仍受微信业务域名及网站自身策略限制。
- 不在URL、小程序代码或Git仓库中保存ERP账号、密码、Django Session、CSRF值、AppSecret或上传私钥。
- 微信身份不会自动等同于ERP身份；一期仍使用ERP共享账号登录。
- 分享入口默认关闭，避免把内部ERP页面作为小程序卡片转发。
- Excel上传、下载和打开在不同微信WebView版本中可能表现不一致，一期优先用于查阅和日常录入。

## 更新规则

ERP网页功能更新并部署到服务器后，小程序下次打开会直接使用新网页，通常不需要重新上传代码包。若原生页面、权限、服务类目、隐私授权、外链或业务域名发生变化，是否需要重新配置或复审以微信平台当时要求为准。

提交前可在仓库根目录执行：

```bash
python scripts/check_miniprogram.py
```
