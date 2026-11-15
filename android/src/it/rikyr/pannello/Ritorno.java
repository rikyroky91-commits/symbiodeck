package it.rikyr.pannello;

import android.content.Context;
import android.content.Intent;
import android.graphics.PixelFormat;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;

/**
 * Striscia invisibile sul bordo sinistro, sopra Artemis: scorrendo verso destra si torna al pannello.
 * Serve il permesso "mostra sopra le altre app" (appops SYSTEM_ALERT_WINDOW, dato con adb).
 */
final class Ritorno {
    private static final int LARGHEZZA_DP = 18;
    private static final int SOGLIA_DP = 120;

    private final Context ctx;
    private final WindowManager wm;
    private View striscia;

    Ritorno(Context ctx) {
        this.ctx = ctx.getApplicationContext();
        wm = (WindowManager) this.ctx.getSystemService(Context.WINDOW_SERVICE);
    }

    boolean attivo() { return striscia != null; }

    void mostra() {
        if (striscia != null) return;
        final float dp = ctx.getResources().getDisplayMetrics().density;
        striscia = new View(ctx);
        striscia.setOnTouchListener(new View.OnTouchListener() {
            float x0;
            @Override public boolean onTouch(View v, MotionEvent e) {
                switch (e.getActionMasked()) {
                    case MotionEvent.ACTION_DOWN:
                        x0 = e.getRawX();
                        return true;
                    case MotionEvent.ACTION_MOVE:
                        if (e.getRawX() - x0 > SOGLIA_DP * dp) torna();
                        return true;
                    default:
                        return true;
                }
            }
        });
        WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                Math.round(LARGHEZZA_DP * dp), WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT);
        lp.gravity = Gravity.START | Gravity.TOP;
        try {
            wm.addView(striscia, lp);
        } catch (RuntimeException e) {  // permesso mancante: si torna col tasto Home
            striscia = null;
        }
    }

    void togli() {
        if (striscia == null) return;
        try { wm.removeView(striscia); } catch (RuntimeException e) { /* già tolta */ }
        striscia = null;
    }

    private void torna() {
        togli();
        ctx.startActivity(new Intent(ctx, Pannello.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT));
    }
}
