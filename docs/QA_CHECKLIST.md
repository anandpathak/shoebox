# Real-device QA checklist

Run this on an **iPhone (Safari, then the installed app)** and an **Android phone (Chrome, then the installed app)**
before calling a release done. Everything here is also covered by browser tests on this Mac
(`cd web && npx playwright test`), but only real phones show real photo pickers, background behaviour
and push notifications.

Setup: Tailscale on the Mac and both phones (same account), then on the Mac:

```sh
uv run photoctl serve --setup-tailscale
```

Record results in the table at the bottom (✓ / ✗ + a note).

## 1. Reaching the app
- [ ] The printed `https://<mac>.<tailnet>.ts.net` opens on the phone with a valid certificate (no warning).
- [ ] With Tailscale switched off on the phone, the address doesn't open.
- [ ] The status line says *Online via <your Mac> · tailnet*.
- [ ] The app asks to *Name this device* once; after saving, Sync → Devices lists it with the right platform.

## 2. Installing
- [ ] iPhone: Safari → Share → Add to Home Screen. The icon looks right and the app opens full screen.
- [ ] Android: Chrome offers *Install app* (or ⋮ → Install app). The icon looks right and the app opens full screen.
- [ ] No sideways scrolling on any screen, in portrait and landscape.

## 3. Library and viewer
- [ ] The grid scrolls smoothly through a large library; thumbnails fill in as you scroll.
- [ ] Search "beach", a person's name, "december 2024", "videos": results make sense, and the *Showing …* line explains them.
- [ ] Device chips filter to items from that phone.
- [ ] Open a photo: swipe left/right moves between items; the close button and the system back gesture return to the grid.
- [ ] A video plays inline with sound.
- [ ] Download photo/video saves the file (Files on iPhone, Downloads on Android).
- [ ] Request original shows *ready within about 12 hours*.

## 4. Editing
- [ ] Change a title, add/remove a tag, toggle favourite, hide, remove → Undo. The changes show on the Mac after a sync.
- [ ] Rename and merge people on the People screen.

## 5. Archive runs from the phone
- [ ] Uploads → Archive from Apple Photos on <Mac> → Album → start. Progress moves.
- [ ] Lock the phone for a minute, unlock: progress picks up where it was (not double-counted).
- [ ] Open Uploads on the other phone at the same time: it shows the run in progress and can't start a second one.
- [ ] Cancel stops after the current items.

## 6. Uploading from the phone
- [ ] Pick 3 photos + 1 long video. They upload, and a big video survives switching to another app and back.
- [ ] Turn on airplane mode, pick a photo: it shows *Waiting for your Mac*. Turn airplane mode off: it uploads by itself.
- [ ] Pick photos that are already archived: *N already backed up* and only the new ones upload.
- [ ] iPhone: in the photo picker, use *Options → Location* off, then on. Off: the viewer shows *Metadata incomplete — No location*. On: the location is there.
- [ ] Android: check whether the picked photo kept its location; if not, the viewer says so.
- [ ] The uploaded items show the phone as their source (Device chip in the Library).

## 7. Offline
- [ ] Airplane mode, reopen the installed app: it opens and says *Offline*; recently seen thumbnails still show.
- [ ] Catalogs → Use on this device, then airplane mode and **force-quit and reopen** the app: it says
      *Offline — on-device catalog* and search works (the browser tests can't reproduce a real relaunch in Safari).
- [ ] Offline: edit a title and favourite a photo; the status shows *N edits waiting*; back online they appear on the Mac.
- [ ] Open the app in two Safari tabs: the second explains it's open in another tab and still works online.

## 8. Notifications
- [ ] Sync → Notifications → Turn on (iPhone: from the installed app) → Send a test: a notification arrives.
- [ ] Start an archive run from the Mac, close the app: *Archive finished* arrives when it's done; tapping it opens Uploads.

## 9. The native app: automatic backup
Build and install from `app/` (see app/README.md), with Tailscale on.
- [ ] The start page accepts the `https://<mac>.<tailnet>.ts.net` address and opens the library.
- [ ] Sync → Back up this phone → *Back up automatically*: the photo permission prompt appears.
      iPhone: choose *Limited* once and check the card warns; then allow full access.
      Android 14: choose *Select photos* once and check the card warns; then *Allow all*. Allow *location in photos*.
- [ ] Take 5 photos and 1 video. *Back up now*: the count goes to *Backed up N of N*. They appear in the Library
      with the phone as their source and **with their location**.
- [ ] Photos already archived from the Mac's Photos library (iCloud Photos) are counted as backed up without uploading
      (watch the Mac's inbox stay empty).
- [ ] Edit a photo (crop): it's backed up again as a new item. Favourite one: nothing is sent.
- [ ] Wi-Fi only: on mobile data the card says *waiting for Wi-Fi*; on Wi-Fi it carries on.
- [ ] Start a large video upload, switch to another app and lock the phone: it finishes (iOS background transfer /
      Android notification *Backing up to your Mac*).
- [ ] Close the app, take a photo, leave the phone on charge and Wi-Fi overnight: next morning it's in the Library
      without opening the app.
- [ ] iPhone: a photo only in iCloud (*Optimise iPhone storage*) is downloaded and backed up on Wi-Fi.
- [ ] Turn the Mac off: the card says *waiting for your Mac*; turn it on: it catches up by itself.

## Results

| Date | Device / OS / browser | Sections passed | Problems |
|---|---|---|---|
| | | | |
