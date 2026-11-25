package it.rikyr.pannello;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RadialGradient;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.os.SystemClock;
import android.view.MotionEvent;
import android.view.View;

/**
 * Indicatore della sentinella sopra il pannello: schermo nero (luce al minimo, la mette il Pannello) e la sfera di
 * Jarvis in versione ALLARME (richiesta dell'utente, 2026-11-25): rossa, due fasci da sirena che girano, nucleo che
 * lampeggia (più lento finché si arma). A sinistra SENTINELLA ATTIVA, cosa usa (telecamera, microfono), notte,
 * registrazione in corso, PC irraggiungibile con gli eventi in coda. La luce cambia di poco (schermo al minimo, la
 * sfera occupa una parte piccola): la sentinella compensa i cambi di luce generali.
 * Si spegne tenendo premuto 2 secondi (un tocco breve mostra solo come si fa).
 */
final class Indicatore extends View {
    private static final long TIENI = 2000;
    private static final int ROSSO = Color.rgb(255, 38, 52);
    private final Paint titolo = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint riga = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint nota = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint barra = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint linea = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint pieno = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF arco = new RectF();
    private final float dp;
    private final long t0 = SystemClock.elapsedRealtime();
    private long premutoDa = 0, aiutoFino = 0;
    private final Runnable spegni = new Runnable() {
        @Override public void run() {
            premutoDa = 0;
            Sentinella.di(getContext()).attiva(false, "tablet");
        }
    };

    Indicatore(Context c) {
        super(c);
        dp = c.getResources().getDisplayMetrics().density;
        setBackgroundColor(Color.BLACK);
        titolo.setColor(ROSSO);
        titolo.setTypeface(Typeface.create(Typeface.DEFAULT, Typeface.BOLD));
        titolo.setTextSize(30 * dp);
        titolo.setLetterSpacing(0.12f);
        riga.setColor(Color.rgb(170, 170, 170));
        riga.setTextSize(17 * dp);
        nota.setColor(Color.rgb(110, 110, 110));
        nota.setTextSize(14 * dp);
        barra.setColor(ROSSO);
        linea.setStyle(Paint.Style.STROKE);
        linea.setColor(ROSSO);
    }

    @Override public boolean onTouchEvent(MotionEvent e) {
        switch (e.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                premutoDa = SystemClock.elapsedRealtime();
                postDelayed(spegni, TIENI);
                invalidate();
                return true;
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL:
                removeCallbacks(spegni);
                if (premutoDa > 0) aiutoFino = SystemClock.elapsedRealtime() + 4000;
                premutoDa = 0;
                invalidate();
                return true;
            default:
                return true;
        }
    }

    @Override protected void onDraw(Canvas c) {
        Sentinella s = Sentinella.di(getContext());
        float w = getWidth(), h = getHeight();
        float t = (SystemClock.elapsedRealtime() - t0) / 1000f;
        boolean armata = s.armata();
        sirena(c, w * 0.74f, h * 0.46f, Math.min(w, h) * 0.30f, t, armata, s.registra());

        float x = 64 * dp, y = h * 0.36f;
        c.drawText("SENTINELLA ATTIVA", x, y, titolo);
        String usa = s.visore() ? "Telecamera davanti · microfono spento: hai il visore"
                : s.percheNonSente().startsWith("rumore spento") ? "Telecamera davanti · rumore spento"
                : s.sente() ? "Telecamera davanti e microfono" : "Telecamera davanti · microfono in apertura";
        if (!s.erroreTelecamera().isEmpty()) usa = "Telecamera non disponibile (" + s.erroreTelecamera() + ")";
        float r = y + 44 * dp;
        c.drawText(usa, x, r, riga);
        r += 30 * dp;
        String stato;
        if (!armata) stato = "Comincio a sorvegliare fra " + s.secondiAllArmamento() + " s";
        else if (s.registra()) stato = "● Registro: movimento o rumore";
        else if (s.eventi() == 0) stato = "Sorveglio: nessun evento finora";
        else stato = "Sorveglio: " + s.eventi() + (s.eventi() == 1 ? " evento registrato" : " eventi registrati");
        c.drawText(stato, x, r, riga);
        if (s.notte()) { r += 30 * dp; c.drawText("Modalità notte: bianco e nero, più luce", x, r, riga); }
        if (!s.pcRaggiungibile()) {
            r += 30 * dp;
            int n = s.inCoda();
            c.drawText("PC non raggiungibile" + (n > 0 ? ": " + n + (n == 1 ? " evento" : " eventi") + " in attesa sul tablet" : ""), x, r, riga);
        }
        long ora = SystemClock.elapsedRealtime();
        String sotto = premutoDa > 0 ? "Tieni premuto…" : ora < aiutoFino ? "Tieni premuto 2 secondi per spegnere la sentinella"
                : "Tieni premuto per spegnerla · foto e video restano sul PC";
        c.drawText(sotto, x, h - 48 * dp, nota);
        if (premutoDa > 0) {
            float f = Math.min(1f, (ora - premutoDa) / (float) TIENI);
            c.drawRect(x, h - 36 * dp, x + (w - 2 * x) * f, h - 32 * dp, barra);
        }
        postInvalidateDelayed(50);
    }

