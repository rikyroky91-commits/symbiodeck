package it.rikyr.pannello;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.audiofx.AcousticEchoCanceler;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaRecorder;
import android.net.Uri;
import android.net.wifi.WifiManager;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;

import org.json.JSONObject;

import java.io.DataInputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Microfono e altoparlante del tablet per Jarvis (server/jarvis sul PC, porta 47802).
 *
 * Servizio in primo piano, avviato dal processo persistente (Applicazione): così funziona anche a schermo spento
 * (Android 9 dà silenzio al microfono delle app in background, non a un servizio in primo piano).
 * - "altoparlante": GET lunga, il PC manda trame [tipo 1 byte][lunghezza 4 byte][dati]:
 *   0 viva, 1 audio PCM 24 kHz da suonare, 2 comando JSON ({"microfono":true}...), 3 taci (svuota l'audio).
 * - "microfono": finché il PC lo chiede, POST a pezzi con il PCM 16 kHz del microfono.
 * Mentre il PC è collegato si tengono un wakelock parziale e il Wi-Fi ad alte prestazioni (anche a batteria):
 * senza, a schermo spento la CPU dorme e l'audio si ferma. PC spento o addormentato: si lascia tutto e si riprova.
 */
public class JarvisServizio extends Service {
    private static final String TAG = "PannelloJarvis";
    private static final int PORTA = 47802;
    private static final int FREQ_IN = 16000, FREQ_OUT = 24000;

    private volatile boolean vivo = true;
    private volatile boolean microfonoVoluto = false;
    private volatile boolean accendiSchermo = false;
    // cancellazione dell'eco (come il vivavoce): il microfono non sente la voce di Jarvis che esce dalle casse
    // del tablet, così lo si può interrompere parlandogli sopra
    private volatile boolean eco = true;
    private volatile String base = null;       // http://host:47802 del PC che risponde
    private volatile String token = "";
    private Thread altoparlante, microfono;
    private AudioTrack traccia;
    private PowerManager.WakeLock sveglio;
    private WifiManager.WifiLock wifi;

    static void avvia(Context c) {
        try {
            c.startForegroundService(new Intent(c, JarvisServizio.class));
        } catch (Exception e) {
            Log.w(TAG, "servizio Jarvis non avviato: " + e);
        }
    }

    @Override public void onCreate() {
        super.onCreate();
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        nm.createNotificationChannel(new NotificationChannel("jarvis", "Jarvis", NotificationManager.IMPORTANCE_MIN));
        Notification n = new Notification.Builder(this, "jarvis")
                .setContentTitle("Jarvis")
                .setContentText("Collegato al PC: microfono e voce")
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .setOngoing(true)
                .build();
        startForeground(47, n);
        sveglio = ((PowerManager) getSystemService(POWER_SERVICE))
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "pannello:jarvis");
        sveglio.setReferenceCounted(false);
        wifi = ((WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE))
                .createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "pannello:jarvis");
        wifi.setReferenceCounted(false);
        altoparlante = new Thread(new Runnable() { @Override public void run() { cicloAltoparlante(); } }, "jarvis-voce");
        altoparlante.start();
    }

    @Override public int onStartCommand(Intent i, int flags, int id) { return START_STICKY; }

    @Override public IBinder onBind(Intent i) { return null; }

    @Override public void onDestroy() {
        vivo = false;
        microfonoVoluto = false;
        if (altoparlante != null) altoparlante.interrupt();
        tieniSveglio(false);
        super.onDestroy();
    }

    private void tieniSveglio(boolean si) {
        try {
            if (si) { sveglio.acquire(); wifi.acquire(); }
            else { if (sveglio.isHeld()) sveglio.release(); if (wifi.isHeld()) wifi.release(); }
        } catch (Exception e) { Log.w(TAG, "wakelock: " + e); }
    }

    /** Indirizzi di Jarvis ricavati da quelli del pannello (stesso host e stesso token, porta 47802). */
    private String[][] candidati() {
        String[] pagine = Applicazione.indirizzi(this);
        String[][] r = new String[pagine.length][];
        for (int i = 0; i < pagine.length; i++) {
            Uri u = Uri.parse(pagine[i]);
            String t = u.getQueryParameter("t");
            r[i] = new String[] { "http://" + u.getHost() + ":" + PORTA, t == null ? "" : t };
        }
        return r;
    }

    // ------------------------------------------------------------------ altoparlante e comandi
    private void cicloAltoparlante() {
        int giro = 0;
        while (vivo) {
            boolean collegato = false;
            for (String[] c : candidati()) {
                if (!vivo) return;
                try {
                    collegato = ascolta(c[0], c[1]);
                } catch (Exception e) {
                    if (giro % 20 == 0) Log.i(TAG, "Jarvis non raggiungibile su " + c[0] + ": " + e);
                }
                if (collegato) break;
            }
            // collegamento caduto (PC spento, in sospensione o Jarvis fermo): si molla tutto e si riprova
            microfonoVoluto = false;
            base = null;
            fermaTraccia();
            tieniSveglio(false);
            giro++;
            try { Thread.sleep(collegato ? 1000 : 4000); } catch (InterruptedException e) { return; }
        }
    }

    /** true se il collegamento è riuscito (anche se poi è caduto). */
    private boolean ascolta(String indirizzo, String t) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(indirizzo + "/jarvis/altoparlante?t=" + Uri.encode(t)).openConnection();
        c.setConnectTimeout(3000);
        c.setReadTimeout(10000);   // il PC manda una trama "viva" ogni 2 s
        c.setUseCaches(false);
        if (c.getResponseCode() != 200) { c.disconnect(); return false; }
        base = indirizzo;
        token = t;
        tieniSveglio(true);
        Log.i(TAG, "collegato a Jarvis: " + indirizzo);
        DataInputStream in = new DataInputStream(c.getInputStream());
        byte[] buf = new byte[64 * 1024];
        try {
            while (vivo) {
                int tipo = in.readUnsignedByte();
                int n = in.readInt();
                if (n < 0 || n > buf.length) throw new IOException("trama troppo grande: " + n);
                in.readFully(buf, 0, n);
                // microfono voluto ma fermo (permesso arrivato dopo, AudioRecord occupato...): si riprova
                if (tipo == 0 && microfonoVoluto) avviaMicrofono();
                if (tipo == 1) suona(buf, n);
                else if (tipo == 2) comando(new String(buf, 0, n, "UTF-8"));
                else if (tipo == 3) taci();
            }
        } catch (EOFException e) {
            Log.i(TAG, "Jarvis ha chiuso il collegamento");
        } finally {
            c.disconnect();
        }
        return true;
    }

    private void comando(String json) {
        try {
            JSONObject o = new JSONObject(json);
            if (o.has("accendi_schermo")) accendiSchermo = o.getBoolean("accendi_schermo");
            if (o.has("eco") && o.getBoolean("eco") != eco) {
                eco = o.getBoolean("eco");
                fermaTraccia();   // la voce riparte con il tipo di flusso giusto; il microfono si riapre da solo
            }
            if (o.has("microfono")) {
                microfonoVoluto = o.getBoolean("microfono");
                if (microfonoVoluto) avviaMicrofono();
            }
            if (o.has("attivato") && accendiSchermo) svegliaSchermo();
            if (o.has("foto")) mandaFoto(o.getString("foto"));
        } catch (Exception e) {
            Log.w(TAG, "comando non valido: " + json);
        }
    }

    /** Jarvis vuole guardare (l'utente ha già detto sì): una foto con la telecamera davanti, mandata al PC. */
    private void mandaFoto(final String id) {
        new Thread(() -> {
            byte[] jpeg = null;
            String errore = null;
            try {
                jpeg = Fotocamera.scatta(this);
            } catch (Exception e) {
                errore = e.getMessage() != null ? e.getMessage() : e.toString();
                Log.w(TAG, "foto non riuscita", e);
            }
            String b = base;
            if (b == null) return;
            try {
                String url = b + "/jarvis/telecamera?id=" + Uri.encode(id) + "&t=" + Uri.encode(token)
                        + (errore != null ? "&errore=" + Uri.encode(errore) : "");
                HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
                c.setConnectTimeout(3000);
                c.setReadTimeout(10000);
                c.setDoOutput(true);
                c.setRequestMethod("POST");
                c.setRequestProperty("Content-Type", "image/jpeg");
                byte[] corpo = jpeg != null ? jpeg : new byte[0];
                c.setFixedLengthStreamingMode(corpo.length);
                try (OutputStream o = c.getOutputStream()) {
                    o.write(corpo);
                }
                Log.i(TAG, "foto per Jarvis: " + c.getResponseCode() + ", " + corpo.length + " byte");
                c.disconnect();
            } catch (IOException e) {
                Log.w(TAG, "foto non mandata: " + e);
            }
        }, "foto").start();
    }

    @SuppressWarnings("deprecation")
    private void svegliaSchermo() {
        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        if (pm.isInteractive()) return;
        pm.newWakeLock(PowerManager.SCREEN_BRIGHT_WAKE_LOCK | PowerManager.ACQUIRE_CAUSES_WAKEUP
                | PowerManager.ON_AFTER_RELEASE, "pannello:jarvis-schermo").acquire(5000);
        startActivity(new Intent(this, Pannello.class).putExtra("jarvis", true)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT));
    }

    // ------------------------------------------------------------------ voce di Jarvis
    private void suona(byte[] pcm, int n) {
        if (traccia == null) {
            int min = AudioTrack.getMinBufferSize(FREQ_OUT, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT);
            traccia = new AudioTrack.Builder()
                    .setAudioAttributes(new AudioAttributes.Builder()
                            .setUsage(eco ? AudioAttributes.USAGE_VOICE_COMMUNICATION : AudioAttributes.USAGE_ASSISTANT)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                    .setAudioFormat(new AudioFormat.Builder().setSampleRate(FREQ_OUT)
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
                    .setBufferSizeInBytes(Math.max(min * 2, FREQ_OUT))   // ~0,5 s
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .build();
            traccia.play();
        }
        traccia.write(pcm, 0, n);
    }

    private void taci() {
        if (traccia == null) return;
        traccia.pause();
        traccia.flush();
        traccia.play();
    }

    private void fermaTraccia() {
        if (traccia == null) return;
        try { traccia.stop(); traccia.release(); } catch (Exception e) { /* già fermata */ }
        traccia = null;
    }

    // ------------------------------------------------------------------ microfono
    private synchronized void avviaMicrofono() {
        if (microfono != null && microfono.isAlive()) return;
        if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            Log.w(TAG, "permesso del microfono non concesso");
            return;
        }
        microfono = new Thread(new Runnable() { @Override public void run() { cicloMicrofono(); } }, "jarvis-microfono");
        microfono.start();
    }

    private void cicloMicrofono() {
        while (vivo && microfonoVoluto && base != null) {
            try {
                inviaMicrofono(base, token);
            } catch (Exception e) {
                Log.i(TAG, "microfono interrotto: " + e);
            }
            try { Thread.sleep(1000); } catch (InterruptedException e) { return; }
        }
    }

    private void inviaMicrofono(String indirizzo, String t) throws IOException {
        int min = AudioRecord.getMinBufferSize(FREQ_IN, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        final boolean conEco = eco;
        AudioManager am = (AudioManager) getSystemService(AUDIO_SERVICE);
        if (conEco) {
            // modalità "chiamata": Android sottrae dal microfono quello che esce dalle casse
            am.setMode(AudioManager.MODE_IN_COMMUNICATION);
            am.setSpeakerphoneOn(true);
        }
        AudioRecord rec = new AudioRecord(conEco ? MediaRecorder.AudioSource.VOICE_COMMUNICATION
                : MediaRecorder.AudioSource.VOICE_RECOGNITION, FREQ_IN,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, Math.max(min, 2560 * 4));
        AcousticEchoCanceler aec = null;
        if (conEco && AcousticEchoCanceler.isAvailable()) {
            aec = AcousticEchoCanceler.create(rec.getAudioSessionId());
            if (aec != null) aec.setEnabled(true);
        }
        boolean aecAttivo = aec != null && aec.getEnabled();
        Log.i(TAG, "microfono: eco " + (conEco ? "on" : "off") + ", AEC " + (aecAttivo ? "attivo" : "non disponibile"));
        HttpURLConnection c = null;
        try {
            rec.startRecording();
            c = (HttpURLConnection) new URL(indirizzo + "/jarvis/microfono?t=" + Uri.encode(t)
                    + "&aec=" + (aecAttivo ? 1 : 0) + "&eco=" + (conEco ? 1 : 0)).openConnection();
            c.setConnectTimeout(3000);
            c.setDoOutput(true);
            c.setRequestMethod("POST");
            c.setChunkedStreamingMode(0);
            c.setRequestProperty("Content-Type", "application/octet-stream");
            OutputStream out = c.getOutputStream();
            byte[] buf = new byte[2560];   // 80 ms
            Log.i(TAG, "microfono collegato a Jarvis");
            while (vivo && microfonoVoluto && indirizzo.equals(base) && conEco == eco) {
                int n = rec.read(buf, 0, buf.length);
                if (n < 0) throw new IOException("AudioRecord: " + n);
                if (n > 0) { out.write(buf, 0, n); out.flush(); }
            }
            out.close();
            InputStream in = c.getInputStream();
            while (in.read() >= 0) { /* risposta finale del PC */ }
        } finally {
            try { rec.stop(); } catch (Exception e) { /* non partito */ }
            if (aec != null) aec.release();
            rec.release();
            if (conEco) am.setMode(AudioManager.MODE_NORMAL);
            if (c != null) c.disconnect();
            Log.i(TAG, "microfono fermato");
        }
    }
}
