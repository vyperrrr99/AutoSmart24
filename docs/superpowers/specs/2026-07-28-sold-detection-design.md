# Rilevazione vendite affidabile — Design

**28/07/2026**

## 1. Il problema, con le prove

Il 28/07/2026 una verifica manuale ha mostrato che il meccanismo di rilevazione vendite produce falsi positivi in massa.

| Campione verificato aprendo le pagine su autoscout24.it | Esito |
|---|---|
| 20 annunci `sold` estratti a caso | 16 corretti, **4 falsi** |
| 12 annunci `sold` di **Lancia** | **12 falsi su 12** |
| 4 annunci `active` (controprova) | 4 corretti |

Il dato che identifica la causa: **139 su 139** annunci Lancia marcati venduti lo erano stati **meno di 60 minuti dopo essere stati visti attivi** nella lista di ricerca — mediana 26 minuti, tutti concentrati in una finestra di circa 30 minuti (fino a 12 dichiarazioni di vendita in un singolo minuto).

Nessun mercato vende 12 auto al minuto di una marca che ne ha 3.261 a catalogo. Il sito stava rispondendo `404`/`410` in modo transitorio — probabilmente una forma di limitazione che non usa `403`/`429`, e che quindi non attiva né `BlockedError` né il backoff adattivo.

Complessivamente **167 record** sono stati corretti a mano (riportati ad `active`, elenco in `falsi-venduti-20260728-1505.csv`, backup in `pre-fix-sold-20260728-1505.dump`). Distribuzione: Lancia 139, MG 12, Opel 10, altri sporadici. Fiat (184), Renault (187) e MINI (52) erano invece corretti.

**Perché conta più di altri difetti.** Il time-to-sell è la metrica centrale della spec di prodotto (Pricing Tool, §3.3). Con i dati sporchi, una mediana reale di 60 giorni scendeva verso i 35: il sistema avrebbe consigliato a un commerciante un prezzo per vendere in un mese e mezzo un'auto che ne richiede due.

## 2. I due percorsi che dichiarano una vendita

Il codice marca `sold` in due punti di `run_manager.py`, con affidabilità opposta.

**Percorso 1 — annunci spariti dalla ricerca.** A fine crawl, `missing_ids = attivi_a_database − visti_sul_sito`. Ogni mancante viene verificato aprendo la sua pagina; se risponde `404`/`410` o `status ≠ Active` diventa venduto, altrimenti resta attivo e viene registrata un'anomalia. Qui il `404` **conferma** un'assenza già osservata: è il percorso che ha prodotto i venduti corretti di Opel, Fiat e Renault.

**Percorso 2 — durante l'arricchimento.** `process_detail_backlog` visita gli annunci con `detail_scraped = false` per raccoglierne i dettagli. Se la pagina risponde `404`/`410`, l'annuncio diventa venduto. Ma quegli annunci sono in coda **proprio perché appena visti nella lista di ricerca**: qui il `404` **contraddice** un'osservazione di pochi minuti prima. Tutti i 139 casi Lancia provengono da questo percorso.

## 3. Principio guida

**Una vendita si dichiara solo su prove concordanti**: l'annuncio è sparito dalla lista **e** la sua pagina lo conferma. Un segnale isolato — a maggior ragione se contraddice quanto appena osservato — non basta.

Il rovescio è dichiarato: una vendita reale può registrarsi con un giro di ritardo. È il costo accettabile, perché i due errori non pesano uguale. Un annuncio dichiarato venduto in 26 minuti quando il tempo vero è 45 giorni sbaglia del 99,96% e, essendo in massa, trascina la mediana verso il basso. Registrarne 46 invece di 45 sbaglia del 2%, sposta tutto nella stessa direzione ed è compensabile perché sistematico. Inoltre un ritardo è riconoscibile, mentre una vendita inventata è indistinguibile da una vera una volta scritta a database.

## 4. Componente 1 — L'arricchimento non dichiara più vendite

In `process_detail_backlog`, una pagina che risponde `404`/`410` o `status ≠ Active` **non marca più l'annuncio come venduto**. L'annuncio:

- resta `status = 'active'`
- mantiene `detail_scraped = false` — non è stato arricchito davvero, quindi deve restare in coda
- produce un `ScrapeEvent` di livello `warning` che registra la risposta anomala

La valutazione è rimandata al giro successivo: se l'annuncio è davvero venduto, sparirà dalla lista di ricerca e verrà giudicato dal percorso 1, che è affidabile.

Da solo, questo elimina tutti e 139 i casi di Lancia e i 12 di MG.

**Il dettaglio da non sbagliare** è `detail_scraped`: marcarlo `true` farebbe uscire l'annuncio dalla coda di arricchimento senza che sia mai stato arricchito, trasformando un falso positivo in un dato mancante permanente.

## 5. Componente 2 — Doppia conferma sugli annunci spariti

Nel percorso 1 gli annunci risultati assenti non diventano più venduti immediatamente: entrano in una lista di **candidati**. A fine sweep, dopo l'arricchimento, ogni candidato viene interrogato una seconda volta. Diventa `sold` solo chi risulta rimosso in **entrambe** le verifiche.

Perché alla fine e non subito:

- tra le due richieste passano minuti, spesso decine: un'anomalia breve non le colpisce entrambe
- il costo è proporzionale e contenuto — una richiesta in più per candidato, circa un minuto ogni 78

