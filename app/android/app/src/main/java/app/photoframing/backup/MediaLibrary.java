package app.photoframing.backup;

import android.Manifest;
import android.content.ContentUris;
import android.content.Context;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.provider.MediaStore;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HashSet;
import java.util.Set;

/**
 * The camera roll through MediaStore (LOCAL_APP_PLAN.md §13.3).
 * Android 11+: "what changed" uses MediaStore generations, so later passes only read new/edited rows.
 * Originals are read with setRequireOriginal so the location isn't redacted (needs ACCESS_MEDIA_LOCATION).
 */
public final class MediaLibrary {
    private final Context ctx;

    public MediaLibrary(Context ctx) { this.ctx = ctx.getApplicationContext(); }

    private boolean granted(String p) {
        return ctx.checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED;
    }

    /** full / limited (Android 14 "selected photos") / denied. */
    public String access() {
        if (Build.VERSION.SDK_INT >= 33) {
            if (granted(Manifest.permission.READ_MEDIA_IMAGES) && granted(Manifest.permission.READ_MEDIA_VIDEO)) return "full";
            if (Build.VERSION.SDK_INT >= 34 && granted(Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED)) return "limited";
            if (granted(Manifest.permission.READ_MEDIA_IMAGES) || granted(Manifest.permission.READ_MEDIA_VIDEO)) return "limited";
            return "denied";
        }
        return granted(Manifest.permission.READ_EXTERNAL_STORAGE) ? "full" : "denied";
    }

