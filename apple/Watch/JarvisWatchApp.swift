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
                TextFieldLink(prompt: Text("Dimmi pure")) {
                    VStack(spacing: 4) {
                        Image(systemName: "waveform.circle.fill").font(.system(size: 44)).foregroundStyle(.cyan)
                        Text("Parla a Jarvis").font(.headline)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
                } onSubmit: { testo in
                    conversazione.zitto()
                    conversazione.invia(testo)
                }
                .buttonStyle(.borderedProminent)
                .tint(.cyan.opacity(0.35))

                if conversazione.inAttesa {
                    ProgressView()
                } else if conversazione.lavoro {
                    Text(conversazione.emergenza ? "Cervello locale" : "Sta lavorando…")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                if !Impostazioni.completa {
                    Text("Apri Jarvis sull'iPhone e salva indirizzo e chiave.")
                        .font(.footnote).foregroundStyle(.orange).multilineTextAlignment(.center)
                }
                ForEach(conversazione.messaggi.suffix(6).reversed()) { m in
                    Text(m.testo)
                        .font(m.chi == "utente" ? .footnote : .body)
                        .foregroundStyle(m.chi == "utente" ? .secondary : (m.chi == "errore" ? .red : .primary))
                        .frame(maxWidth: .infinity, alignment: m.chi == "utente" ? .trailing : .leading)
                }
            }
        }
    }
}
