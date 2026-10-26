// 温湿度モニターの Android アプリ（GitHub Actions で APK を作る）
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "TempMonitor"
include(":app")
