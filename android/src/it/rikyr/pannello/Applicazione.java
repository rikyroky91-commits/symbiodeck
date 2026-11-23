package it.rikyr.pannello;

import android.app.Application;
import android.app.admin.DevicePolicyManager;
import android.content.BroadcastReceiver;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.os.BatteryManager;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.provider.Settings;
import android.util.Log;

/**
 * Processo dell'app, dichiarato "persistent" (app di sistema): Android lo avvia all'accensione, non lo chiude per
 * liberare memoria e lo riavvia se muore. Qui vive la simbiosi con il PC (prima stava nell'activity: se Android la
 * chiudeva a schermo spento, nessuno riaccendeva più lo schermo quando il PC tornava).
 */
public class Applicazione extends Application {
    private static final String TAG = "PannelloSimbiosi";
    private final Handler principale = new Handler(Looper.getMainLooper());
    private Simbiosi simbiosi;
    private PowerManager.WakeLock sveglio;
    private Integer ultimaPresa = null;

    /** Indirizzi del pannello: quello scelto a mano (se c'è), poi quelli dell'abbinamento (USB, rete), poi quelli
     *  scritti nell'APK da costruisci.py. Vuoto se l'APK è generico e il tablet non è ancora abbinato. */
    static String[] indirizzi(Context c) {
        android.content.SharedPreferences p = c.getSharedPreferences("pannello", MODE_PRIVATE);
        java.util.LinkedHashSet<String> l = new java.util.LinkedHashSet<>();
        for (String u : new String[] { p.getString("url", null), p.getString("url_usb", null),
                p.getString("url_rete", null), Config.URL_USB, Config.URL_RETE })
            if (u != null && !u.isEmpty()) l.add(u);
        return l.toArray(new String[0]);
    }

    /** Nome del PC e app di Apollo per il secondo monitor: dall'APK, altrimenti dall'abbinamento. */
    static String valore(Context c, String chiave, String dallApk) {
        return dallApk != null && !dallApk.isEmpty() ? dallApk
                : c.getSharedPreferences("pannello", MODE_PRIVATE).getString(chiave, "");
    }

    /** Chiamato dopo un abbinamento riuscito: la simbiosi riparte con i nuovi indirizzi. */
    void abbinato() {
        if (simbiosi != null) simbiosi.ferma();
        simbiosi = null;
        avviaSimbiosi(false);
    }

    private void avviaSimbiosi(boolean spento) {
        String[] pagine = indirizzi(this);
        if (pagine.length == 0) { Log.i(TAG, "tablet non abbinato: simbiosi in attesa"); return; }
        simbiosi = new Simbiosi(pagine, spento, new Simbiosi.Azioni() {
            @Override public void spegniSchermo() { principale.post(new Runnable() { @Override public void run() { Applicazione.this.spegniSchermo(); } }); }
            @Override public void accendiSchermo() { principale.post(new Runnable() { @Override public void run() { Applicazione.this.accendiSchermo(); } }); }
            @Override public void spegniTablet() { principale.post(new Runnable() { @Override public void run() { Applicazione.this.spegniTablet(); } }); }
            @Override public void riavviaAdb() { Applicazione.this.riavviaAdb(); }
        });
        simbiosi.start();
        Log.i(TAG, "simbiosi avviata nel processo dell'app");
    }

    @Override public void onCreate() {
        super.onCreate();
        // In carica la CPU resta sveglia anche a schermo spento, così il tablet si accorge subito del PC;
        // a batteria dorme (la corrente la risveglia). Lo prende e lo lascia il ricevitore della batteria.
        sveglio = ((PowerManager) getSystemService(POWER_SERVICE))
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "pannello:simbiosi");
        sveglio.setReferenceCounted(false);
        boolean spento = !((PowerManager) getSystemService(POWER_SERVICE)).isInteractive();
        avviaSimbiosi(spento);
        registerReceiver(batteria, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        registerReceiver(usb, new IntentFilter("android.hardware.usb.action.USB_STATE"));
        // Jarvis: microfono e voce del tablet, anche a schermo spento (si collega da solo quando il PC c'è)
        JarvisServizio.avvia(this);
    }

