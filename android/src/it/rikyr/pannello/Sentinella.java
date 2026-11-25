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
import android.media.MediaFormat;
import android.net.Uri;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.PowerManager;
import android.os.StatFs;
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
 * Modalità sentinella: il tablet fa da telecamera di sicurezza (richieste dell'utente, 2026-11-25).
 *
 * - Movimento, sensibile "come una Ring": telecamera DAVANTI, fotogrammi YUV 640x480 ridotti a una griglia 64x48 di
 *   medie e confrontati con uno sfondo che si aggiorna piano. Conta la MACCHIA più grande di celle cambiate (vicine
 *   tra loro): una persona piccola e lontana fa una macchia, il rumore del sensore fa puntini sparsi. Scatta se la
 *   macchia supera la soglia (sensibilità 8 di serie: 8 celle, 0,26% dell'immagine) in due analisi di fila.
 *   Contro i falsi allarmi da luce: il cambio di luce di tutta l'immagine (esposizione automatica, nuvola, lampada)
 *   si compensa con un guadagno, e se cambia gran parte dell'immagine tutta nello stesso verso (più chiara o più
 *   scura) è luce, non movimento: lo sfondo si rifà senza evento.
 * - Notte automatica: se la stanza è buia (luce media dei fotogrammi sotto 40 per 3 s; torna a colori sopra 70 per
 *   5 s) il video e le foto diventano in BIANCO E NERO, con il livello del nero tolto, guadagno fino a 8x, una curva
 *   che schiarisce le ombre e la media con il fotogramma prima (meno rumore). La telecamera resta impostata uguale
 *   (esposizione lunga sempre permessa): così il passaggio non oscilla. Niente infrarossi: al buio totale non si
 *   vede nulla, serve almeno un po' di luce (lampione, standby, luce dal corridoio).
 * - Rumore: RMS di blocchi da 80 ms sopra il rumore di fondo di un margine per almeno 240 ms. Microfono condiviso con
 *   Jarvis (JarvisServizio); col visore addosso il microfono del tablet resta spento: si guarda solo.
 * - Quando scatta: foto subito (al PC per prima: da lì parte l'avviso), poi la CLIP VIDEO (H.264, Video.java) da ~5 s
 *   PRIMA dell'evento fino a 12 s dopo, allungata finché c'è ancora movimento o rumore (al massimo 30 s), con l'audio
 *   (AAC). Se l'encoder non c'è: fotogrammi JPEG e WAV come nella 1.11.0.
 * - Tutto va SOLO al PC (/api/sentinella/file). PC spento o irraggiungibile: gli eventi restano in coda nel tablet
 *   (files/sentinella-coda, al massimo 300 MB e mai sotto 500 MB liberi, i più vecchi si buttano) e partono appena
 *   il PC torna. Impostazioni e stato attiva/spenta sono salvati nel tablet: funziona anche a PC spento.
 * - Niente scatti da soli: armamento (20 s), e ogni cambio di schermo ferma il movimento per 3 s.
 */
final class Sentinella {
    interface Ascoltatore { void cambiata(); }

    private static final String TAG = "PannelloSentinella";
    static final String PROVA = "it.rikyr.pannello.SENTINELLA_PROVA";
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
    private volatile long fineSuono = 0;
    private volatile long ultimoAudio = 0, ultimoFotogramma = 0;
    private volatile String erroreTelecamera = "";
    private volatile int eventiDaAccensione = 0;
    private volatile long ultimoEvento = 0;
    private volatile String ultimoComando = "";
    private volatile String impostazioniPc = "";
    private volatile String ultimoDa = "";            // chi l'ha accesa o spenta per ultimo (va al PC)
    private volatile int riaperture = 0;              // telecamera riaperta dalla guardia (per il registro e il PC)
    private long ultimaProvaVideo = 0;
    private int larghezza = 0, altezza = 0;

    // impostazioni (dal PC, config.json "sentinella"; salvate anche qui; predefinite uguali a server/sentinella.py)
    private volatile boolean movimentoOn = true, rumoreOn = true;
    private volatile int sensMovimento = 8, sensRumore = 5, armamento = 20, pausa = 20;
    private volatile String schermo = "attenuato";
    private volatile String modoNotte = "auto";      // "auto", "sempre", "mai"

    // telecamera e video
    private final HandlerThread filo = new HandlerThread("sentinella-telecamera");
    private final Handler h;
    private CameraDevice telecamera;
    private CameraCaptureSession sessione;
    private ImageReader lettore;
    private int orientamentoSensore = 0;
    private boolean davanti = true;
    private int prestata = 0;                       // Jarvis sta usando la telecamera per una foto
    private boolean aprendo = false;
    private final Video video = new Video();
    private volatile boolean videoOk = false;
    private String erroreVideo = "";
    private byte[] luma, yOut, uOut, vOut;
    private int[] media;                            // per la riduzione del rumore di notte
    private final int[] curva = new int[256];       // luce -> luce, di notte
    private float[] griglia, sfondo;
    private boolean[] cambiate;
    private int[] coda;
    private final int[] istogramma = new int[256];
    private int sopraDiFila = 0;
    private volatile int fotogrammi = 0;
    private volatile float luce = -1;               // luce media dell'ultimo fotogramma (0-255)
    private volatile boolean notte = false;
    private long buioDa = 0, chiaroDa = 0;
    private long ultimaAnalisi = 0, ultimoCodificato = 0, ultimoJpeg = 0;
    private float movimentoMax = 0;                 // per il registro: la macchia più grande (frazione) in 30 s
    private final ArrayList<byte[]> anello = new ArrayList<>();   // JPEG degli ultimi ~2 s (solo senza video)
    private static final int GW = 64, GH = 48;

    // audio
    private final byte[] anelloAudio = new byte[16000 * 2 * 6];  // ultimi 6 s di PCM 16 kHz
    private int posAudio = 0;
    private boolean anelloPieno = false;
    private long fineAnelloUs = 0;                  // istante dell'ultimo campione nell'anello
    private float fondo = Float.NaN;
    private int bloccoSopra = 0;
    private float rumoreMax = -120;

    private Evento evento;
    private final Thread invio;

    private static final class Evento {
        String id, tipo;
        double valore;
        long oraMs, inizio, fine;
        byte[] foto;
        boolean fotoChiusa;
        final ArrayList<byte[]> clip = new ArrayList<>();
        final List<Video.Pacchetto> video = Collections.synchronizedList(new ArrayList<Video.Pacchetto>());
        final ByteArrayOutputStream audio = new ByteArrayOutputStream();
        long audioInizioUs;
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
            @Override public void onReceive(Context x, Intent i) {
                calma(3000);
                if (Intent.ACTION_SCREEN_OFF.equals(i.getAction()) && attiva) {
                    h.removeCallbacks(riaccendi);
                    h.postDelayed(riaccendi, 3000);
                }
            }
        }, f);
        // prova dal PC con adb (solo chi ha il permesso DUMP, cioè adb shell e il sistema):
        //   adb shell am broadcast -a it.rikyr.pannello.SENTINELLA_PROVA --es tipo movimento
        c.registerReceiver(new BroadcastReceiver() {
            @Override public void onReceive(Context x, Intent i) {
                final String tipo = "rumore".equals(i.getStringExtra("tipo")) ? "rumore" : "movimento";
                Log.i(TAG, "prova da adb: " + tipo);
                h.post(new Runnable() { @Override public void run() { ultimoEvento = 0; scatta(tipo, -1); } });
            }
        }, new IntentFilter(PROVA), "android.permission.DUMP", null);
        NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
        nm.createNotificationChannel(new NotificationChannel("sentinella", "Sentinella", NotificationManager.IMPORTANCE_LOW));
        String salvate = prefs.getString("imp", null);
        if (salvate != null) {
            try { applica(new JSONObject(salvate)); } catch (Exception e) { Log.w(TAG, "impostazioni salvate: " + e); }
        }
        invio = new Thread(new Runnable() { @Override public void run() { cicloInvio(); } }, "sentinella-invio");
        invio.setDaemon(true);
        invio.start();
    }

    // ------------------------------------------------------------------ stato e comandi
    boolean attiva() { return attiva; }
    boolean armata() { return attiva && SystemClock.elapsedRealtime() >= armataDa; }
    int secondiAllArmamento() { return (int) Math.max(0, (armataDa - SystemClock.elapsedRealtime() + 999) / 1000); }
    /** Mai: la sentinella non spegne lo schermo, lo attenua soltanto (richiesta dell'utente, 2026-11-25). */
    boolean schermoSpento() { return false; }
    boolean visore() { return visore; }
    boolean notte() { return notte; }
    boolean registra() { synchronized (this) { return evento != null; } }
    int eventi() { return eventiDaAccensione; }
    String erroreTelecamera() { return erroreTelecamera; }

    /** Microfono in uso adesso per la sentinella (blocchi arrivati negli ultimi 2 s). */
    boolean sente() { return attiva && SystemClock.elapsedRealtime() - ultimoAudio < 2000; }

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
            ultimoDa = da;
            prefs.edit().putBoolean("attiva", si).putString("da", da).apply();
            Log.i(TAG, (si ? "attivata" : "spenta") + " (" + da + ")");
            if (si) {
                armataDa = SystemClock.elapsedRealtime() + armamento * 1000L;
                eventiDaAccensione = 0;
                fondo = Float.NaN;
                h.post(new Runnable() { @Override public void run() { apri(); } });
            } else {
                synchronized (this) { if (evento != null) chiudiEvento(true); }
                h.post(new Runnable() {
                    @Override public void run() {
                        chiudi();
                        video.ferma();
                        videoOk = false;
                        notte = false;
                    }
                });
            }
        }
        notifica();
        JarvisServizio.svegliaMicrofono();
        if (si && !"riavvio".equals(da)) mostraPannello();
        avvisaAscoltatori();
    }

    /** Schermo spento con la sentinella accesa (tempo scaduto con un'altra app davanti, tasto, chiunque): dopo 3 s
     *  si riaccende, attenuato, con il pannello e l'indicatore davanti. */
    private final Runnable riaccendi = new Runnable() {
        @Override public void run() {
            PowerManager pm = (PowerManager) app.getSystemService(Context.POWER_SERVICE);
            if (!attiva || pm.isInteractive()) return;
            Log.i(TAG, "schermo spento con la sentinella accesa: lo riaccendo (attenuato)");
            mostraPannello();
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
            boolean rumorePrima = rumoreOn;
            applica(imp);
            prefs.edit().putString("imp", impostazioniPc).apply();   // valgono anche a PC spento e dopo un riavvio
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

    private void applica(JSONObject imp) {
        impostazioniPc = imp.toString();
        movimentoOn = imp.optBoolean("movimento", true);
        rumoreOn = imp.optBoolean("rumore", true);
        sensMovimento = limita(imp.optInt("sens_movimento", 8), 1, 10);
        sensRumore = limita(imp.optInt("sens_rumore", 5), 1, 10);
        armamento = limita(imp.optInt("armamento", 20), 0, 300);
        pausa = limita(imp.optInt("pausa", 20), 5, 600);
        schermo = imp.optString("schermo", "attenuato");
        modoNotte = imp.optString("notte", "auto");
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
        fineSuono = Math.max(fineSuono, ora) + durata;
        silenzioFino = fineSuono + 1500;
    }

    // ------------------------------------------------------------------ notifica
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
            Range<Integer>[] campi = car.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES);
            final Range<Integer> fps = fps(campi);
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
            larghezza = misura.getWidth();
            altezza = misura.getHeight();
            if (!video.attivo()) {
                ultimaProvaVideo = SystemClock.elapsedRealtime();
                videoOk = video.avvia(misura.getWidth(), misura.getHeight());
                erroreVideo = videoOk ? "" : "encoder non disponibile";
            }
            aprendo = true;
            Log.i(TAG, "apro la telecamera " + scelta + (davanti ? " (davanti)" : "") + " " + misura + " fps " + fps
                    + " (possibili " + Arrays.toString(campi) + ")");
            cm.openCamera(scelta, new CameraDevice.StateCallback() {
                @Override public void onOpened(final CameraDevice d) {
                    aprendo = false;
                    if (!attiva || prestata > 0) { d.close(); return; }
                    telecamera = d;
                    try {
                        d.createCaptureSession(Collections.singletonList(lettore.getSurface()),
                                new CameraCaptureSession.StateCallback() {
                                    @Override public void onConfigured(CameraCaptureSession s) {
                                        sessione = s;
                                        try {
                                            CaptureRequest.Builder b = d.createCaptureRequest(CameraDevice.TEMPLATE_RECORD);
                                            b.addTarget(lettore.getSurface());
                                            b.set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO);
                                            b.set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON);
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
        media = null;
        synchronized (this) { anello.clear(); }
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

    /** Il minimo più basso possibile (al buio l'esposizione si allunga), con un massimo tra 15 e 30 (più in alto
     *  la telecamera e il telefono lavorano per niente: il video ne usa ~15 al secondo). */
    private static Range<Integer> fps(Range<Integer>[] campi) {
        if (campi == null) return null;
        Range<Integer> scelto = null;
        for (Range<Integer> r : campi) {
            if (r.getUpper() < 15 || r.getUpper() > 30) continue;
            if (scelto == null || r.getLower() < scelto.getLower()
                    || (r.getLower().equals(scelto.getLower()) && r.getUpper() < scelto.getUpper())) scelto = r;
        }
        return scelto;
    }

    // ------------------------------------------------------------------ fotogrammi (thread telecamera)
    private void fotogramma(Image im) {
        long ora = SystemClock.elapsedRealtime();
        ultimoFotogramma = ora;
        boolean analisi = ora - ultimaAnalisi >= 200;
        boolean codifica = videoOk && ora - ultimoCodificato >= 62;       // ~15 al secondo
        Evento ev;
        synchronized (this) { ev = evento; }
        boolean serveFoto = ev != null && !ev.fotoChiusa;
        boolean serveJpeg = serveFoto || (!videoOk && ora - ultimoJpeg >= 500);
        if (!analisi && !codifica && !serveJpeg) return;
        int w = im.getWidth(), hh = im.getHeight();
        if (luma == null || luma.length != w * hh) {
            luma = new byte[w * hh];
            yOut = new byte[w * hh];
            uOut = new byte[w * hh / 4];
            vOut = new byte[w * hh / 4];
            media = null;
        }
        copiaLuma(im, luma, w, hh);
        if (analisi) {
            ultimaAnalisi = ora;
            analizza(luma, w, hh, ora);
        }
        if (!codifica && !serveJpeg) return;
        elabora(im, w, hh);
        if (codifica) {
            ultimoCodificato = ora;
            try {
                video.codifica(yOut, uOut, vOut, SystemClock.elapsedRealtimeNanos() / 1000);
            } catch (Exception e) {
                Log.w(TAG, "video: " + e);
                videoOk = false;
                erroreVideo = e.toString();
                video.ferma();
            }
        }
        if (serveJpeg) {
            ultimoJpeg = ora;
            byte[] jpeg = jpeg(w, hh);
            if (jpeg == null) return;
            synchronized (this) {
                if (!videoOk) {
                    anello.add(jpeg);
                    while (anello.size() > 4) anello.remove(0);
                }
                if (evento != null) {
                    if (!evento.fotoChiusa) {
                        evento.foto = jpeg;
                        evento.fotoChiusa = true;
                        evento.ultimoClip = ora;
                        mandaFoto(evento);
                    } else if (!videoOk && ora - evento.ultimoClip >= 450 && evento.clip.size() < 40) {
                        evento.clip.add(jpeg);
                        evento.ultimoClip = ora;
                    }
                }
            }
        }
        synchronized (this) { if (evento != null && ora > evento.fine) chiudiEvento(false); }
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

    /** Il fotogramma che va nel video e nelle foto: a colori di giorno; di notte bianco e nero, più luce, meno rumore. */
    private void elabora(Image im, int w, int hh) {
        int n = w * hh;
        if (!notte) {
            System.arraycopy(luma, 0, yOut, 0, n);
            Image.Plane[] p = im.getPlanes();
            ByteBuffer u = p[1].getBuffer(), v = p[2].getBuffer();
            int passo = p[1].getRowStride(), pixel = p[1].getPixelStride();
            int k = 0;
            for (int r = 0; r < hh / 2; r++) {
                int base = r * passo;
                for (int c = 0; c < w / 2; c++, k++) {
                    int i = base + c * pixel;
                    uOut[k] = u.get(i);
                    vOut[k] = v.get(i);
                }
            }
            media = null;
            return;
        }
        if (media == null || media.length != n) {
            media = new int[n];
            for (int i = 0; i < n; i++) media[i] = curva[luma[i] & 0xff] << 4;
        }
        // media mobile (metà fotogramma nuovo, metà vecchio) in virgola fissa: il rumore del sensore cala
        for (int i = 0; i < n; i++) {
            int nuovo = curva[luma[i] & 0xff] << 4;
            int m = (media[i] + nuovo) >> 1;
            media[i] = m;
            yOut[i] = (byte) (m >> 4);
        }
        Arrays.fill(uOut, (byte) 128);
        Arrays.fill(vOut, (byte) 128);
    }

    /** Curva della notte: toglie il livello del nero, guadagno fino a 8x e schiarisce le ombre. */
    private void preparaCurva(int nero, float mediaLuce) {
        float guadagno = Math.max(1f, Math.min(8f, 110f / Math.max(1f, mediaLuce - nero)));
        for (int i = 0; i < 256; i++) {
            float x = Math.max(0, i - nero) * guadagno / 255f;
            float y = (float) Math.pow(Math.min(1f, x), 0.75);
            curva[i] = Math.min(255, Math.round(y * 255));
        }
    }

    private void analizza(byte[] y, int w, int hh, long ora) {
        int n = GW * GH;
        if (griglia == null) {
            griglia = new float[n];
            cambiate = new boolean[n];
            coda = new int[n];
        }
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
        float mediaLuce = somma / n;
        luce = mediaLuce;
        fotogrammi++;
        decidiNotte(mediaLuce, ora);
        if (sfondo == null || ora < calmaFino) {
            if (sfondo == null) sfondo = new float[n];
            System.arraycopy(griglia, 0, sfondo, 0, n);
            sopraDiFila = 0;
            return;
        }
        float mediaSfondo = 0;
        for (float v : sfondo) mediaSfondo += v;
        mediaSfondo /= n;
        // cambio di luce generale: si compensa con un guadagno (al buio il rapporto sarebbe solo rumore)
        float guadagno = 1f;
        if (Math.max(mediaLuce, mediaSfondo) >= 12) {
            guadagno = mediaSfondo / Math.max(1f, mediaLuce);
            if (guadagno < 0.4f || guadagno > 2.5f) { rifaiSfondo(ora); return; }   // luce cambiata del tutto
        }
        Arrays.fill(istogramma, 0);
        float[] diff = new float[n];
        for (int i = 0; i < n; i++) {
            float d = griglia[i] * guadagno - sfondo[i];
            diff[i] = d;
            istogramma[Math.min(255, (int) Math.abs(d))]++;
        }
        int meta = n / 2, cum = 0, mediana = 0;
        for (int i = 0; i < 256; i++) { cum += istogramma[i]; if (cum >= meta) { mediana = i; break; } }
        // soglia per cella: sopra il rumore del sensore (al buio è alto), più bassa con la sensibilità alta
        float sogliaCella = Math.max(26f - 1.5f * sensMovimento, 3.5f * mediana + 3);
        int cambi = 0, piu = 0;
        for (int i = 0; i < n; i++) {
            boolean c = Math.abs(diff[i]) > sogliaCella;
            cambiate[i] = c;
            if (c) { cambi++; if (diff[i] > 0) piu++; }
        }
        int macchia = macchiaPiuGrande();
        float frazione = (float) macchia / n;
        if (frazione > movimentoMax) movimentoMax = frazione;
        // tanta parte dell'immagine cambiata tutta nello stesso verso: è la luce (sole, nuvola, lampada), non movimento
        if (cambi > n * 0.35f && (piu > cambi * 0.85f || piu < cambi * 0.15f)) { rifaiSfondo(ora); return; }
        int minimo = Math.max(3, Math.round(200f * (float) Math.pow(0.63, sensMovimento - 1)));
        boolean mosso = macchia >= minimo;
        sopraDiFila = mosso ? sopraDiFila + 1 : 0;
        float a = mosso ? 0.01f : 0.08f;      // lo sfondo segue i cambiamenti lenti; fermo mentre qualcosa si muove
        for (int i = 0; i < n; i++) sfondo[i] = sfondo[i] * (1 - a) + griglia[i] * a;
        if (sopraDiFila >= 2 && movimentoOn && armata()) {
            sopraDiFila = 0;
            scatta("movimento", Math.round(frazione * 10000) / 10000.0);
        }
    }

    private void rifaiSfondo(long ora) {
        System.arraycopy(griglia, 0, sfondo, 0, griglia.length);
        calmaFino = ora + 1500;
        sopraDiFila = 0;
    }

    /** La macchia più grande di celle cambiate che si toccano (anche in diagonale). */
    private int macchiaPiuGrande() {
        int n = GW * GH, migliore = 0;
        boolean[] visto = new boolean[n];
        for (int s = 0; s < n; s++) {
            if (!cambiate[s] || visto[s]) continue;
            int testa = 0, fondoCoda = 0, conta = 0;
            coda[fondoCoda++] = s;
            visto[s] = true;
            while (testa < fondoCoda) {
                int i = coda[testa++];
                conta++;
                int x = i % GW, y = i / GW;
                for (int dy = -1; dy <= 1; dy++) {
                    for (int dx = -1; dx <= 1; dx++) {
                        int nx = x + dx, ny = y + dy;
                        if (nx < 0 || ny < 0 || nx >= GW || ny >= GH) continue;
                        int j = ny * GW + nx;
                        if (cambiate[j] && !visto[j]) { visto[j] = true; coda[fondoCoda++] = j; }
                    }
                }
            }
            if (conta > migliore) migliore = conta;
        }
        return migliore;
    }

    /** Notte se la stanza resta buia per 3 s, di nuovo giorno se resta chiara per 5 s. */
    private void decidiNotte(float mediaLuce, long ora) {
        if (mediaLuce < 40) { chiaroDa = 0; if (buioDa == 0) buioDa = ora; }
        else if (mediaLuce > 70) { buioDa = 0; if (chiaroDa == 0) chiaroDa = ora; }
        else { buioDa = 0; chiaroDa = 0; }
        boolean prima = notte;
        if (!notte && buioDa > 0 && ora - buioDa > 3000) notte = true;
        if (notte && chiaroDa > 0 && ora - chiaroDa > 5000) notte = false;
        if ("sempre".equals(modoNotte)) notte = true;
        else if ("mai".equals(modoNotte)) notte = false;
        if (notte) {
            // livello del nero: le celle più scure (5%)
            float[] c = griglia.clone();
            Arrays.sort(c);
            preparaCurva((int) Math.max(0, Math.min(40, c[c.length / 20] - 2)), mediaLuce);
        }
        if (prima != notte) {
            Log.i(TAG, notte ? "modalità notte: bianco e nero, più luce" : "di nuovo giorno: colori");
            avvisaAscoltatori();
        }
    }

    /** yOut/uOut/vOut -> NV21 -> JPEG, con l'orientamento nell'EXIF. */
    private byte[] jpeg(int w, int hh) {
        try {
            byte[] nv21 = new byte[w * hh * 3 / 2];
            System.arraycopy(yOut, 0, nv21, 0, w * hh);
            int pos = w * hh;
            for (int k = 0; k < uOut.length; k++) { nv21[pos++] = vOut[k]; nv21[pos++] = uOut[k]; }
            ByteArrayOutputStream out = new ByteArrayOutputStream(64 * 1024);
            new YuvImage(nv21, ImageFormat.NV21, w, hh, null).compressToJpeg(new Rect(0, 0, w, hh), 80, out);
            return conOrientamento(out.toByteArray(), rotazione());
        } catch (Exception e) {
            Log.w(TAG, "jpeg: " + e);
            return null;
        }
    }

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
                (byte) 0xFF, (byte) 0xE1, 0, 34,
                'E', 'x', 'i', 'f', 0, 0,
                'M', 'M', 0, 42, 0, 0, 0, 8,
                0, 1,
                0x01, 0x12, 0, 3, 0, 0, 0, 1, 0, (byte) o, 0, 0,
                0, 0, 0, 0 };
        byte[] r = new byte[jpeg.length + exif.length];
        r[0] = jpeg[0];
        r[1] = jpeg[1];
        System.arraycopy(exif, 0, r, 2, exif.length);
        System.arraycopy(jpeg, 2, r, 2 + exif.length, jpeg.length - 2);
        return r;
    }

    // ------------------------------------------------------------------ audio (thread del microfono)
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
        synchronized (this) {
            int k = Math.min(n, anelloAudio.length);
            for (int i = 0; i < k; i++) {
                anelloAudio[posAudio] = b[i];
                posAudio = (posAudio + 1) % anelloAudio.length;
            }
            if (posAudio < k) anelloPieno = true;
            fineAnelloUs = SystemClock.elapsedRealtimeNanos() / 1000;
            if (evento != null && evento.audio.size() < 16000 * 2 * 40) evento.audio.write(b, 0, n);
        }
        if (ora < silenzioFino) { bloccoSopra = 0; return; }
        if (db > rumoreMax) rumoreMax = db;
        if (Float.isNaN(fondo)) fondo = db;
        else if (db < fondo) fondo = fondo * 0.8f + db * 0.2f;
        else fondo = Math.min(db, fondo + 0.03f);
        float margine = 30f - 2.2f * (sensRumore - 1);
        float soglia = Math.max(fondo + margine, -55f);
        bloccoSopra = db > soglia ? bloccoSopra + 1 : 0;
        if (bloccoSopra >= 3 && rumoreOn && armata() && !visore) {
            bloccoSopra = 0;
            scatta("rumore", Math.round(db * 10) / 10.0);
        }
    }

    // ------------------------------------------------------------------ eventi
    private void scatta(String tipo, double valore) {
        long ora = SystemClock.elapsedRealtime();
        synchronized (this) {
            if (evento != null) {
                // ancora movimento o rumore durante la registrazione: la clip si allunga (al massimo 30 s)
                evento.fine = Math.min(evento.inizio + 30_000, Math.max(evento.fine, ora + 8000));
                return;
            }
            if (!armata() || (ultimoEvento != 0 && ora - ultimoEvento < pausa * 1000L)) return;
            ultimoEvento = ora;
            final Evento ev = new Evento();
            ev.tipo = tipo;
            ev.valore = valore;
            ev.oraMs = System.currentTimeMillis();
            ev.inizio = ora;
            ev.fine = ora + 12_000;
            ev.id = new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.ROOT).format(new Date(ev.oraMs)) + "-" + tipo + "-"
                    + String.format(Locale.ROOT, "%04x", new Random().nextInt(0x10000));
            long adessoUs = SystemClock.elapsedRealtimeNanos() / 1000;
            if (videoOk) {
                ev.video.addAll(video.da(adessoUs - 5_000_000));   // ~5 s prima dell'evento
                video.uscita = new Video.Uscita() { @Override public void pacchetto(Video.Pacchetto p) { ev.video.add(p); } };
            } else {
                ev.clip.addAll(anello);
            }
            // audio: gli ultimi 5 s dell'anello
            int pieno = anelloPieno ? anelloAudio.length : posAudio;
            int prendi = Math.min(pieno, 16000 * 2 * 5);
            int da = (posAudio - prendi + anelloAudio.length) % anelloAudio.length;
            for (int i = 0; i < prendi; i++) ev.audio.write(anelloAudio[(da + i) % anelloAudio.length]);
            ev.audioInizioUs = fineAnelloUs - prendi / 2 * 1_000_000L / 16000;
            if (telecamera == null || ora - ultimoFotogramma > 2000) ev.fotoChiusa = true;   // telecamera ferma
            evento = ev;
            eventiDaAccensione++;
            Log.i(TAG, "scattata: " + tipo + " " + valore + (notte ? " (notte)" : "") + " -> " + ev.id
                    + ", video da " + (ev.video.isEmpty() ? 0 : (adessoUs - ev.video.get(0).pts) / 1000) + " ms prima");
            if (ev.fotoChiusa) mandaFoto(ev);
        }
        h.postDelayed(new Runnable() {
            @Override public void run() {
                synchronized (Sentinella.this) {
                    if (evento != null && !evento.fotoChiusa) { evento.fotoChiusa = true; mandaFoto(evento); }
                }
            }
        }, 1500);
        // telecamera ferma a metà: si chiude comunque
        h.postDelayed(new Runnable() {
            @Override public void run() {
                synchronized (Sentinella.this) {
                    if (evento != null && SystemClock.elapsedRealtime() > evento.fine + 2000) chiudiEvento(true);
                }
            }
        }, 33_000);
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
            m.put("notte", notte);
            scrivi(new File(c, "evento.json"), m.toString().getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) {
            Log.w(TAG, "evento non salvato: " + e);
        }
        invia();
    }

    /** Fine dell'evento: la clip si monta (thread a parte) e va nella coda. Sotto blocco. */
    private void chiudiEvento(final boolean comunque) {
        final Evento ev = evento;
        if (ev == null) return;
        evento = null;
        video.uscita = null;
        if (ev.cartella == null) { ev.fotoChiusa = true; mandaFoto(ev); }
        final MediaFormat formato = video.formato();
        final int rot = rotazione();
        new Thread(new Runnable() {
            @Override public void run() {
                try {
                    byte[] pcm = ev.audio.toByteArray();
                    List<Video.Pacchetto> pacchetti;
                    synchronized (ev.video) { pacchetti = new ArrayList<>(ev.video); }
                    boolean fatto = false;
                    if (!pacchetti.isEmpty() && formato != null) {
                        File tmp = new File(ev.cartella, "clip.mp4.tmp");
                        try {
                            Video.monta(tmp, formato, pacchetti, pcm, ev.audioInizioUs, rot);
                            fatto = tmp.renameTo(new File(ev.cartella, "clip.mp4"));
                            Log.i(TAG, "evento " + ev.id + ": clip di " + (pacchetti.get(pacchetti.size() - 1).pts
                                    - pacchetti.get(0).pts) / 100_000 / 10.0 + " s, " + tmp.getParentFile().list().length
                                    + " file, audio " + pcm.length / 32000.0 + " s" + (comunque ? " (chiusa per tempo)" : ""));
                        } catch (Exception e) {
                            Log.w(TAG, "clip non montata: " + e);
                            tmp.delete();
                        }
                    }
                    if (!fatto) {
                        int i = 1;
                        for (byte[] f : ev.clip) scrivi(new File(ev.cartella, String.format(Locale.ROOT, "clip-%02d.jpg", i++)), f);
                        if (pcm.length > 16000) scrivi(new File(ev.cartella, "audio.wav"), wav(pcm));
                        Log.i(TAG, "evento " + ev.id + ": " + ev.clip.size() + " fotogrammi JPEG, " + pcm.length / 32000.0 + " s di audio");
                    }
                    scrivi(new File(ev.cartella, "completo"), new byte[0]);
                } catch (Exception e) {
                    Log.w(TAG, "evento non chiuso: " + e);
                }
                invia();
                avvisaAscoltatori();
            }
        }, "sentinella-clip").start();
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
    private long ultimoStato = 0, ultimoRegistro = 0;
    private String statoInviato = "";
    private volatile String ultimiLivelli = "";
    private volatile boolean pcRaggiungibile = true;

    private void invia() {
        synchronized (sveglia) { daInviare = true; sveglia.notifyAll(); }
    }

    boolean pcRaggiungibile() { return pcRaggiungibile; }
    int inCoda() { return contaCoda(); }

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
                if (vuoleMicrofono() && SystemClock.elapsedRealtime() - ultimoAudio > 4000) JarvisServizio.svegliaMicrofono();
                guardia();
                long ora = SystemClock.elapsedRealtime();
                if (ora - ultimoRegistro > 30_000 && attiva) {
                    ultimoRegistro = ora;
                    ultimiLivelli = String.format(Locale.ROOT, "macchia max %.4f (%d analisi, luce %.0f%s), rum %.1f dB, fondo %.1f dB",
                            movimentoMax, fotogrammi, luce, notte ? ", notte" : "", rumoreMax, fondo);
                    Log.i(TAG, "ultimi 30 s: " + ultimiLivelli);
                    fotogrammi = 0;
                    movimentoMax = 0;
                    rumoreMax = -120;
                }
            } catch (Exception e) {
                Log.w(TAG, "invio: " + e);
                ok = false;
            }
            if (ok != pcRaggiungibile) { pcRaggiungibile = ok; avvisaAscoltatori(); }
            attesa = ok ? 5000 : 20_000;
        }
    }

    /** Ogni ~5 s: la sentinella accesa sta davvero guardando? Telecamera senza fotogrammi da 20 s (bloccata, presa da
     *  un'altra app, servizio della telecamera riavviato) -> la si chiude e riapre. Encoder video che non era partito
     *  (su questo tablet a volte il servizio dei codec non risponde all'avvio) -> si riprova ogni 5 minuti. */
    private void guardia() {
        if (!attiva) return;
        final long ora = SystemClock.elapsedRealtime();
        h.post(new Runnable() {
            @Override public void run() {
                if (!attiva || prestata > 0 || aprendo) return;
                if (telecamera != null && ultimoFotogramma > 0 && ora - ultimoFotogramma > 20_000) {
                    riaperture++;
                    Log.w(TAG, "guardia: nessun fotogramma da " + (ora - ultimoFotogramma) / 1000 + " s, riapro la telecamera");
                    ultimoFotogramma = ora;
                    chiudi();
                    apri();
                } else if (telecamera == null && !aprendo) {
                    apri();
                }
                if (!videoOk && larghezza > 0 && ora - ultimaProvaVideo > 300_000) {
                    ultimaProvaVideo = ora;
                    video.ferma();
                    videoOk = video.avvia(larghezza, altezza);
                    erroreVideo = videoOk ? "" : "encoder non disponibile";
                    Log.i(TAG, "guardia: encoder video " + (videoOk ? "ripartito" : "ancora non disponibile"));
                }
            }
        });
    }

    private boolean mandaStato() throws Exception {
        JSONObject s = new JSONObject();
        s.put("attiva", attiva);
        s.put("armata", armata());
        s.put("armamento", secondiAllArmamento());
        s.put("rumore", sente());
        s.put("perche_rumore", attiva ? percheNonSente() : "");
        s.put("telecamera", attiva ? (erroreTelecamera.isEmpty() ? "ok" : erroreTelecamera) : "");
        s.put("video", attiva ? (videoOk ? "ok" : erroreVideo) : "");
        s.put("notte", attiva && notte);
        s.put("visore", visore);
        s.put("da", ultimoDa);
        s.put("riaperture", riaperture);
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
        cartelle = limitaCoda(cartelle);
        for (File c : cartelle) {
            File meta = new File(c, "evento.json");
            if (!meta.exists()) {
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
                if (!(n.startsWith("clip-") && n.endsWith(".jpg")) && !n.equals("audio.wav") && !n.equals("clip.mp4")) continue;
                File f = new File(c, n);
                String tipo = n.endsWith(".wav") ? "audio/wav" : n.endsWith(".mp4") ? "video/mp4" : "image/jpeg";
                if (!post("/api/sentinella/file", "id=" + Uri.encode(c.getName()) + "&nome=" + n, leggi(f), tipo)) return false;
                f.delete();
            }
            if (new File(c, "completo").exists()) cancellaTutto(c);
        }
        return true;
    }

    /** Coda troppo grande (PC spento a lungo): al massimo 300 MB e mai sotto 500 MB liberi; si buttano i più vecchi. */
    private File[] limitaCoda(File[] cartelle) {
        long totale = 0;
        for (File c : cartelle) totale += dimensione(c);
        long liberi;
        try { liberi = new StatFs(app.getFilesDir().getPath()).getAvailableBytes(); } catch (Exception e) { liberi = Long.MAX_VALUE; }
        int i = 0;
        while (i < cartelle.length - 1 && (totale > 300L * 1024 * 1024 || liberi < 500L * 1024 * 1024)) {
            long d = dimensione(cartelle[i]);
            totale -= d;
            liberi += d;
            Log.w(TAG, "coda piena: butto " + cartelle[i].getName());
            cancellaTutto(cartelle[i]);
            i++;
        }
        return Arrays.copyOfRange(cartelle, i, cartelle.length);
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
                c.setReadTimeout(30_000);
                c.setDoOutput(true);
                c.setRequestMethod("POST");
                c.setRequestProperty("Content-Type", tipo);
                c.setFixedLengthStreamingMode(corpo.length);
                try (OutputStream o = c.getOutputStream()) { o.write(corpo); }
                int codice = c.getResponseCode();
                if (codice == 200) return true;
                Log.w(TAG, percorso + " su " + base + ": " + codice);
                if (codice == 400 || codice == 413) return true;   // il PC non lo vuole: inutile riprovare all'infinito
            } catch (IOException e) {
                Log.d(TAG, percorso + " su " + base + ": " + e);
            } finally {
                if (c != null) c.disconnect();
            }
        }
        return false;
    }
}
