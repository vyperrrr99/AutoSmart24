# Coda di scraping seriale, progresso live e calibrazione — Design

**27/07/2026**

## 1. Obiettivo

Rendere osservabile e controllabile lo scraping ora che le marche monitorate sono passate da 10 a 25 e la finestra temporale da 5 a 10 anni:

1. **Progresso live per marca**: durante una run la dashboard mostra fase corrente (ricerca / dettaglio), quanti annunci sono stati elaborati sul totale, percentuale e tempo residuo stimato.
2. **Coda seriale**: le run vengono eseguite una alla volta, con una vista d'insieme di cosa sta girando, cosa è in attesa e in che ordine.
3. **Calibrazione**: durata e velocità di ciascuna fase vengono registrate a ogni run e consultabili come storico, per dimensionare i cron e prevedere la durata di un giro completo.
4. **Navigazione**: ricerca e filtri sulle marche, necessari ora che la griglia ne contiene 25.

Fuori perimetro: l'applicazione `auto-bi` (analisi prezzi per auto trader), che resta un progetto separato con il proprio ciclo spec → piano.

## 2. Contesto e problemi riscontrati

Questa spec nasce da problemi osservati sul campo il 27/07/2026, durante la migrazione su nuova macchina e il primo batch di scraping delle marche aggiuntive.

### 2.1 I contatori restano a zero per tutta la run

`run_brand_sweep` (`run_manager.py`) accumula `listings_seen`, `new_listings` e `price_changes` in **variabili locali** e li assegna alla riga `ScrapeRun` solo alla riga 377, cioè al termine dello sweep. Durante l'esecuzione la riga a DB riporta quindi `listings_seen = 0`.

Conseguenza pratica: la dashboard mostra la marca come "In esecuzione" con tutti i contatori a zero, indistinguibile da una run bloccata. L'unico segnale di avanzamento reale sono gli eventi `ScrapeEvent` di livello `info` emessi da `process_detail_backlog` ("Detail backlog page: enriched N"), che però coprono solo la fase di dettaglio: **la fase di ricerca non emette alcun evento** ed è completamente cieca.

Nota: `session.commit()` viene già invocato a ogni batch (`run_manager.py:313`). Persistere il progresso non richiede quindi commit aggiuntivi, solo di assegnare i contatori alla riga della run prima del commit già presente.

### 2.2 Non esiste una coda: fino a 10 run in parallelo

`BrandScheduler` istanzia `BackgroundScheduler()` senza configurare gli executor. Verificato sui sorgenti di APScheduler 3.10.4:

- `BaseScheduler._create_default_executor()` restituisce `ThreadPoolExecutor()`
- `apscheduler.executors.pool.ThreadPoolExecutor.__init__` ha `max_workers=10`
- job defaults: `misfire_grace_time=1` (secondo), `max_instances=1`

Con tutte le marche schedulate allo stesso orario (03:00), alle 03:00 lo scheduler invia tutti i job all'executor: **10 vengono eseguiti simultaneamente**, i restanti attendono nella coda interna del thread pool — invisibile, non ispezionabile, non riordinabile. Ogni run apre un proprio pool di `SCRAPE_CONCURRENCY=6` worker HTTP, quindi il picco è di **~60 richieste parallele** verso autoscout24.it.

`BrandRunGuard` non mitiga il problema: protegge dallo sweep concorrente *della stessa marca*, non dal numero totale di run attive.

Con 5-10 marche il carico restava sotto la soglia di rischio; con 25 marche il rate limiting o il ban dell'IP diventano probabili. In attesa dell'implementazione di questa spec tutte le marche sono state messe in pausa, per evitare l'avvio automatico delle 03:00.

### 2.3 Il dettaglio marca non si aggiorna

`BrandDetail.tsx` carica run ed eventi in un `useEffect` con dipendenza `[brandSlug]`: i dati vengono recuperati una sola volta all'apertura. Aprendo il dettaglio di una marca in esecuzione, il contenuto resta congelato finché non si chiude e riapre il pannello. `App.tsx` implementa già un polling adattivo (3s con run attiva, 15s altrimenti), ma copre solo l'elenco marche.

