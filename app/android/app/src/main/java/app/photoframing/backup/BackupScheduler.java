package app.photoframing.backup;

import android.content.Context;
import android.provider.MediaStore;

import androidx.work.Constraints;
import androidx.work.ExistingPeriodicWorkPolicy;
import androidx.work.ExistingWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.OneTimeWorkRequest;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;

import java.util.concurrent.TimeUnit;

/** When background passes run (§13.2): every 6 hours, and within minutes of a new photo or video. */
final class BackupScheduler {
    private BackupScheduler() { }

    static void schedule(Context ctx) {
        WorkManager wm = WorkManager.getInstance(ctx);
        Constraints basic = new Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED).setRequiresBatteryNotLow(true).build();
        wm.enqueueUniquePeriodicWork("backup-periodic", ExistingPeriodicWorkPolicy.KEEP,
                new PeriodicWorkRequest.Builder(BackgroundPassWorker.class, 6, TimeUnit.HOURS).setConstraints(basic).build());
        scheduleOnNewMedia(ctx);
    }

    /** A content-URI trigger fires once, so it's queued again after every pass. */
    static void scheduleOnNewMedia(Context ctx) {
        Constraints c = new Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED).setRequiresBatteryNotLow(true)
                .addContentUriTrigger(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, true)
                .addContentUriTrigger(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, true)
                .setTriggerContentUpdateDelay(1, TimeUnit.MINUTES)
                .setTriggerContentMaxDelay(10, TimeUnit.MINUTES)
                .build();
        WorkManager.getInstance(ctx).enqueueUniqueWork("backup-new-media", ExistingWorkPolicy.REPLACE,
                new OneTimeWorkRequest.Builder(BackgroundPassWorker.class).setConstraints(c).build());
    }
}
