# SPEC — AutoScout24 Price Intelligence
**Versione 1.0 — 23/07/2026 — Documento di specifica per implementazione con Claude Code**

Due applicazioni distinte che condividono un data layer:

1. **`as24-scraper`** — raccolta continua degli annunci auto da autoscout24.it
2. **`auto-bi`** — webapp di analisi prezzi e time-to-sell per auto trader

---

## 0. Contesto e obiettivo di business

L'utente finale è un auto trader che acquista veicoli da canali B2B o privati e deve decidere:
- **a che prezzo può rivendere** un veicolo specifico (marca, modello, anno, km, alimentazione, cambio, versione);
- **in quanto tempo** lo venderà a un dato prezzo;
- **come varia** prezzo/tempo in base alla **localizzazione** del veicolo in Italia.

La base dati è costruita osservando nel tempo gli annunci pubblicati su autoscout24.it (~428k annunci attivi in Italia): prezzi pubblicati, variazioni di prezzo, durata di pubblicazione (proxy del tempo di vendita).

---

## 1. Architettura complessiva e fasi

### Fase 0 — MVP locale (sviluppo e calibrazione)
- Tutto in **Docker Compose** sulla macchina di sviluppo (ThinkPad, Ubuntu): container `postgres:16`, container scraper, container API + frontend Auto-BI.
- Worker di scraping aggiuntivi eseguibili su altri PC della LAN: puntano allo **stesso Postgres centrale** (il coordinamento avviene interamente via DB, vedi §2.6 — nessun message broker).
- Obiettivo della fase: validare parser, calibrare i rate limit sostenibili senza IP rotation, raccogliere le prime settimane di storico su un sottoinsieme di marche.

### Fase 1 — Migrazione Google Cloud (a regime)
- **Cloud SQL for PostgreSQL** come DB operativo (stesso schema, migrazione via dump/restore — zero modifiche al codice, solo connection string).
- **BigQuery** come datalake analitico: sync incrementale giornaliero dalle tabelle operative (job `export-bq` in §2.9; in alternativa Datastream CDC). BigQuery serve le query analitiche pesanti di Auto-BI, BigQuery ML / Vertex AI per i modelli di pricing di fase 2.
- Scraper su **1-N VM Compute Engine e2-small** (o Cloud Run Jobs schedulati): i worker sono identici a quelli locali, cambia solo `DATABASE_URL`.
- Auto-BI su **Cloud Run** (API + frontend), auth via **Firebase Auth / Google Identity Platform**.

> **Decisione architetturale (da rispettare):** il datalake (BigQuery) è il layer *analitico*, non lo store primario dello scraper. Lo scraper richiede upsert, lock, code di lavoro e integrità transazionale: serve un DB relazionale. Postgres è quindi il DB operativo in entrambe le fasi; BigQuery si aggiunge in Fase 1 come replica analitica. Il codice deve essere scritto fin da subito con: (a) accesso DB isolato in un layer repository, (b) config via env vars, (c) nessuna dipendenza da filesystem locale per lo stato.

### Repository
Monorepo:
```
as24-intelligence/
├── scraper/            # as24-scraper (Python)
│   ├── src/as24_scraper/
│   ├── tests/
│   └── pyproject.toml
├── autobi/
│   ├── api/            # FastAPI
│   └── web/            # React + Vite
├── db/
│   ├── migrations/     # Alembic
│   └── bq/             # DDL BigQuery + job di sync
├── docker-compose.yml
├── deploy/             # Terraform GCP (fase 1)
└── docs/
```

---

## 2. Applicazione 1 — `as24-scraper`

### 2.1 Strategia di fetching (importante: NON replicare l'approccio Zalando alla lettera)

autoscout24.it è un'applicazione **Next.js**: ogni pagina (lista risultati e dettaglio annuncio) incorpora un tag `<script id="__NEXT_DATA__" type="application/json">` con **tutti i dati strutturati** già in JSON (annunci della pagina, prezzo, km, immatricolazione, alimentazione, cambio, potenza, equipaggiamenti, dati venditore, contatore risultati totali, paginazione).

Strategia a due livelli:

