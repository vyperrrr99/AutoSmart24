# Gestione marche, pianificazione e monitoraggio live — Design

Data: 2026-07-25

## 1. Obiettivo

Oggi le 5 marche monitorate (Fiat, Volkswagen, BMW, Audi, Mercedes-Benz) sono una costante fissa nel codice (`MVP_BRANDS` in `config.py`): per cambiarle serve modificare il codice e ricostruire il container. Il filtro anno (`SCRAPE_MAX_LISTING_AGE_YEARS`) e la cadenza (`SCRAPE_INTERVAL_DAYS`) sono variabili d'ambiente globali, uguali per tutte le marche.

Questo documento copre tre funzionalità nuove, tutte gestibili dalla dashboard senza toccare codice o variabili d'ambiente:

1. **Selezione marche**: scegliere quali marche monitorare da un catalogo completo del sito (non più solo 5 fisse).
2. **Configurazione per marca**: filtro anno e pianificazione (giorno/ora) impostabili singolarmente per ogni marca, con un meccanismo per applicare un valore predefinito a tutte in un colpo solo.
3. **Monitoraggio in tempo reale**: la dashboard si aggiorna automaticamente ogni pochi secondi durante un run attivo, mostrando progressi/eventi/blocchi senza bisogno di ricaricare.

## 2. Scoperta tecnica: il catalogo marche è già disponibile in una singola richiesta

Verificato in diretta sul sito: la risposta JSON di **qualunque** pagina di ricerca (`__NEXT_DATA__.props.pageProps.taxonomy`) contiene, oltre ai dati già usati (`taxonomy.models`), anche `taxonomy.makes` — il catalogo **completo** di tutte le marche del sito, 290 al momento della verifica, ciascuna con `{label, value}` dove `value` è l'ID interno necessario per interrogare i modelli di quella marca:

```json
"makes": {
  "6": {"label": "Alfa Romeo", "value": 6},
  "8": {"label": "Aston Martin", "value": 8},
  "9": {"label": "Audi", "value": 9},
  "28": {"label": "Fiat", "value": 28},
  ...
}
```

Conseguenza diretta: **non serve interrogare il sito una marca alla volta**. Una singola richiesta (es. alla pagina di ricerca Fiat, già usata oggi per scoprire i modelli) restituisce l'intero catalogo. Il catalogo va scaricato una volta e salvato, con un pulsante per aggiornarlo quando si vuole (le marche cambiano raramente).

**Nota aperta**: il campo non include lo slug URL (es. `fiat`, `mercedes-benz`), necessario per costruire le richieste di ricerca. Va derivato dal nome (minuscolo, spazi sostituiti da trattini) — pattern che corrisponde alle 5 marche attuali (`Mercedes-Benz` → `mercedes-benz`) ma non ancora validato su tutte le 290. Il piano di implementazione deve includere una verifica empirica su un campione ampio prima di considerarlo affidabile, con un meccanismo di correzione manuale per le eccezioni (es. un campo slug sovrascrivibile in caso lo slug derivato non funzioni).

## 3. Modello dati — due nuove tabelle

**`brand_catalog`** — il catalogo completo del sito, popolato dal fetch di sezione 2:

| Campo | Tipo | Note |
|---|---|---|
| `make_id` | INTEGER, chiave primaria | `taxonomy.makes[k].value` |
| `display_name` | VARCHAR(64) | `taxonomy.makes[k].label` |
| `slug` | VARCHAR(64) | derivato automaticamente, sovrascrivibile manualmente se necessario |
| `synced_at` | TIMESTAMP | quando è stato scaricato/aggiornato |

**`tracked_brands`** — le marche effettivamente monitorate (sostituisce `MVP_BRANDS`):