### 2.4 Dati di calibrazione misurati

Misurazioni reali del 27/07/2026, con `SCRAPE_CONCURRENCY=6` e ritardo cortese 3-8s:

| Marca | Fase ricerca | Fase dettaglio |
|---|---|---|
| Citroën | 7.256 annunci in 478s → **911/min** | 6.995 annunci in 7.152s → **58,7/min** |
| Opel | 6.539 annunci in 443s → **886/min** | ~60/min (stabile) |

Il rapporto è di circa **15:1**: la fase di dettaglio domina il tempo totale. Regola pratica derivata: **~1 ora ogni 3.500 annunci da arricchire**.

Per confronto, la spec `2026-07-25-scraper-throughput-design.md` misurava 157/min in ricerca e 11/min in dettaglio prima della parallelizzazione: il guadagno effettivo della concorrenza a 6 è di ~5,4x sulla fase di dettaglio.

Impatto della finestra a 10 anni, misurato interrogando il sito su 5 marche campione (fiat, volkswagen, opel, toyota, citroen): il volume passa da 54.814 a 85.466 annunci, **fattore 1,56x**. Proiezione su 25 marche: da ~190.000 a **~296.000 annunci**. Un giro completo di sola ricerca costa quindi ~5,5h; l'arricchimento iniziale del delta sulle marche già acquisite (~75.000 annunci) costa ~21h.

## 3. Modello dati — migrazione `0007_run_progress`

Cinque colonne aggiunte a `scrape_runs`:

```python
phase              String(16)  nullable   # 'search' | 'detail' | None (conclusa)
search_finished_at DateTime    nullable   # confine tra le due fasi
search_total       Integer     nullable   # annunci attesi in ricerca
detail_total       Integer     nullable   # annunci da arricchire (COUNT a inizio fase)
detail_enriched    Integer     default 0  # annunci arricchiti finora
```

Più una modifica **senza migrazione**: `listings_seen` viene assegnato alla riga della run dentro il loop dei batch, sfruttando il commit già esistente.

Copertura dei requisiti con questi soli campi:

- **Percentuale**: `listings_seen / search_total` in fase ricerca, `detail_enriched / detail_total` in fase dettaglio.
- **Durate**: ricerca = `started_at → search_finished_at`; dettaglio = `search_finished_at → finished_at`.
- **Velocità storiche**: derivate da volume e durata, senza colonne dedicate.
- **ETA**: volume residuo diviso la velocità media delle run precedenti della stessa marca.

`search_total` e `detail_total` sono nullable per scelta: finché il denominatore non è noto la UI mostra avanzamento senza percentuale, invece di esibire una stima inventata. `search_total` viene popolato dai valori `numberOfResults` che il crawler già riceve durante la fase di probe dei modelli; `detail_total` dal `COUNT` degli annunci con `detail_scraped = false` all'inizio della fase di dettaglio.

## 4. Coda seriale

### 4.1 Esecuzione

`BrandScheduler` configura esplicitamente:

```python
BackgroundScheduler(
    executors={"default": ThreadPoolExecutor(max_workers=1)},
    job_defaults={"misfire_grace_time": 3600, "max_instances": 1},
)
```

Effetto: i job accodati eseguono **uno alla volta**; il parallelismo HTTP resta costante a 6 richieste (un solo pool di worker attivo) invece dei ~60 di picco odierni. `misfire_grace_time` ampio evita che un job in attesa venga scartato perché lo scheduler lo processa in ritardo.

`max_instances=1` fornisce una protezione anti-valanga: se una marca è ancora in coda o in esecuzione quando scatta il suo trigger del giorno successivo, quella nuova esecuzione viene saltata anziché accumularsi. È rilevante perché il giro a 10 anni sulle marche storiche (~26h stimate) sfora le 24 ore.

