package it.rikyr.pannello;

import android.app.Activity;
import android.app.KeyguardManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Color;
import android.os.BatteryManager;
import android.os.Bundle;
import android.os.Handler;
import android.provider.Settings;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;
import android.view.inputmethod.InputMethodManager;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/**
 * Schermata principale del tablet: mostra a tutto schermo il pannello servito dal PC.
 * È registrata come "Home", quindi parte da sola all'accensione e torna in primo piano
 * con il tasto Home. Con il tablet in carica lo schermo resta acceso.
 *
 * Indirizzi provati in ordine: 127.0.0.1 (il PC lo inoltra via USB con adb reverse),
 * poi l'IP del PC in rete locale. Se nessuno risponde mostra una pagina di attesa e riprova.
 */
public class Pannello extends Activity {
    private WebView web;
    private String[] indirizzi;
    private Abbinamento cerca;
    private int tentativo = 0;
    private boolean caricato = false;
    private final Handler handler = new Handler();
    private Ritorno ritorno;
    private Indicatore indicatore;
    private boolean inCarica = false;
    private float lucePagina = WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE;   // quella scelta dalla pagina
    private final Sentinella.Ascoltatore sentinella = new Sentinella.Ascoltatore() {
        @Override public void cambiata() {
            runOnUiThread(new Runnable() { @Override public void run() { aggiornaSentinella(); } });
        }
    };