1. **Primaria — HTTP puro**: richieste GET con **`curl_cffi`** (impersonificazione TLS/JA3 di Chrome, es. `impersonate="chrome124"`), header realistici, cookie jar persistente per worker. Parsing: estrarre `__NEXT_DATA__` con selectolax/regex e fare `json.loads`. **Nessun parsing del DOM per i dati** (il DOM si usa solo per localizzare lo script tag). ~10x più veloce e leggero di un browser, molto più robusto ai redesign grafici.
2. **Fallback — Playwright Firefox** con i pattern anti-detection dello scraper Zalando di riferimento (`firefox_user_prefs`, stealth init script, viewport/UA randomizzati, locale it-IT/Europe/Rome). Si attiva automaticamente per un worker quando il canale HTTP riceve challenge anti-bot ripetute (403/429/pagina challenge riconosciuta), e serve anche come sonda in fase di calibrazione.

**Primo task di implementazione (spike obbligatorio):** script `probe.py` che scarica 1 pagina lista + 1 pagina dettaglio, salva il JSON `__NEXT_DATA__` grezzo in `docs/samples/`, e genera la mappatura campo JSON → colonna DB. Tutti i parser si scrivono contro questi sample salvati (fixture per i test). Se la struttura reale differisse da quanto qui assunto, adeguare la mappatura, non l'architettura.

### 2.2 Vincolo strutturale: cap dei risultati e motore di segmentazione

Le ricerche AutoScout24 restituiscono al massimo **~20 pagine × 20 annunci ≈ 400 annunci per query**, qualunque sia il totale dichiarato. Con ~428k annunci attivi, la copertura completa richiede di partizionare il mercato in **segmenti** ciascuno con `risultati ≤ 380` (margine di sicurezza).

**Segmento** = combinazione di filtri URL, es. `/lst/volkswagen/golf?fregfrom=2018&fregto=2020&pricefrom=15000&priceto=20000&cy=I&damaged_listing=exclude&atype=C&ustate=N,U`.

**Algoritmo di segmentazione ricorsiva** (componente centrale):
1. Nodo radice per marca (lista marche da pagina/sitemap AutoScout24, tabella `makes`).
2. Per un nodo, leggere il **conteggio totale risultati** dal JSON della prima pagina lista.
3. Se `count ≤ 380` → segmento **foglia** (scansionabile). Altrimenti **split** sulla prossima dimensione nell'ordine: `modello → fascia prezzo → anno immatricolazione → fascia km → regione`. Le fasce prezzo/km si dividono per bisezione dell'intervallo.
4. I segmenti foglia vengono salvati in tabella `segments` con i loro filtri (JSONB), `expected_count`, `last_scan_at`.
5. **Ri-bilanciamento**: se durante uno scan un segmento supera 380 risultati → marcarlo `needs_split` e ri-segmentarlo; se due segmenti fratelli sommano < 250 → merge. Job `rebalance` eseguito settimanalmente.

Stima dimensioni: ~428k annunci / ~300 medi per segmento ≈ **1.500-2.500 segmenti foglia**, ~21.500 pagine lista per ciclo completo.

### 2.3 Ciclo di scansione (due pipeline separate)

**Pipeline A — List Scan** (frequente, leggera): per ogni segmento foglia, scorrere tutte le pagine lista ed estrarre per ogni annuncio: `listing_id` (GUID AutoScout), prezzo, marca, modello, versione, anno, km, alimentazione, cambio, potenza, CAP/provincia venditore, tipo venditore (dealer/privato), URL, flag (es. leasing). Per ogni annuncio:
- **nuovo** → insert in `listings` (stato `pending_detail`) + push in coda dettagli;
- **noto** → aggiorna `last_seen_run_id`; se il prezzo differisce dall'ultimo registrato → insert in `price_history`;
- fine ciclo completo (tutti i segmenti scansionati in un `scan_cycle`): tutti i listing `active` con `last_seen_run_id < ciclo corrente` → stato `delisted`, `delisted_at = now()`, calcolo `days_on_market = delisted_at - first_seen_at`.

**Pipeline B — Detail Fetch** (solo annunci nuovi): visita la pagina dettaglio ed estrae il set completo: versione/allestimento, cilindrata, kW/CV, classe emissioni, colore, tipo carrozzeria, porte/posti, proprietari precedenti, garanzia, equipaggiamenti (array), descrizione (troncata a 2.000 char), dealer name e provincia. Stato → `active`.