Esiti possibili:

| Prima verifica | Seconda verifica | Risultato |
|---|---|---|
| rimosso | rimosso | `sold` |
| rimosso | attivo | resta `active`, anomalia registrata |
| rimosso | errore di rete | resta `active` — nel dubbio non si dichiara |

## 5-bis. Interazione con il riuso degli ID (scoperto in parallelo)

Mentre questa spec veniva scritta, la macchina Windows ha documentato un difetto distinto ma intrecciato a questo: `2026-07-28-listing-id-reuse-known-issue.md`. AutoScout24 **riassegna l'id di un annuncio** a un'auto diversa — anche di un'altra marca — quando il vecchio annuncio viene ritirato. L'URL originale risponde con un redirect `308` verso il nuovo annuncio.

Questo apre un buco nella doppia conferma appena descritta. `RateLimitedClient` è costruito con `follow_redirects=True`, quindi verificando un annuncio ritirato il codice segue silenziosamente il redirect, atterra sulla pagina di un'auto **diversa ma attiva**, legge `status = Active` e conclude che l'annuncio non è venduto. Entrambe le verifiche darebbero lo stesso esito: il candidato resterebbe `active` per sempre.

È l'errore speculare a quello che questa spec corregge — un falso **negativo** invece di un falso positivo — e senza contromisura la doppia conferma lo renderebbe sistematico proprio sugli annunci ritirati da più tempo.

**Contromisura: verificare l'identità, non solo lo stato.** Una verifica non deve limitarsi a chiedere "questa pagina è attiva?", ma "questa pagina è ancora *quell'annuncio*?". In pratica si confronta ciò che la pagina restituisce (marca, e dove disponibile modello) con quanto risulta a database per quell'id. Se non corrispondono, l'annuncio originale non esiste più: la verifica conta come **rimozione confermata**, non come annuncio vivo.

Il controllo va applicato a entrambe le verifiche del componente 2, ed è la stessa informazione che servirà al fix del riuso id — dove però il seguito è diverso: là bisognerà anche registrare la nuova auto, qui basta concludere correttamente sul vecchio annuncio. Le due cose restano lavori separati; questa spec si limita a non farsi ingannare dal redirect.

## 6. Gestione degli errori

| Situazione | Comportamento |
|---|---|
| Arricchimento riceve `404`/`410` | annuncio attivo, `detail_scraped` invariato, evento `warning` |
| Prima verifica rimosso, seconda attivo | attivo + anomalia: il sito rispondeva male |
| Seconda verifica in errore | il candidato non diventa venduto |
| Nessun candidato a fine run | nessuna seconda fase, nessun costo |
| `BlockedError` durante la seconda fase | la fase si interrompe, i candidati non confermati restano attivi |

## 7. Testing

Il difetto è nato da uno scenario che nessun test copriva, quindi i test partono da lì.

**Riproduzione dell'incidente** — un annuncio visto vivo nella ricerca, la cui pagina di dettaglio risponde poi `410`, deve restare `active` con `detail_scraped = false`. Scritto contro il codice attuale, questo test deve **fallire**: è la prova che riproduce il difetto e non un'altra cosa.

**Doppia conferma** — rimosso+rimosso → `sold`; rimosso+attivo → resta `active` con anomalia; rimosso+errore → resta `active`. Verificare anche che senza candidati non parta alcuna richiesta.

**Test esistenti da riscrivere.** Due asseriscono il comportamento che stiamo rimuovendo:

- `test_process_detail_backlog_returns_sold_count`
- `test_run_brand_sweep_counts_backlog_confirmed_sold_in_sold_detected`

Falliranno, ed è corretto: documentavano un comportamento dannoso. Vanno riscritti per asserire il nuovo, non adattati per farli passare. Ci sono 51 riferimenti a `sold` nella suite: gli altri riguardano il percorso 1 e devono continuare a passare invariati.

Da preservare `test_run_brand_sweep_relists_a_previously_sold_listing_that_reappears`, il cui commento cita un *"Live incident"*: qualcuno aveva già osservato annunci marcati venduti per errore e poi riapparsi. Il recupero esisteva già; mancava la prevenzione.

## 8. Fuori perimetro

- **Freno di plausibilità sul tasso di vendite per run.** Valutato e scartato: una soglia sul totale (ipotizzata al 15%) non avrebbe intercettato Lancia, che si è fermata al 4,3% degli annunci della marca. Il segnale non era nel totale ma nella concentrazione — fino a 12 dichiarazioni al minuto. Una difesa migliore misurerebbe la causa anziché l'effetto: estendere `BlockRateTracker` a contare anche `404`/`410` nella finestra mobile, **limitatamente alla fase di arricchimento** (nel percorso 1 quelle risposte sono attese per costruzione e un rilevatore cieco alla differenza scambierebbe il funzionamento normale per un guasto). Rimandato: i componenti 1 e 2 coprono il caso osservato, e una difesa aggiuntiva va calibrata su dati, non su intuizioni.
- **Correzione dei dati storici**: già effettuata manualmente il 28/07.
- **Resilienza del worker pool ai timeout** (un errore fa scartare l'intera coda dei job): problema vicino ma distinto, documentato in coda alla spec `2026-07-27-scraper-queue-and-live-progress-design.md`.
