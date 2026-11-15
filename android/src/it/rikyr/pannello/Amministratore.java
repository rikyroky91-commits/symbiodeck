package it.rikyr.pannello;

import android.app.admin.DeviceAdminReceiver;

/** Serve solo per spegnere lo schermo (DevicePolicyManager.lockNow) quando il PC si spegne.
 *  Si attiva una volta con: adb shell dpm set-active-admin it.rikyr.pannello/.Amministratore */
public class Amministratore extends DeviceAdminReceiver {
}