> **Privacy/GDPR — vincolo hard:** per i venditori **privati** NON raccogliere né salvare nome, telefono, indirizzo esatto o altri dati personali; solo CAP/provincia e flag `seller_type='private'`. Per i dealer si salva la ragione sociale (dato aziendale pubblico).

**Rilevazione ripubblicazioni:** quando un annuncio nuovo compare, cercare tra i `delisted` degli ultimi 14 giorni un match su `(make, model, year, fuel, gearbox, power_kw, km ± 1.000, seller_zip)`. Se match → `repost_of = <old_id>`; l'annuncio vecchio viene marcato `relisted` (escluso dalle statistiche di vendita), e `first_seen_at` effettivo per il days-on-market della nuova inserzione eredita quello originale. Questo evita di conteggiare come "venduta" un'auto solo ripubblicata.

### 2.4 Stima volumi e frequenza (per dimensionare i default)

- Ciclo lista completo: ~21.500 richieste. A 1 req/4s → ~21.600 req/giorno per worker ⇒ **1 worker copre l'intero mercato in ~1 giorno**; frequenza target di 2-3 giorni ampiamente raggiungibile anche con margini prudenziali o con metà rate.
- Dettagli: solo nuovi annunci. Ipotesi churn 5-8%/settimana ⇒ 20-35k dettagli/settimana, assorbibili da 1-2 worker dedicati.
- La risoluzione del days-on-market = frequenza di scan ⇒ con scan ogni 2-3 giorni, errore ±2-3 giorni: adeguato allo scopo.

### 2.5 Rate limiting, anti-detection, calibrazione

Config per worker (env/YAML, tutti modificabili a caldo via tabella `config`):
- `min_delay` / `max_delay` tra richieste (default iniziale prudente: 3.0-6.0s, jitter uniforme);
- max richieste/ora per worker (default 800);
- pause lunghe casuali (ogni 200-400 richieste, pausa 60-180s: simula sessioni umane);
- rotazione User-Agent realistici; cookie jar persistente per worker (sessioni coerenti, non "amnesiache");
- fascia oraria operativa configurabile (default 07:00-24:00).

**Backoff adattivo:** su 403/429/challenge → retry con backoff esponenziale (30s, 2m, 8m); 3 fallimenti consecutivi → il worker entra in `cooldown` 30-60 min e il segmento torna in coda; block-rate di ciclo > 2% → dimezzamento automatico del rate globale (scritto in `config`, log WARNING). Nessuna IP rotation in Fase 0; se i test dimostrano che il rate necessario non è sostenibile da IP singoli, la scalata è **più worker su IP diversi** (LAN/VM), non proxy rotanti.

**Comando `calibrate`:** procedura automatica che, su segmenti di test, riduce progressivamente i delay a gradini (6s → 4s → 3s → 2s → 1.5s), misura block-rate per gradino su ≥500 richieste e riporta il rate massimo sostenibile. Output: report + raccomandazione di config. Da eseguire nella prima settimana per determinare la "X" di frequenza.

### 2.6 Coordinamento multi-worker (via DB, senza broker)

- Unità di lavoro = **segmento** (pipeline A) o **listing_id** (pipeline B), in tabelle-coda su Postgres.
- Claim atomico con `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1`, campi `claimed_by` (worker_id = hostname+pid), `claimed_at`, `lease_expires_at` (15 min; scaduto il lease il task torna disponibile → tolleranza ai crash).
- Ogni worker è un processo indipendente: `python -m as24_scraper worker --pipelines list,detail`. Identico su ThinkPad, PC in LAN, VM GCP.
- Tabella `scan_cycles` (id, started_at, finished_at, segments_done/total, listings_seen, new, price_changes, delisted, errors, block_rate) — è anche la base del monitoring.

### 2.7 Schema DB operativo (Postgres 16, migrazioni Alembic)

