# Scraper Autoscout24 — Design: velocità, filtro anno, camuffamento

Data: 2026-07-25

## 1. Contesto e motivazione

Durante il primo test dal vivo del piano a 19 task (§ commit `14634d0`..`0dbbbe3`, più i fix successivi), sono emersi tre problemi legati alla velocità e al volume:

1. **Il backlog dettaglio era limitato a 50 annunci per run** (`DETAIL_BATCH_SIZE = 50` in `run_manager.py`), un cap introdotto nel piano di implementazione approvato senza che fosse segnalato come deviazione dalla spec originale (`docs/superpowers/specs/2026-07-24-autoscout-scraper-design.md`, sezione 5, Pipeline B), che prevede che **ogni** annuncio nuovo venga messo in coda per la visita alla pagina di dettaglio, non un sottoinsieme fisso per giro.
2. **Lo scraping è interamente sequenziale a thread singolo.** Un test dal vivo su Fiat ha misurato ~157 annunci/minuto in fase ricerca, ma solo ~11/minuto in fase dettaglio (il ritardo cortese di 3-8s si applica per singola richiesta di dettaglio, non per pagina di 20 risultati come in fase ricerca). Per l'intero catalogo Fiat (~44.800 annunci reali, confermato interrogando live il sito), il solo primo giro di dettaglio richiederebbe **oltre 3 giorni** a thread singolo.
3. **Molto dell'inventario è costituito da auto datate** che non interessano ai fini di questo tracciamento: sui dati Fiat raccolti finora (11.533 annunci), il 29,9% ha immatricolazione oltre 15 anni, il 44,1% oltre 10 anni.

Questo documento copre le modifiche per affrontare tutti e tre i punti, restando dentro i vincoli già stabiliti dalla spec originale: **un solo IP, nessuna IP rotation** (sezione 2 della spec originale) — la scalata prevista in caso di rate insostenibile è "più worker su IP diversi", non proxy rotanti; qui restiamo comunque su una singola macchina, quindi introduciamo concorrenza *dentro* il processo esistente, non nuovi host.

## 2. Obiettivo

- Rimuovere il cap dei 50 annunci/run: la fase dettaglio processa **tutti** gli annunci con `detail_scraped = false`.
- Introdurre concorrenza configurabile (grado di parallelismo, default iniziale **6**) sia in fase ricerca che in fase dettaglio, mantenendo tutto dentro il processo Python esistente (nessun nuovo worker/processo separato, nessun coordinamento via DB).
- Introdurre un filtro sull'anno di immatricolazione configurabile (default iniziale **5 anni**), applicato direttamente alla query di ricerca (non solo come filtro a valle), per ridurre il volume totale scaricato.
- Aggiungere misure di camuffamento leggere (rotazione User-Agent, reset periodico di sessione/cookie) e backoff adattivo sul tasso di blocco — senza introdurre IP rotation né impersonazione TLS (curl_cffi), valutabili in futuro solo se emergeranno blocchi reali.

**Esplicitamente fuori scope per questa iterazione:** worker multipli come processi separati coordinati via DB (architettura da spec originale sezione 2.6); impersonazione TLS (curl_cffi); fallback Playwright.

## 3. Filtro anno di immatricolazione

Nuova variabile d'ambiente `SCRAPE_MAX_LISTING_AGE_YEARS` (default `5`, come richiesto). A runtime si calcola `year_from = anno_corrente - SCRAPE_MAX_LISTING_AGE_YEARS` e lo si passa come **pavimento** a `crawl_brand`, che lo combina con l'eventuale split per anno già esistente (`split_year_ranges`, invariato): il pavimento viene applicato come limite inferiore assoluto prima di qualunque ulteriore suddivisione, così un modello che necessita comunque di split (perché supera `MAX_RESULTS_PER_QUERY` anche ristretto agli ultimi N anni) continua a funzionare come oggi, semplicemente partendo da un intervallo di partenza più stretto.

`search_query.py` non richiede modifiche: `build_search_url` accetta già `year_from`/`year_to`.

## 4. Concorrenza — fase ricerca (`crawler.py`)

**Approccio scelto (due fasi, non coda dinamica condivisa):**