| Campo | Tipo | Note |
|---|---|---|
| `make_id` | INTEGER, chiave primaria, FK a `brand_catalog` | |
| `slug` | VARCHAR(64) | copiato da `brand_catalog` al momento dell'aggiunta |
| `display_name` | VARCHAR(64) | copiato da `brand_catalog` al momento dell'aggiunta |
| `paused` | BOOLEAN, default false | sostituisce l'attuale stato solo-in-memoria dello scheduler — ora persiste tra riavvii |
| `year_from_years` | INTEGER, nullable | filtro anno per questa marca; NULL = nessun filtro (scrape tutti gli anni) |
| `schedule_day_of_week` | VARCHAR(3), nullable | `mon`..`sun`; NULL = ogni giorno |
| `schedule_hour` | INTEGER, default 3 | 0-23 |
| `schedule_minute` | INTEGER, default 0 | 0-59 |
| `created_at` | TIMESTAMP | |

Pianificazione volutamente semplice (giorno-della-settimana opzionale + ora + minuto, non cron completo): un menu a tendina più un selettore d'ora si costruiscono facilmente in un'interfaccia, una sintassi cron no. Si traduce direttamente in un `CronTrigger` di APScheduler (`day_of_week=None` → ogni giorno, altrimenti un giorno preciso una volta a settimana).