### 4.2 Controllo

Un `QueueController` mantiene lo stato della coda:

```python
halted: bool
halted_reason: str | None      # es. "blocco rilevato su Toyota"
halted_at: datetime | None
```

Regole:

- Una run che termina con stato `blocked` imposta `halted = True`, registrando marca e orario.
- Ogni run, all'avvio, verifica il flag: se la coda è ferma registra un `ScrapeEvent` di livello `warning` ("saltata: coda ferma") ed esce **senza effettuare alcuna richiesta HTTP**. Questo impedisce che un blocco IP si propaghi in decine di fallimenti a catena, peggiorando il blocco.
- Una run che termina con stato `error` non tocca il flag: si tratta di un problema isolato e la coda prosegue con la marca successiva.
- La ripresa è manuale, dalla dashboard (`POST /queue/resume`): azzera il flag e lascia che i job successivi riprendano.

Lo stato della coda vive in memoria nel processo, coerentemente con il fatto che lo scheduler stesso è in-process. Un riavvio del container azzera il flag: comportamento accettabile e documentato, perché un riavvio è già un intervento manuale esplicito.

### 4.3 Ordine ed ETA

Le marche condividono lo stesso trigger (03:00), quindi la coda si forma nell'ordine di registrazione dei job, deterministico. La dashboard mostra posizione in coda, ETA per marca e ETA complessivo, ricavati dai job pendenti dello scheduler combinati con le velocità storiche. Non serve una tabella di coda dedicata a DB.

## 5. Nuove API

```
GET  /queue                  stato complessivo della coda
POST /queue/resume           sblocca una coda ferma
GET  /brands/{slug}/metrics  storico di calibrazione della marca
```

`GET /queue`:

```json
{
  "halted": false,
  "halted_reason": null,
  "halted_at": null,
  "current": {
    "slug": "opel", "brand": "Opel", "phase": "detail",
    "done": 1449, "total": 6800, "percent": 21.3,
    "eta_seconds": 5300, "eta_is_fallback": false,
    "started_at": "2026-07-27T14:15:33"
  },
  "pending": [
    { "slug": "toyota", "brand": "Toyota", "position": 1, "eta_seconds": 7200 }
  ],
  "total_eta_seconds": 54000
}
```

`GET /brands/{slug}/metrics` restituisce, per ogni run conclusa, durata e volume di ciascuna fase più la velocità derivata:

```json
[
  { "run_id": 42, "started_at": "...", "status": "success",
    "search_seconds": 478, "search_items": 7256, "search_rate_per_min": 911,
    "detail_seconds": 7152, "detail_items": 6995, "detail_rate_per_min": 58.7 }
]
```

`RunOut` viene esteso con i cinque nuovi campi, così le viste esistenti possono mostrare il progresso senza chiamate aggiuntive.

**Il calcolo di percentuali ed ETA risiede nel backend**, non nel frontend: dashboard, script da terminale ed eventuali worker su altre macchine condividono così la stessa logica. La stima usa la velocità media delle ultime run riuscite della stessa marca, per fase. In assenza di storico ricade sulle medie globali misurate (911/min ricerca, 59/min dettaglio) e marca la risposta con `eta_is_fallback: true`, così la UI può dichiarare l'approssimazione invece di simulare precisione.

## 6. Interfaccia

### 6.1 Panoramica

In cima il nuovo `QueuePanel`:

- **Coda ferma**: banner di allerta — *"Coda ferma: blocco rilevato su Toyota alle 04:12"* — con pulsante **Riprendi coda**.
- **Coda attiva**: *"In esecuzione: Opel — dettaglio 21% — resta ~1h28m"* con barra di avanzamento, e *"In attesa: 14 marche — totale ~15h"*.

Sotto, `BrandFilters`: ricerca testuale, filtro per stato (in esecuzione / in pausa / in errore / attive) e ordinamento. Con 25 marche la griglia non filtrata è inutilizzabile.

