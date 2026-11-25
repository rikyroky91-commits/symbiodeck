package it.rikyr.pannello;

import android.media.Image;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaFormat;
import android.media.MediaMuxer;
import android.util.Log;

import java.io.File;
import java.nio.ByteBuffer;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;

/**
 * Video della sentinella: i fotogrammi della telecamera (già elaborati: bianco e nero e più luce di notte) vanno
 * sempre in un encoder H.264 e i pacchetti degli ultimi ANELLO_US restano in memoria. Così quando scatta un evento la
 * clip comincia qualche secondo PRIMA (come una Ring). Alla fine dell'evento i pacchetti e l'audio (PCM 16 kHz, che
 * qui diventa AAC) si montano in un MP4. Un fotogramma chiave al secondo: la clip parte da quello giusto.
 * Tutto dal thread della telecamera, tranne monta() (thread a parte, alla fine dell'evento).
 */
final class Video {
    private static final String TAG = "PannelloSentinella";
    static final long ANELLO_US = 9_000_000;

    static final class Pacchetto {
        final byte[] dati;
        final long pts;
        final boolean chiave;
        Pacchetto(byte[] d, long p, boolean k) { dati = d; pts = p; chiave = k; }
    }

    interface Uscita { void pacchetto(Pacchetto p); }

    private MediaCodec cod;
    private MediaFormat formato;
    private final ArrayDeque<Pacchetto> anello = new ArrayDeque<>();
    private final MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
    private int w, h;
    private long ultimoPts = 0;
    Uscita uscita;                    // l'evento in corso riceve anche i pacchetti nuovi

