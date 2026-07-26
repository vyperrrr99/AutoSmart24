# Structured Detail-Page Fields, Dealers Table, and Raw-JSON Retirement — Design

Data: 2026-07-26

## 1. Obiettivo

Oggi ogni annuncio arricchito conserva, oltre ai campi strutturati già estratti, l'intero JSON grezzo della pagina di dettaglio (`raw_detail`) e dello snippet di ricerca (`raw_snippet`). Su 69.128 annunci arricchiti questo pesa **989 MB** — l'87% dell'intero database — quasi tutto sprecato in dati pubblicitari, tracciamento, e widget di finanziamento senza alcun valore analitico.

Questo documento copre tre cambiamenti collegati:

1. **Nuovi campi strutturati** su `listings`, estratti da sezioni del JSON grezzo oggi scartate ma di reale interesse (dati tecnici, ambientali, popolarità, idoneità neopatentati).
2. **Una tabella `dealers` separata**, per non ripetere le statistiche del concessionario (stelle, recensioni, % raccomandazione) su ogni singolo annuncio dello stesso concessionario.
3. **L'eliminazione di `raw_detail`/`raw_snippet`**, una volta verificato che tutto il valore che vogliamo conservare è stato estratto nelle colonne strutturate.

Verificato empiricamente durante il brainstorming (non assunto): ho scaricato un annuncio reale, ispezionato il JSON campo per campo, controllato dal vivo sul sito quali sezioni della pagina corrispondono a quali chiavi JSON, e misurato il peso reale di ogni sezione su tutti gli annunci già raccolti.

## 2. Campi esclusi, con motivazione

- **Descrizione libera del venditore** (`description`): esclusa su richiesta esplicita. ~111 MB su 69k annunci.
- **Equipaggiamento/dotazioni** (`vehicle.equipment`, 146 voci distinte su 4 categorie): escluso per ora. Verificato dal vivo che il sito mostra la lista **completa** (non un sottoinsieme "in evidenza") dietro un link "Di Più" collassato, in una sezione separata dal blocco "sotto le foto" — non esiste quindi una versione ridotta a cui allinearsi. Se in futuro si deciderà di includerlo, verrà aggiunto come attributi propri del database tramite una nuova raccolta dati, non recuperando il JSON grezzo (che non viene conservato per questo scopo).
- **Foto, dati di finanziamento/leasing, parametri di tracciamento pubblicitario, link CARFAX**: puro rumore ad-tech, mai stato oggetto di interesse. ~840 MB combinati su 69k annunci.
- **Storico prezzi**: non presente nel JSON scaricato senza autenticazione (verificato: la sessione anonima mostra solo le stringhe dell'interfaccia e un interruttore "mostra il link", mai i dati storici veri). Per ora ci si affida al monitoraggio regolare dello scraper (un nuovo prezzo diverso dall'ultimo registrato viene già rilevato e salvato in `price_history`). Uno script separato, autenticato con le credenziali dell'utente **inserite dall'utente stesso** (non da Claude), che raccoglie lo storico prezzi in un'unica passata una tantum, è rimandato a un secondo momento — fuori ambito per questo documento.

## 3. Nuovi campi su `listings`

Tutti nullable, tutti opzionali nel JSON sorgente. Fonte = percorso nel JSON di `raw_detail` (`ld`) usato oggi da `detail_mapper.py`.

| Colonna | Tipo | Fonte JSON | Note |
|---|---|---|---|
| `had_accident` | `bool \| None` | `vehicle.hadAccident` | |
| `has_full_service_history` | `bool \| None` | `vehicle.hasFullServiceHistory` | |
| `gears` | `int \| None` | `vehicle.gears` | |
| `drive_train` | `str \| None` (64) | `vehicle.driveTrain` | es. "Anteriore" |
| `cylinders` | `int \| None` | `vehicle.cylinders` | |
| `weight_kg` | `int \| None` | `vehicle.weight` | stringa tipo `"1.226 kg"`, va parsata (rimuovere separatore migliaia e unità) |
| `co2_emissions_g_km` | `float \| None` | `vehicle.co2emissionInGramPerKmWithFallback.raw` | |
| `fuel_consumption_combined` | `float \| None` | `vehicle.fuelConsumptionCombined.raw` | l/100km |
| `fuel_consumption_urban` | `float \| None` | `vehicle.fuelConsumptionUrban.raw` | |
| `fuel_consumption_extra_urban` | `float \| None` | `vehicle.fuelConsumptionExtraUrban.raw` | |
| `emission_class` | `str \| None` (32) | `vehicle.environmentEuDirective.formatted` | es. "Euro 6d" — usa `.formatted` per coerenza con `fuel` che già usa `.formatted` sullo stesso pattern |
| `upholstery` | `str \| None` (64) | `vehicle.upholstery` | |
| `upholstery_color` | `str \| None` (64) | `vehicle.upholsteryColor` | |
| `is_conditional_price` | `bool \| None` | `price.isConditionalPrice` (nota: `price` di primo livello, distinto da `prices`) | corrisponde al badge "Prezzo con condizioni" |
| `interaction_count` | `int \| None` | `dpvStatistics.interaction` | sezione "Popolarità" |
| `favorites_count` | `int \| None` | `dpvStatistics.favorites` | sezione "Popolarità" |
| `new_driver_suitable` | `bool \| None` | `vehicle.newDriverSuitable` | "Per neopatentati" |
| `dealer_id` | `int \| None`, FK a `dealers.id` | `seller.id`, solo quando `seller.isDealer` è vero | `NULL` per venditori privati |

