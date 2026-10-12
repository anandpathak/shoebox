package app.photoframing.backup;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.HashSet;
import java.util.Iterator;
import java.util.Set;

/** Small native state: the OS upload queue's results and the ids seen on the last listing. */
public final class BackupStore {
    private final SharedPreferences prefs;

    public BackupStore(Context ctx) {
        prefs = ctx.getApplicationContext().getSharedPreferences("photoframing.backup", Context.MODE_PRIVATE);
    }

    public synchronized JSONObject uploads() {
        try { return new JSONObject(prefs.getString("uploads", "{}")); } catch (JSONException e) { return new JSONObject(); }
    }

    public synchronized void putUpload(String assetId, JSONObject state) {
        JSONObject all = uploads();
        try { all.put(assetId, state); } catch (JSONException ignored) { }
        prefs.edit().putString("uploads", all.toString()).apply();
    }

    public synchronized void removeUploads(Iterable<String> ids) {
        JSONObject all = uploads();
        for (String id : ids) all.remove(id);
        prefs.edit().putString("uploads", all.toString()).apply();
    }

    public synchronized JSONArray uploadList() {
        JSONArray out = new JSONArray();
        JSONObject all = uploads();
        for (Iterator<String> it = all.keys(); it.hasNext(); ) out.put(all.optJSONObject(it.next()));
        return out;
    }

    public synchronized String config() { return prefs.getString("config", null); }

    public synchronized void setConfig(String json) { prefs.edit().putString("config", json).apply(); }

    public synchronized String bgToken() { return prefs.getString("bgToken", null); }

    public synchronized void setBgToken(String token) { prefs.edit().putString("bgToken", token).apply(); }

    public synchronized Set<String> knownIds() { return new HashSet<>(prefs.getStringSet("known", new HashSet<>())); }

    public synchronized void setKnownIds(Set<String> ids) { prefs.edit().putStringSet("known", new HashSet<>(ids)).apply(); }
}
