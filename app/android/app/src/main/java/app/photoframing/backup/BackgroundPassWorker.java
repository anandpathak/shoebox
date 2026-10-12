package app.photoframing.backup;

import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.BatteryManager;

import androidx.annotation.NonNull;
import androidx.work.Worker;
import androidx.work.WorkerParameters;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * A pass while the app is closed: new items → hash → ask the Mac → queue uploads. The app's ledger
 * (web/src/backup) catches up next time it opens, and the Mac's check stops anything being sent twice.
 */
public class BackgroundPassWorker extends Worker {
    private static final int LIMIT = 100;

    public BackgroundPassWorker(@NonNull Context ctx, @NonNull WorkerParameters params) { super(ctx, params); }

    @NonNull
    @Override
    public Result doWork() {
        Context ctx = getApplicationContext();
        try {
            pass(ctx);
        } catch (Exception ignored) {
            // nothing lost: the token only moves on after a complete pass
        } finally {
            BackupScheduler.scheduleOnNewMedia(ctx);
        }
        return Result.success();
    }

    private void pass(Context ctx) throws Exception {
        BackupStore store = new BackupStore(ctx);
        MediaLibrary library = new MediaLibrary(ctx);
        String raw = store.config();
        if (raw == null || "denied".equals(library.access())) return;
        JSONObject c = new JSONObject(raw);
        if (!c.optBoolean("enabled", false)) return;
        String origin = c.getString("origin");
        boolean wifiOnly = c.optBoolean("wifiOnly", true);
        boolean charging = charging(ctx), wifi = unmetered(ctx);
        if (store.bgToken() == null) {        // never configured from the app: start from now, the backlog is the app's job
            store.setBgToken(library.changes(null, store, false).getString("token"));
            return;
        }
        JSONObject ch = library.changes(store.bgToken(), store, false);
        JSONArray assets = ch.getJSONArray("assets");
        List<JSONObject> todo = new ArrayList<>();
        int wanted = 0;
        for (int i = 0; i < assets.length(); i++) {
            JSONObject a = assets.getJSONObject(i);
            if (!wanted(a, c)) continue;
            wanted++;
            if ("video".equals(a.optString("kind")) && c.optBoolean("videosWhileCharging", true) && !charging) continue;
            if (todo.size() < LIMIT) todo.add(a);
        }
        List<String> shas = new ArrayList<>();
        for (JSONObject a : todo) {
            if (isStopped()) return;
            shas.add(library.hash(a.getString("id")).getString("sha256"));
        }
        if (!todo.isEmpty() && !(wifiOnly && !wifi)) {
            Set<String> missing = check(origin, shas);
            Set<String> sent = new HashSet<>();
            for (int i = 0; i < todo.size(); i++) {
                JSONObject a = todo.get(i);
                String sha = shas.get(i), id = a.getString("id");
                if (!missing.contains(sha) || !sent.add(sha)) continue;
                JSONObject h = new JSONObject().put("X-Device-Id", c.getString("deviceId")).put("X-Asset-Id", id)
                        .put("X-Sha256", sha).put("X-Filename", a.optString("filename", id));
                if (!a.isNull("created")) h.put("X-Last-Modified", String.valueOf(a.getLong("created")));
                UploadWorker.enqueue(ctx, store, id, origin + "/api/uploads/raw", h.toString(), library.file(id), wifiOnly);
            }
        }
        // Move on only when every new item was looked at (and sent if needed); otherwise the next pass retries.
        if (todo.size() == wanted && !(wifiOnly && !wifi && !todo.isEmpty())) store.setBgToken(ch.getString("token"));
    }

    static boolean wanted(JSONObject a, JSONObject c) throws JSONException {
        if ("video".equals(a.optString("kind")) && !c.optBoolean("videos", true)) return false;
        if (a.optBoolean("screenshot") && !c.optBoolean("screenshots", false)) return false;
        if (!c.isNull("from") && c.has("from") && !a.isNull("created") && a.getLong("created") < c.getLong("from")) return false;
        JSONArray albums = c.optJSONArray("albums");
        if (albums != null) {
            JSONArray mine = a.optJSONArray("albums");
            boolean any = false;
            for (int i = 0; mine != null && i < mine.length() && !any; i++)
                for (int j = 0; j < albums.length() && !any; j++) any = mine.getString(i).equals(albums.getString(j));
            return any;
        }
        return true;
    }

    private static Set<String> check(String origin, List<String> shas) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(origin + "/api/backup/check").openConnection();
        conn.setRequestMethod("POST");
        conn.setDoOutput(true);
        conn.setConnectTimeout(20_000);
        conn.setReadTimeout(30_000);
        conn.setRequestProperty("Content-Type", "application/json");
        JSONArray list = new JSONArray(new ArrayList<>(new HashSet<>(shas)));
        try (OutputStream out = conn.getOutputStream()) {
            out.write(new JSONObject().put("hashes", list).toString().getBytes(StandardCharsets.UTF_8));
        }
        if (conn.getResponseCode() != 200) throw new Exception("the Mac answered " + conn.getResponseCode());
        ByteArrayOutputStream body = new ByteArrayOutputStream();
        try (InputStream in = conn.getInputStream()) {
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) body.write(buf, 0, n);
        }
        JSONArray missing = new JSONObject(body.toString("UTF-8")).getJSONArray("missing");
        Set<String> out = new HashSet<>();
        for (int i = 0; i < missing.length(); i++) out.add(missing.getString(i));
        return out;
    }

    private static boolean charging(Context ctx) {
        Intent b = ctx.registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        return b != null && b.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) != 0;
    }

    private static boolean unmetered(Context ctx) {
        ConnectivityManager cm = ctx.getSystemService(ConnectivityManager.class);
        Network n = cm == null ? null : cm.getActiveNetwork();
        NetworkCapabilities caps = n == null ? null : cm.getNetworkCapabilities(n);
        return caps != null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED);
    }
}
