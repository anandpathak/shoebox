# Photoframing for iPhone and Android

A thin native app around the same web app the browser shows. Its start page asks for the Mac's tailnet
address (`https://<mac>.<tailnet>.ts.net`, printed by `photoctl serve --setup-tailscale`), then loads the
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

The iOS project uses Swift Package Manager (no CocoaPods). App id: `app.photoframing` on both platforms.

Notes
- Only `https://….ts.net` addresses are accepted (plus `127.0.0.1`/`10.0.2.2` over http for development).
- Android app data isn't included in Google backups (`allowBackup="false"`): the backup ledger is rebuilt from
  the Mac when the app is reinstalled.
- To point the app at a different Mac: open it while the old Mac is unreachable (the start page stays open
  with the address field), or reinstall.
