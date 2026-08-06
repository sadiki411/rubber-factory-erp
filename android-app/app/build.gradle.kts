plugins {
    id("com.android.application")
}

fun environment(name: String): String? = providers.environmentVariable(name).orNull?.takeIf { it.isNotBlank() }

val releaseStoreFile = environment("ANDROID_KEYSTORE_FILE")
val releaseStorePassword = environment("ANDROID_KEYSTORE_PASSWORD")
val releaseKeyAlias = environment("ANDROID_KEY_ALIAS")
val releaseKeyPassword = environment("ANDROID_KEY_PASSWORD")
val hasReleaseSigning = listOf(
    releaseStoreFile,
    releaseStorePassword,
    releaseKeyAlias,
    releaseKeyPassword,
).all { it != null }
val requireReleaseSigning = providers.gradleProperty("requireReleaseSigning").orNull.toBoolean()

if (requireReleaseSigning && !hasReleaseSigning) {
    throw GradleException(
        "Release signing is required. Set ANDROID_KEYSTORE_FILE, ANDROID_KEYSTORE_PASSWORD, " +
            "ANDROID_KEY_ALIAS and ANDROID_KEY_PASSWORD.",
    )
}

android {
    namespace = "com.qvgro.erp"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.qvgro.erp"
        minSdk = 26
        targetSdk = 36
        versionCode = environment("ANDROID_VERSION_CODE")?.toIntOrNull() ?: 1
        versionName = environment("ANDROID_VERSION_NAME") ?: "1.0.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        if (hasReleaseSigning) {
            create("release") {
                storeFile = file(releaseStoreFile!!)
                storePassword = releaseStorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
                enableV1Signing = true
                enableV2Signing = true
                enableV3Signing = true
                enableV4Signing = true
            }
        }
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
            isDebuggable = true
        }
        release {
            isDebuggable = false
            isMinifyEnabled = false
            isShrinkResources = false
            if (hasReleaseSigning) {
                signingConfig = signingConfigs.getByName("release")
            }
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        abortOnError = true
        checkReleaseBuilds = true
        warningsAsErrors = false
    }

    packaging {
        resources.excludes += setOf(
            "META-INF/AL2.0",
            "META-INF/LGPL2.1",
        )
    }
}

dependencies {
    implementation("androidx.activity:activity:1.10.1")
    implementation("androidx.core:core:1.16.0")

    testImplementation("junit:junit:4.13.2")
}
