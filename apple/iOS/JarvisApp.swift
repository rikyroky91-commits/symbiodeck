// App per iPhone: conversazione con Jarvis, pulsante per parlare, impostazioni (che passano anche all'orologio).
import Speech
import SwiftUI

@main
struct JarvisApp: App {
    init() { Collegamento.condiviso.avvia() }

    var body: some Scene {
        WindowGroup { SchermataPrincipale() }
    }
}

// MARK: - Dettatura (riconoscimento vocale italiano di iOS)

@MainActor
final class Dettatura: ObservableObject {
    @Published var testo = ""
    @Published var attiva = false
    @Published var errore: String?

    private let riconoscitore = SFSpeechRecognizer(locale: Locale(identifier: "it-IT"))
    private let motore = AVAudioEngine()
    private var richiesta: SFSpeechAudioBufferRecognitionRequest?
    private var compito: SFSpeechRecognitionTask?

    func avvia() {
        errore = nil
        SFSpeechRecognizer.requestAuthorization { stato in
            Task { @MainActor in
                guard stato == .authorized else { self.errore = "Permesso di riconoscimento vocale negato."; return }
                AVAudioApplication.requestRecordPermission { ok in
                    Task { @MainActor in
                        if ok { self.inizia() } else { self.errore = "Permesso del microfono negato." }
                    }
                }
            }
        }
    }

    private func inizia() {
        guard let riconoscitore, riconoscitore.isAvailable else { errore = "Riconoscimento vocale non disponibile."; return }
        testo = ""
        do {
            let sessione = AVAudioSession.sharedInstance()
            try sessione.setCategory(.record, mode: .measurement, options: .duckOthers)
            try sessione.setActive(true, options: .notifyOthersOnDeactivation)
            let r = SFSpeechAudioBufferRecognitionRequest()
            r.shouldReportPartialResults = true
            richiesta = r
            let ingresso = motore.inputNode
            let formato = ingresso.outputFormat(forBus: 0)
            ingresso.installTap(onBus: 0, bufferSize: 1024, format: formato) { buffer, _ in r.append(buffer) }
            motore.prepare()
            try motore.start()
            attiva = true
            compito = riconoscitore.recognitionTask(with: r) { risultato, _ in
                guard let risultato else { return }
                Task { @MainActor in self.testo = risultato.bestTranscription.formattedString }
            }
        } catch {
            errore = error.localizedDescription
            ferma()
        }
    }

    /// Ferma l'ascolto e restituisce il testo capito.
    @discardableResult
    func ferma() -> String {
        if motore.isRunning {
            motore.stop()
            motore.inputNode.removeTap(onBus: 0)
        }
        richiesta?.endAudio()
        compito?.cancel()
        richiesta = nil
        compito = nil
        attiva = false
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        return testo
    }
}

// MARK: - Schermata principale

