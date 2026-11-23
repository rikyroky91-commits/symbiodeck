// App per Apple Watch: un tocco, detti la richiesta, l'orologio ti legge la risposta di Jarvis.
import SwiftUI

@main
struct JarvisWatchApp: App {
    init() { Collegamento.condiviso.avvia() }

    var body: some Scene {
        WindowGroup { SchermataOrologio() }
    }
}

struct SchermataOrologio: View {
    @StateObject private var conversazione = Conversazione()

    var body: some View {
        ScrollView {
            VStack(spacing: 8) {
                pulsante
                stato
                ForEach(ultimi) { m in
                    RigaOrologio(messaggio: m)
                }
            }
        }
    }

    private var ultimi: [Messaggio] {
        Array(conversazione.messaggi.suffix(6).reversed())
    }

    private var pulsante: some View {
        TextFieldLink(prompt: Text("Dimmi pure")) {
            EtichettaParla()
        } onSubmit: { testo in
            conversazione.zitto()
            conversazione.invia(testo)
        }
        .buttonStyle(.borderedProminent)
        .tint(Color.cyan.opacity(0.35))
    }

    @ViewBuilder
    private var stato: some View {
        if conversazione.inAttesa {
            ProgressView()
        } else if conversazione.lavoro {
            Text(conversazione.emergenza ? "Cervello locale" : "Sta lavorando…")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        if !Impostazioni.completa {
            Text("Apri Jarvis sull'iPhone e salva indirizzo e chiave.")
                .font(.footnote)
                .foregroundStyle(.orange)
                .multilineTextAlignment(.center)
        }
    }
}

struct EtichettaParla: View {
    var body: some View {
        VStack(spacing: 4) {
            Image(systemName: "waveform.circle.fill")
                .font(.system(size: 44))
                .foregroundStyle(Color.cyan)
            Text("Parla a Jarvis").font(.headline)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 6)
    }
}

struct RigaOrologio: View {
    let messaggio: Messaggio

    private var daUtente: Bool { messaggio.chi == "utente" }

    private var colore: Color {
        if daUtente { return .secondary }
        return messaggio.chi == "errore" ? .red : .primary
    }

    var body: some View {
        Text(messaggio.testo)
            .font(daUtente ? .footnote : .body)
            .foregroundStyle(colore)
            .frame(maxWidth: .infinity, alignment: daUtente ? .trailing : .leading)
    }
}
