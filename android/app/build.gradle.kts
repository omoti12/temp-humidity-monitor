plugins {
    id("com.android.application")
}

android {
    namespace = "jp.omoti12.tempmonitor"
    compileSdk = 34

    defaultConfig {
        applicationId = "jp.omoti12.tempmonitor"
        minSdk = 24             // Android 7.0 以上
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    // 署名は、GitHub Actions が作るたびに自動で用意する試作用の鍵（debug）を使う。
    // 鍵をリポジトリに置かないため、新しい版を入れるときは古いアプリを消してから入れ直す
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