- **Fase 1 — scoperta e probe paralleli.** Si scoprono i modelli del brand (1 richiesta, invariato). Poi, per ogni modello, si effettua in parallelo (pool di `SCRAPE_CONCURRENCY` worker) la richiesta di pagina 1 con `year_from` applicato: questa richiesta rivela `numberOfResults`/`numberOfPages` **e** contiene già i dati della pagina 1 stessa (non sprecata — va restituita come parte del risultato, esattamente come nel codice sequenziale attuale). Per i modelli che superano `MAX_RESULTS_PER_QUERY` anche con il filtro anno applicato, si esegue lo split ricorsivo esistente (`split_year_ranges`) — resta sequenziale/ricorsivo per sua natura, ma modelli diversi che necessitano di split vengono comunque calcolati in parallelo tra loro.
- **Fase 2 — scaricamento parallelo.** Una volta note tutte le unità di lavoro (modello, eventuale fascia anno, numero pagine), si costruisce l'elenco completo di tutte le pagine 2..N ancora da scaricare e le si scarica con lo stesso pool di `SCRAPE_CONCURRENCY` worker, usando `concurrent.futures.ThreadPoolExecutor` + `as_completed`.

`crawl_brand` resta un generatore verso l'esterno (`Iterator[dict]`, come oggi): internamente cede i risultati man mano che i future completano, in ordine di completamento (non di richiesta) — ordine ininfluente, dato che `run_manager.py` già tratta i risultati come un flusso non ordinato da accumulare a batch. **Questo significa che il meccanismo di commit incrementale a batch aggiunto ieri (`run_brand_sweep`) non richiede modifiche**: riceve semplicemente risultati più velocemente dalla stessa interfaccia.

Firma aggiornata: `crawl_brand(client_factory, brand_slug, make_id, year_from=None, concurrency=1)` — il parametro `client` singolo viene sostituito da una `client_factory: Callable[[], RateLimitedClient]` (vedi sezione 6), perché ogni worker deve avere un proprio client indipendente.

## 5. Concorrenza — fase dettaglio (`run_manager.py`)

Molto più semplice della fase ricerca: non c'è scoperta dinamica, l'elenco di annunci da arricchire è già noto dalla query `SELECT ... WHERE detail_scraped = false`. `process_detail_backlog` viene modificato per:

- Rimuovere il cap `DETAIL_BATCH_SIZE` come limite totale per run: la query seleziona **tutti** i pending (eventualmente paginata lato DB in blocchi per non caricare tutto in memoria in un colpo solo, ma senza fermarsi dopo il primo blocco).
- Ogni blocco viene scaricato in parallelo con lo stesso pool di `SCRAPE_CONCURRENCY` worker (stesso pattern client-factory), poi scritto e committato riusando esattamente il meccanismo a batch già esistente (commit dopo ogni blocco, non un unico commit finale — invariato rispetto a ieri).
- Il loop di conferma "venduto" (`missing_ids`) viene parallelizzato allo stesso modo, per coerenza, anche se il suo volume è tipicamente ridotto (solo annunci spariti dal giro corrente).

## 6. Camuffamento leggero (`http_client.py`)

- **Pool di User-Agent realistici**: 4-5 stringhe UA plausibili (Chrome/Firefox/Edge su Windows), scelta casuale a ogni creazione di client.
- **Client factory**: funzione che crea un nuovo `RateLimitedClient` (nuovo `httpx.Client` interno, quindi nuovi cookie, e nuovo UA a rotazione). Ogni worker (thread) della fase ricerca e della fase dettaglio possiede il proprio client, creato tramite questa factory — nessuna condivisione di client tra thread.
- **Refresh periodico di sessione**: nuova variabile `SCRAPE_SESSION_REFRESH_REQUESTS` (default proposto `30`): ogni worker, dopo N richieste con lo stesso client, lo chiude e ne richiede uno nuovo alla factory (nuovo UA, cookie azzerati) — evita un'unica sessione lunghissima e sempre identica per l'intera durata di un run.
- **Backoff adattivo**: un contatore condiviso e thread-safe (lock) tiene traccia del tasso di blocco/errore nella finestra recente (es. ultime 100 richieste). Se il tasso supera una soglia (default proposto `2%`, coerente con la spec originale), i ritardi minimo/massimo (`SCRAPE_MIN_DELAY_SECONDS`/`SCRAPE_MAX_DELAY_SECONDS`) vengono raddoppiati per tutti i worker attivi finché il tasso non rientra sotto soglia; viene loggato un `ScrapeEvent` di livello `warning` quando scatta. Questo è indipendente dalla gestione esistente di `BlockedError` (403/429 espliciti, che restano gestiti come oggi: fermano subito il run).

