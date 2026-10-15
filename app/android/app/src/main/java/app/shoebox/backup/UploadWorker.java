package app.shoebox.backup;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import android.content.pm.ServiceInfo;
import android.os.Build;

import androidx.annotation.NonNull;
import androidx.work.Constraints;
import androidx.work.Data;
import androidx.work.ExistingWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.OneTimeWorkRequest;
import androidx.work.WorkManager;
import androidx.core.app.NotificationCompat;
import androidx.work.ForegroundInfo;
import androidx.work.Worker;
import androidx.work.WorkerParameters;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Iterator;

/**
 * Sends one original to the Mac (POST /api/uploads/raw). WorkManager runs it when the network constraint
 * (Wi-Fi only, or any) is met, and keeps it going after the app is closed; big files run as a foreground
 * job with a notification so Android doesn't stop them halfway (§13.2).
 */
public class UploadWorker extends Worker {
    static final String CHANNEL = "backup";
    private static final long FOREGROUND_ABOVE = 50L * 1024 * 1024;

    public UploadWorker(@NonNull Context ctx, @NonNull WorkerParameters params) { super(ctx, params); }

    /** Queue one upload; WorkManager waits for Wi-Fi (or any network) and survives the app closing. */
    static void enqueue(Context ctx, BackupStore store, String assetId, String url, String headers, File file, boolean wifiOnly)
            throws JSONException {
        OneTimeWorkRequest req = new OneTimeWorkRequest.Builder(UploadWorker.class)
                .setConstraints(new Constraints.Builder()
                        .setRequiredNetworkType(wifiOnly ? NetworkType.UNMETERED : NetworkType.CONNECTED).build())
                .setInputData(new Data.Builder().putString("assetId", assetId).putString("url", url)
                        .putString("headers", headers).putString("file", file.getAbsolutePath()).build())
                .addTag("backup")
                .build();
        store.putUpload(assetId, new JSONObject().put("assetId", assetId).put("state", "queued"));
        WorkManager.getInstance(ctx).enqueueUniqueWork("upload-" + assetId, ExistingWorkPolicy.REPLACE, req);
    }

    @NonNull
    @Override
    public Result doWork() {
        String assetId = getInputData().getString("assetId");
        String url = getInputData().getString("url");
        String headers = getInputData().getString("headers");
        String path = getInputData().getString("file");
        BackupStore store = new BackupStore(getApplicationContext());
        if (assetId == null || url == null || path == null) return Result.failure();
        File file = new File(path);
        JSONObject state = new JSONObject();
        try {
            state.put("assetId", assetId);
            state.put("state", "running");
            store.putUpload(assetId, state);
            if (file.length() > FOREGROUND_ABOVE) setForegroundAsync(foreground(file.getName()));
            HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
            c.setRequestMethod("POST");
            c.setDoOutput(true);
            c.setConnectTimeout(30_000);
            c.setReadTimeout(120_000);
            c.setFixedLengthStreamingMode(file.length());
            c.setRequestProperty("Content-Type", "application/octet-stream");
            JSONObject h = new JSONObject(headers == null ? "{}" : headers);
            for (Iterator<String> it = h.keys(); it.hasNext(); ) {
                String k = it.next();
                c.setRequestProperty(k, h.getString(k));
            }
            try (InputStream in = new FileInputStream(file); OutputStream out = c.getOutputStream()) {
                byte[] buf = new byte[1 << 16];
                int n;
                while ((n = in.read(buf)) > 0) {
                    if (isStopped()) throw new IOException("stopped");
                    out.write(buf, 0, n);
                }
            }
            int status = c.getResponseCode();
            InputStream body = status < 400 ? c.getInputStream() : c.getErrorStream();
            state.put("state", "done");
            state.put("status", status);
            state.put("body", read(body));
            if (status == 200) //noinspection ResultOfMethodCallIgnored
                file.delete();
        } catch (IOException | JSONException e) {
            try {
                state.put("state", "failed");
                state.put("error", e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage());
            } catch (JSONException ignored) { }
        }
        store.putUpload(assetId, state);
        PhotoBackupPlugin.uploadFinished();
        return Result.success();       // retries are the backup engine's decision (web/src/backup/engine.ts)
    }

    private static String read(InputStream in) throws IOException {
        if (in == null) return "";
        try (InputStream s = in) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = s.read(buf)) > 0 && out.size() < 65536) out.write(buf, 0, n);
            return out.toString("UTF-8");
        }
    }

    private ForegroundInfo foreground(String name) {
        Context ctx = getApplicationContext();
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationManager nm = ctx.getSystemService(NotificationManager.class);
            nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Photo backup", NotificationManager.IMPORTANCE_LOW));
        }
        android.app.Notification n = new NotificationCompat.Builder(ctx, CHANNEL)
                .setContentTitle("Backing up to your Mac")
                .setContentText(name)
                .setSmallIcon(android.R.drawable.stat_sys_upload)
                .setOngoing(true)
                .build();
        if (Build.VERSION.SDK_INT >= 29) return new ForegroundInfo(42, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        return new ForegroundInfo(42, n);
    }
}