struct SchermataPrincipale: View {
    @StateObject private var conversazione = Conversazione()
    @StateObject private var dettatura = Dettatura()
    @State private var scritto = ""
    @State private var impostazioni = !Impostazioni.completa

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                ScrollViewReader { scorrimento in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 10) {
                            ForEach(conversazione.messaggi) { m in
                                Bolla(messaggio: m).id(m.id)
                            }
                            if conversazione.inAttesa { ProgressView().padding(.leading, 8) }
                        }
                        .padding()
                    }
                    .onChange(of: conversazione.messaggi.count) {
                        if let ultimo = conversazione.messaggi.last { scorrimento.scrollTo(ultimo.id, anchor: .bottom) }
                    }
                }
                if conversazione.lavoro || conversazione.emergenza {
                    Text(conversazione.emergenza ? "Modalità emergenza: cervello locale" : "Jarvis sta lavorando…")
                        .font(.footnote).foregroundStyle(.secondary).padding(.bottom, 4)
                }
                if dettatura.attiva {
                    Text(dettatura.testo.isEmpty ? "Ti ascolto…" : dettatura.testo)
                        .font(.callout).padding(.horizontal).padding(.bottom, 6)
                }
                if let c = conversazione.conferma {
                    VStack(spacing: 6) {
                        Text("Jarvis chiede conferma: \(c.descrizione)")
                            .font(.footnote).multilineTextAlignment(.center)
                        HStack(spacing: 16) {
                            Button("Sì, procedi") { conversazione.rispondi(true) }.buttonStyle(.borderedProminent).tint(.red)
                            Button("No") { conversazione.rispondi(false) }.buttonStyle(.bordered)
                        }
                    }
                    .padding(8)
                }
                if let errore = dettatura.errore {
                    Text(errore).font(.footnote).foregroundStyle(.red).padding(.bottom, 4)
                }
                barra
            }
            .navigationTitle("Jarvis")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { impostazioni = true } label: { Image(systemName: "gearshape") }
                }
                ToolbarItem(placement: .topBarLeading) {
                    Button { conversazione.zitto() } label: { Image(systemName: "speaker.slash") }
                }
            }
            .sheet(isPresented: $impostazioni) { SchermataImpostazioni() }
        }
    }

    private var barra: some View {
        HStack(spacing: 10) {
            TextField("Scrivi a Jarvis…", text: $scritto)
                .textFieldStyle(.roundedBorder)
                .submitLabel(.send)
                .onSubmit { conversazione.invia(scritto); scritto = "" }
            Button {
                if dettatura.attiva {
                    conversazione.invia(dettatura.ferma())
                } else {
                    conversazione.zitto()
                    dettatura.avvia()
                }
            } label: {
                Image(systemName: dettatura.attiva ? "stop.circle.fill" : "mic.circle.fill")
                    .font(.system(size: 44))
                    .foregroundStyle(dettatura.attiva ? .red : .cyan)
            }
        }
        .padding()
        .background(.bar)
    }
}

struct Bolla: View {
    let messaggio: Messaggio

    var body: some View {
        HStack {
            if messaggio.chi == "utente" { Spacer(minLength: 40) }
            Text(messaggio.testo)
                .padding(10)
                .background(colore, in: RoundedRectangle(cornerRadius: 14))
                .foregroundStyle(messaggio.chi == "errore" ? .red : .primary)
            if messaggio.chi != "utente" { Spacer(minLength: 40) }
        }
    }

    private var colore: Color {
        switch messaggio.chi {
        case "utente": return .cyan.opacity(0.25)
        case "errore": return .red.opacity(0.12)
        default: return .gray.opacity(0.18)
        }
    }
}

// MARK: - Impostazioni

struct SchermataImpostazioni: View {
    @Environment(\.dismiss) private var chiudi
    @State private var indirizzo = Impostazioni.indirizzo
    @State private var chiave = Impostazioni.chiave
    @State private var parlaACasa = Impostazioni.parlaACasa

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("192.168.1.96 oppure 100.x.y.z", text: $indirizzo)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    SecureField("Chiave del pannello", text: $chiave)
                    Toggle("Parla anche dal tablet a casa", isOn: $parlaACasa)
                } header: {
                    Text("PC con Jarvis")
                } footer: {
                    Text("In casa: l'indirizzo del PC in rete locale. Fuori casa: installa Tailscale sul PC e sull'iPhone "
                         + "e usa l'indirizzo Tailscale del PC. La chiave è quella del pannello del tablet "
                         + "(pagina delle impostazioni sul PC, sezione Jarvis). L'Apple Watch riceve tutto da qui.")
                }
            }
            .navigationTitle("Impostazioni")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Salva") {
                        Impostazioni.indirizzo = indirizzo.trimmingCharacters(in: .whitespaces)
                        Impostazioni.chiave = chiave.trimmingCharacters(in: .whitespaces)
                        Impostazioni.parlaACasa = parlaACasa
                        Collegamento.condiviso.mandaImpostazioni()
                        chiudi()
                    }
                }
            }
        }
    }
}
