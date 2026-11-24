package it.rikyr.jarvisxr;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;

/** Apre Jarvis XR (la pagina del PC) nel Browser del Quest e si chiude: è solo l'icona nella Libreria. */
public class Apri extends Activity {
    @Override
    protected void onCreate(Bundle stato) {
        super.onCreate(stato);
        Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(Config.URL)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        i.setPackage("com.oculus.browser");
        try {
            startActivity(i);
        } catch (Exception e) {   // senza il Browser del Quest: il browser predefinito
            startActivity(i.setPackage(null));
        }
        finish();
    }
}