```sql
makes(id, name, slug, active bool)
models(id, make_id FK, name, slug)

segments(
  id, make_id, model_id NULL, filters JSONB, url_template TEXT,
  expected_count INT, status TEXT,           -- active | needs_split | merged
  last_scan_cycle_id, last_scan_at, created_at)

listings(
  id TEXT PK,                                -- GUID AutoScout24
  make_id, model_id, version TEXT,
  first_reg_month INT, first_reg_year INT,
  mileage_km INT, fuel TEXT, gearbox TEXT, power_kw INT,
  body_type TEXT, doors INT, seats INT, color TEXT,
  emission_class TEXT, previous_owners INT, warranty_months INT,
  vehicle_condition TEXT,                    -- new | used | km0 (da ustate)
  seller_type TEXT,                          -- dealer | private
  seller_name TEXT NULL,                     -- SOLO dealer
  seller_zip TEXT, seller_province TEXT, seller_region TEXT,
  url TEXT, equipment JSONB, description TEXT,
  current_price NUMERIC, initial_price NUMERIC,
  status TEXT,        -- pending_detail | active | delisted | relisted | error
  repost_of TEXT NULL FK,
  first_seen_at TIMESTAMPTZ, last_seen_at TIMESTAMPTZ,
  last_seen_run_id, delisted_at TIMESTAMPTZ NULL,
  days_on_market INT NULL,
  detail_scraped_at TIMESTAMPTZ NULL, raw_detail JSONB)  -- JSON grezzo per re-parsing

price_history(listing_id FK, observed_at, price NUMERIC, scan_cycle_id)
scan_cycles(...)          -- vedi §2.6
work_queue(...)           -- code pipeline A/B con lease
config(key, value, updated_at)
errors(id, ts, worker_id, url, http_status, kind, payload_snippet)
```
Indici: `listings(make_id, model_id, first_reg_year)`, `listings(status)`, `listings(seller_region)`, `price_history(listing_id, observed_at)`. Mappatura CAP→provincia→regione: tabella statica `geo_zip` inclusa nel repo.

`raw_detail JSONB` conserva il `__NEXT_DATA__` di dettaglio: consente di ri-estrarre campi in futuro senza ri-scaricare.

### 2.8 Perimetro dati (filtri fissi in ogni URL di segmento)
- Solo autovetture: `atype=C`
- Solo Italia: `cy=I`
- Escluse danneggiate: `damaged_listing=exclude`
- Nuovo + usato + km0: `ustate=N,U` (il campo `vehicle_condition` permette di filtrare in analisi)
- Dealer e privati inclusi entrambi.

### 2.9 CLI e operatività

```
as24 init-db            # migrazioni
as24 seed-makes         # popola marche/modelli
as24 build-segments [--make X]   # segmentazione ricorsiva
as24 rebalance
as24 worker --pipelines list,detail [--rate-profile safe|normal|fast]
as24 calibrate --segment-sample 5
as24 cycle-status       # avanzamento ciclo corrente
as24 stats              # KPI DB: annunci attivi, delisted, block rate...
as24 export-bq [--full|--incremental]   # sync verso BigQuery (fase 1)
```
Logging strutturato JSON (stdout + file), livelli per componente. Ogni run scrive metriche in `scan_cycles`.

### 2.10 Test e qualità
- Parser testati su fixture reali salvate (da `probe.py`), inclusi casi limite: prezzo "su richiesta", leasing (rata ≠ prezzo: salvare `price_type`), annunci senza km, versioni con caratteri speciali.
- Test d'integrazione della macchina a stati listing (new→active→price change→delisted→repost) su DB effimero.
- **Definition of Done Fase 0:** 5 marche pilota (proposta: Fiat, Volkswagen, BMW, Toyota, Dacia — mix volume/segmento) scansionate integralmente per 3 cicli consecutivi, block-rate < 1%, delisting e price history verificati a campione manualmente su 30 annunci.

---

## 3. Applicazione 2 — `auto-bi`

### 3.1 Stack
- **Backend:** FastAPI (Python 3.12) + SQLAlchemy; legge da Postgres (Fase 0) con query layer isolato che in Fase 1 potrà instradare le query pesanti su BigQuery. Cache Redis opzionale per aggregazioni (fase 2; in MVP bastano **viste materializzate** Postgres aggiornate a fine ciclo di scan).
- **Frontend:** React + Vite + TypeScript, shadcn/ui + Tailwind, grafici con Recharts, mappa Italia con ECharts/D3 (choropleth per regione/provincia).
- **Auth (predisposta al SaaS multi-utente):** Firebase Auth (email+Google). Tabelle `users`, `organizations`, `memberships` fin dall'MVP; tutte le API richiedono JWT; ruoli `admin`/`viewer`. In MVP esiste un solo utente/org, ma il modello dati è già multi-tenant.

