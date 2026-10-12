package app.photoframing;

import android.os.Bundle;

import app.photoframing.backup.PhotoBackupPlugin;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(PhotoBackupPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
