package it.rikyr.pannello;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.os.SystemClock;
import android.view.MotionEvent;
import android.view.View;

/**
 * Indicatore della sentinella sopra il pannello: schermo nero (luce al minimo, la mette il Pannello), un punto rosso
 * fisso e la scritta SENTINELLA ATTIVA, con cosa sta usando (telecamera, microfono) e il conto alla rovescia
 * dell'armamento. Niente animazioni: una luce che cambia la vedrebbe anche la telecamera.
 * Si spegne tenendo premuto 2 secondi (un tocco breve mostra solo come si fa).
 */
final class Indicatore extends View {
    private static final long TIENI = 2000;
    private final Paint punto = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint titolo = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint riga = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint nota = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint barra = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final float dp;
    private long premutoDa = 0, aiutoFino = 0;
    private final Runnable spegni = new Runnable() {
        @Override public void run() {
            premutoDa = 0;
            Sentinella.di(getContext()).attiva(false, "tablet");
        }
    };
    private final Runnable aggiorna = new Runnable() {
        @Override public void run() {
            invalidate();
            if (getVisibility() == VISIBLE) postDelayed(this, premutoDa > 0 ? 100 : 1000);
        }
    };

    Indicatore(Context c) {
        super(c);
        dp = c.getResources().getDisplayMetrics().density;
        setBackgroundColor(Color.BLACK);
        punto.setColor(Color.rgb(230, 40, 40));
        titolo.setColor(Color.rgb(230, 60, 60));
        titolo.setTypeface(Typeface.create(Typeface.DEFAULT, Typeface.BOLD));
        titolo.setTextSize(30 * dp);
        titolo.setLetterSpacing(0.12f);
        riga.setColor(Color.rgb(170, 170, 170));
        riga.setTextSize(17 * dp);
        nota.setColor(Color.rgb(110, 110, 110));
        nota.setTextSize(14 * dp);
        barra.setColor(Color.rgb(230, 60, 60));
        setKeepScreenOn(false);
    }

    @Override protected void onVisibilityChanged(View v, int vis) {
        super.onVisibilityChanged(v, vis);
        removeCallbacks(aggiorna);
        if (vis == VISIBLE) post(aggiorna);
    }

    @Override public boolean onTouchEvent(MotionEvent e) {
        switch (e.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                premutoDa = SystemClock.elapsedRealtime();
                postDelayed(spegni, TIENI);
                removeCallbacks(aggiorna);
                post(aggiorna);
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
        float x = 64 * dp, y = h * 0.38f;
        c.drawCircle(x + 12 * dp, y - 11 * dp, 12 * dp, punto);
        c.drawText("SENTINELLA ATTIVA", x + 40 * dp, y, titolo);
        String usa = s.visore() ? "Telecamera davanti · microfono spento: hai il visore"
                : s.percheNonSente().startsWith("rumore spento") ? "Telecamera davanti · rumore spento nelle impostazioni"
                : s.sente() ? "Telecamera davanti e microfono" : "Telecamera davanti · microfono in apertura";
        if (!s.erroreTelecamera().isEmpty()) usa = "Telecamera non disponibile (" + s.erroreTelecamera() + ")";
        c.drawText(usa, x, y + 42 * dp, riga);
        String stato;
        if (!s.armata()) stato = "Comincio a sorvegliare fra " + s.secondiAllArmamento() + " s";
        else if (s.eventi() == 0) stato = "Sorveglio: nessun evento finora";
        else stato = "Sorveglio: " + s.eventi() + (s.eventi() == 1 ? " evento registrato" : " eventi registrati");
        c.drawText(stato, x, y + 72 * dp, riga);
        long ora = SystemClock.elapsedRealtime();
        String sotto = premutoDa > 0 ? "Tieni premuto…" : ora < aiutoFino ? "Tieni premuto 2 secondi per spegnere la sentinella"
                : "Tieni premuto per spegnerla · foto e audio restano sul PC";
        c.drawText(sotto, x, h - 48 * dp, nota);
        if (premutoDa > 0) {
            float f = Math.min(1f, (ora - premutoDa) / (float) TIENI);
            c.drawRect(x, h - 36 * dp, x + (w - 2 * x) * f, h - 32 * dp, barra);
        }
    }
}
