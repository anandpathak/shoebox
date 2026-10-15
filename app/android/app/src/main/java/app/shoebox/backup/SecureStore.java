package app.shoebox.backup;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * The read-only S3 key for browsing without the Mac, encrypted with an AES key that lives in the Android
 * Keystore (it can't be read out of the phone); only the ciphertext is in SharedPreferences.
 */
final class SecureStore {
    private static final String ALIAS = "shoebox_viewer_access";
    private static final String PREFS = "shoebox.secure";
    private static final String ENTRY = "viewer_access";

    private SecureStore() { }

    private static SecretKey key() throws Exception {
        KeyStore ks = KeyStore.getInstance("AndroidKeyStore");
        ks.load(null);
        if (ks.containsAlias(ALIAS)) return ((KeyStore.SecretKeyEntry) ks.getEntry(ALIAS, null)).getSecretKey();
        KeyGenerator gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        gen.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build());
        return gen.generateKey();
    }

    private static SharedPreferences prefs(Context ctx) {
        return ctx.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static void set(Context ctx, String value) throws Exception {
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.ENCRYPT_MODE, key());
        byte[] iv = c.getIV();
        byte[] sealed = c.doFinal(value.getBytes(StandardCharsets.UTF_8));
        String stored = Base64.encodeToString(iv, Base64.NO_WRAP) + ":" + Base64.encodeToString(sealed, Base64.NO_WRAP);
        prefs(ctx).edit().putString(ENTRY, stored).apply();
    }

    static String get(Context ctx) {
        String stored = prefs(ctx).getString(ENTRY, null);
        if (stored == null || !stored.contains(":")) return null;
        try {
            String[] parts = stored.split(":", 2);
            Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
            c.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)));
            return new String(c.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), StandardCharsets.UTF_8);
        } catch (Exception e) {
            return null;            // key lost (e.g. restored onto another phone): set it up again
        }
    }

    static void clear(Context ctx) { prefs(ctx).edit().remove(ENTRY).apply(); }
}