    /** true se l'encoder è partito (altrimenti la sentinella fa foto e fotogrammi JPEG come prima). */
    boolean avvia(int larghezza, int altezza) {
        w = larghezza;
        h = altezza;
        try {
            MediaFormat f = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, w, h);
            f.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible);
            f.setInteger(MediaFormat.KEY_BIT_RATE, 1_200_000);
            f.setInteger(MediaFormat.KEY_FRAME_RATE, 15);
            f.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1);
            cod = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC);
            cod.configure(f, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            cod.start();
            Log.i(TAG, "video: encoder " + cod.getName() + " " + w + "x" + h);
            return true;
        } catch (Exception e) {
            Log.w(TAG, "video non disponibile: " + e);
            ferma();
            return false;
        }
    }

    boolean attivo() { return cod != null; }

    void ferma() {
        if (cod != null) {
            try { cod.stop(); } catch (Exception e) { /* già fermo */ }
            try { cod.release(); } catch (Exception e) { /* già rilasciato */ }
        }
        cod = null;
        synchronized (this) { anello.clear(); }
    }

    /** Un fotogramma: y (w*h), u e v (w/2*h/2, planari). Se l'encoder è pieno il fotogramma si salta. */
    void codifica(byte[] y, byte[] u, byte[] v, long ptsUs) throws Exception {
        if (cod == null) return;
        if (ptsUs <= ultimoPts) ptsUs = ultimoPts + 1000;
        int i = cod.dequeueInputBuffer(0);
        if (i >= 0) {
            Image im = cod.getInputImage(i);
            if (im == null) throw new IllegalStateException("l'encoder non dà immagini di ingresso");
            Image.Plane[] p = im.getPlanes();
            scrivi(p[0], y, w, h);
            scrivi(p[1], u, w / 2, h / 2);
            scrivi(p[2], v, w / 2, h / 2);
            cod.queueInputBuffer(i, 0, w * h * 3 / 2, ptsUs, 0);
            ultimoPts = ptsUs;
        }
        svuota();
    }

    private static void scrivi(Image.Plane p, byte[] src, int pw, int ph) {
        ByteBuffer b = p.getBuffer();
        int riga = p.getRowStride(), passo = p.getPixelStride();
        if (passo == 1) {
            for (int r = 0; r < ph; r++) {
                b.position(r * riga);
                b.put(src, r * pw, pw);
            }
        } else {
            for (int r = 0; r < ph; r++) {
                int base = r * riga, s = r * pw;
                for (int c = 0; c < pw; c++) b.put(base + c * passo, src[s + c]);
            }
        }
    }

    private void svuota() {
        while (true) {
            int o = cod.dequeueOutputBuffer(info, 0);
            if (o == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) { formato = cod.getOutputFormat(); continue; }
            if (o < 0) return;
            if ((info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0 && info.size > 0) {
                ByteBuffer b = cod.getOutputBuffer(o);
                byte[] d = new byte[info.size];
                b.position(info.offset);
                b.get(d);
                Pacchetto pk = new Pacchetto(d, info.presentationTimeUs, (info.flags & MediaCodec.BUFFER_FLAG_KEY_FRAME) != 0);
                synchronized (this) {
                    anello.addLast(pk);
                    // si tengono gli ultimi ANELLO_US, sempre a partire da un fotogramma chiave
                    while (!anello.isEmpty() && pk.pts - anello.peekFirst().pts > ANELLO_US) anello.pollFirst();
                    while (!anello.isEmpty() && !anello.peekFirst().chiave) anello.pollFirst();
                }
                Uscita u = uscita;
                if (u != null) u.pacchetto(pk);
            }
            cod.releaseOutputBuffer(o, false);
        }
    }

    /** I pacchetti dal fotogramma chiave più vicino prima di "daUs" (o il primo che c'è) fino ad adesso. */
    synchronized List<Pacchetto> da(long daUs) {
        List<Pacchetto> tutti = new ArrayList<>(anello);
        int inizio = -1;
        for (int i = 0; i < tutti.size(); i++) {
            Pacchetto p = tutti.get(i);
            if (p.chiave && (p.pts <= daUs || inizio < 0)) inizio = i;
            if (p.pts > daUs && inizio >= 0) break;
        }
        return inizio < 0 ? new ArrayList<Pacchetto>() : new ArrayList<>(tutti.subList(inizio, tutti.size()));
    }

    MediaFormat formato() { return formato; }

    // ------------------------------------------------------------------ montaggio MP4 (video + audio AAC)
    /** Scrive l'MP4. pcm: 16 kHz mono, il primo campione all'istante pcmInizioUs (stesso orologio del video). */
    static void monta(File file, MediaFormat fv, List<Pacchetto> video, byte[] pcm, long pcmInizioUs, int rotazione)
            throws Exception {
        if (fv == null || video.isEmpty()) throw new IllegalStateException("niente video");
        List<Pacchetto> audio = new ArrayList<>();
        MediaFormat fa = null;
        if (pcm != null && pcm.length >= 32000) {
            try {
                Object[] r = aac(pcm, pcmInizioUs);
                fa = (MediaFormat) r[0];
                @SuppressWarnings("unchecked") List<Pacchetto> a = (List<Pacchetto>) r[1];
                audio = a;
            } catch (Exception e) {
                Log.w(TAG, "audio AAC non riuscito: " + e);
                fa = null;
            }
        }
        long base = video.get(0).pts;
        MediaMuxer m = new MediaMuxer(file.getPath(), MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
        try {
            m.setOrientationHint(rotazione);
            int tv = m.addTrack(fv);
            int ta = fa != null ? m.addTrack(fa) : -1;
            m.start();
            MediaCodec.BufferInfo bi = new MediaCodec.BufferInfo();
            int iv = 0, ia = 0;
            while (iv < video.size() || (ta >= 0 && ia < audio.size())) {
                boolean prendiVideo = ta < 0 || ia >= audio.size()
                        || (iv < video.size() && video.get(iv).pts <= audio.get(ia).pts);
                Pacchetto p = prendiVideo ? video.get(iv++) : audio.get(ia++);
                if (p.pts < base) continue;   // audio di prima dell'inizio del video
                bi.set(0, p.dati.length, p.pts - base, p.chiave ? MediaCodec.BUFFER_FLAG_KEY_FRAME : 0);
                m.writeSampleData(prendiVideo ? tv : ta, ByteBuffer.wrap(p.dati), bi);
            }
            m.stop();
        } finally {
            m.release();
        }
    }

    /** PCM 16 kHz mono -> AAC LC 32 kbit/s. Restituisce {formato, pacchetti}. */
    private static Object[] aac(byte[] pcm, long inizioUs) throws Exception {
        MediaFormat f = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_AAC, 16000, 1);
        f.setInteger(MediaFormat.KEY_AAC_PROFILE, MediaCodecInfo.CodecProfileLevel.AACObjectLC);
        f.setInteger(MediaFormat.KEY_BIT_RATE, 32000);
        f.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 4096);
        MediaCodec c = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_AAC);
        List<Pacchetto> out = new ArrayList<>();
        MediaFormat formato = null;
        try {
            c.configure(f, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            c.start();
            MediaCodec.BufferInfo bi = new MediaCodec.BufferInfo();
            int pos = 0;
            boolean finito = false, fine = false;
            long limite = System.currentTimeMillis() + 15_000;
            while (!fine && System.currentTimeMillis() < limite) {
                if (!finito) {
                    int i = c.dequeueInputBuffer(10_000);
                    if (i >= 0) {
                        ByteBuffer b = c.getInputBuffer(i);
                        int n = Math.min(Math.min(b.remaining(), 2048), pcm.length - pos);
                        long pts = inizioUs + (pos / 2) * 1_000_000L / 16000;
                        if (n <= 0) {
                            c.queueInputBuffer(i, 0, 0, pts, MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                            finito = true;
                        } else {
                            b.put(pcm, pos, n);
                            c.queueInputBuffer(i, 0, n, pts, 0);
                            pos += n;
                        }
                    }
                }
                int o = c.dequeueOutputBuffer(bi, 10_000);
                if (o == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) { formato = c.getOutputFormat(); continue; }
                if (o < 0) continue;
                if ((bi.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0 && bi.size > 0) {
                    ByteBuffer b = c.getOutputBuffer(o);
                    byte[] d = new byte[bi.size];
                    b.position(bi.offset);
                    b.get(d);
                    out.add(new Pacchetto(d, bi.presentationTimeUs, true));
                }
                if ((bi.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) fine = true;
                c.releaseOutputBuffer(o, false);
            }
        } finally {
            try { c.stop(); } catch (Exception e) { /* già fermo */ }
            c.release();
        }
        if (formato == null) throw new IllegalStateException("formato AAC sconosciuto");
        return new Object[] { formato, out };
    }
}
