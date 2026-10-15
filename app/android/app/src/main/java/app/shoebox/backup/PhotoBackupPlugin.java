package app.shoebox.backup;

import android.Manifest;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.BatteryManager;
import android.os.Build;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

import org.json.JSONException;

import java.io.File;
import java.lang.ref.WeakReference;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** The `PhotoBackup` plugin for the web app (web/src/backup/native.ts). */
@CapacitorPlugin(
    name = "PhotoBackup",
    permissions = {
        @Permission(alias = "photos", strings = { Manifest.permission.READ_MEDIA_IMAGES, Manifest.permission.READ_MEDIA_VIDEO }),
        @Permission(alias = "photosLegacy", strings = { Manifest.permission.READ_EXTERNAL_STORAGE }),
        @Permission(alias = "location", strings = { Manifest.permission.ACCESS_MEDIA_LOCATION }),
        @Permission(alias = "notifications", strings = { Manifest.permission.POST_NOTIFICATIONS }),
    }
)
public class PhotoBackupPlugin extends Plugin {
    private static WeakReference<PhotoBackupPlugin> live = new WeakReference<>(null);
    private final ExecutorService work = Executors.newSingleThreadExecutor();
    private MediaLibrary library;
    private BackupStore store;

    @Override
    public void load() {
        library = new MediaLibrary(getContext());
        store = new BackupStore(getContext());
        live = new WeakReference<>(this);
        BackupScheduler.schedule(getContext());
    }

    /** Called by UploadWorker (any thread) when the OS finished an upload. */
    static void uploadFinished() {
        PhotoBackupPlugin p = live.get();
        if (p != null) p.notifyListeners("uploadFinished", new JSObject());
    }

    @PluginMethod
    public void access(PluginCall call) { call.resolve(new JSObject().put("access", library.access())); }

    @PluginMethod
    public void requestAccess(PluginCall call) {
        if (!"denied".equals(library.access())) { askLocation(call); return; }
        requestPermissionForAlias(Build.VERSION.SDK_INT >= 33 ? "photos" : "photosLegacy", call, "accessDone");
    }

    @PermissionCallback
    private void accessDone(PluginCall call) { askLocation(call); }

    /** Without ACCESS_MEDIA_LOCATION Android strips GPS from the originals we read. */
    private void askLocation(PluginCall call) {
        if (Build.VERSION.SDK_INT >= 29 && getPermissionState("location") != PermissionState.GRANTED) {
            requestPermissionForAlias("location", call, "locationDone");
            return;
        }
        locationDone(call);
    }

    @PermissionCallback
    private void locationDone(PluginCall call) {
        if (Build.VERSION.SDK_INT >= 33 && getPermissionState("notifications") != PermissionState.GRANTED) {
            requestPermissionForAlias("notifications", call, "finishAccess");   // progress for long uploads
            return;
        }
        finishAccess(call);
    }

    @PermissionCallback
    private void finishAccess(PluginCall call) { call.resolve(new JSObject().put("access", library.access())); }

    @PluginMethod
    public void changes(PluginCall call) {
        String token = call.getString("token");
        work.execute(() -> {
            try { call.resolve(JSObject.fromJSONObject(library.changes(token, store))); }
            catch (Exception e) { call.reject(e.getMessage(), e); }
        });
    }

    @PluginMethod
    public void hash(PluginCall call) {
        String id = call.getString("id");
        if (id == null) { call.reject("id is required"); return; }
        work.execute(() -> {
            try { call.resolve(JSObject.fromJSONObject(library.hash(id))); }
            catch (Exception e) { call.reject(e.getMessage(), e); }
        });
    }

    @PluginMethod
    public void upload(PluginCall call) {
        String id = call.getString("assetId"), url = call.getString("url");
        JSObject headers = call.getObject("headers", new JSObject());
        if (id == null || url == null) { call.reject("assetId and url are required"); return; }
        File file = library.file(id);
        if (!file.exists()) { call.reject(id + " has to be hashed before it's uploaded"); return; }
        boolean wifiOnly = Boolean.TRUE.equals(call.getBoolean("wifiOnly", true));
        try {
            UploadWorker.enqueue(getContext(), store, id, url, headers.toString(), file, wifiOnly);
            call.resolve();
        } catch (JSONException e) { call.reject(e.getMessage()); }
    }

    @PluginMethod
    public void uploads(PluginCall call) {
        call.resolve(new JSObject().put("uploads", store.uploadList()));
    }

    @PluginMethod
    public void forget(PluginCall call) {
        try {
            List<String> ids = call.getArray("ids", new JSArray()).toList();
            store.removeUploads(ids);
            for (String id : ids) //noinspection ResultOfMethodCallIgnored
                library.file(id).delete();
            call.resolve();
        } catch (JSONException e) { call.reject(e.getMessage()); }
    }

    @PluginMethod
    public void conditions(PluginCall call) {
        Context ctx = getContext();
        ConnectivityManager cm = ctx.getSystemService(ConnectivityManager.class);
        Network n = cm == null ? null : cm.getActiveNetwork();
        NetworkCapabilities caps = n == null ? null : cm.getNetworkCapabilities(n);
        boolean online = caps != null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET);
        boolean unmetered = caps != null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED);
        Intent battery = ctx.registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        int plugged = battery == null ? 0 : battery.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0);
        call.resolve(new JSObject().put("online", online).put("wifi", online && unmetered).put("charging", plugged != 0));
    }

    @PluginMethod
    public void finishBackground(PluginCall call) { call.resolve(); }

    /** The read-only S3 key for browsing without the Mac (SecureStore: Android Keystore). */
    @PluginMethod
    public void setViewerAccess(PluginCall call) {
        String json = call.getString("json");
        if (json == null) { call.reject("json is required"); return; }
        try { SecureStore.set(getContext(), json); call.resolve(); }
        catch (Exception e) { call.reject("Couldn't store the key: " + e.getMessage()); }
    }

    @PluginMethod
    public void getViewerAccess(PluginCall call) {
        String json = SecureStore.get(getContext());
        JSObject out = new JSObject();
        out.put("json", json == null ? JSObject.NULL : json);
        call.resolve(out);
    }

    @PluginMethod
    public void clearViewerAccess(PluginCall call) {
        SecureStore.clear(getContext());
        call.resolve();
    }

    /** Origin, device id and backup settings, for passes made while the app is closed (BackgroundPassWorker). */
    @PluginMethod
    public void configure(PluginCall call) {
        JSObject c = call.getData();
        if (c.getString("origin") == null || c.getString("deviceId") == null) { call.reject("origin and deviceId are required"); return; }
        work.execute(() -> {
            store.setConfig(c.toString());
            if (store.bgToken() == null) {                 // background passes only look at what's new from here on
                try { store.setBgToken(library.changes(null, store, false).getString("token")); } catch (Exception ignored) { }
            }
            BackupScheduler.schedule(getContext());
            call.resolve();
        });
    }
}