### 3.2 Dashboard MVP (4)

**D1 — Market Explorer.** Barra filtri persistente (riusata in tutte le dashboard): marca → modello → versione (ricerca testuale), anno immatricolazione (range), km (range), alimentazione, cambio, tipo venditore, condizione (nuovo/usato/km0), regione/provincia, stato annuncio (attivi / venduti ultimi N giorni / tutti). Output: n° annunci, prezzo mediano, P25-P75, min-max; istogramma distribuzione prezzi; trend prezzo mediano nel tempo; tabella annunci ordinabile con sparkline dello storico prezzo e link ad AutoScout; scatter prezzo vs km colorato per anno.

**D2 — Time-to-Sell.** Sul filtro corrente, solo annunci `delisted` (esclusi `relisted`): giorni di pubblicazione mediani e distribuzione; **curva prezzo↔velocità**: days-on-market mediano per quintile di prezzo relativo alla mediana dei comparables (es. "−10% vs mediana → 12 gg; +10% → 41 gg"); confronto per regione; % annunci che hanno ribassato prima della vendita ed entità media del ribasso.

**D3 — Pricing Tool (la killer feature).** Form di input veicolo: marca, modello, versione (opzionale, match fuzzy), anno, km, alimentazione, cambio, potenza, provincia dell'auto. Output:
- **Comparables**: selezione automatica (vedi §3.3) con tabella e possibilità di escludere manualmente singoli comparables (ricalcolo live);
- **Prezzo suggerito**: P25 / mediana / P75 dei comparables attivi + venduti recenti;
- **Curva prezzo → tempo di vendita atteso**: per 5 livelli di prezzo (da P10 a P90) il days-on-market atteso stimato dai venduti;
- **Effetto location**: stessa analisi ristretta a macro-area (Nord-Ovest/Nord-Est/Centro/Sud-Isole) e delta vs nazionale;
- **Calcolatore margine**: input prezzo d'acquisto e costi (trasporto, ricondizionamento, fisso) → margine ai vari prezzi di vendita e tempo atteso. Pulsante "salva valutazione" (tabella `valuations`, storico delle valutazioni fatte).

**D4 — Price Dynamics & Geo.** Mappa choropleth Italia: prezzo mediano e days-on-market per regione sul filtro corrente; indice ribassi (frequenza e profondità delle riduzioni di prezzo) per regione e per fascia di prezzo; volumi di nuovo inventario vs delisting per settimana (proxy domanda/offerta).

### 3.3 Modulo pricing — metodologia MVP (no ML)

