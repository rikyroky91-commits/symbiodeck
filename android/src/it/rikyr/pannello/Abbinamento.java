package it.rikyr.pannello;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.net.URL;

/**
 * Trova il PC da solo, senza ricompilare l'APK. Ogni 3 s:
 *  1. chiede la chiave a http://127.0.0.1:47800/api/abbina (tablet col cavo USB e adb reverse attivo);
 *  2. manda in broadcast "PANNELLO-PC?" sulla porta UDP 47801 e aspetta la risposta del server.
 * Il server dà la chiave solo con l'abbinamento aperto (primo avvio o pulsante "Abbina un tablet" sul PC).
 * Senza chiave la risposta serve comunque a ritrovare il PC già abbinato se il suo IP è cambiato.
 */
final class Abbinamento extends Thread {
    interface Esito { void riuscito(); }

    private static final String TAG = "PannelloAbbinamento";
    private static final int PORTA = 47800;
    private final Context contesto;
    private final Esito esito;
    private volatile boolean attivo = true;

    Abbinamento(Context c, Esito esito) {
        super("abbinamento");
        setDaemon(true);
        this.contesto = c.getApplicationContext();
        this.esito = esito;
    }

    void ferma() { attivo = false; interrupt(); }

    @Override public void run() {
        while (attivo) {
            if (provaUsb() || provaRete()) {
                if (attivo) esito.riuscito();
                return;
            }
            try { Thread.sleep(3000); } catch (InterruptedException e) { return; }
        }
    }

    private boolean provaUsb() {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL("http://127.0.0.1:" + PORTA + "/api/abbina").openConnection();
            c.setConnectTimeout(1500);
            c.setReadTimeout(3000);
            if (c.getResponseCode() != 200) return false;
            return salva(new JSONObject(leggi(c.getInputStream())), "127.0.0.1", true);
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    private boolean provaRete() {
        DatagramSocket s = null;
        try {
            s = new DatagramSocket();
            s.setBroadcast(true);
            s.setSoTimeout(2000);
            byte[] domanda = "PANNELLO-PC?".getBytes("UTF-8");
            s.send(new DatagramPacket(domanda, domanda.length, InetAddress.getByName("255.255.255.255"), PORTA + 1));
            byte[] buf = new byte[1024];
            DatagramPacket risposta = new DatagramPacket(buf, buf.length);
            s.receive(risposta);
            JSONObject j = new JSONObject(new String(risposta.getData(), 0, risposta.getLength(), "UTF-8"));
            return salva(j, risposta.getAddress().getHostAddress(), false);
        } catch (SocketTimeoutException e) {
            return false;
        } catch (Exception e) {
            Log.d(TAG, "broadcast: " + e);
            return false;
        } finally {
            if (s != null) s.close();
        }
    }

    /** Salva gli indirizzi del PC. Senza chiave nella risposta vale solo se il tablet ne ha già una. */
    private boolean salva(JSONObject j, String ip, boolean usb) {
        if (j.optInt("pannello", 0) != 1) return false;
        SharedPreferences p = contesto.getSharedPreferences("pannello", Context.MODE_PRIVATE);
        String token = j.optString("token", p.getString("token", ""));
        if (token.isEmpty()) return false;
        int porta = j.optInt("porta", PORTA);
        SharedPreferences.Editor e = p.edit()
                .putString("token", token)
                .putString("pc_nome", j.optString("pc", ""))
                .putString("app_monitor", j.optString("app_monitor", "Virtual Display"))
                .putString("app_monitor_uuid", j.optString("app_monitor_uuid", ""))
                .putString("url_usb", "http://127.0.0.1:" + porta + "/?t=" + token);
        if (!usb) e.putString("url_rete", "http://" + ip + ":" + porta + "/?t=" + token);
        e.commit();
        Log.i(TAG, "PC trovato: " + (usb ? "USB" : ip));
        return true;
    }

    private static String leggi(InputStream in) throws java.io.IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        byte[] buf = new byte[1024];
        for (int n; (n = in.read(buf)) > 0; ) b.write(buf, 0, n);
        return b.toString("UTF-8");
    }
}
