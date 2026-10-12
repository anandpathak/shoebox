# Photoframing for iPhone and Android

A thin native app around the same web app the browser shows. Its start page asks for the Mac's address:
`https://<mac>.<tailnet>.ts.net` (from `photoctl serve --setup-tailscale`), or on the same Wi-Fi
`http://<mac-ip>:8765` (from `photoctl serve --lan`; the phone is then paired with a code). It then loads the
web UI from the Mac, so sign-in, screens and updates are exactly the ones in the browser. What the native
app adds is automatic backup of the camera roll (iOS PhotoKit / Android MediaStore), which a web page can't do.

```sh
cd app
npm install
uv run python ../scripts/make_icons.py   # icons and splash screens (PNGs aren't committed)
npx cap sync
npx cap open ios        # Xcode 16+, then Run on a phone or simulator
npx cap open android    # Android Studio, then Run
npm test                # the start page's address checks
```

Android without Android Studio (needs the Android SDK, e.g. `brew install --cask android-commandlinetools` with
`ANDROID_HOME` set, and JDK 21+; Capacitor 7 won't compile on JDK 17):

```sh
cd app && npx cap sync android && cd android
JAVA_HOME=$(/usr/libexec/java_home -v 21+ 2>/dev/null || echo /opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home) \
  ./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk    # phone with USB debugging on
```

The iOS project uses Swift Package Manager (no CocoaPods). App id: `app.photoframing` on both platforms.

Notes
- Only `https://….ts.net` addresses, or private-network / `.local` addresses (http, for `serve --lan`) are accepted.
- Android app data isn't included in Google backups (`allowBackup="false"`): the backup ledger is rebuilt from
  the Mac when the app is reinstalled.
- To point the app at a different Mac: open it while the old Mac is unreachable (the start page stays open
  with the address field), or reinstall.