Selezione comparables a **rilassamento progressivo** finché `n ≥ 30` (minimo assoluto 10, sotto il quale l'app mostra warning "bassa confidenza"):
1. stesso modello, anno ±0, km ±15%, stessa alimentazione e cambio, stessa regione;
2. allarga a anno ±1, km ±25%, macro-area;
3. allarga a anno ±2, km ±40%, nazionale (con nota dei criteri usati, sempre visibili all'utente).

Statistiche calcolate separatamente su **attivi** (prezzi chiesti oggi) e **delisted ultimi 90 giorni** (prezzi a cui il mercato ha assorbito). La curva prezzo→tempo usa i delisted: bucket per prezzo relativo alla mediana dei comparables (−20/−10/0/+10/+20%) → mediana days-on-market per bucket.

**Fase 2 (predisposta, non in MVP):** modello di quantile regression (LightGBM, oppure BigQuery ML una volta in GCP) addestrato su tutti i delisted: input attributi veicolo + prezzo relativo → distribuzione del tempo di vendita; e modello di prezzo edonico per il "fair value". Lo schema dati MVP contiene già tutte le feature necessarie: nessun rework.

### 3.4 API principali

```
GET  /api/filters/makes | /models?make= | /versions?model=&q=
POST /api/market/summary        # filtri → KPI + distribuzioni (D1)
POST /api/market/listings       # filtri → tabella paginata
GET  /api/listings/{id}         # dettaglio + price history
POST /api/tts/summary           # filtri → metriche time-to-sell (D2)
POST /api/pricing/evaluate      # veicolo → comparables + prezzi + curva (D3)
POST /api/pricing/valuations    # salva/lista valutazioni
POST /api/geo/summary           # filtri → dati mappa (D4)
GET  /api/meta/data-freshness   # ultimo ciclo scan completato (mostrato in header UI)
```
Tutte POST con body `filters` condiviso (stesso schema Pydantic ovunque). Risposte < 2s sui filtri tipici: usare le viste materializzate per D1/D2/D4; `pricing/evaluate` può calcolare live (query su indici).

### 3.5 Non-goals dell'MVP Auto-BI
No export PDF, no alert/notifiche, no billing, no white-label, no app mobile (layout responsive sì). Verranno valutati dopo, anche osservando prodotti comparabili sul mercato.

---

## 4. Roadmap e milestone

| # | Milestone | Contenuto | Accettazione |
|---|-----------|-----------|--------------|
| M0 | Spike & fondamenta (sett. 1) | `probe.py`, mappatura JSON→schema, docker-compose, migrazioni DB, seed marche | Sample JSON committati, parser lista+dettaglio verdi su fixture |
| M1 | Scraper pilota (sett. 2-3) | Segmentazione, pipeline A+B, lifecycle listing, `calibrate`, 5 marche pilota | DoD §2.10 soddisfatta; report calibrazione con X sostenibile |
| M2 | Auto-BI MVP (sett. 3-5, in parallelo da M1) | API + D1, D2, D3; auth; viste materializzate | Pricing Tool valida 5 casi reali forniti dall'utente con giudizio "utilizzabile" |
| M3 | Estensione totale (sett. 5-6) | Tutte le marche, rebalance, D4, hardening (retry, monitor, alert su block-rate) | 2 cicli completi nazionali consecutivi senza intervento manuale |
| M4 | Migrazione GCP | Terraform, Cloud SQL, VM worker, Cloud Run, `export-bq`, Firebase Auth in prod | Parità funzionale in cloud; sync BigQuery giornaliero attivo |

---

## 5. Rischi e note

- **Anti-bot:** AutoScout24 potrebbe usare protezioni gestite (es. challenge JS). Il design a doppio canale (HTTP + Playwright fallback) e il backoff adattivo sono la mitigazione; se il block-rate resta alto anche a rate bassi, la leva è aggiungere worker su IP distinti, non accelerare i singoli worker. Da verificare empiricamente in M0/M1.
- **Cap ~400 risultati:** valore da **validare empiricamente** in M0; l'algoritmo di segmentazione prende comunque la soglia da config.
- **Semantica "venduto":** delisting è un proxy (include ritiri); la repost-detection mitiga, e in aggregato la metrica è solida. Nell'UI usare sempre la dicitura "giorni di pubblicazione".
- **Legale:** lo scraping di AutoScout24 è quasi certamente contrario ai loro ToS; il perimetro qui definito (dati pubblici, nessun dato personale di privati, rate contenuti) riduce ma non elimina il rischio di contestazioni o blocchi. Decisione di business dell'utente; il vincolo GDPR §2.3 è invece non negoziabile nel codice.
- **Leasing/"prezzo su richiesta":** gestiti con `price_type` ed esclusi di default dalle statistiche di prezzo.

---

## 6. Istruzioni operative per Claude Code

1. Partire da M0: **non scrivere parser prima di aver salvato i sample reali** con `probe.py`.
2. Ogni componente con test; i parser esclusivamente contro fixture committate.
3. Config solo via env/`config` table; nessun valore hardcodato di rate/soglie.
4. Commit atomici per milestone; ogni milestone chiude con un breve report in `docs/` (cosa è stato validato, metriche osservate, deviazioni dalla spec).
5. In caso di divergenza tra questa spec e la realtà del sito (struttura JSON, cap risultati, filtri URL), adeguare i dettagli mantenendo l'architettura, e documentare la deviazione in `docs/deviations.md`.
