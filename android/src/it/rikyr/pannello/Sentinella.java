package it.rikyr.pannello;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.admin.DevicePolicyManager;
import android.content.BroadcastReceiver;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.graphics.ImageFormat;
import android.graphics.Rect;
import android.graphics.YuvImage;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.media.Image;
import android.media.ImageReader;
import android.net.Uri;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.PowerManager;
import android.os.SystemClock;
import android.util.Log;
import android.util.Range;
import android.util.Size;
import android.view.Surface;
import android.view.WindowManager;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.Random;
import java.util.concurrent.Callable;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * Modalità sentinella: il tablet fa da telecamera di sicurezza (richiesta dell'utente, 2026-11-25).
 *
 * - Movimento: telecamera DAVANTI (come per Jarvis "guarda"), fotogrammi YUV ~640x480 ridotti a una griglia 64x48 di
 *   medie; ognuno si confronta con uno sfondo che si aggiorna piano. Prima del confronto si toglie il cambio di
 *   luce generale (guadagno: esposizione automatica, schermo che si attenua, nuvola), così scatta solo se cambia una
 *   parte dell'immagine. Serve la soglia superata in due analisi di fila (~0,4 s).
 * - Rumore: livello RMS del microfono (blocchi da 80 ms) sopra il rumore di fondo di un margine (sensibilità) per
 *   almeno 240 ms. Il microfono è lo stesso di Jarvis: se Jarvis lo sta usando si leggono i suoi blocchi, se no
 *   JarvisServizio lo apre solo per la sentinella (in locale, niente va al PC finché non scatta). Mentre il tablet
 *   suona (voce di Jarvis) il rumore non conta. Con il visore addosso il microfono del tablet resta SPENTO, come per
 *   Jarvis: la sentinella guarda solo con la telecamera (lo dice nell'indicatore e al PC).
 * - Quando scatta: una foto subito (mandata al PC per prima: l'avviso parte da lì), poi i fotogrammi da 2 s prima a
 *   4 s dopo (2 al secondo) e l'audio da 3 s prima a 5 s dopo. Tutto va SOLO al PC (/api/sentinella/file); se il PC
 *   non risponde resta in coda nel tablet (files/sentinella-coda, al massimo ~60 MB) e parte appena torna.
 * - Schermo: niente scatti da soli. Per 20 s (armamento) dopo l'accensione non scatta; ogni cambio di schermo
 *   (acceso, spento, luce) azzera lo sfondo e mette in pausa il movimento per 3 s.
 * Stato, impostazioni e comandi passano dal flusso del pannello (Simbiosi) e da POST /api/sentinella/stato.
 */
final class Sentinella {
    interface Ascoltatore { void cambiata(); }

    private static final String TAG = "PannelloSentinella";
    private static Sentinella unica;

    static synchronized Sentinella di(Context c) {
        if (unica == null) unica = new Sentinella(c.getApplicationContext());
        return unica;
    }

    private final Context app;
    private final SharedPreferences prefs;
    private final CopyOnWriteArrayList<Ascoltatore> ascoltatori = new CopyOnWriteArrayList<>();
    private final Object blocco = new Object();

    // stato
    private volatile boolean attiva = false;
    private volatile long armataDa = 0;               // elapsedRealtime: da quando sorveglia
    private volatile boolean visore = false;          // l'utente ha il visore addosso: microfono del tablet spento
    private volatile long calmaFino = 0;              // schermo appena cambiato: il movimento non conta
    private volatile long silenzioFino = 0;           // il tablet sta suonando: il rumore non conta
    private volatile long ultimoAudio = 0, ultimoFotogramma = 0;
    private volatile String erroreTelecamera = "";
    private volatile int eventiDaAccensione = 0;
    private volatile long ultimoEvento = 0;
    private volatile String ultimoComando = "";
    private volatile String impostazioniPc = "";

    // impostazioni (dal PC, config.json "sentinella"; valori predefiniti uguali a server/sentinella.py)
    private volatile boolean movimentoOn = true, rumoreOn = true, voceOn = true;
    private volatile int sensMovimento = 6, sensRumore = 5, armamento = 20, pausa = 20;
    private volatile String schermo = "attenuato";

    // telecamera
    private final HandlerThread filo = new HandlerThread("sentinella-telecamera");
    private final Handler h;
    private CameraDevice telecamera;
    private CameraCaptureSession sessione;
    private ImageReader lettore;
    private int orientamentoSensore = 0;
    private boolean davanti = true;
    private int prestata = 0;                       // Jarvis sta usando la telecamera per una foto
    private byte[] luma, nv21;
    private float[] griglia, sfondo;
    private final int[] istogramma = new int[256];
    private int sopraDiFila = 0, luceDiFila = 0;
    private volatile int fotogrammi = 0;            // per il registro: fotogrammi analizzati negli ultimi 30 s
    private volatile float luce = -1;               // luce media dell'ultimo fotogramma (0-255), per il registro
    private long ultimaAnalisi = 0, ultimoJpeg = 0;
    private float movimentoMax = 0;                 // per il registro e il PC: il massimo degli ultimi 30 s
    private final ArrayList<long[]> tempiAnello = new ArrayList<>();
    private final ArrayList<byte[]> anello = new ArrayList<>();   // JPEG degli ultimi ~2 s
    private static final int GW = 64, GH = 48;

    // audio
    private final byte[] anelloAudio = new byte[16000 * 2 * 3];  // ultimi 3 s di PCM 16 kHz
    private int posAudio = 0;
    private boolean anelloPieno = false;
    private float fondo = Float.NaN;
    private int bloccoSopra = 0;
    private float rumoreMax = -120;

    // evento in corso
    private Evento evento;

    private final Thread invio;

    private static final class Evento {
        String id, tipo;
        double valore;
        long oraMs, inizio;
        byte[] foto;
        boolean fotoChiusa;                        // foto arrivata o rinunciata (telecamera ferma)
        final ArrayList<byte[]> clip = new ArrayList<>();
        final ByteArrayOutputStream audio = new ByteArrayOutputStream();
        boolean audioChiuso;
        long ultimoClip;
        File cartella;
    }

    private Sentinella(Context c) {
        app = c;
        prefs = c.getSharedPreferences("sentinella", Context.MODE_PRIVATE);
        filo.start();
        h = new Handler(filo.getLooper());
        IntentFilter f = new IntentFilter(Intent.ACTION_SCREEN_ON);
        f.addAction(Intent.ACTION_SCREEN_OFF);
        f.addAction(Intent.ACTION_USER_PRESENT);
        c.registerReceiver(new BroadcastReceiver() {
            @Override public void onReceive(Context x, Intent i) { calma(3000); }
        }, f);
        NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
        nm.createNotificationChannel(new NotificationChannel("sentinella", "Sentinella", NotificationManager.IMPORTANCE_LOW));
        invio = new Thread(new Runnable() { @Override public void run() { cicloInvio(); } }, "sentinella-invio");
        invio.setDaemon(true);
        invio.start();
    }

    // ------------------------------------------------------------------ stato e comandi
    boolean attiva() { return attiva; }
    boolean armata() { return attiva && SystemClock.elapsedRealtime() >= armataDa; }
    int secondiAllArmamento() { return (int) Math.max(0, (armataDa - SystemClock.elapsedRealtime() + 999) / 1000); }
    boolean schermoSpento() { return "spento".equals(schermo); }
    boolean visore() { return visore; }
    int eventi() { return eventiDaAccensione; }
    String erroreTelecamera() { return erroreTelecamera; }

    /** Microfono in uso adesso per la sentinella (blocchi arrivati negli ultimi 2 s). */
    boolean sente() { return attiva && SystemClock.elapsedRealtime() - ultimoAudio < 2000; }

    /** Perché non sente, per l'indicatore e il PC ("" se sente). */
    String percheNonSente() {
        if (!rumoreOn) return "rumore spento nelle impostazioni";
        if (visore) return "visore addosso";
        if (!sente()) return "microfono non disponibile";
        return "";
    }

    /** La sentinella vuole il microfono (JarvisServizio lo apre anche se Jarvis non lo usa). */
    boolean vuoleMicrofono() { return attiva && rumoreOn && !visore; }

    void ascolta(Ascoltatore a) { ascoltatori.add(a); }
    void smetti(Ascoltatore a) { ascoltatori.remove(a); }

    private void avvisaAscoltatori() {
        for (Ascoltatore a : ascoltatori) {
            try { a.cambiata(); } catch (Exception e) { Log.w(TAG, "ascoltatore: " + e); }
        }
        invia();
    }

    /** All'avvio del processo: se era attiva (il tablet si è riavviato da solo), riparte. */
    void riprendi() {
        if (prefs.getBoolean("attiva", false)) attiva(true, "riavvio");
    }

    void attiva(boolean si, String da) {
        synchronized (blocco) {
            if (si == attiva) { if (si) avvisaAscoltatori(); return; }
            attiva = si;
            prefs.edit().putBoolean("attiva", si).apply();
            Log.i(TAG, (si ? "attivata" : "spenta") + " (" + da + ")");
            if (si) {
                armataDa = SystemClock.elapsedRealtime() + armamento * 1000L;
                eventiDaAccensione = 0;
                fondo = Float.NaN;
                h.post(new Runnable() { @Override public void run() { apri(); } });
                // lo schermo spento all'armamento, se è stato scelto "spento" (in modalità attenuata resta acceso)
                h.postDelayed(spegniSeVoluto, armamento * 1000L + 500);
            } else {
                h.removeCallbacks(spegniSeVoluto);
                h.post(new Runnable() { @Override public void run() { chiudi(); } });
                synchronized (this) { if (evento != null) chiudiEvento(true); }
            }
        }
        notifica();
        JarvisServizio.svegliaMicrofono();
        if (si && !"riavvio".equals(da)) mostraPannello();
        avvisaAscoltatori();
    }

    private final Runnable spegniSeVoluto = new Runnable() {
        @Override public void run() {
            if (!attiva || !schermoSpento()) return;
            DevicePolicyManager dpm = (DevicePolicyManager) app.getSystemService(Context.DEVICE_POLICY_SERVICE);
            if (dpm.isAdminActive(new ComponentName(app, Amministratore.class))) {
                Log.i(TAG, "schermo spento per la sorveglianza");
                dpm.lockNow();
            }
        }
    };

    /** Accesa da lontano (PC, Jarvis): il pannello davanti, così l'indicatore si vede. */
    @SuppressWarnings("deprecation")
    private void mostraPannello() {
        try {
            PowerManager pm = (PowerManager) app.getSystemService(Context.POWER_SERVICE);
            if (!pm.isInteractive() && !schermoSpento()) {
                pm.newWakeLock(PowerManager.SCREEN_DIM_WAKE_LOCK | PowerManager.ACQUIRE_CAUSES_WAKEUP,
                        "pannello:sentinella").acquire(3000);
            }
            app.startActivity(new Intent(app, Pannello.class)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT));
        } catch (Exception e) {
            Log.w(TAG, "pannello non portato davanti: " + e);
        }
    }

    /** Dal flusso del pannello (Simbiosi), ogni secondo: impostazioni e comandi dal PC. */
    void dalPc(JSONObject s) {
        JSONObject imp = s.optJSONObject("imp");
        if (imp != null && !imp.toString().equals(impostazioniPc)) {
            impostazioniPc = imp.toString();
            boolean rumorePrima = rumoreOn;
            movimentoOn = imp.optBoolean("movimento", true);
            rumoreOn = imp.optBoolean("rumore", true);
            voceOn = imp.optBoolean("voce", true);
            sensMovimento = limita(imp.optInt("sens_movimento", 6), 1, 10);
            sensRumore = limita(imp.optInt("sens_rumore", 5), 1, 10);
            armamento = limita(imp.optInt("armamento", 20), 0, 300);
            pausa = limita(imp.optInt("pausa", 20), 5, 600);
            schermo = imp.optString("schermo", "attenuato");
            Log.i(TAG, "impostazioni dal PC: " + impostazioniPc);
            if (rumorePrima != rumoreOn) JarvisServizio.svegliaMicrofono();
            calma(1500);
            avvisaAscoltatori();
        }
        JSONObject c = s.optJSONObject("comando");
        if (c != null) {
            String id = c.optString("id", "");
            if (!id.isEmpty() && !id.equals(ultimoComando)) {
                ultimoComando = id;
                attiva(c.optBoolean("attiva", false), "PC");
            }
        }
    }

    private static int limita(int v, int lo, int hi) { return Math.max(lo, Math.min(hi, v)); }

    /** Jarvis dice se l'utente ha il visore addosso (il microfono del tablet segue il visore). */
    void visore(boolean si) {
        if (si == visore) return;
        visore = si;
        Log.i(TAG, si ? "visore addosso: la sentinella non usa il microfono" : "visore tolto");
        JarvisServizio.svegliaMicrofono();
        avvisaAscoltatori();
        notifica();
    }

    /** Schermo acceso, spento o con un'altra luce: lo sfondo si rifà e il movimento aspetta. */
    void calma(long ms) {
        calmaFino = Math.max(calmaFino, SystemClock.elapsedRealtime() + ms);
        h.post(new Runnable() { @Override public void run() { sfondo = null; sopraDiFila = 0; } });
    }

    /** Il tablet sta suonando (voce di Jarvis): il rumore non conta fino a 1,5 s dopo la fine. */
    void suonoInUscita(int byte24k) {
        long durata = byte24k * 1000L / (24000 * 2);
        long ora = SystemClock.elapsedRealtime();
        fineSuono = Math.max(fineSuono, ora) + durata;   // l'audio in coda suona uno dopo l'altro
        silenzioFino = fineSuono + 1500;
    }
    private volatile long fineSuono = 0;

    // ------------------------------------------------------------------ indicatore (notifica)
    private void notifica() {
        NotificationManager nm = (NotificationManager) app.getSystemService(Context.NOTIFICATION_SERVICE);
        if (!attiva) { nm.cancel(48); return; }
        PendingIntent apri = PendingIntent.getActivity(app, 0, new Intent(app, Pannello.class), 0);
        nm.notify(48, new Notification.Builder(app, "sentinella")
                .setContentTitle("Sentinella attiva")
                .setContentText(visore || !rumoreOn ? "Telecamera davanti (microfono spento). I dati restano sul PC."
                        : "Telecamera davanti e microfono. I dati restano sul PC.")
                .setSmallIcon(android.R.drawable.ic_menu_camera)
                .setOngoing(true)
                .setContentIntent(apri)
                .build());
    }

    // ------------------------------------------------------------------ telecamera
    private boolean aprendo = false;
    private final Runnable riapri = new Runnable() { @Override public void run() { apri(); } };

    private void apri() {
        if (!attiva || telecamera != null || prestata > 0 || aprendo) return;
        if (app.checkSelfPermission(android.Manifest.permission.CAMERA) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            erroreTelecamera = "permesso della telecamera non concesso";
            Log.w(TAG, erroreTelecamera);
            avvisaAscoltatori();
            return;
        }
        try {
            final CameraManager cm = (CameraManager) app.getSystemService(Context.CAMERA_SERVICE);
            String scelta = null;
            for (String id : cm.getCameraIdList()) {
                Integer verso = cm.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING);
                if (scelta == null) scelta = id;
                if (verso != null && verso == CameraCharacteristics.LENS_FACING_FRONT) { scelta = id; break; }
            }
            if (scelta == null) throw new IllegalStateException("nessuna telecamera");
            CameraCharacteristics car = cm.getCameraCharacteristics(scelta);
            Integer o = car.get(CameraCharacteristics.SENSOR_ORIENTATION);
            orientamentoSensore = o == null ? 0 : o;
            Integer verso = car.get(CameraCharacteristics.LENS_FACING);
            davanti = verso != null && verso == CameraCharacteristics.LENS_FACING_FRONT;
            Size misura = misura(car.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
                    .getOutputSizes(ImageFormat.YUV_420_888));
            final Range<Integer> fps = fps(car.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES));
            lettore = ImageReader.newInstance(misura.getWidth(), misura.getHeight(), ImageFormat.YUV_420_888, 3);
            lettore.setOnImageAvailableListener(new ImageReader.OnImageAvailableListener() {
                @Override public void onImageAvailable(ImageReader r) {
                    Image im = null;
                    try {
                        im = r.acquireLatestImage();
                        if (im != null) fotogramma(im);
                    } catch (Exception e) {
                        Log.w(TAG, "fotogramma: " + e);
                    } finally {
                        if (im != null) im.close();
                    }
                }
            }, h);
            aprendo = true;
            Log.i(TAG, "apro la telecamera " + scelta + (davanti ? " (davanti)" : "") + " " + misura + " fps " + fps);
            cm.openCamera(scelta, new CameraDevice.StateCallback() {
                @Override public void onOpened(CameraDevice d) {
                    aprendo = false;
                    if (!attiva || prestata > 0) { d.close(); return; }
                    telecamera = d;
                    try {
                        d.createCaptureSession(Collections.singletonList(lettore.getSurface()),
                                new CameraCaptureSession.StateCallback() {
                                    @Override public void onConfigured(CameraCaptureSession s) {
                                        sessione = s;
                                        try {
                                            CaptureRequest.Builder b = d.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW);
                                            b.addTarget(lettore.getSurface());
                                            b.set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO);
                                            if (fps != null) b.set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, fps);
                                            s.setRepeatingRequest(b.build(), null, h);
                                            erroreTelecamera = "";
                                            calma(2500);
                                            avvisaAscoltatori();
                                        } catch (Exception e) {
                                            guasto("anteprima: " + e);
                                        }
                                    }
                                    @Override public void onConfigureFailed(CameraCaptureSession s) { guasto("sessione non configurata"); }
                                }, h);
                    } catch (Exception e) {
                        guasto("sessione: " + e);
                    }
                }
                @Override public void onDisconnected(CameraDevice d) { aprendo = false; d.close(); if (telecamera == d) telecamera = null; guasto("telecamera scollegata"); }
                @Override public void onError(CameraDevice d, int e) { aprendo = false; d.close(); if (telecamera == d) telecamera = null; guasto("telecamera occupata o guasta (" + e + ")"); }
            }, h);
        } catch (Exception e) {
            aprendo = false;
            guasto(e.toString());
        }
    }

    /** Telecamera persa: si chiude tutto e si riprova fra 10 s. */
    private void guasto(String perche) {
        erroreTelecamera = perche;
        Log.w(TAG, "telecamera: " + perche);
        chiudi();
        avvisaAscoltatori();
        h.removeCallbacks(riapri);
        if (attiva) h.postDelayed(riapri, 10_000);
    }

    private void chiudi() {
        try { if (sessione != null) sessione.close(); } catch (Exception e) { /* già chiusa */ }
        sessione = null;
        if (telecamera != null) { try { telecamera.close(); } catch (Exception e) { /* già chiusa */ } }
        telecamera = null;
        if (lettore != null) lettore.close();
        lettore = null;
        sfondo = null;
        synchronized (this) { anello.clear(); tempiAnello.clear(); }
    }

    /** Jarvis vuole una foto (Fotocamera.scatta) mentre la sentinella usa la telecamera: gliela si presta. */
    byte[] prestaTelecamera(Callable<byte[]> scatto) throws Exception {
        final CountDownLatch chiusa = new CountDownLatch(1);
        h.post(new Runnable() { @Override public void run() { prestata++; chiudi(); chiusa.countDown(); } });
        chiusa.await(3, TimeUnit.SECONDS);
        try {
            Thread.sleep(300);
            return scatto.call();
        } finally {
            h.post(new Runnable() { @Override public void run() { prestata--; apri(); } });
        }
    }

    private static Size misura(Size[] misure) {
        Size migliore = misure[0];
        int meglio = Integer.MAX_VALUE;
        for (Size s : misure) {
            int d = Math.abs(s.getWidth() - 640) + Math.abs(s.getHeight() - 480) + (s.getWidth() < 320 ? 5000 : 0);
            if (d < meglio) { meglio = d; migliore = s; }
        }
        return migliore;
    }

    /** Fotogrammi lenti (esposizione più lunga al buio, meno lavoro): il campo con il massimo più basso da 10 in su. */
    private static Range<Integer> fps(Range<Integer>[] campi) {
        if (campi == null) return null;
        Range<Integer> scelto = null;
        for (Range<Integer> r : campi) {
            if (r.getUpper() < 10) continue;
            if (scelto == null || r.getUpper() < scelto.getUpper()
                    || (r.getUpper().equals(scelto.getUpper()) && r.getLower() < scelto.getLower())) scelto = r;
        }
        return scelto;
    }

    // ------------------------------------------------------------------ analisi dei fotogrammi (thread telecamera)
    private void fotogramma(Image im) {
        long ora = SystemClock.elapsedRealtime();
        ultimoFotogramma = ora;
        boolean analisi = ora - ultimaAnalisi >= 200;
        Evento ev;
        synchronized (this) { ev = evento; }
        boolean serveJpeg = ora - ultimoJpeg >= 500 || (ev != null && !ev.fotoChiusa);
        if (!analisi && !serveJpeg) return;
        int w = im.getWidth(), hh = im.getHeight();
        if (luma == null || luma.length != w * hh) {
            luma = new byte[w * hh];
            nv21 = new byte[w * hh * 3 / 2];
        }
        copiaLuma(im, luma, w, hh);
        if (analisi) {
            ultimaAnalisi = ora;
            analizza(luma, w, hh, ora);
        }
        if (serveJpeg) {
            ultimoJpeg = ora;
            byte[] jpeg = jpeg(im, w, hh);
            if (jpeg == null) return;
            synchronized (this) {
                anello.add(jpeg);
                tempiAnello.add(new long[] { ora });
                while (anello.size() > 4) { anello.remove(0); tempiAnello.remove(0); }
                if (evento != null) {
                    if (!evento.fotoChiusa) {
                        evento.foto = jpeg;
                        evento.fotoChiusa = true;
                        evento.ultimoClip = ora;
                        mandaFoto(evento);
                    } else if (ora - evento.ultimoClip >= 450 && ora - evento.inizio <= 4500 && evento.clip.size() < 20) {
                        evento.clip.add(jpeg);
                        evento.ultimoClip = ora;
                    }
                    if (ora - evento.inizio > 4500 && evento.audioChiuso) chiudiEvento(false);
                }
            }
        }
    }

    private static void copiaLuma(Image im, byte[] out, int w, int hh) {
        Image.Plane p = im.getPlanes()[0];
        ByteBuffer y = p.getBuffer();
        int passo = p.getRowStride();
        if (passo == w) {
            y.position(0);
            y.get(out, 0, w * hh);
            return;
        }
        for (int r = 0; r < hh; r++) {
            y.position(r * passo);
            y.get(out, r * w, w);
        }
    }

    private void analizza(byte[] y, int w, int hh, long ora) {
        int n = GW * GH;
        if (griglia == null) griglia = new float[n];
        int cw = w / GW, ch = hh / GH;
        float somma = 0;
        for (int gy = 0; gy < GH; gy++) {
            for (int gx = 0; gx < GW; gx++) {
                int s = 0, c = 0;
                for (int yy = gy * ch; yy < gy * ch + ch; yy += 2) {
                    int base = yy * w + gx * cw;
                    for (int xx = 0; xx < cw; xx += 2) { s += y[base + xx] & 0xff; c++; }
                }
                float v = c > 0 ? (float) s / c : 0;
                griglia[gy * GW + gx] = v;
                somma += v;
            }
        }
        float media = somma / n;
        luce = media;
        fotogrammi++;
        if (sfondo == null || ora < calmaFino) {
            if (sfondo == null) sfondo = new float[n];
            System.arraycopy(griglia, 0, sfondo, 0, n);
            sopraDiFila = 0;
            return;
        }
        float mediaSfondo = 0;
        for (float v : sfondo) mediaSfondo += v;
        mediaSfondo /= n;
        // Cambio di luce generale (esposizione automatica, nuvola): si compensa con un guadagno. Al buio (media
        // bassissima) il rapporto sarebbe solo rumore: niente compensazione, decide la soglia per cella.
        float guadagno = 1f;
        if (Math.max(media, mediaSfondo) >= 12) {
            guadagno = mediaSfondo / Math.max(1f, media);
            if (guadagno < 0.5f || guadagno > 2f) {
                // la luce è cambiata del tutto (una lampada accesa o spenta): se resta così è un evento, poi si
                // riparte con lo sfondo nuovo. Lo schermo del tablet non c'entra: i suoi cambi passano da calma().
                if (++luceDiFila >= 2) {
                    luceDiFila = 0;
                    System.arraycopy(griglia, 0, sfondo, 0, n);
                    calmaFino = ora + 1500;
                    sopraDiFila = 0;
                    movimentoMax = 1f;
                    if (movimentoOn && armata()) scatta("movimento", 1.0);
                }
                return;
            }
        }
        luceDiFila = 0;
        Arrays.fill(istogramma, 0);
        float[] diff = new float[n];
        for (int i = 0; i < n; i++) {
            float d = Math.abs(griglia[i] * guadagno - sfondo[i]);
            diff[i] = d;
            istogramma[Math.min(255, (int) d)]++;
        }
        int meta = n / 2, cum = 0, mediana = 0;
        for (int i = 0; i < 256; i++) { cum += istogramma[i]; if (cum >= meta) { mediana = i; break; } }
        // soglia per cella: sopra il rumore del sensore (al buio è alto) e più bassa con la sensibilità alta
        float sogliaCella = Math.max(24 - sensMovimento, 3.5f * mediana + 4);
        int sopra = 0;
        for (float d : diff) if (d > sogliaCella) sopra++;
        float frazione = (float) sopra / n;
        float sogliaFrazione = (float) (0.30 * Math.pow(0.62, sensMovimento - 1));
        if (frazione > movimentoMax) movimentoMax = frazione;
        boolean mosso = frazione > sogliaFrazione;
        sopraDiFila = mosso ? sopraDiFila + 1 : 0;
        // lo sfondo segue piano i cambiamenti lenti (luce del giorno); fermo mentre qualcosa si muove
        float a = mosso ? 0.01f : 0.08f;
        for (int i = 0; i < n; i++) sfondo[i] = sfondo[i] * (1 - a) + griglia[i] * a;
        if (sopraDiFila >= 2 && movimentoOn && armata()) {
            sopraDiFila = 0;
            scatta("movimento", Math.round(frazione * 1000) / 1000.0);
        }
    }

    /** YUV_420_888 -> NV21 -> JPEG ~640x480, con l'orientamento nell'EXIF (la pagina e il PC lo raddrizzano). */
    private byte[] jpeg(Image im, int w, int hh) {
        try {
            System.arraycopy(luma, 0, nv21, 0, w * hh);
            Image.Plane[] p = im.getPlanes();
            ByteBuffer u = p[1].getBuffer(), v = p[2].getBuffer();
            int passo = p[1].getRowStride(), pixel = p[1].getPixelStride();
            int pos = w * hh;
            for (int r = 0; r < hh / 2; r++) {
                for (int c = 0; c < w / 2; c++) {
                    int i = r * passo + c * pixel;
                    nv21[pos++] = v.get(i);
                    nv21[pos++] = u.get(i);
                }
            }
            ByteArrayOutputStream out = new ByteArrayOutputStream(64 * 1024);
            new YuvImage(nv21, ImageFormat.NV21, w, hh, null).compressToJpeg(new Rect(0, 0, w, hh), 75, out);
            return conOrientamento(out.toByteArray(), rotazione());
        } catch (Exception e) {
            Log.w(TAG, "jpeg: " + e);
            return null;
        }
    }

    /** Di quanto va girata l'immagine (in senso orario) per vederla dritta con il tablet com'è adesso. */
    private int rotazione() {
        int gradi = 0;
        try {
            int r = ((WindowManager) app.getSystemService(Context.WINDOW_SERVICE)).getDefaultDisplay().getRotation();
            gradi = r == Surface.ROTATION_90 ? 90 : r == Surface.ROTATION_180 ? 180 : r == Surface.ROTATION_270 ? 270 : 0;
        } catch (Exception e) { /* senza finestra: 0 */ }
        return davanti ? (orientamentoSensore + gradi) % 360 : (orientamentoSensore - gradi + 360) % 360;
    }

    /** Aggiunge un segmento EXIF con il solo tag Orientation subito dopo l'inizio del JPEG. */
    static byte[] conOrientamento(byte[] jpeg, int gradi) {
        int o = gradi == 90 ? 6 : gradi == 180 ? 3 : gradi == 270 ? 8 : 1;
        byte[] exif = {
                (byte) 0xFF, (byte) 0xE1, 0, 34,                       // APP1, lunghezza 34
                'E', 'x', 'i', 'f', 0, 0,
                'M', 'M', 0, 42, 0, 0, 0, 8,                            // TIFF big-endian, IFD a 8
                0, 1,                                                   // una voce
                0x01, 0x12, 0, 3, 0, 0, 0, 1, 0, (byte) o, 0, 0,        // Orientation, SHORT, 1, valore
                0, 0, 0, 0 };                                           // nessun IFD dopo
        byte[] r = new byte[jpeg.length + exif.length];
        r[0] = jpeg[0];
        r[1] = jpeg[1];
        System.arraycopy(exif, 0, r, 2, exif.length);
        System.arraycopy(jpeg, 2, r, 2 + exif.length, jpeg.length - 2);
        return r;
    }

    // ------------------------------------------------------------------ audio (thread del microfono)
    /** Un blocco PCM 16 kHz dal microfono (quello di Jarvis o quello aperto solo per la sentinella). */
    void audio(byte[] b, int n) {
        if (!attiva || n < 2) return;
        long ora = SystemClock.elapsedRealtime();
        ultimoAudio = ora;
        double s = 0;
        for (int i = 0; i + 1 < n; i += 2) {
            int v = (short) ((b[i] & 0xff) | (b[i + 1] << 8));
            s += (double) v * v;
        }
        double rms = Math.sqrt(s / (n / 2));
        float db = (float) (20 * Math.log10(rms / 32768.0 + 1e-9));
        boolean scattato = false;
        synchronized (this) {
            int k = Math.min(n, anelloAudio.length);
            for (int i = 0; i < k; i++) {
                anelloAudio[posAudio] = b[i];
                posAudio = (posAudio + 1) % anelloAudio.length;
            }
            if (posAudio < k) anelloPieno = true;
            if (evento != null && !evento.audioChiuso) {
                evento.audio.write(b, 0, n);
                if (evento.audio.size() >= 16000 * 2 * 8) evento.audioChiuso = true;   // 3 s prima + 5 s dopo
                if (evento.audioChiuso && evento.fotoChiusa && ora - evento.inizio > 4500) chiudiEvento(false);
            }
        }
        if (ora < silenzioFino) { bloccoSopra = 0; return; }   // sta suonando Jarvis: né soglia né fondo
        if (db > rumoreMax) rumoreMax = db;
        if (Float.isNaN(fondo)) fondo = db;
        else if (db < fondo) fondo = fondo * 0.8f + db * 0.2f;
        else fondo = Math.min(db, fondo + 0.03f);          // sale piano (~0,4 dB/s): un rumore costante non scatta per sempre
        float margine = 30f - 2.2f * (sensRumore - 1);
        float soglia = Math.max(fondo + margine, -55f);
        bloccoSopra = db > soglia ? bloccoSopra + 1 : 0;
        if (bloccoSopra >= 3 && rumoreOn && armata() && !visore) {
            bloccoSopra = 0;
            scattato = true;
        }
        if (scattato) scatta("rumore", Math.round(db * 10) / 10.0);
    }

    // ------------------------------------------------------------------ eventi
    private void scatta(String tipo, double valore) {
        long ora = SystemClock.elapsedRealtime();
        synchronized (this) {
            if (!armata() || evento != null || (ultimoEvento != 0 && ora - ultimoEvento < pausa * 1000L)) return;
            ultimoEvento = ora;
            Evento ev = new Evento();
            ev.tipo = tipo;
            ev.valore = valore;
            ev.oraMs = System.currentTimeMillis();
            ev.inizio = ora;
            ev.id = new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.ROOT).format(new Date(ev.oraMs)) + "-" + tipo + "-"
                    + String.format(Locale.ROOT, "%04x", new Random().nextInt(0x10000));
            // i fotogrammi già presi (~2 s prima) aprono la clip; l'audio parte da 3 s prima
            ev.clip.addAll(anello);
            if (anelloPieno) ev.audio.write(anelloAudio, posAudio, anelloAudio.length - posAudio);
            ev.audio.write(anelloAudio, 0, posAudio);
            if (!sente()) ev.audioChiuso = true;
            if (telecamera == null || ora - ultimoFotogramma > 2000) ev.fotoChiusa = true;   // telecamera ferma
            evento = ev;
            eventiDaAccensione++;
            Log.i(TAG, "scattata: " + tipo + " " + valore + " -> " + ev.id);
            if (ev.fotoChiusa) mandaFoto(ev);
        }
        // la foto la prende il prossimo fotogramma; se non arriva entro 1,5 s l'evento parte senza
        h.postDelayed(new Runnable() {
            @Override public void run() {
                synchronized (Sentinella.this) {
                    if (evento != null && !evento.fotoChiusa) { evento.fotoChiusa = true; mandaFoto(evento); }
                }
            }
        }, 1500);
        // a 6 s dall'inizio si chiude comunque (microfono fermo a metà, telecamera persa)
        h.postDelayed(new Runnable() {
            @Override public void run() { synchronized (Sentinella.this) { if (evento != null) chiudiEvento(true); } }
        }, 6500);
        avvisaAscoltatori();
    }

    /** Foto (o niente) e dati dell'evento nella coda: il PC lo riceve per primo e avvisa. Sotto blocco. */
    private void mandaFoto(Evento ev) {
        try {
            File c = new File(coda(), ev.id);
            c.mkdirs();
            ev.cartella = c;
            if (ev.foto != null) scrivi(new File(c, "foto.jpg"), ev.foto);
            JSONObject m = new JSONObject();
            m.put("tipo", ev.tipo);
            m.put("valore", ev.valore);
            m.put("ora", ev.oraMs);
            scrivi(new File(c, "evento.json"), m.toString().getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) {
            Log.w(TAG, "evento non salvato: " + e);
        }
        invia();
    }

    /** Fotogrammi e audio dell'evento nella coda, poi l'evento è finito. Sotto blocco. */
    private void chiudiEvento(boolean comunque) {
        Evento ev = evento;
        if (ev == null) return;
        evento = null;
        try {
            if (ev.cartella == null) { ev.fotoChiusa = true; mandaFoto(ev); }
            int i = 1;
            for (byte[] f : ev.clip) scrivi(new File(ev.cartella, String.format(Locale.ROOT, "clip-%02d.jpg", i++)), f);
            if (ev.audio.size() > 16000) scrivi(new File(ev.cartella, "audio.wav"), wav(ev.audio.toByteArray()));
            scrivi(new File(ev.cartella, "completo"), new byte[0]);
            Log.i(TAG, "evento " + ev.id + ": " + ev.clip.size() + " fotogrammi, " + ev.audio.size() / 32000.0 + " s di audio"
                    + (comunque ? " (chiuso per tempo)" : ""));
        } catch (Exception e) {
            Log.w(TAG, "evento non chiuso: " + e);
        }
        invia();
    }

    private static byte[] wav(byte[] pcm) {
        ByteBuffer b = ByteBuffer.allocate(44 + pcm.length).order(java.nio.ByteOrder.LITTLE_ENDIAN);
        b.put("RIFF".getBytes(StandardCharsets.US_ASCII)).putInt(36 + pcm.length).put("WAVE".getBytes(StandardCharsets.US_ASCII));
        b.put("fmt ".getBytes(StandardCharsets.US_ASCII)).putInt(16).putShort((short) 1).putShort((short) 1)
                .putInt(16000).putInt(32000).putShort((short) 2).putShort((short) 16);
        b.put("data".getBytes(StandardCharsets.US_ASCII)).putInt(pcm.length).put(pcm);
        return b.array();
    }

    private static void scrivi(File f, byte[] dati) throws IOException {
        File tmp = new File(f.getPath() + ".tmp");
        try (FileOutputStream o = new FileOutputStream(tmp)) { o.write(dati); }
        if (!tmp.renameTo(f)) throw new IOException("rinomina non riuscita: " + f);
    }

    private File coda() { return new File(app.getFilesDir(), "sentinella-coda"); }

    // ------------------------------------------------------------------ invio al PC (solo al PC)
    private final Object sveglia = new Object();
    private volatile boolean daInviare = true;
    private long ultimoStato = 0;
    private String statoInviato = "";

    private void invia() {
        synchronized (sveglia) { daInviare = true; sveglia.notifyAll(); }
    }

    private void cicloInvio() {
        long attesa = 0;
        while (true) {
            synchronized (sveglia) {
                if (!daInviare) { try { sveglia.wait(Math.max(1000, attesa)); } catch (InterruptedException e) { return; } }
                daInviare = false;
            }
            boolean ok = true;
            try {
                ok = svuotaCoda();
                ok &= mandaStato();
                // microfono voluto ma muto (JarvisServizio l'aveva lasciato): lo si risveglia
                if (vuoleMicrofono() && SystemClock.elapsedRealtime() - ultimoAudio > 4000) JarvisServizio.svegliaMicrofono();
                long ora = SystemClock.elapsedRealtime();
                if (ora - ultimoRegistro > 30_000 && attiva) {
                    ultimoRegistro = ora;
                    ultimiLivelli = String.format(Locale.ROOT, "mov %.3f (%d analisi, luce %.0f), rum %.1f dB, fondo %.1f dB",
                            movimentoMax, fotogrammi, luce, rumoreMax, fondo);
                    Log.i(TAG, "ultimi 30 s: " + ultimiLivelli);
                    fotogrammi = 0;
                    movimentoMax = 0;
                    rumoreMax = -120;
                }
            } catch (Exception e) {
                Log.w(TAG, "invio: " + e);
                ok = false;
            }
            attesa = ok ? 5000 : 20_000;
        }
    }
    private long ultimoRegistro = 0;
    private volatile String ultimiLivelli = "";

    private boolean mandaStato() throws Exception {
        JSONObject s = new JSONObject();
        s.put("attiva", attiva);
        s.put("armata", armata());
        s.put("armamento", secondiAllArmamento());
        s.put("rumore", sente());
        s.put("perche_rumore", attiva ? percheNonSente() : "");
        s.put("telecamera", attiva ? (erroreTelecamera.isEmpty() ? "ok" : erroreTelecamera) : "");
        s.put("visore", visore);
        s.put("in_coda", contaCoda());
        s.put("livelli", ultimiLivelli);
        String testo = s.toString();
        long ora = SystemClock.elapsedRealtime();
        String senzaTempo = testo.replaceAll("\"armamento\":\\d+,?", "").replaceAll("\"livelli\":\"[^\"]*\",?", "");
        if (senzaTempo.equals(statoInviato) && ora - ultimoStato < 30_000) return true;
        if (!post("/api/sentinella/stato", null, testo.getBytes(StandardCharsets.UTF_8), "application/json")) return false;
        statoInviato = senzaTempo;
        ultimoStato = ora;
        return true;
    }

    private int contaCoda() {
        File[] c = coda().listFiles();
        return c == null ? 0 : c.length;
    }

    /** Manda gli eventi in coda, il più vecchio prima: foto (o "evento") per prima, poi il resto. true se tutto ok. */
    private boolean svuotaCoda() throws Exception {
        File[] cartelle = coda().listFiles();
        if (cartelle == null || cartelle.length == 0) return true;
        Arrays.sort(cartelle);
        limitaCoda(cartelle);
        for (File c : cartelle) {
            File meta = new File(c, "evento.json");
            if (!meta.exists()) {
                // senza dati dell'evento: rimasta a metà da un riavvio, o non ancora pronta
                if (System.currentTimeMillis() - c.lastModified() > 60_000) cancellaTutto(c);
                continue;
            }
            JSONObject m = new JSONObject(new String(leggi(meta), StandardCharsets.UTF_8));
            String extra = "&valore=" + m.optDouble("valore", 0) + "&ora=" + m.optLong("ora", 0);
            File creato = new File(c, "creato");
            if (!creato.exists()) {
                File foto = new File(c, "foto.jpg");
                String nome = foto.exists() ? "foto.jpg" : "evento";
                if (!post("/api/sentinella/file", "id=" + Uri.encode(c.getName()) + "&nome=" + nome + extra,
                        foto.exists() ? leggi(foto) : new byte[0], "image/jpeg")) return false;
                scrivi(creato, new byte[0]);
                if (foto.exists()) foto.delete();
            }
            String[] nomi = c.list();
            if (nomi == null) continue;
            Arrays.sort(nomi);
            for (String n : nomi) {
                if (!(n.startsWith("clip-") && n.endsWith(".jpg")) && !n.equals("audio.wav")) continue;
                File f = new File(c, n);
                if (!post("/api/sentinella/file", "id=" + Uri.encode(c.getName()) + "&nome=" + n,
                        leggi(f), n.endsWith(".wav") ? "audio/wav" : "image/jpeg")) return false;
                f.delete();
            }
            if (new File(c, "completo").exists()) cancellaTutto(c);
        }
        return true;
    }

    /** Coda troppo grande (PC spento per giorni): si buttano gli eventi più vecchi. */
    private void limitaCoda(File[] cartelle) {
        long totale = 0;
        for (File c : cartelle) totale += dimensione(c);
        for (int i = 0; i < cartelle.length && totale > 60L * 1024 * 1024; i++) {
            totale -= dimensione(cartelle[i]);
            Log.w(TAG, "coda piena: butto " + cartelle[i].getName());
            cancellaTutto(cartelle[i]);
        }
    }

    private static long dimensione(File c) {
        long t = 0;
        File[] f = c.listFiles();
        if (f != null) for (File x : f) t += x.length();
        return t;
    }

    private static void cancellaTutto(File c) {
        File[] f = c.listFiles();
        if (f != null) for (File x : f) x.delete();
        c.delete();
    }

    private static byte[] leggi(File f) throws IOException {
        byte[] b = new byte[(int) f.length()];
        try (InputStream in = new java.io.FileInputStream(f)) {
            int letti = 0;
            while (letti < b.length) {
                int n = in.read(b, letti, b.length - letti);
                if (n < 0) break;
                letti += n;
            }
        }
        return b;
    }

    /** POST al pannello del PC (gli stessi indirizzi della pagina: USB e poi rete). true se 200. */
    private boolean post(String percorso, String query, byte[] corpo, String tipo) {
        List<String> provati = new ArrayList<>();
        for (String pagina : Applicazione.indirizzi(app)) {
            Uri u = Uri.parse(pagina);
            String base = u.getScheme() + "://" + u.getEncodedAuthority();
            if (provati.contains(base)) continue;
            provati.add(base);
            String t = u.getQueryParameter("t");
            HttpURLConnection c = null;
            try {
                c = (HttpURLConnection) new URL(base + percorso + "?t=" + Uri.encode(t == null ? "" : t)
                        + (query != null ? "&" + query : "")).openConnection();
                c.setConnectTimeout(3000);
                c.setReadTimeout(10_000);
                c.setDoOutput(true);
                c.setRequestMethod("POST");
                c.setRequestProperty("Content-Type", tipo);
                c.setFixedLengthStreamingMode(corpo.length);
                try (OutputStream o = c.getOutputStream()) { o.write(corpo); }
                int codice = c.getResponseCode();
                if (codice == 200) return true;
                Log.w(TAG, percorso + " su " + base + ": " + codice);
                if (codice == 400) return true;   // il PC non lo vuole (dati sbagliati): inutile riprovare all'infinito
            } catch (IOException e) {
                Log.d(TAG, percorso + " su " + base + ": " + e);
            } finally {
                if (c != null) c.disconnect();
            }
        }
        return false;
    }
}
