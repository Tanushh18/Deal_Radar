# The @JavascriptInterface bridge is called by name from injected JS, so its
# methods must survive shrinking — R8 has no way to see those call sites.
-keepclassmembers class com.dealradar.app.WebAppBridge {
    public *;
}
-keepattributes JavascriptInterface