    /** La sfera di Jarvis in allarme: anelli rossi, due fasci da sirena che girano, nucleo che lampeggia. */
    private void sirena(Canvas c, float cx, float cy, float raggio, float t, boolean armata, boolean registra) {
        float velocita = registra ? 5f : armata ? 3.2f : 1.2f;
        float lampo = 0.5f + 0.5f * (float) Math.max(0, Math.sin(t * (registra ? 9 : armata ? 6 : 3)));
        // fasci
        c.save();
        c.rotate((float) Math.toDegrees(t * velocita), cx, cy);
        pieno.setShader(new RadialGradient(cx, cy, raggio * 1.15f, Color.argb(150, 255, 38, 52), Color.argb(0, 255, 38, 52),
                Shader.TileMode.CLAMP));
        arco.set(cx - raggio * 1.15f, cy - raggio * 1.15f, cx + raggio * 1.15f, cy + raggio * 1.15f);
        c.drawArc(arco, -20, 40, true, pieno);
        c.drawArc(arco, 160, 40, true, pieno);
        pieno.setShader(null);
        c.restore();
        // tacche esterne che girano piano
        linea.setStrokeWidth(1.5f * dp);
        linea.setAlpha(120);
        c.save();
        c.rotate(t * 8, cx, cy);
        for (int i = 0; i < 72; i++) {
            c.drawLine(cx, cy - raggio, cx, cy - raggio + (i % 6 == 0 ? 12 : 6) * dp, linea);
            c.rotate(5, cx, cy);
        }
        c.restore();
        // archi segmentati in senso opposto
        linea.setStrokeWidth(4 * dp);
        linea.setAlpha(200);
        arco.set(cx - raggio * 0.85f, cy - raggio * 0.85f, cx + raggio * 0.85f, cy + raggio * 0.85f);
        for (int k = 0; k < 3; k++) c.drawArc(arco, -t * 60 + k * 120, 70, false, linea);
        linea.setStrokeWidth(1.5f * dp);
        linea.setAlpha(140);
        arco.set(cx - raggio * 0.72f, cy - raggio * 0.72f, cx + raggio * 0.72f, cy + raggio * 0.72f);
        for (int k = 0; k < 6; k++) c.drawArc(arco, t * 90 + k * 60, 30, false, linea);
        // nucleo
        float rn = raggio * (0.30f + 0.06f * lampo);
        pieno.setShader(new RadialGradient(cx, cy, rn * 1.7f,
                new int[] { Color.argb((int) (150 + 100 * lampo), 255, 220, 220), Color.argb((int) (120 + 110 * lampo), 255, 38, 52),
                        Color.argb(0, 255, 38, 52) }, new float[] { 0f, 0.35f, 1f }, Shader.TileMode.CLAMP));
        c.drawCircle(cx, cy, rn * 1.7f, pieno);
        pieno.setShader(null);
        linea.setStrokeWidth(2 * dp);
        linea.setAlpha(230);
        c.drawCircle(cx, cy, rn, linea);
    }
}