**Migrazione dei dati esistenti**: al primo avvio, se `tracked_brands` è vuota, viene popolata automaticamente con le 5 marche attuali (stessi `make_id`/slug di oggi, filtro anno e pianificazione presi dalle variabili d'ambiente attuali) — il comportamento di oggi non si perde nel passaggio.

## 4. Scheduler dinamico

Lo scheduler (`BrandScheduler`) oggi crea i job una sola volta all'avvio da `MVP_BRANDS`. Deve diventare "vivo":

- All'avvio, legge tutte le righe di `tracked_brands` e crea un job per ciascuna (rispettando `paused`).
- Ogni azione dall'interfaccia (aggiungi, modifica pianificazione, rimuovi, pausa/riprendi) agisce **subito** sia sulla riga del database sia sul job live corrispondente (aggiunta/riprogrammazione/rimozione) — nessun riavvio necessario, coerente con come già oggi funzionano pausa/ripresa/avvia-ora.
- **Il filtro anno viene riletto dal database a ogni esecuzione**, non "congelato" nel momento in cui il job viene programmato — così una modifica al filtro anno ha effetto anche su un job già schedulato da tempo, senza dover ricreare il job.

## 5. Nuove API

| Endpoint | Funzione |
|---|---|
| `POST /brand-catalog/refresh` | Scarica/aggiorna il catalogo completo dal sito (sezione 2), restituisce quante marche trovate |
| `GET /brand-catalog` | Elenco completo del catalogo, per il selettore nell'interfaccia |
| `POST /brands/bulk` | Aggiunge più marche insieme: `{make_ids: [...], year_from_years?, schedule_day_of_week?, schedule_hour, schedule_minute}` — crea le righe `tracked_brands` e i job corrispondenti |
| `PATCH /brands/{slug}` | Modifica filtro anno e/o pianificazione di una singola marca monitorata |
| `PATCH /brands/apply-defaults` | Applica filtro anno e/o pianificazione a **tutte** le marche monitorate in un colpo solo (sovrascrittura esplicita, nessuno stato "personalizzato" nascosto da gestire) |
| `DELETE /brands/{slug}` | Rimuove una marca dal monitoraggio (riga + job) |

Le API esistenti (`GET /brands`, `GET /brands/{slug}/runs`, `GET /brands/{slug}/events`, pausa/ripresa/avvia-ora) restano identiche nella forma, ma leggono da `tracked_brands` invece che dalla costante fissa.

## 6. Interfaccia

**Nuova schermata "Gestisci marche"**:
- Pulsante "Aggiorna catalogo" (chiama il refresh di sezione 5), con data ultimo aggiornamento visibile.
- Tabella filtrabile/ricercabile su tutte le marche del catalogo, con casella di spunta per riga (cerchi "peu", spunti Peugeot, ecc.).
- Selezione multipla → un'unica azione "Aggiungi selezionate", che usa il filtro anno e la pianificazione predefiniti correnti per tutte le marche appena aggiunte.
- Campo "Anno predefinito" + pianificazione predefinita, con pulsante "Applica a tutte le marche monitorate" — sovrascrive tutte in un colpo solo; da lì si può comunque modificare singolarmente una marca (icona di modifica sulla riga), eccezione che resta finché non si rilancia di nuovo "Applica a tutte".
- Elenco marche attualmente monitorate con modifica/rimozione per riga.

**Monitoraggio in tempo reale**: le card marca esistenti passano a un polling più frequente (es. ogni 3 secondi invece degli attuali 15) quando l'ultimo run di quella marca è `running` — tornano al ritmo normale (15s) quando non c'è nulla in corso, per non sovraccaricare inutilmente il backend quando tutto è fermo.

## 7. Gestione errori

- `POST /brand-catalog/refresh`: se il sito è irraggiungibile o il formato della risposta cambia, l'errore va mostrato chiaramente in interfaccia (non un fallimento silenzioso) — il catalogo esistente resta invariato finché il refresh non va a buon fine.
- `POST /brands/bulk` / `PATCH /brands/{slug}`: `make_id` non presente nel catalogo → errore esplicito, nessuna riga creata.
- Slug derivato che non corrisponde a una pagina valida del sito: il primo run di quella marca fallirebbe con un errore HTTP already gestito dalla rete di sicurezza esistente (`except Exception` → `status="error"`, evento loggato) — visibile in dashboard, correggibile manualmente aggiornando lo slug.

## 8. Testing

- Parsing del catalogo (`taxonomy.makes`) con fixture HTTP mockate (respx), inclusi i casi limite (marca senza spazi, con spazi, con trattino esistente nel nome).
- Derivazione slug: test su un campione rappresentativo di nomi marca (non solo le 5 attuali).
- CRUD marche monitorate: verifica che ogni azione (aggiungi/modifica/rimuovi/pausa) produca sia la riga DB corretta sia l'effetto corretto sullo scheduler (job creato/aggiornato/rimosso), usando uno scheduler fittizio nei test come già avviene oggi.
- `PATCH /brands/apply-defaults`: verifica che sovrascriva tutte le righe esistenti e che le marche aggiunte dopo non vengano toccate retroattivamente.
- Costruzione del `CronTrigger`: `schedule_day_of_week=None` → ogni giorno; valorizzato → una volta a settimana nel giorno giusto.
- Filtro anno riletto ad ogni esecuzione, non congelato al momento della programmazione (test che modifica il filtro dopo la creazione del job e verifica che il run successivo usi il nuovo valore).
- Migrazione di popolamento iniziale da `MVP_BRANDS` quando `tracked_brands` è vuota.
- Interfaccia: test per la nuova schermata (ricerca, selezione multipla, aggiunta, applica-a-tutte, modifica singola, rimozione) con le convenzioni Vitest+Testing Library già in uso.

## 9. Rischi e note aperte

- Lo slug derivato automaticamente è la parte meno verificata di questo design — va validato empiricamente su un campione ampio del catalogo reale prima di fidarsene, con correzione manuale come rete di sicurezza.
- 290 marche × modelli/anno ciascuna è un volume enorme se monitorate tutte insieme: questo design riguarda solo la *selezione e configurazione*, non introduce automaticamente parallelismo aggiuntivo oltre a `SCRAPE_CONCURRENCY` già esistente — monitorare molte marche contemporaneamente resta comunque vincolato dallo stesso principio di un solo IP, nessuna rotazione, già stabilito.
- Il polling più frequente durante un run attivo (sezione 6) resta polling, non push istantaneo (WebSocket) — scelta deliberata per semplicità, coerente con l'architettura REST esistente.