**Esplicitamente non incluso in questa iterazione:** impersonazione TLS/JA3 (curl_cffi) e fallback Playwright — da valutare solo se, dopo l'introduzione della concorrenza, si osservano blocchi reali (finora, anche con ore di scraping continuo a thread singolo, non ne abbiamo mai ricevuti).

## 7. Gestione errori

- **`BlockedError` durante la fase 2 (scaricamento parallelo) o durante il backlog dettaglio**: il primo worker che lo incontra deve fermare l'invio di nuovo lavoro e annullare i future ancora pendenti/non ancora iniziati (`ThreadPoolExecutor` con cancellazione dei future in coda) — nessun worker deve continuare a martellare un sito che ha appena risposto con un blocco. Il comportamento a valle (run marcato `blocked`, conteggio parziale) resta quello già implementato.
- **Eccezioni impreviste**: la rete di sicurezza aggiunta nel fix precedente (`except Exception` in `run_brand_sweep`, che marca il run `status="error"` invece di lasciarlo bloccato per sempre a `"running"`) resta valida e non richiede modifiche — cattura anche eventuali eccezioni propagate dal pool di thread.

## 8. Testing

- `crawler.py`: applicazione corretta di `year_from` come pavimento combinato con lo split esistente; scoperta+fetch paralleli producono lo stesso insieme di risultati della versione sequenziale a parità di dati (test con `respx`, concorrenza configurabile a 1 per determinismo dove serve, più un test dedicato che verifica che le richieste avvengano effettivamente in parallelo); interruzione pulita su `BlockedError` durante la fase 2 (nessun future pendente completato dopo il blocco).
- `http_client.py`: rotazione UA (chiamate multiple alla factory restituiscono client con UA diversi dal pool); refresh dopo N richieste; backoff adattivo (simulare un tasso di blocco crescente, verificare che i ritardi aumentino e che un evento venga loggato).
- `run_manager.py`: backlog dettaglio processa l'intero pending set (non più limitato a 50), a lotti, con fetch concorrente simulato; threading di `year_from`/`concurrency` dalla configurazione fino a `crawl_brand`.
- `config.py`: parsing e default delle nuove variabili d'ambiente.

## 9. Configurazione — riepilogo nuove variabili

| Variabile | Default | Significato |
|---|---|---|
| `SCRAPE_CONCURRENCY` | `6` | Grado di parallelismo (worker/thread), condiviso tra fase ricerca e fase dettaglio |
| `SCRAPE_MAX_LISTING_AGE_YEARS` | `5` | Esclude annunci con immatricolazione oltre N anni fa |
| `SCRAPE_SESSION_REFRESH_REQUESTS` | `30` | Ogni quante richieste un worker rigenera il proprio client (UA + cookie) |

Tutte configurabili senza modifiche al codice, coerentemente con il vincolo di calibrazione già stabilito per `SCRAPE_MIN/MAX_DELAY_SECONDS`/`SCRAPE_INTERVAL_DAYS`.

## 10. Rischi e note aperte

- La stima di velocità (concorrenza N ⇒ throughput ≈ N× lineare) è ottimistica: presuppone che il collo di bottiglia resti la rete/il ritardo cortese e non la scrittura DB o il rate limiting reale del sito. Il primo test dal vivo con `SCRAPE_CONCURRENCY=6` è anche il test di calibrazione per questa ipotesi.
- Il primo giro completo su un brand ad alto volume (es. Fiat, anche con filtro 5 anni) resta l'operazione più lenta; i giri successivi processano solo gli annunci realmente nuovi (churn), molto più veloci — coerente con l'assunzione della spec originale.
- Se in pratica il tasso di blocco resta alto anche col backoff adattivo, la spec originale indica come prossimo passo l'aggiunta di worker su IP diversi (LAN/altre macchine), non l'IP rotation — fuori scope qui, da riconsiderare se necessario.