    // Solo per lo schermo sempre acceso in carica: la simbiosi con il PC (e i suoi ricevitori) è in Applicazione
    private final BroadcastReceiver batteria = new BroadcastReceiver() {
        @Override public void onReceive(Context c, Intent i) {
            inCarica = i.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) != 0;
            schermoAcceso();
        }
    };

    /** Schermo sempre acceso in carica, e con la sentinella attiva "attenuata" (l'indicatore deve vedersi). */
    private void schermoAcceso() {
        Sentinella s = Sentinella.di(this);
        if (inCarica || (s.attiva() && !s.schermoSpento()))
            getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    }

    /** Sentinella accesa: indicatore nero sopra il pannello e luce al minimo; spenta: tutto come prima.
     *  Ogni cambio di luce lo si dice alla sentinella, che per qualche secondo non guarda il movimento. */
    private void aggiornaSentinella() {
        Sentinella s = Sentinella.di(this);
        boolean on = s.attiva();
        boolean prima = indicatore.getVisibility() == View.VISIBLE;
        indicatore.setVisibility(on ? View.VISIBLE : View.GONE);
        indicatore.invalidate();
        if (on != prima) {
            s.calma(3000);
            luce(on ? 0.01f : lucePagina);
            if (on) {
                InputMethodManager imm = (InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
                imm.hideSoftInputFromWindow(web.getWindowToken(), 0);
            }
        }
        schermoAcceso();
    }

    private View videoAperto;

    private void mostraVideo(String url) {
        chiudiVideo();
        final FrameLayout fondo = new FrameLayout(this);
        fondo.setBackgroundColor(Color.argb(235, 0, 0, 0));
        final android.widget.VideoView v = new android.widget.VideoView(this);
        // tutto lo schermo che serve, con le proporzioni del video (VideoView le tiene da sé)
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT, android.view.Gravity.CENTER);
        fondo.addView(v, lp);
        android.widget.TextView chiudi = new android.widget.TextView(this);
        chiudi.setText("✕  Chiudi");
        chiudi.setTextColor(Color.WHITE);
        chiudi.setTextSize(20);
        chiudi.setPadding(36, 24, 36, 24);
        chiudi.setBackgroundColor(Color.argb(160, 40, 40, 40));
        fondo.addView(chiudi, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, android.view.Gravity.TOP | android.view.Gravity.END));
        chiudi.setOnClickListener(new View.OnClickListener() { @Override public void onClick(View x) { chiudiVideo(); } });
        android.widget.MediaController comandi = new android.widget.MediaController(this);
        comandi.setAnchorView(v);
        v.setMediaController(comandi);
        v.setOnErrorListener(new android.media.MediaPlayer.OnErrorListener() {
            @Override public boolean onError(android.media.MediaPlayer mp, int a, int b) {
                web.evaluateJavascript("window.dispatchEvent(new CustomEvent('errore-nativo',{detail:'Video non riproducibile ("
                        + a + "/" + b + ")'}))", null);
                chiudiVideo();
                return true;
            }
        });
        v.setOnPreparedListener(new android.media.MediaPlayer.OnPreparedListener() {
            @Override public void onPrepared(android.media.MediaPlayer mp) { v.start(); }
        });
        ((ViewGroup) indicatore.getParent()).addView(fondo, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        videoAperto = fondo;
        v.setVideoURI(android.net.Uri.parse(url));
    }

    private void chiudiVideo() {
        if (videoAperto == null) return;
        ((ViewGroup) videoAperto.getParent()).removeView(videoAperto);
        videoAperto = null;
    }

    private void luce(float valore) {
        WindowManager.LayoutParams lp = getWindow().getAttributes();
        lp.screenBrightness = valore;
        getWindow().setAttributes(lp);
    }

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        String scelto = getIntent().getStringExtra("url");
        if (scelto != null) getSharedPreferences("pannello", MODE_PRIVATE).edit().putString("url", scelto).commit();
        indirizzi = Applicazione.indirizzi(this);

        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
                | WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD);
        web = new Vista(this);
        web.setBackgroundColor(Color.rgb(7, 9, 13));
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);
        s.setMediaPlaybackRequiresUserGesture(true);
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        web.setHapticFeedbackEnabled(true);
        web.setLongClickable(false);
        web.setOnLongClickListener(new View.OnLongClickListener() {
            @Override public boolean onLongClick(View v) { return true; }  // niente menu "copia/seleziona"
        });
        web.addJavascriptInterface(new Ponte(), "Pannello");
        // L'esito si decide in onPageFinished, che arriva sempre (anche dopo un errore):
        // decidere in onReceivedError farebbe partire il caricamento successivo prima che
        // finisca quello fallito.
        web.setWebViewClient(new WebViewClient() {
            @Override public void onPageStarted(WebView v, String url, android.graphics.Bitmap icona) {
                if (!url.startsWith("file:")) errore = false;
            }
            @Override public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
                if (r.isForMainFrame()) errore = true;
            }
            @Override public void onPageFinished(WebView v, String url) {
                if (url.startsWith("file:")) return;
                if (errore) {
                    tentativo++;
                    web.loadUrl(ATTESA);
                    // tre giri a vuoto: il PC forse ha cambiato IP, lo si cerca in rete (non serve riabbinare)
                    if (tentativo >= indirizzi.length * 3) cercaPc();
                    // dopo un giro completo degli indirizzi si aspetta un po' di più
                    riprova(tentativo % indirizzi.length == 0 ? 4000 : 400);
                } else {
                    caricato = true;
                    tentativo = 0;
                    if (cerca != null) { cerca.ferma(); cerca = null; }
                }
            }
        });
        FrameLayout radice = new FrameLayout(this);
        radice.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        indicatore = new Indicatore(this);
        indicatore.setVisibility(View.GONE);
        radice.addView(indicatore, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(radice);
        web.loadUrl(ATTESA);
        if (indirizzi.length == 0) cercaPc();  // APK generico, tablet non ancora abbinato
        else riprova(300);

        registerReceiver(batteria, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        ritorno = new Ritorno(this);
        Sentinella.di(this).ascolta(sentinella);
        aggiornaSentinella();
        // Jarvis usa il microfono del tablet: si chiede una volta (l'installazione lo concede già con pm grant)
        if (!microfonoConcesso()) requestPermissions(new String[] { android.Manifest.permission.RECORD_AUDIO }, 1);
        JarvisServizio.avvia(this);
    }

    private boolean microfonoConcesso() {
        return checkSelfPermission(android.Manifest.permission.RECORD_AUDIO)
                == android.content.pm.PackageManager.PERMISSION_GRANTED;
    }

    /** Jarvis attivato a schermo spento con "accendi lo schermo": si apre il suo pannello. */
    @Override protected void onNewIntent(Intent i) {
        super.onNewIntent(i);
        if (i.getBooleanExtra("jarvis", false) && web != null)
            web.evaluateJavascript("window.dispatchEvent(new CustomEvent('apri-jarvis'))", null);
    }

    /** Cerca il PC in rete o via USB (vedi Abbinamento); appena lo trova ricarica il pannello. */
    private void cercaPc() {
        if (cerca != null && cerca.isAlive()) return;
        cerca = new Abbinamento(this, new Abbinamento.Esito() {
            @Override public void riuscito() {
                runOnUiThread(new Runnable() {
                    @Override public void run() {
                        indirizzi = Applicazione.indirizzi(Pannello.this);
                        ((Applicazione) getApplication()).abbinato();
                        tentativo = 0;
                        riprova(0);
                    }
                });
            }
        });
        cerca.start();
    }

    @Override protected void onDestroy() {
        if (cerca != null) cerca.ferma();
        if (ritorno != null) ritorno.togli();
        Sentinella.di(this).smetti(sentinella);
        try { unregisterReceiver(batteria); } catch (IllegalArgumentException e) { /* già tolto */ }
        super.onDestroy();
    }

    private static final String ATTESA = "file:///android_asset/attesa.html";
    private boolean errore = false;
    private final Runnable carica = new Runnable() {
        @Override public void run() {
            if (indirizzi.length == 0) return;  // si aspetta l'abbinamento
            caricato = false;
            web.loadUrl(indirizzi[tentativo % indirizzi.length]);
        }
    };

    private void riprova(long ms) {
        handler.removeCallbacks(carica);
        handler.postDelayed(carica, ms);
    }

    @Override protected void onResume() {
        super.onResume();
        if (ritorno != null) ritorno.togli();  // tornati al pannello: la striscia sopra Artemis non serve più
        schermoPieno();
        sbloccaPoi(null);  // schermata di blocco lasciata da lockNow: via subito, così le altre app si aprono
        if (!caricato) riprova(300);
    }

    @Override public void onWindowFocusChanged(boolean f) {
        super.onWindowFocusChanged(f);
        if (f) schermoPieno();
    }

    private void schermoPieno() {
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                | View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
    }

    @Override public void onBackPressed() {
        if (videoAperto != null) { chiudiVideo(); return; }
        // È la schermata Home: Indietro non deve chiuderla
    }

    /** WebView con la tastiera "normale": in orizzontale Android la aprirebbe a tutto schermo
     *  (modalità extract) e la pagina sparirebbe sotto. */
    private static class Vista extends WebView {
        Vista(Context c) { super(c); }
        @Override public InputConnection onCreateInputConnection(EditorInfo info) {
            InputConnection ic = super.onCreateInputConnection(info);
            info.imeOptions |= EditorInfo.IME_FLAG_NO_EXTRACT_UI | EditorInfo.IME_FLAG_NO_FULLSCREEN;
            return ic;
        }
    }

    /** Apre Artemis direttamente sull'app di Apollo che crea il monitor virtuale sul PC.
     *  Il PC e l'app si cercano per nome nell'elenco che Artemis ha già in memoria (serve l'abbinamento). */
    private void secondoMonitor() {
        // Dopo uno spegnimento dello schermo con lockNow resta attiva la schermata di blocco (senza PIN): il
        // pannello ci sta sopra (FLAG_SHOW_WHEN_LOCKED), Artemis no, e Android lo mette in pausa appena aperto
        // (lui allora si chiude da solo). Prima si toglie la schermata di blocco, poi si apre Artemis.
        sbloccaPoi(new Runnable() { @Override public void run() { apriArtemis(); } });
    }

    /** Toglie la schermata di blocco se c'è e non ha PIN (nessun tocco richiesto), poi esegue "dopo". */
    private void sbloccaPoi(final Runnable dopo) {
        KeyguardManager km = (KeyguardManager) getSystemService(KEYGUARD_SERVICE);
        if (km == null || !km.isKeyguardLocked() || km.isKeyguardSecure()) {
            if (dopo != null) dopo.run();
            return;
        }
        km.requestDismissKeyguard(this, new KeyguardManager.KeyguardDismissCallback() {
            @Override public void onDismissSucceeded() { if (dopo != null) dopo.run(); }
            @Override public void onDismissError() { if (dopo != null) dopo.run(); }
            @Override public void onDismissCancelled() { if (dopo != null) dopo.run(); }
        });
    }

    private void apriArtemis() {
        Intent i = new Intent();
        i.setClassName("com.limelight.noir", "com.limelight.ShortcutTrampoline");
        i.putExtra("Name", Applicazione.valore(this, "pc_nome", Config.PC_NOME));
        i.putExtra("AppName", Applicazione.valore(this, "app_monitor", Config.APP_MONITOR));
        String uuid = Applicazione.valore(this, "app_monitor_uuid", Config.APP_MONITOR_UUID);
        if (!uuid.isEmpty()) i.putExtra("AppUUID", uuid);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            startActivity(i);
            ritorno.mostra();
        } catch (Exception e) {
            web.evaluateJavascript("window.dispatchEvent(new CustomEvent('errore-nativo',{detail:'Artemis non trovato'}))", null);
        }
    }

    /** Funzioni chiamate dalla pagina (window.Pannello). */
    private class Ponte {
        @JavascriptInterface public void secondoMonitor() {
            runOnUiThread(new Runnable() { @Override public void run() { Pannello.this.secondoMonitor(); } });
        }

        @JavascriptInterface public void chiudiTastiera() {
            runOnUiThread(new Runnable() {
                @Override public void run() {
                    InputMethodManager imm = (InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
                    imm.hideSoftInputFromWindow(web.getWindowToken(), 0);
                    schermoPieno();
                }
            });
        }

        /** Apre il launcher di LineageOS (con tutte le app); se non c'è, le Impostazioni.
         *  Il tasto Home riporta sempre al pannello. */
        @JavascriptInterface public void apriAndroid() {
            Intent i = new Intent(Intent.ACTION_MAIN);
            i.setClassName("com.android.launcher3", "com.android.launcher3.lineage.LineageLauncher");
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            try {
                startActivity(i);
                return;
            } catch (Exception e) { /* non è LineageOS: si cerca un altro launcher */ }
            Intent home = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME);
            for (android.content.pm.ResolveInfo r : getPackageManager().queryIntentActivities(home, 0)) {
                if (r.activityInfo.packageName.equals(getPackageName())) continue;
                try {
                    startActivity(new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
                            .setClassName(r.activityInfo.packageName, r.activityInfo.name)
                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                    return;
                } catch (Exception e) { /* si prova il prossimo */ }
            }
            startActivity(new Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        }

        /** true se il tablet conosce già un PC (la pagina d'attesa mostra le istruzioni per abbinarlo). */
        @JavascriptInterface public boolean abbinato() { return indirizzi.length > 0; }

        @JavascriptInterface public void luminosita(final float valore) {
            runOnUiThread(new Runnable() {
                @Override public void run() {
                    lucePagina = Math.max(0.02f, Math.min(1f, valore));
                    if (Sentinella.di(Pannello.this).attiva()) return;   // resta al minimo finché sorveglia
                    luce(lucePagina);
                }
            });
        }

        /** Sentinella: accende o spegne la modalità (pulsante 🛡 del pannello). */
        @JavascriptInterface public void sentinella(boolean attiva) {
            Sentinella.di(Pannello.this).attiva(attiva, "tablet");
        }

        @JavascriptInterface public boolean sentinellaAttiva() { return Sentinella.di(Pannello.this).attiva(); }

        /** Clip della sentinella con il lettore di Android: il lettore del WebView su questo tablet non ottiene il
         *  decoder H.264 ("Cannot obtain IOmx service"), VideoView decodifica nel servizio media di sistema. */
        @JavascriptInterface public void video(final String url) {
            runOnUiThread(new Runnable() { @Override public void run() { mostraVideo(url); } });
        }

        /** Jarvis: il microfono del tablet è utilizzabile? */
        @JavascriptInterface public boolean microfono() { return microfonoConcesso(); }

        @JavascriptInterface public void chiediMicrofono() {
            runOnUiThread(new Runnable() {
                @Override public void run() {
                    requestPermissions(new String[] { android.Manifest.permission.RECORD_AUDIO }, 1);
                }
            });
        }

        @JavascriptInterface public void riprova() {
            runOnUiThread(new Runnable() { @Override public void run() { tentativo = 0; Pannello.this.riprova(0); } });
        }
    }
}
