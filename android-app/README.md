# 东橡生产助手（Android）

这是橡胶工厂 ERP 的原生 Android WebView 客户端，应用名称为“东橡生产助手”，包名为 `com.qvgro.erp`。它固定加载 `https://erp.qvgro.com/`，适配 Android 8.0 及以上系统（`minSdk 26`），并以 Android 16（`targetSdk 36`）为目标版本。

## 更新方式

- ERP 页面、功能和数据仍由服务器提供。网站部署新版本后，App下次打开或刷新时会直接使用新页面，不需要重新安装APK。
- 只有原生外壳本身发生变化（例如上传、下载、系统权限或启动图标）时才需要发布新APK。
- 后续APK必须继续使用相同包名和同一份签名密钥，才能在手机上覆盖升级。
- 每次原生APK升级还必须提高 `versionCode`；GitHub Actions会自动使用递增的运行编号，本地构建时需手动传入比已安装版本更大的数值。
- App不保存或迁移服务器SQLite数据库；构建、安装和升级APK都不会删除服务器业务数据。

## 安全边界

- WebView只加载 `https://erp.qvgro.com:443` 的页面和网络资源，禁止HTTP降级、混合内容、SSL错误绕过和第三方Cookie。
- 登录继续使用网站现有的同域Session Cookie和CSRF保护，没有把账号密码写入APK。
- 支持Excel/图片文件选择、拍照上传以及带登录Cookie的同域文件下载。
- 没有JavaScript原生桥，也不会把ERP登录Cookie发送到其他域名。
- 断网时只允许重试，不提供离线写入，避免产生无法同步的数据。

## 本机构建正式版

本机工具默认安装在：

- JDK 17：`D:\develop\jdk17`
- Android SDK：`D:\develop\android-sdk`
- Gradle缓存：`D:\develop\gradle-home`
- 私有签名材料：`D:\develop\android-signing`

执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\android-app\build-release.ps1 `
  -VersionName 1.0.0 `
  -VersionCode 1
```

脚本会先执行单元测试和Release Lint，再生成签名APK及SHA-256校验文件到 `outputs\android`。签名密钥、密码、APK和构建目录均已被 `.gitignore` 排除。

首发签名证书SHA-256指纹：

```text
4F:7F:BB:8F:FD:F6:4E:A0:A1:D7:D6:E1:31:C9:F9:D4:43:EB:18:5E:09:6B:F3:85:8D:68:A9:CD:88:AB:E3:AF
```

请把JKS与凭据文件分别离线备份。签名丢失后，新APK将无法覆盖已安装版本；任何签名密码都不得提交到GitHub。

## GitHub Actions发布

工作流 `.github/workflows/android-apk.yml` 会：

- 在相关代码的Pull Request或推送中执行测试、Lint并生成Debug APK构建产物。
- 手动运行时构建签名Release APK并保存为Actions构建产物。
- 推送 `android-v1.0.0` 形式的标签时，自动创建GitHub Release并上传APK和SHA-256文件。

仓库需配置以下Actions Secrets：

- `ANDROID_KEYSTORE_BASE64`
- `ANDROID_KEYSTORE_PASSWORD`
- `ANDROID_KEY_ALIAS`
- `ANDROID_KEY_PASSWORD`

其中 `ANDROID_KEYSTORE_BASE64` 是JKS文件的Base64内容。工作流只把解码后的密钥临时写到GitHub Runner的临时目录，不会放入仓库或构建产物。

## OPPO / ColorOS安装

1. 把正式APK传到手机并点击安装。
2. 如果系统提示没有安装权限，只为当前打开APK的应用（例如“文件管理”或浏览器）开启“允许安装未知应用”。
3. 安装完成后关闭该临时授权也不影响使用。
4. 更新时直接安装使用相同签名的新版本，不要先卸载旧版；卸载会清除App本地Cookie，但不会删除服务器数据。

建议保持“Android System WebView”组件为最新版本。当前电脑没有连接安卓真机，因此发布前仍需在实际OPPO手机上确认首次登录、Excel上传、拍照、下载、返回手势和覆盖安装。