    private final BroadcastReceiver batteria = new BroadcastReceiver() {
        @Override public void onReceive(Context c, Intent i) {
            int presa = i.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0);
            boolean collegato = presa != 0;
            int scala = i.getIntExtra(BatteryManager.EXTRA_SCALE, 100);
            int livello = scala > 0 ? i.getIntExtra(BatteryManager.EXTRA_LEVEL, 100) * 100 / scala : 100;
            if (collegato && !sveglio.isHeld()) sveglio.acquire();
            else if (!collegato && sveglio.isHeld()) sveglio.release();
            ultimaPresa = ultimaPresa == null ? Integer.valueOf(presa) : ultimaPresa;
            boolean cambiata = ultimaPresa != presa;
            ultimaPresa = presa;
            if (simbiosi == null) return;  // non ancora abbinato
            simbiosi.alimentazione(collegato, presa == BatteryManager.BATTERY_PLUGGED_AC, livello);
            // Android accende da sé lo schermo quando la corrente arriva, se ne va o cambia tipo (la porta del PC
            // spento o in sospensione la toglie e la ridà): se il PC non risponde, lo si rispegne subito.
            if (cambiata) {
                principale.postDelayed(new Runnable() {
                    @Override public void run() { if (simbiosi != null) simbiosi.rispegniDopoCorrente(); }
                }, 1200);
            }
        }
    };

    // Collegamento dati USB (broadcast di sistema, sticky): "configured" = un PC acceso ha riconosciuto il tablet
    private final BroadcastReceiver usb = new BroadcastReceiver() {
        @Override public void onReceive(Context c, Intent i) {
            if (simbiosi != null) simbiosi.usb(i.getBooleanExtra("configured", false));
        }
    };

    /** PC giù: schermo spento (serve l'amministratore del dispositivo, attivato con dpm). */
    private void spegniSchermo() {
        DevicePolicyManager dpm = (DevicePolicyManager) getSystemService(DEVICE_POLICY_SERVICE);
        if (dpm.isAdminActive(new ComponentName(this, Amministratore.class))) dpm.lockNow();
    }

    /** PC tornato: si accende lo schermo e il pannello torna davanti a tutto. */
    @SuppressWarnings("deprecation")
    private void accendiSchermo() {
        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        PowerManager.WakeLock luce = pm.newWakeLock(PowerManager.SCREEN_BRIGHT_WAKE_LOCK
                | PowerManager.ACQUIRE_CAUSES_WAKEUP | PowerManager.ON_AFTER_RELEASE, "pannello:sveglia");
        luce.acquire(5000);
        startActivity(new Intent(this, Pannello.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT));
    }

    /**
     * Collegamento adb rimasto "offline" dopo la ripresa del PC: spegne e riaccende il debug USB, come staccare e
     * riattaccare il cavo. Serve WRITE_SECURE_SETTINGS (privapp-permissions-pannello.xml).
     */
    private void riavviaAdb() {
        new Thread("riavvio-adb") {
            @Override public void run() {
                try {
                    Settings.Global.putInt(getContentResolver(), Settings.Global.ADB_ENABLED, 0);
                    Thread.sleep(3000);
                    Settings.Global.putInt(getContentResolver(), Settings.Global.ADB_ENABLED, 1);
                    Log.i(TAG, "debug USB riavviato");
                } catch (Exception e) {
                    Log.w(TAG, "riavvio del debug USB non riuscito: " + e);
                    try { Settings.Global.putInt(getContentResolver(), Settings.Global.ADB_ENABLED, 1); }
                    catch (Exception ignorata) { /* senza permesso non è cambiato nulla */ }
                }
            }
        }.start();
    }

    /** PC spento, niente corrente e batteria quasi scarica: spegnimento completo (permesso SHUTDOWN, app di sistema). */
    private void spegniTablet() {
        try {
            Intent i = new Intent("com.android.internal.intent.action.REQUEST_SHUTDOWN");
            i.putExtra("android.intent.extra.KEY_CONFIRM", false);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
        } catch (Exception e) {
            Log.w(TAG, "spegnimento non permesso: " + e);
        }
    }
}