    private static Uri files() {
        return Build.VERSION.SDK_INT >= 29 ? MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL) : MediaStore.Files.getContentUri("external");
    }

    private static final String MEDIA = "(" + MediaStore.Files.FileColumns.MEDIA_TYPE + "=" + MediaStore.Files.FileColumns.MEDIA_TYPE_IMAGE
            + " OR " + MediaStore.Files.FileColumns.MEDIA_TYPE + "=" + MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO + ")";

    /** The token is "<volume version>:<generation>" on Android 11+, "date:<seconds>" before. */
    private String currentToken() {
        if (Build.VERSION.SDK_INT >= 30) {
            return MediaStore.getVersion(ctx) + ":" + MediaStore.getGeneration(ctx, MediaStore.VOLUME_EXTERNAL_PRIMARY);
        }
        return "date:" + (System.currentTimeMillis() / 1000);
    }

    /** Items added or edited since `token` (null: everything), and ids that are gone. */
    public JSONObject changes(String token, BackupStore store) throws JSONException { return changes(token, store, true); }

    /** trackDeletions=false for background passes: the ids-seen list belongs to the app's own listing. */
    public JSONObject changes(String token, BackupStore store, boolean trackDeletions) throws JSONException {
        String next = currentToken();
        String where = MEDIA;
        String[] args = null;
        boolean full = true;
        if (token != null && Build.VERSION.SDK_INT >= 30 && token.contains(":") && !token.startsWith("date:")) {
            String[] parts = token.split(":", 2);
            if (parts[0].equals(MediaStore.getVersion(ctx))) {     // a new volume version means generations restarted
                where += " AND " + MediaStore.MediaColumns.GENERATION_MODIFIED + " > ?";
                args = new String[] { parts[1] };
                full = false;
            }
        } else if (token != null && token.startsWith("date:")) {
            where += " AND " + MediaStore.MediaColumns.DATE_MODIFIED + " > ?";
            args = new String[] { token.substring(5) };
            full = false;
        }
        JSONArray assets = new JSONArray();
        String[] cols = Build.VERSION.SDK_INT >= 30
                ? new String[] { "_id", "media_type", "_display_name", "_size", "datetaken", "date_modified", "relative_path", "bucket_display_name", "generation_modified" }
                : new String[] { "_id", "media_type", "_display_name", "_size", "datetaken", "date_modified", "bucket_display_name" };
        try (Cursor c = ctx.getContentResolver().query(files(), cols, where, args, "_id ASC")) {
            if (c != null) {
                while (c.moveToNext()) {
                    JSONObject a = new JSONObject();
                    a.put("id", String.valueOf(c.getLong(0)));
                    boolean video = c.getInt(1) == MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO;
                    a.put("kind", video ? "video" : "photo");
                    a.put("filename", c.isNull(2) ? c.getLong(0) + (video ? ".mp4" : ".jpg") : c.getString(2));
                    a.put("bytes", c.isNull(3) ? JSONObject.NULL : c.getLong(3));
                    long modifiedSec = c.getLong(5);
                    a.put("created", c.isNull(4) ? modifiedSec * 1000 : c.getLong(4));
                    String path = Build.VERSION.SDK_INT >= 30 && !c.isNull(6) ? c.getString(6) : "";
                    String bucket = c.isNull(Build.VERSION.SDK_INT >= 30 ? 7 : 6) ? null : c.getString(Build.VERSION.SDK_INT >= 30 ? 7 : 6);
                    // generation_modified grows with every edit; it is what "edited" compares on Android 11+.
                    a.put("modified", Build.VERSION.SDK_INT >= 30 ? c.getLong(8) : modifiedSec * 1000);
                    a.put("screenshot", path.toLowerCase().contains("screenshots") || "Screenshots".equalsIgnoreCase(bucket));
                    JSONArray albums = new JSONArray();
                    if (bucket != null) albums.put(bucket);
                    a.put("albums", albums);
                    assets.put(a);
                }
            }
        }
        // MediaStore keeps no record of deletions: compare against the ids seen last time.
        JSONArray deleted = new JSONArray();
        if (trackDeletions) {
            Set<String> now = allIds();
            for (String id : store.knownIds()) if (!now.contains(id)) deleted.put(id);
            store.setKnownIds(now);
        }
        JSONObject out = new JSONObject();
        out.put("assets", assets);
        out.put("deleted", deleted);
        out.put("token", next);
        out.put("full", full);
        return out;
    }

    private Set<String> allIds() {
        Set<String> ids = new HashSet<>();
        try (Cursor c = ctx.getContentResolver().query(files(), new String[] { "_id" }, MEDIA, null, null)) {
            if (c != null) while (c.moveToNext()) ids.add(String.valueOf(c.getLong(0)));
        }
        return ids;
    }

    private Uri uri(String id) {
        long n = Long.parseLong(id);
        String type = null;
        try (Cursor c = ctx.getContentResolver().query(files(), new String[] { "media_type" }, "_id=?", new String[] { id }, null)) {
            if (c != null && c.moveToFirst()) type = c.getInt(0) == MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO ? "video" : "image";
        }
        if (type == null) return null;
        Uri base = type.equals("video") ? MediaStore.Video.Media.EXTERNAL_CONTENT_URI : MediaStore.Images.Media.EXTERNAL_CONTENT_URI;
        Uri u = ContentUris.withAppendedId(base, n);
        if (Build.VERSION.SDK_INT >= 29 && granted(Manifest.permission.ACCESS_MEDIA_LOCATION)) u = MediaStore.setRequireOriginal(u);
        return u;
    }

    public File file(String id) {
        File dir = new File(ctx.getCacheDir(), "backup");
        //noinspection ResultOfMethodCallIgnored
        dir.mkdirs();
        return new File(dir, id.replaceAll("[^A-Za-z0-9-]", "_"));
    }

    /** Copy the original to the cache (the copy is what gets uploaded) and SHA-256 it on the way. */
    public JSONObject hash(String id) throws IOException, JSONException {
        Uri u = uri(id);
        if (u == null) throw new IOException("The item " + id + " is no longer on this phone");
        MessageDigest md;
        try { md = MessageDigest.getInstance("SHA-256"); } catch (NoSuchAlgorithmException e) { throw new IOException(e); }
        File target = file(id);
        long total = 0;
        try (InputStream in = ctx.getContentResolver().openInputStream(u); OutputStream out = new FileOutputStream(target)) {
            if (in == null) throw new IOException("Couldn't open " + id);
            byte[] buf = new byte[1 << 20];
            int n;
            while ((n = in.read(buf)) > 0) {
                md.update(buf, 0, n);
                out.write(buf, 0, n);
                total += n;
            }
        }
        StringBuilder hex = new StringBuilder();
        for (byte b : md.digest()) hex.append(String.format("%02x", b));
        JSONObject r = new JSONObject();
        r.put("sha256", hex.toString());
        r.put("bytes", total);
        return r;
    }
}
