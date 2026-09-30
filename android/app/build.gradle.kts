plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

val appVersion: String = providers.gradleProperty("aasVersion").getOrElse("0.0.0")
val ks = file(System.getenv("AAS_KEYSTORE") ?: "${System.getProperty("user.home")}/.config/aas-mail/android-release.jks")

android {
    namespace = "ru.olesyaba.aasmail"
    compileSdk = 35
    defaultConfig {
        applicationId = "ru.olesyaba.aasmail"
        minSdk = 31
        targetSdk = 35
        versionName = appVersion
        versionCode = appVersion.split(".").fold(0) { acc, p -> acc * 100 + (p.toIntOrNull() ?: 0) }.coerceAtLeast(1)
        // Galaxy Fold build: arm64 only; -PaasAbis=arm64-v8a,x86_64 for the universal APK.
        ndk { abiFilters += providers.gradleProperty("aasAbis").getOrElse("arm64-v8a").split(",") }
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    signingConfigs {
        create("release") {
            storeFile = ks
            storePassword = System.getenv("AAS_KEYSTORE_PASS")
            keyAlias = "aas"
            keyPassword = System.getenv("AAS_KEYSTORE_PASS")
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
        }
    }
    sourceSets["main"].assets.srcDir("../stage/assets")
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
}

chaquopy {
    defaultConfig {
        version = "3.12"
        pip {
            install("requests")
            install("python-dateutil")
            install("certifi")
        }
    }
    sourceSets {
        getByName("main") { srcDir("src/main/python"); srcDir("../stage/python") }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.webkit:webkit:1.12.1")
    implementation("androidx.work:work-runtime-ktx:2.10.0")
    implementation("androidx.documentfile:documentfile:1.0.1")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
}
