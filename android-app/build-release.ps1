[CmdletBinding()]
param(
    [ValidatePattern('^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$')]
    [string]$VersionName = '1.0.0',

    [ValidateRange(1, 2100000000)]
    [int]$VersionCode = 1,

    [string]$CredentialFile = 'D:\develop\android-signing\dongxiang-production-assistant-credentials.json',

    [string]$OutputDirectory = ''
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $PSScriptRoot '..\outputs\android'
}

$javaHome = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { 'D:\develop\jdk17' }
$androidSdk = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { 'D:\develop\android-sdk' }
$gradleHome = if ($env:GRADLE_USER_HOME) { $env:GRADLE_USER_HOME } else { 'D:\develop\gradle-home' }
$gradleWrapper = Join-Path $PSScriptRoot 'gradlew.bat'

foreach ($requiredPath in @(
    (Join-Path $javaHome 'bin\java.exe'),
    (Join-Path $androidSdk 'platforms\android-36\android.jar'),
    $gradleWrapper,
    $CredentialFile
)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Missing Android build dependency: $requiredPath"
    }
}

$credentials = Get-Content -Raw -Encoding UTF8 -LiteralPath $CredentialFile | ConvertFrom-Json
foreach ($field in @('keystore_path', 'keystore_password', 'key_alias', 'key_password')) {
    if ([string]::IsNullOrWhiteSpace([string]$credentials.$field)) {
        throw "Missing signing credential field: $field"
    }
}
if (-not (Test-Path -LiteralPath $credentials.keystore_path)) {
    throw "Release keystore not found: $($credentials.keystore_path)"
}

$env:JAVA_HOME = $javaHome
$env:ANDROID_SDK_ROOT = $androidSdk
$env:ANDROID_HOME = $androidSdk
$env:GRADLE_USER_HOME = $gradleHome
$env:ANDROID_KEYSTORE_FILE = $credentials.keystore_path
$env:ANDROID_KEYSTORE_PASSWORD = $credentials.keystore_password
$env:ANDROID_KEY_ALIAS = $credentials.key_alias
$env:ANDROID_KEY_PASSWORD = $credentials.key_password
$env:ANDROID_VERSION_NAME = $VersionName
$env:ANDROID_VERSION_CODE = [string]$VersionCode
$env:Path = "$(Join-Path $javaHome 'bin');$(Join-Path $androidSdk 'platform-tools');$env:Path"

Push-Location $PSScriptRoot
try {
    & $gradleWrapper '--no-daemon' 'clean' 'testDebugUnitTest' 'lintRelease' 'assembleRelease' '-PrequireReleaseSigning=true'
    if ($LASTEXITCODE -ne 0) {
        throw "Android build failed with Gradle exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$sourceApk = Join-Path $PSScriptRoot 'app\build\outputs\apk\release\app-release.apk'
if (-not (Test-Path -LiteralPath $sourceApk)) {
    throw "Release APK was not created at: $sourceApk"
}

$resolvedOutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $resolvedOutputDirectory | Out-Null
$appName = -join ([char[]](0x4e1c, 0x6a61, 0x751f, 0x4ea7, 0x52a9, 0x624b))
$fileName = "$appName-v$VersionName.apk"
$destinationApk = Join-Path $resolvedOutputDirectory $fileName
Copy-Item -LiteralPath $sourceApk -Destination $destinationApk -Force

$sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $destinationApk).Hash.ToLowerInvariant()
$checksumFile = "$destinationApk.sha256"
[IO.File]::WriteAllText(
    $checksumFile,
    "$sha256  $fileName`n",
    [Text.UTF8Encoding]::new($false)
)

Write-Host "Release APK: $destinationApk"
Write-Host "SHA-256: $sha256"
Write-Host "Checksum file: $checksumFile"
