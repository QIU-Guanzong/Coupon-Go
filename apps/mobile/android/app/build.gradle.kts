plugins {
    id("com.android.application")
    id("kotlin-android")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// The public MVP uses a separate, persistent signing identity and package ID.
// All signing material is supplied from outside the checkout at build time.
val previewKeystore = System.getenv("COUPONGO_PREVIEW_KEYSTORE")
val previewPassword = System.getenv("COUPONGO_PREVIEW_PASSWORD")
require(previewKeystore.isNullOrBlank() == previewPassword.isNullOrBlank()) {
    "Both Coupon GO preview signing variables must be provided together."
}
val previewSigning = !previewKeystore.isNullOrBlank() && !previewPassword.isNullOrBlank()

android {
    namespace = "com.example.coupon_go"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }

    kotlinOptions {
        jvmTarget = JavaVersion.VERSION_11.toString()
    }

    defaultConfig {
        // TODO: Specify your own unique Application ID (https://developer.android.com/studio/build/application-id.html).
        applicationId = "com.example.coupon_go"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
        manifestPlaceholders["appLabel"] = "Coupon GO!"
    }

    signingConfigs {
        if (previewSigning) {
            create("preview") {
                storeFile = file(previewKeystore!!)
                storePassword = previewPassword
                keyAlias = "coupon-go-preview"
                keyPassword = previewPassword
            }
        }
    }

    buildTypes {
        release {
            // TODO: Add your own signing config for the release build.
            // Signing with the debug keys for now, so `flutter run --release` works.
            if (previewSigning) {
                applicationIdSuffix = ".preview"
                manifestPlaceholders["appLabel"] = "Coupon GO! 演示"
            }
            signingConfig = signingConfigs.getByName(if (previewSigning) "preview" else "debug")
        }
    }
}

flutter {
    source = "../.."
}