Infine la griglia di `BrandCard`, ciascuna con barra di avanzamento e fase quando la marca è in esecuzione, e i contatori dell'ultimo run come oggi.

### 6.2 Dettaglio marca

`BrandDetail` passa al **polling** (stesso intervallo adattivo di `App.tsx`: 3s con run attiva, 15s altrimenti), correggendo il congelamento descritto in §2.3. Contenuti: progresso live in evidenza, storico run (grafico esistente), nuovo pannello di calibrazione con velocità di ricerca e dettaglio per run, elenco eventi.

### 6.3 Gestisci marche

Invariata: la configurazione di anno, giorno, ora e minuto per marca e i predefiniti applicabili a tutte funzionano già e coprono il requisito di pianificazione.

### 6.4 Componenti

Nuovi: `QueuePanel`, `RunProgress` (riusato in card e dettaglio), `BrandFilters`, `BrandMetrics`.

## 7. Gestione errori

| Situazione | Comportamento |
|---|---|
| Run termina `blocked` | Coda ferma, banner con causa e orario, ripresa manuale |
| Run termina `error` | Coda prosegue; badge sulla marca ed evento nel dettaglio |
| `search_total` / `detail_total` assenti | Barra indeterminata, nessuna percentuale mostrata |
| Nessuno storico per l'ETA | Stima di fallback, dichiarata come approssimativa in UI |
| API irraggiungibile | Messaggio d'errore tramite `ApiError` / `describeError` esistenti |

## 8. Testing

**Backend** (pytest):

- `run_manager`: il progresso (`phase`, `listings_seen`, `detail_enriched`) è persistito a ogni batch, non solo a fine run; `search_finished_at` è valorizzato al passaggio di fase; i totali sono coerenti a run conclusa.
- `scheduler`: con `max_workers=1` due run non si sovrappongono; un job accodato oltre `misfire_grace_time` non viene scartato.
- `QueueController`: `blocked` ferma la coda; `error` la lascia proseguire; una run avviata a coda ferma esce senza richieste HTTP; `resume` ripristina l'esecuzione.
- API: `/queue` con e senza run attiva e con coda ferma; `/metrics` con e senza storico; `RunOut` esteso.

**Frontend** (vitest + testing-library):

- `RunProgress`: mostra fase, percentuale ed ETA; barra indeterminata quando manca il totale; segnala l'ETA di fallback.
- `QueuePanel`: banner e pulsante di ripresa a coda ferma; riepilogo corrente e attesa a coda attiva.
- `BrandFilters`: filtra per testo e per stato.
- `BrandDetail`: esegue effettivamente il refetch periodico.

## 9. Sequenza di deploy

Le due metà hanno vincoli operativi diversi:

- **Frontend**: rebuild del solo container `dashboard`, eseguibile in qualsiasi momento senza interferire con lo scraping in corso.
- **Backend**: migrazione Alembic e rebuild del container `app`, che **interrompe le run in esecuzione**.

Il piano di implementazione deve quindi collocare il lavoro di backend in una finestra a scraping fermo (o a valle del batch corrente), mentre il lavoro di frontend può procedere in parallelo. Al momento della stesura, un batch manuale sta elaborando in sequenza le 15 marche aggiunte.

## 10. Rischi e note aperte

