// Parte comune a iPhone e Apple Watch: impostazioni, chiamate al server di Jarvis sul PC, conversazione e voce.
import AVFoundation
import Foundation
import WatchConnectivity

// MARK: - Impostazioni (indirizzo del PC e chiave del pannello)

enum Impostazioni {
    private static let archivio = UserDefaults.standard

    /// Es. "192.168.1.96" in casa, oppure l'indirizzo Tailscale del PC (100.x.y.z) da fuori.
    static var indirizzo: String {
        get { archivio.string(forKey: "indirizzo") ?? "" }
        set { archivio.set(newValue, forKey: "indirizzo") }
    }

    /// La chiave del pannello del tablet (la stessa del tablet: impostazioni sul PC > Jarvis).
    static var chiave: String {
        get { archivio.string(forKey: "chiave") ?? "" }
        set { archivio.set(newValue, forKey: "chiave") }
    }

    /// Se le risposte si leggono anche dal tablet a casa (di solito no: sei fuori e ti risponde il telefono).
    static var parlaACasa: Bool {
        get { archivio.bool(forKey: "parlaACasa") }
        set { archivio.set(newValue, forKey: "parlaACasa") }
    }

    static var completa: Bool { !indirizzo.isEmpty && !chiave.isEmpty }
}

// MARK: - Server di Jarvis

struct Conferma: Decodable, Equatable {
    let id: String
    let descrizione: String
}

struct RispostaChiedi: Decodable {
    let risposta: String
    let ultimo: Int
    let lavoro: Bool
    let conferma: Conferma?
}

struct MessaggioServer: Decodable {
    let chi: String
    let testo: String
}

struct Novita: Decodable {
    let ultimo: Int
    let messaggi: [MessaggioServer]
    let stato: String
    let lavoro: Bool
    let emergenza: Bool
    let conferma: Conferma?
}

enum ErroreJarvis: LocalizedError {
    case daConfigurare
    case risposta(Int, String)

    var errorDescription: String? {
        switch self {
        case .daConfigurare: return "Imposta indirizzo del PC e chiave nell'app sull'iPhone."
        case .risposta(let codice, let testo): return "Jarvis ha risposto \(codice): \(testo)"
        }
    }
}

struct ServerJarvis {
    let indirizzo: String
    let chiave: String

    static var configurato: ServerJarvis { ServerJarvis(indirizzo: Impostazioni.indirizzo, chiave: Impostazioni.chiave) }

    /// "192.168.1.96" -> "http://192.168.1.96:47802"
    func url(_ percorso: String) throws -> URL {
        var base = indirizzo.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !base.isEmpty, !chiave.isEmpty else { throw ErroreJarvis.daConfigurare }
        if !base.contains("://") { base = "http://" + base }
        if var componenti = URLComponents(string: base) {
            if componenti.port == nil { componenti.port = 47802 }
            componenti.path = percorso
            if let url = componenti.url { return url }
        }
        throw ErroreJarvis.daConfigurare
    }

    private func esegui(_ richiesta: URLRequest) async throws -> Data {
        var r = richiesta
        r.setValue(chiave, forHTTPHeaderField: "X-Token")
        let (dati, risposta) = try await URLSession.shared.data(for: r)
        let codice = (risposta as? HTTPURLResponse)?.statusCode ?? 0
        guard codice == 200 else {
            throw ErroreJarvis.risposta(codice, String(data: dati, encoding: .utf8) ?? "")
        }
        return dati
    }

    func chiedi(_ testo: String) async throws -> RispostaChiedi {
        var r = URLRequest(url: try url("/jarvis/chiedi"))
        r.httpMethod = "POST"
        r.timeoutInterval = 70
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try JSONSerialization.data(withJSONObject: ["testo": testo, "attesa": 40,
                                                                  "parla_a_casa": Impostazioni.parlaACasa])
        return try JSONDecoder().decode(RispostaChiedi.self, from: try await esegui(r))
    }

    /// Risposta a una conferma ("Devo cancellare dei file. Confermi?").
    func conferma(id: String, ok: Bool) async throws {
        var r = URLRequest(url: try url("/jarvis/conferma"))
        r.httpMethod = "POST"
        r.timeoutInterval = 15
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try JSONSerialization.data(withJSONObject: ["id": id, "ok": ok])
        _ = try await esegui(r)
    }

    func novita(dopo: Int) async throws -> Novita {
        var componenti = URLComponents(url: try url("/jarvis/eventi"), resolvingAgainstBaseURL: false)!
        componenti.queryItems = [URLQueryItem(name: "dopo", value: String(dopo))]
        var r = URLRequest(url: componenti.url!)
        r.timeoutInterval = 15
        return try JSONDecoder().decode(Novita.self, from: try await esegui(r))
    }
}

// MARK: - Conversazione

struct Messaggio: Identifiable, Hashable {
    let id = UUID()
    let chi: String      // "utente", "jarvis", "azione", "errore"
    let testo: String
}

