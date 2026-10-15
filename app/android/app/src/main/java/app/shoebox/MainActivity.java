package app.shoebox;

import android.os.Bundle;

import app.shoebox.backup.PhotoBackupPlugin;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(PhotoBackupPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