- **`search_total` è una stima progressiva.** Il crawler scopre il totale interrogando il sito per modello e fascia d'anno: il denominatore della fase di ricerca si consolida man mano che la fase avanza. La percentuale di quella fase è quindi approssimata all'inizio. Accettabile: la fase di ricerca dura minuti, contro le ore della fase di dettaglio.
- **Lo stato della coda non è persistito.** Un riavvio del container azzera il flag `halted`, riattivando una coda che era stata fermata per blocco. Documentato e accettato in questa iterazione, dato che il riavvio è un'azione manuale; se in futuro i worker diventeranno multipli o remoti (fase 1 della spec di prodotto), lo stato dovrà migrare a DB.
- **Il giro a 10 anni sulle marche storiche eccede le 24 ore.** Le ~21h di arricchimento del delta più le ~5,5h di ricerca superano la finestra giornaliera. `max_instances=1` evita l'accumulo, ma il primo giro completo va pianificato come operazione dedicata, non affidato al cron notturno.
- **La percentuale della fase di dettaglio può non raggiungere il 100%.** `process_detail_backlog` parcheggia in `failed_ids` gli annunci che il pool non è riuscito a processare, per non ripescarli all'infinito: quegli annunci restano `detail_scraped = false` e non vengono conteggiati in `detail_enriched`. La barra si ferma quindi poco sotto il totale prima che la run chiuda con `success`. La UI deve considerare conclusa la fase quando la run cambia stato, non quando la percentuale tocca 100.
- **L'ordine della coda non è configurabile.** Deriva dall'ordine di registrazione dei job. Se in futuro servisse dare priorità a certe marche, servirà una coda esplicita a DB (opzione valutata e scartata per YAGNI in questa iterazione).

---

## Addendum 28/07/2026 — resilienza agli errori di rete (da affrontare)

Difetto emerso in produzione la notte del 27/07: due marche (MINI, MG) sono fallite con `timed out` nella stessa finestra di tre minuti. La coda ha proseguito correttamente con le altre — l'asimmetria `blocked`/`error` ha funzionato — ma le due marche colpite sono state perse per intero invece di saltare le sole pagine problematiche. MINI aveva già 5.499 annunci arricchiti su 5.988 e ne mancavano 488.

### Catena del fallimento

`RateLimitedClient` ritenta già 3 volte per richiesta (timeout 15s, pause in mezzo). Il problema è a valle: in `scraping/concurrency.py`, il worker che esaurisce i tentativi fa

```python
except BaseException as exc:
    error_holder.append(exc)
    _drain_queue()        # scarta TUTTI i job rimanenti
```

Gli altri worker trovano la coda vuota ed escono; il pool rilancia l'eccezione; `process_detail_backlog` cattura solo `BlockedError`, quindi il timeout arriva all'`except Exception` di `run_brand_sweep`, che chiude la run con `error`. Una pagina scaduta annulla l'intera marca, compreso il lavoro già riuscito.

Il commento nel codice dichiara l'intento — evitare che un thread muoia in silenzio troncando la lista dei job — ed è corretto; è la reazione a essere sproporzionata per un guasto transitorio.

### Perché non è una modifica banale

Le due fasi hanno conseguenze opposte se si salta del lavoro:

- **Dettaglio**: saltare un annuncio è innocuo. Resta `detail_scraped = false` e viene ripreso alla run successiva.
- **Ricerca**: saltare una pagina significa non vedere quegli annunci, che finiscono in `missing_ids` e vengono trattati come *potenzialmente venduti*. Oggi ciascuno viene verificato prima di essere dichiarato tale, quindi non nascono vendite dal nulla; ma se anche quelle verifiche cadessero nella stessa finestra di rete, si otterrebbero **falsi `sold` su annunci vivi** — una corruzione silenziosa, peggiore di una marca da rifare.

### Direzione proposta

1. Il pool non svuota più la coda su errore di un job: lo registra e prosegue.
2. Soglia di rinuncia (indicativamente 20% dei job falliti): oltre quella, abortire è giusto — la rete è davvero giù.
3. Fase dettaglio: job falliti saltati, contati in `errors_count`, run chiusa `success`.
4. Fase ricerca: se qualche pagina è andata persa, marcare la run **parziale** e saltare la rilevazione dei venduti per quel giro. È il punto che protegge dai falsi `sold`, e cambia il significato di una run — perciò merita una spec propria.
5. Ripresa mirata dei job falliti a fine sweep, dato che il guasto tipico dura minuti.

Il punto 4 è il vero contenuto di design: gli altri sono conseguenze.
