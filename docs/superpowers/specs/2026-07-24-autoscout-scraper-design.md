# Scraper Autoscout24 — Design Spec

Data: 2026-07-24

## 1. Obiettivo

Raccogliere in modo continuo i dati degli annunci auto pubblicati su autoscout24.it (marca, modello, anno immatricolazione, km, alimentazione, potenza, localizzazione, prezzo e altri attributi di dettaglio), mantenendo uno storico dei prezzi per annuncio e rilevando quando un annuncio smette di essere disponibile (auto "venduta"), calcolando il tempo di permanenza sul mercato. Questi dati alimenteranno una seconda applicazione separata (Auto-BI, spec a parte) per l'analisi dei prezzi di vendita.

Questo documento copre **solo lo Scraper**, non la webapp di analisi.

## 2. Scope MVP

- **Marche incluse inizialmente:** Fiat, Volkswagen, BMW, Audi, Mercedes-Benz (mix di volumi alti e fascia premium, per validare lo scraper su profili di annunci diversi). Espansione a tutte le marche italiane in una fase successiva, fuori da questo MVP.
- **Infrastruttura:** una singola macchina (il PC locale dell'utente), un solo IP, nessun sistema di IP rotation nella prima fase. Il design deve comunque restare aperto a una futura distribuzione su più macchine (partizionamento per marca), ma l'implementazione MVP assume un solo host.
- **Deployment:** Docker Desktop su Windows. Tutti i componenti (scraper, Postgres, backend, dashboard) girano in container.

## 3. Scoperta tecnica preliminare

Verifica fatta su una pagina di ricerca (`autoscout24.it/lst/fiat?...`) e su una pagina di dettaglio con richieste HTTP semplici (`curl`, senza browser):

- Entrambe le pagine sono renderizzate **server-side** (Next.js) e incorporano un tag `<script id="__NEXT_DATA__" type="application/json">` con **tutti i dati strutturati della pagina in JSON**, incluso l'elenco completo degli annunci (`pageProps.listings` nella pagina di ricerca) o il dettaglio completo (`pageProps.listingDetails` nella pagina di annuncio) — valori numerici già "raw" (prezzo, km, kW/CV, cilindrata), non stringhe formattate da parsare. Nessun segnale di rendering solo-JS lato client, nessuna sfida CAPTCHA/Cloudflare visibile in una richiesta semplice.
- Ogni annuncio ha un identificatore stabile `id` (UUID), presente sia nello snippet che nel dettaglio e incluso nell'URL dell'annuncio — è la chiave primaria naturale per `listings`.
- La pagina di dettaglio espone un campo `status` (es. `"Active"`) utile per la logica di rilevamento "venduto" (sezione 8).
- La **paginazione è limitata a 200 pagine per query** indipendentemente dal totale risultati (es. Fiat: 44.772 annunci totali, `numberOfPages: 200`, ~20 risultati/pagina ≈ 4.000 raggiungibili in sequenza).
- Conseguenza diretta: per marche con molti annunci occorre **suddividere la ricerca per modello** (parametro `mmmv` nell'URL, es. `mmmv=<makeId>|<modelId>||`) e, se un singolo modello supera comunque ~4.000 risultati, ulteriormente per fascia di anno immatricolazione (parametri `firstRegistrationFrom`/`firstRegistrationTo`, da confermare l'esatto nome nella query string durante l'implementazione).
- **Vincolo di design importante:** il criterio di suddivisione **non deve mai essere il prezzo**, perché il prezzo è l'attributo che vogliamo tracciare nel tempo. Se un'auto cambiasse "bucket" di ricerca ogni volta che il prezzo cambia, genererebbe falsi "nuovo annuncio" / falsi "venduto".

**Conseguenza architetturale:** dato che i dati sono già disponibili come JSON strutturato lato server, **non è necessario un browser headless (Playwright)** né per la fase snippet né per la fase dettaglio: un client HTTP leggero con header realistici (`httpx`), che scarica la pagina ed estrae il JSON da `__NEXT_DATA__`, è sufficiente. Questo riduce drasticamente il carico (nessun rendering browser), la superficie di rilevamento anti-bot, e la complessità del codice (niente selettori CSS fragili).

I limiti anti-bot reali sotto crawling sostenuto (rate limit, eventuali protezioni più sofisticate che scattano dopo centinaia/migliaia di richieste) **non sono ancora stati misurati**: verificarli con un test isolato ora, fuori dall'infrastruttura di backoff/monitoraggio pianificata, rischierebbe di far bloccare l'IP prima ancora di partire. La fase di calibrazione (sezione 6) è quindi un **task esplicito del piano di implementazione**, che aumenta gradualmente il ritmo delle richieste mentre l'infrastruttura di rilevamento blocchi/backoff/dashboard è già attiva e monitora l'esito.

## 4. Architettura e componenti

- **Scraper engine** (Python + `httpx`): per ogni marca configurata, esegue le ricerche (con lo split modello/anno necessario), estrae i dati dal JSON `__NEXT_DATA__` delle pagine di ricerca, e visita le pagine di dettaglio (stesso meccanismo di estrazione) delle auto nuove. Nessuna dipendenza da browser headless nell'MVP; Playwright resta un'opzione di fallback futura solo se emergeranno protezioni che richiedono esecuzione JS reale (es. challenge JS-based), da rivalutare sulla base degli esiti della calibrazione.
- **Scheduler interno** (APScheduler, incluso nel processo Python): decide quando far partire un nuovo giro di scraping per ciascuna marca. Nessuna dipendenza da Task Scheduler di Windows.
- **PostgreSQL**: storicizzazione di annunci, prezzi, run ed eventi.
- **Backend API** (FastAPI): espone stato/metriche per la dashboard, riceve i comandi manuali (avvio/pausa per marca).
- **Dashboard web** (frontend React con grafici): monitoraggio in tempo reale, sostituisce qualsiasi canale di notifica esterno (niente email/Telegram).

## 5. Strategia di crawling in due fasi

Per ogni run di scraping di una marca:

1. **Fase snippet (prioritaria, veloce).** Scorre tutte le pagine di ricerca risultanti dallo split modello/anno ed estrae i dati già presenti nel JSON `__NEXT_DATA__` di ogni pagina: marca, modello, versione, prezzo (raw numerico), anno/mese immatricolazione, km, alimentazione, cambio, potenza (kW/CV), località, tipo venditore. Questa fase da sola copre già la maggior parte dei campi chiave necessari ad Auto-BI, quindi ha valore anche prima che la fase di dettaglio sia completata.
2. **Fase dettaglio (in background, ritmo prudente).** Ogni annuncio mai visto prima viene messo in una coda per la visita alla pagina di dettaglio (stesso meccanismo: fetch HTTP + estrazione `__NEXT_DATA__.props.pageProps.listingDetails`), che arricchisce il record con i campi rimanenti: coordinate geografiche, colore carrozzeria, cilindrata, numero posti/porte, cambio dettagliato (marce), consumi/emissioni WLTP, dettagli venditore, data di creazione annuncio, ecc. Questa coda viene processata a velocità sicura, senza bloccare l'avvio del prossimo giro di scraping della fase snippet.

## 6. Calibrazione del ritmo e della frequenza (x giorni)

La frequenza dei run (ogni "x" giorni, come richiesto originariamente) **non è fissata a priori**: viene determinata empiricamente.

- Il crawler parte con un ritmo conservativo: richieste sequenziali (non parallele) all'interno di una marca, delay randomico 3–8 secondi tra le richieste, pause più lunghe periodiche per simulare un pattern meno robotico.
- Durante una fase iniziale di calibrazione — condotta **dentro** l'infrastruttura di scraping vera e propria (non con test isolati esterni), quindi con backoff e logging già attivi — il sistema aumenta gradualmente il ritmo di richieste nel tempo e registra i segnali di blocco/rallentamento (HTTP 403, CAPTCHA, redirect a pagine di verifica, tempi di risposta anomali, rate limit espliciti) in funzione del ritmo raggiunto. Questo permette di scoprire in modo controllato se e a quale volume autoscout24 introduce protezioni più sofisticate, senza rischiare un blocco immediato dell'unico IP disponibile.
- Questi dati calibrano sia il ritmo per-richiesta sia la frequenza x tra un giro completo e il successivo per marca.
- Dato il volume iniziale (decine di migliaia di annunci per marca nel primo run "baseline"), la prima scansione completa di dettaglio per tutte le marche MVP richiederà presumibilmente diversi giorni: questo è atteso e accettato, dato che la fase snippet fornisce comunque valore immediato.

## 7. Modello dati (schema DB)

**`listings`** — stato corrente di ogni annuncio, chiave primaria = `id` (UUID) fornito da autoscout24, stabile tra snippet e dettaglio e presente nell'URL dell'annuncio.

Campi: `cross_reference_id` (ID numerico secondario, da `identifier.crossReferenceId`), marca, modello, versione/allestimento, anno/mese immatricolazione (da `firstRegistrationDateRaw`), km (da `mileageInKmRaw`), alimentazione, cambio, potenza kW/CV (da `rawPowerInKw`/`rawPowerInHp`), cilindrata (da `rawDisplacementInCCM`), carrozzeria, colore, numero proprietari, provincia/comune (da `location.city`, formato "Comune - Provincia - Sigla"), coordinate (`location.latitude`/`longitude`), tipo venditore (privato/concessionario, da `seller.type`), prezzo corrente (raw numerico), IVA esposta (bool), url, data creazione annuncio (da `createdTimestampWithOffset`), `first_seen_at`, `last_seen_at`, `last_checked_at`, `status` (attivo/venduto/rimosso — nostro stato interno, distinto dal campo `status` di autoscout24), `sold_at`, `detail_scraped` (bool), `raw_snippet`/`raw_detail` (jsonb — copia grezza dei blocchi JSON, per poter arricchire il modello con nuovi campi senza bloccare lo scraping).

**`price_history`** — una riga per ogni variazione di prezzo osservata: `listing_id`, `prezzo`, `rilevato_il`.

**`scrape_runs`** — un run per marca/sessione: `iniziato_il`, `finito_il`, esito, contatori (annunci visti, nuovi, prezzi variati, venduti rilevati, errori). Alimenta la dashboard.

**`scrape_events`** — log strutturato di errori/anomalie/blocchi per run: livello, messaggio, url, timestamp. Usato dal log viewer della dashboard.

## 8. Rilevamento cambiamenti e logica "venduto"

Per ogni run di una marca, confrontiamo l'insieme degli ID annuncio trovati (attraverso tutte le sotto-query modello/anno — copertura completa) con quelli già presenti a DB in stato "attivo":

- **ID nuovo** → nuova riga in `listings`, prima riga in `price_history`, messo in coda per la visita di dettaglio.
- **ID noto, prezzo invariato** → aggiornati solo `last_seen_at`/`last_checked_at`.
- **ID noto, prezzo cambiato** → aggiornato il prezzo corrente e aggiunta una riga in `price_history`.
- **ID noto ma non trovato in nessuna sotto-query del run corrente** → non marcato subito "venduto". Prima viene **verificata direttamente la pagina di dettaglio dell'annuncio** (fetch dell'url salvato): se la richiesta ritorna un errore HTTP (404/410) oppure il JSON viene restituito ma il campo `listingDetails.status` non è più `"Active"`, si conferma `status = venduto`, si registra `sold_at` e si calcola la durata di permanenza sul mercato da `first_seen_at`. Se invece la pagina di dettaglio risponde 200 con `status: "Active"`, l'annuncio **non** viene marcato venduto: si registra un evento di anomalia (possibile falla di copertura nello split modello/anno) e l'annuncio resta attivo, da ricontrollare al giro successivo.

Questo doppio controllo evita falsi "venduto" dovuti a riordinamenti dei risultati di ricerca o a casi limite nei bucket di split.

## 9. Dashboard di monitoraggio

Frontend React (dietro l'API FastAPI), con:

- Stato corrente per marca: in esecuzione / in pausa / bloccato / in attesa di prossimo run programmato.
- Ultimo run per marca con esito e contatori (nuovi annunci, prezzi aggiornati, venduti rilevati, errori).
- Grafici andamento nel tempo: annunci raccolti, prezzi medi, tasso di errori/blocchi per run.
- Log viewer: eventi/errori consultabili con filtro per marca/run/livello.
- Controlli manuali: avvia/metti in pausa lo scraping per singola marca, forza un run immediato.

## 10. Gestione blocchi ed errori

- Se lo scraper rileva segnali di blocco (403 ripetuti, CAPTCHA, tempi di risposta anomali), applica **backoff esponenziale** e riprova automaticamente.
- Se il blocco persiste oltre una soglia configurabile, il run per quella marca passa in stato **"bloccato"**, visibile immediatamente in dashboard (badge di stato + evento in log), e lo scraping per quella marca si ferma finché non si risolve da solo entro un timeout più lungo oppure viene ripreso manualmente dalla dashboard.
- Nessun canale di notifica esterno (email/Telegram): la dashboard stessa è il canale di notifica.

## 11. Testing

- **Unit test** su parsing dei blocchi JSON `__NEXT_DATA__` (snippet e dettaglio) e sulla logica di split modello/anno, usando fixture JSON/HTML reali salvate localmente (nessuna dipendenza da rete nei test).
- **Unit test** sulla logica di rilevamento nuovo/prezzo cambiato/venduto contro un Postgres di test (container dedicato).
- **Test di integrazione leggeri** su un sottoinsieme reale limitato (es. un solo modello, poche pagine) per validare la pipeline end-to-end senza sovraccaricare autoscout24 durante lo sviluppo.
- Nessun test end-to-end automatizzato continuo contro il sito reale in CI, per non consumare la "quota" di richieste sicure: la validazione contro il sito reale avviene manualmente/con run mirati durante la fase di calibrazione.

## 12. Fuori scope (di questo documento)

- Espansione a tutte le marche/tutta Italia (fase successiva al MVP).
- Distribuzione su più macchine e sistemi di IP rotation (da valutare solo se i limiti reali misurati in fase di calibrazione lo richiedono).
- La webapp di analisi **Auto-BI**: spec separata, che consumerà i dati prodotti da questo scraper.