@MainActor
final class Conversazione: ObservableObject {
    @Published var messaggi: [Messaggio] = []
    @Published var inAttesa = false
    @Published var lavoro = false
    @Published var emergenza = false
    @Published var conferma: Conferma?
    @Published var leggiAdAltaVoce = true

    private let voce = AVSpeechSynthesizer()
    private var ascolto: Task<Void, Never>?

    func invia(_ testo: String) {
        let pulito = testo.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !pulito.isEmpty else { return }
        messaggi.append(Messaggio(chi: "utente", testo: pulito))
        inAttesa = true
        Task {
            do {
                let r = try await ServerJarvis.configurato.chiedi(pulito)
                inAttesa = false
                lavoro = r.lavoro
                conferma = r.conferma
                if !r.risposta.isEmpty {
                    messaggi.append(Messaggio(chi: "jarvis", testo: r.risposta))
                    parla(r.risposta)
                }
                segui(dopo: r.ultimo)
            } catch {
                inAttesa = false
                messaggi.append(Messaggio(chi: "errore", testo: error.localizedDescription))
            }
        }
    }

    /// Dopo la risposta veloce, l'esecutore può ancora lavorare: si controllano le novità per 10 minuti.
    private func segui(dopo inizio: Int) {
        ascolto?.cancel()
        ascolto = Task {
            var ultimo = inizio
            let partenza = Date()
            let fine = partenza.addingTimeInterval(600)
            while !Task.isCancelled && Date() < fine {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                guard let n = try? await ServerJarvis.configurato.novita(dopo: ultimo) else { continue }
                ultimo = n.ultimo
                lavoro = n.lavoro
                emergenza = n.emergenza
                if n.conferma != conferma {
                    conferma = n.conferma
                    if let c = n.conferma {
                        let domanda = "Devo \(c.descrizione.components(separatedBy: ":").first ?? c.descrizione). Confermi?"
                        messaggi.append(Messaggio(chi: "jarvis", testo: domanda))
                        parla(domanda)
                    }
                }
                for m in n.messaggi where m.chi == "jarvis" {
                    messaggi.append(Messaggio(chi: "jarvis", testo: m.testo))
                    parla(m.testo)
                }
                // niente più lavoro in corso e Jarvis a riposo: non arriverà altro
                if !n.lavoro && n.stato == "riposo" && Date().timeIntervalSince(partenza) > 20 { break }
            }
        }
    }

    func rispondi(_ ok: Bool) {
        guard let c = conferma else { return }
        conferma = nil
        messaggi.append(Messaggio(chi: "utente", testo: ok ? "Sì, procedi" : "No"))
        Task {
            do {
                try await ServerJarvis.configurato.conferma(id: c.id, ok: ok)
            } catch {
                messaggi.append(Messaggio(chi: "errore", testo: error.localizedDescription))
            }
        }
    }

    func parla(_ testo: String) {
        guard leggiAdAltaVoce else { return }
        #if os(iOS)
        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
        try? AVAudioSession.sharedInstance().setActive(true)
        #endif
        let frase = AVSpeechUtterance(string: testo)
        frase.voice = AVSpeechSynthesisVoice(language: "it-IT")
        frase.rate = AVSpeechUtteranceDefaultSpeechRate * 1.05
        voce.speak(frase)
    }

    func zitto() {
        voce.stopSpeaking(at: .immediate)
    }
}

// MARK: - iPhone <-> Watch: l'orologio riceve indirizzo e chiave dall'iPhone

final class Collegamento: NSObject, WCSessionDelegate {
    static let condiviso = Collegamento()

    func avvia() {
        guard WCSession.isSupported() else { return }
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    /// Sull'iPhone: manda all'orologio le impostazioni correnti.
    func mandaImpostazioni() {
        guard WCSession.isSupported(), WCSession.default.activationState == .activated else { return }
        try? WCSession.default.updateApplicationContext(["indirizzo": Impostazioni.indirizzo,
                                                         "chiave": Impostazioni.chiave])
    }

    func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState,
                 error: Error?) {
        #if os(iOS)
        mandaImpostazioni()
        #else
        ricevi(session.receivedApplicationContext)
        #endif
    }

    func session(_ session: WCSession, didReceiveApplicationContext applicationContext: [String: Any]) {
        ricevi(applicationContext)
    }

    private func ricevi(_ contesto: [String: Any]) {
        if let indirizzo = contesto["indirizzo"] as? String, !indirizzo.isEmpty { Impostazioni.indirizzo = indirizzo }
        if let chiave = contesto["chiave"] as? String, !chiave.isEmpty { Impostazioni.chiave = chiave }
    }

    #if os(iOS)
    func sessionDidBecomeInactive(_ session: WCSession) {}
    func sessionDidDeactivate(_ session: WCSession) { WCSession.default.activate() }
    #endif
}