Peso stimato aggiuntivo: trascurabile (poche centinaia di byte per annuncio — booleani, interi, stringhe corte). Non cambia in modo significativo la stima totale del database.

## 4. Nuova tabella `dealers`

Un concessionario che appare su centinaia di annunci non deve ripetere le stesse statistiche centinaia di volte.

| Colonna | Tipo | Fonte JSON | Note |
|---|---|---|---|
| `id` | `int`, chiave primaria | `seller.id` | l'id interno di autoscout24 per il concessionario — stabile, non generato da noi |
| `company_name` | `str \| None` (256) | `seller.companyName` | |
| `ratings_stars` | `float \| None` | `ratings.ratingsStars` | valore arrotondato mostrato come stelle (es. 4, 4.5) — verificato dal vivo su 5 concessionari reali, è il valore corretto (non `ratingsAverage`, che è una media più precisa con virgola, non richiesta) |
| `ratings_count` | `int \| None` | `ratings.ratingsCount` | |
| `recommend_percentage` | `int \| None` | `ratings.recommendPercentage` | |
| `synced_at` | `datetime` | — | quando la riga è stata creata/aggiornata l'ultima volta |

Righe create solo per venditori con `seller.isDealer == true` — i privati continuano a usare le colonne esistenti `seller_type`/`seller_company_name` su `listings`, invariate.

Comportamento di scrittura: upsert ad ogni arricchimento di un annuncio di quel concessionario (le statistiche possono cambiare nel tempo — più recensioni, media diversa).

## 5. Sequenza di migrazione — ordine vincolante

Il requisito esplicito dell'utente: **estrarre prima tutte le informazioni utili dal JSON grezzo già raccolto, eliminarlo solo dopo**. La sequenza è quindi vincolata in questo ordine, non intercambiabile:

1. **Migrazione additiva**: aggiungere le nuove colonne (tutte nullable) a `listings` e creare la tabella `dealers`. Nessun dato esistente toccato, operazione sicura.
2. **Aggiornare il codice di scraping** (`detail_mapper.py`, e il punto in `run_manager.py` dove il risultato di `map_detail_listing` viene scritto su `Listing`) perché popoli i nuovi campi e faccia l'upsert su `dealers` per ogni futuro annuncio arricchito. Da questo momento in poi, ogni nuovo arricchimento popola già tutto correttamente.
3. **Script di backfill una tantum**: per tutti gli annunci già arricchiti (`detail_scraped = true`, `raw_detail IS NOT NULL` — oggi 69.128 righe), leggere i nuovi campi **dal `raw_detail` già presente in database** (nessuna nuova richiesta HTTP: è un'operazione DB-a-DB, veloce) e popolare le nuove colonne + creare/aggiornare le righe di `dealers`.
4. **Verifica di completezza del backfill**: confrontare, per un campione di righe, il valore letto direttamente dal JSON con quello scritto nella colonna; controllare la percentuale di copertura per ogni nuovo campo (alcuni saranno `NULL` semplicemente perché il campo era assente nel JSON originale — non un errore).
5. **Solo dopo la verifica**: rimuovere le colonne `raw_detail` e `raw_snippet` dal modello e dal database (migrazione separata), e aggiornare `detail_mapper.py`/`snippet_mapper.py` perché non restituiscano più quelle chiavi. Verificare che nessun altro punto del codice (es. `run_manager.py`) legga ancora `Listing.raw_detail`/`raw_snippet` prima di rimuoverle.

Il passaggio 5 è irreversibile per gli annunci già scaricati (il JSON grezzo, una volta eliminato, non è recuperabile se l'annuncio è nel frattempo sparito dal sito) — motivo per cui i passaggi 3-4 devono essere completati e verificati per intero prima di procedere.

## 6. Stima finale del database

Con equipment escluso e JSON grezzo eliminato dopo il backfill, la stima per il traguardo di 20-30 marche a 10 anni (~270.000-290.000 annunci) scende a **~650-700 MB** — dentro la maggior parte dei piani gratuiti o a bassissimo costo.

## 7. Testing

- Parsing dei nuovi campi da fixture JSON realistiche (valori presenti, valori assenti/null, stringa peso da parsare).
- Backfill: verificato su un campione che i valori scritti corrispondano esattamente al JSON sorgente, sia per campi presenti che assenti (NULL corretto, non un errore silenzioso).
- Idempotenza del backfill (rieseguito due volte non duplica né corrompe dati).
- Upsert su `dealers`: un venditore già esistente viene aggiornato, non duplicato; un privato non crea mai una riga `dealers`.
- Migrazione additiva (passo 1) verificata contro il Postgres reale con i dati esistenti intatti, come da prassi consolidata in questo progetto.
- Rimozione delle colonne (passo 5) eseguita solo dopo conferma esplicita che il backfill è completo e verificato — non nella stessa migrazione del passo 1.

## 8. Rischi e note aperte

- `weight_kg` richiede il parsing di una stringa localizzata (`"1.226 kg"`) — va gestito con attenzione al separatore delle migliaia italiano, non l'unico posto nel codice dove serve (vedi `_parse_int` già esistente in `snippet_mapper.py` per un pattern simile).
- L'esclusione dell'equipment è una decisione rivedibile ma non gratuita: se rivista in futuro, i dati sugli annunci già scaduti nel frattempo non saranno più recuperabili — accettato esplicitamente dall'utente.
- Lo storico prezzi autenticato resta un progetto separato, non pianificato in dettaglio qui.
