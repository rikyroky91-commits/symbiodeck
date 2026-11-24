package it.rikyr.pannello;

import android.content.Context;
import android.graphics.ImageFormat;
import android.graphics.SurfaceTexture;
import android.hardware.camera2.CameraAccessException;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.media.Image;
import android.media.ImageReader;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Size;
import android.view.Surface;

import java.nio.ByteBuffer;
import java.util.Arrays;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * Una foto con la telecamera davanti del tablet (dalla parte dello schermo), per Jarvis ("guarda questa scheda madre"): Jarvis chiede
 * prima il permesso all'utente, poi manda il comando {"foto": id}. Si apre la telecamera, si lascia lavorare
 * esposizione e messa a fuoco per ~1 s su una superficie finta, si scatta un JPEG (~1920 px) e si chiude tutto.
 * Bloccante: va chiamata da un thread a parte.
 */
final class Fotocamera {
    private Fotocamera() {}

    static byte[] scatta(Context contesto) throws Exception {
        CameraManager cm = (CameraManager) contesto.getSystemService(Context.CAMERA_SERVICE);
        String id = scegli(cm);
        if (id == null) throw new IllegalStateException("nessuna telecamera");
        StreamConfigurationMap mappa = cm.getCameraCharacteristics(id)
                .get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
        Size misura = misura(mappa.getOutputSizes(ImageFormat.JPEG));

        HandlerThread filo = new HandlerThread("fotocamera");
        filo.start();
        Handler h = new Handler(filo.getLooper());
        ImageReader lettore = ImageReader.newInstance(misura.getWidth(), misura.getHeight(), ImageFormat.JPEG, 2);
        SurfaceTexture finta = new SurfaceTexture(0);
        finta.setDefaultBufferSize(640, 480);
        Surface anteprima = new Surface(finta);
        final CameraDevice[] dispositivo = new CameraDevice[1];
        final CameraCaptureSession[] sessione = new CameraCaptureSession[1];
        final byte[][] foto = new byte[1][];
        final Exception[] errore = new Exception[1];
        final CountDownLatch aperta = new CountDownLatch(1), pronta = new CountDownLatch(1), scattata = new CountDownLatch(1);
        try {
            cm.openCamera(id, new CameraDevice.StateCallback() {
                @Override public void onOpened(CameraDevice d) { dispositivo[0] = d; aperta.countDown(); }
                @Override public void onDisconnected(CameraDevice d) { d.close(); aperta.countDown(); }
                @Override public void onError(CameraDevice d, int e) {
                    errore[0] = new IllegalStateException("telecamera occupata o non disponibile (" + e + ")");
                    d.close();
                    aperta.countDown();
                }
            }, h);
            if (!aperta.await(5, TimeUnit.SECONDS) || dispositivo[0] == null) {
                throw errore[0] != null ? errore[0] : new IllegalStateException("la telecamera non si apre");
            }
            dispositivo[0].createCaptureSession(Arrays.asList(anteprima, lettore.getSurface()),
                    new CameraCaptureSession.StateCallback() {
                        @Override public void onConfigured(CameraCaptureSession s) { sessione[0] = s; pronta.countDown(); }
                        @Override public void onConfigureFailed(CameraCaptureSession s) { pronta.countDown(); }
                    }, h);
            if (!pronta.await(5, TimeUnit.SECONDS) || sessione[0] == null) throw new IllegalStateException("sessione della telecamera non pronta");

            // anteprima per ~1 s: esposizione, bilanciamento del bianco e messa a fuoco si assestano
            CaptureRequest.Builder prova = dispositivo[0].createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW);
            prova.addTarget(anteprima);
            prova.set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO);
            prova.set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE);
            sessione[0].setRepeatingRequest(prova.build(), null, h);
            Thread.sleep(1100);

            lettore.setOnImageAvailableListener(r -> {
                Image im = r.acquireLatestImage();
                if (im == null) return;
                try {
                    ByteBuffer b = im.getPlanes()[0].getBuffer();
                    byte[] dati = new byte[b.remaining()];
                    b.get(dati);
                    foto[0] = dati;
                } finally {
                    im.close();
                    scattata.countDown();
                }
            }, h);
            CaptureRequest.Builder scatto = dispositivo[0].createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE);
            scatto.addTarget(lettore.getSurface());
            scatto.set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO);
            scatto.set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE);
            scatto.set(CaptureRequest.JPEG_QUALITY, (byte) 88);
            sessione[0].stopRepeating();
            sessione[0].capture(scatto.build(), null, h);
            if (!scattata.await(6, TimeUnit.SECONDS) || foto[0] == null) throw new IllegalStateException("la foto non è arrivata");
            return foto[0];
        } finally {
            try { if (sessione[0] != null) sessione[0].close(); } catch (Exception ignorato) { }
            if (dispositivo[0] != null) dispositivo[0].close();
            lettore.close();
            anteprima.release();
            finta.release();
            filo.quitSafely();
        }
    }

    /** La telecamera davanti (dalla parte dello schermo: il tablet sta in piedi di fronte all'utente, che le mostra
     *  le cose), se no la prima che c'è. Richiesta dell'utente, 2026-11-24: la posteriore guardava il muro. */
    private static String scegli(CameraManager cm) throws CameraAccessException {
        String prima = null;
        for (String id : cm.getCameraIdList()) {
            if (prima == null) prima = id;
            Integer verso = cm.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING);
            if (verso != null && verso == CameraCharacteristics.LENS_FACING_FRONT) return id;
        }
        return prima;
    }

    /** La misura più vicina a 1920 px di lato lungo senza superarla di molto (Claude guarda fino a ~1568 px). */
    private static Size misura(Size[] misure) {
        Size migliore = misure[0];
        int meglio = Integer.MAX_VALUE;
        for (Size s : misure) {
            int lato = Math.max(s.getWidth(), s.getHeight());
            int distanza = Math.abs(lato - 1920) + (lato < 1280 ? 4000 : 0);
            if (distanza < meglio) { meglio = distanza; migliore = s; }
        }
        return migliore;
    }
}
