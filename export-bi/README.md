# AutoSmart24 — dataset annunci auto usate (Italia)

Estratto del **29/07/2026 09:45 UTC** dal database dello scraper AutoSmart24, che
raccoglie gli annunci di auto usate di autoscout24.it.

Destinazione: progettazione di un'applicazione di business intelligence sul
mercato dell'usato.

---

## 1. Cosa c'è dentro

| file | righe | contenuto |
|---|---|---|
| `listings.csv` | 24.957 | l'annuncio: auto, prezzo, venditore, stato |
| `price_history.csv` | 25.296 | ogni variazione di prezzo osservata |
| `dealers.csv` | 4.262 | i concessionari citati dagli annunci |
| `brand_catalog.csv` | 290 | tutte le marche esistenti su AutoScout |
| `tracked_brands.csv` | 25 | le marche effettivamente raccolte, con la loro configurazione |

**Questo è un campione, non il database completo.** Il database contiene
294.886 annunci su 25 marche. Il campione è costruito così:

- **tutti i 6.961 annunci venduti**, senza eccezioni — sono l'evento raro e il
  segnale su cui si costruisce qualunque analisi di mercato
- **17.996 annunci attivi**, estratti per marca in proporzione al peso della
  marca, con un minimo di 50, così che le distribuzioni marginali (prezzo, anno,
  alimentazione, area geografica) restino fedeli all'originale
- selezione deterministica su `md5(id)`: rieseguire l'estrazione dà lo stesso
  campione

Il database intero è disponibile su richiesta come dump PostgreSQL (~75 MB) o
come gli stessi CSV senza campionamento (~180 MB).

---

## 2. Le tre cose da sapere prima di calcolare qualsiasi cosa

### 2.1 Il tempo di vendita NON si calcola da `first_seen_at`

`first_seen_at` è quando **noi** abbiamo visto l'annuncio per la prima volta, e
la raccolta è cominciata il **24/07/2026**. Un annuncio pubblicato a maggio e
venduto ieri risulta "visto per la prima volta" cinque giorni fa.

```
mediana sold_at − first_seen_at         →  1,8 giorni   ← privo di significato
mediana sold_at − created_at_source     → 19,9 giorni   ← questo è il dato
```

**Usare `created_at_source`**, che è la data di pubblicazione dichiarata da
AutoScout. È popolata sul 100% degli annunci arricchiti e su 6.283 dei 6.961
venduti.

### 2.2 Anche 19,9 giorni è distorto verso il basso

La finestra di osservazione è di **cinque giorni**. Vediamo solo le auto che si
sono vendute in quella finestra, quindi le vendite lente sono sistematicamente
assenti: è troncamento a destra (right censoring) da manuale.

Una mediana calcolata ingenuamente sul campione dei venduti **sottostima il
tempo di vendita reale**, e l'errore si riduce col passare delle settimane.
Per stime corrette servono metodi che trattano i dati troncati — Kaplan-Meier
sull'insieme completo, dove gli annunci ancora attivi entrano come osservazioni
censurate anziché essere esclusi.

Se la BI deve mostrare un "tempo medio di vendita" già adesso, va accompagnato
dalla finestra di osservazione, non presentato come valore assoluto.

### 2.3 `status = 'sold'` significa "sparito dal sito", non "venduto"

L'annuncio non è più raggiungibile ed è stato confermato rimosso da due
verifiche indipendenti. Nella grande maggioranza dei casi è una vendita, ma un
annuncio ritirato dal venditore, scaduto o rimosso per altre ragioni finisce
nella stessa categoria. Non esiste un segnale che li distingua.

Nota storica sulla qualità: fino al 28/07/2026 un difetto dello scraper
produceva falsi positivi in massa (un errore transitorio del sito veniva letto
come rimozione). Sono stati individuati e riportati ad `active` 268 record, e la
causa è stata corretta il 29/07. **I dati in questo estratto sono successivi
alla correzione e sono stati verificati**: nessun annuncio marcato venduto meno
di un'ora dopo essere stato visto vivo, che era la firma del difetto.

---

## 3. `listings.csv`

Una riga per annuncio. Chiave primaria `id`.

### Identificazione

| colonna | tipo | note |
|---|---|---|
| `id` | testo | id AutoScout, UUID. **Attenzione: AutoScout riassegna gli id.** Un id ritirato può ricomparire su un'auto diversa, a volte di un'altra marca. Misurato sul nostro campione: riguarda l'1-4% degli annunci nuovi. Non trattare l'id come identificatore stabile di un veicolo fisico. |
| `url` | testo | pagina dell'annuncio |
| `cross_reference_id` | testo | riferimento incrociato AutoScout, spesso vuoto |

### L'auto

| colonna | tipo | copertura | note |
|---|---|---|---|
| `brand` | testo | 100% | nome visualizzato: `Mercedes-Benz`, `Alfa Romeo`, `CUPRA` |
| `model`, `model_group`, `variant` | testo | alta | `model_group` è il raggruppamento grossolano, `model` il modello, `variant` l'allestimento |
| `motor_type_name`, `version_input` | testo | media | descrizione motore e versione come inserite dal venditore, testo libero |
| `first_registration` | data | 95,9% | prima immatricolazione — la base per l'età del veicolo |
| `mileage_km` | intero | 97,3% | chilometraggio |
| `power_kw`, `power_cv` | intero | 99,8% | potenza; `power_cv` è la misura usata in Italia |
| `displacement_ccm` | intero | alta | cilindrata |
| `fuel` | testo | 99,9% | `Diesel`, `Benzina`, `Elettrica/Benzina` (ibrido), `Elettrica/Diesel`, `GPL`, `Elettrica`, `Metano`, `Altro` |
| `transmission` | testo | 99,1% | `Automatico`, `Manuale`, `Semiautomatico` |
| `drive_train` | testo | 78% | `Anteriore`, `4x4`, `Posteriore` |
| `body_type` | testo | 100% | `SUV/Fuoristrada/Pick-up`, `Berlina`, `Station Wagon`, `City car`, `Furgoni/Van`, `Monovolume`, … |
| `body_color`, `upholstery`, `upholstery_color` | testo | media | colore e interni |
| `num_seats`, `num_doors`, `gears`, `cylinders`, `weight_kg` | intero | media | |
| `num_previous_owners` | intero | 46,4% | proprietari precedenti |
| `had_accident` | booleano | 76,5% | dichiarazione di sinistri |
| `has_full_service_history` | booleano | media | tagliandi completi |
| `new_driver_suitable` | booleano | media | adatto a neopatentati |
| `co2_emissions_g_km`, `emission_class` | numerico/testo | 34% | emissioni |
| `fuel_consumption_combined` / `_urban` / `_extra_urban` | numerico | bassa | consumi dichiarati |

### Prezzo

| colonna | tipo | note |
|---|---|---|
| `price` | intero | prezzo richiesto in euro. Mediana del dataset: **19.700 €** |
| `vat_exposed` | booleano | IVA esposta (rilevante per acquirenti con partita IVA) |
| `is_conditional_price` | booleano | prezzo soggetto a condizioni, es. permuta o finanziamento |
| `price_evaluation_median` | intero | **stima AutoScout del prezzo mediano di mercato** per auto comparabili. Molto utile: è un riferimento già normalizzato per modello, anno e km |
| `price_evaluation_category` | intero 0-6 | valutazione di convenienza di AutoScout. La scala non è documentata da loro; misurata sui nostri dati risulta perfettamente monotona: |

```
categoria   n annunci   mediana di price / price_evaluation_median
    0          5.011                0,780      ← molto sotto mercato
    1         37.234                0,896
    2         59.975                0,959
    3         86.678                1,023      ← in linea col mercato
    4         36.887                1,099
    5         15.322                1,191
    6          3.008                1,371      ← molto sopra mercato
```

Il rapporto `price / price_evaluation_median` è calcolabile direttamente ed è
probabilmente più utile della categoria, che ne è una discretizzazione.

### Venditore e luogo

| colonna | tipo | copertura | note |
|---|---|---|---|
| `seller_type` | testo | 100% | `Dealer` (73%) o `PrivateSeller` (27%) |
| `seller_company_name` | testo | sui Dealer | ragione sociale |
| `dealer_id` | intero | 75,1% | → `dealers.csv`. Assente sui privati |
| `city`, `zip_code` | testo | alta | comune e CAP |
| `province` | testo | **27,9%** | copertura bassa: per l'analisi geografica conviene ricavare la provincia dal CAP o dalle coordinate |
| `latitude`, `longitude` | numerico | alta | posizione |

### Popolarità

| colonna | note |
|---|---|
| `interaction_count` | interazioni sull'annuncio secondo AutoScout, 100% popolato. Cresce nel tempo, quindi va normalizzato per l'età dell'annuncio prima di confrontare annunci diversi |
| `favorites_count` | salvataggi tra i preferiti |

### Ciclo di vita — le colonne che governano le analisi temporali

| colonna | significato |
|---|---|
| `created_at_source` | **pubblicazione dell'annuncio secondo AutoScout.** Il riferimento per età e tempo di vendita. Il più vecchio del dataset risale al 24/02/2011 |
| `first_seen_at` | prima volta che *il nostro scraper* l'ha visto. Non è la pubblicazione (vedi §2.1) |
| `last_seen_at` | ultima volta trovato nei risultati di ricerca |
| `last_checked_at` | ultima volta che la sua pagina è stata aperta |
| `status` | `active` (287.925 nel database) o `sold` (6.961) |
| `sold_at` | quando è stata confermata la rimozione. Vuoto sugli attivi |
| `detail_scraped` | `true` se la pagina di dettaglio è stata letta. **Le righe con `false` hanno solo i campi della lista di ricerca**, il resto è vuoto — inclusa `created_at_source`, quindi vanno escluse da qualunque analisi temporale. Nel campione sono 678 su 24.957 (2,7%), quasi tutte annunci scoperti nelle ultime ore |

---

## 4. `price_history.csv`

Una riga per variazione di prezzo osservata.

| colonna | note |
|---|---|
| `id` | chiave |
| `listing_id` | → `listings.id` |
| `price` | il prezzo a quel momento |
| `recorded_at` | quando è stato registrato |

Il primo record di ogni annuncio è il prezzo alla scoperta, non un ribasso.
Contiene solo le variazioni viste **da quando raccogliamo noi** (dal 24/07), non
lo storico completo dell'annuncio: un'auto pubblicata a marzo e già ribassata
tre volte prima del 24/07 ci risulta senza ribassi.

---

## 5. `dealers.csv`

| colonna | note |
|---|---|
| `id` | id AutoScout, stabile |
| `company_name` | ragione sociale |
| `ratings_stars` | media recensioni (0-5) |
| `ratings_count` | numero di recensioni |
| `recommend_percentage` | percentuale di clienti che raccomandano |
| `synced_at` | ultimo aggiornamento della scheda |

---

## 6. `tracked_brands.csv` e `brand_catalog.csv`

`brand_catalog` è l'elenco completo delle 290 marche esistenti su AutoScout.
`tracked_brands` sono le 25 che raccogliamo, con:

- `year_from_years = 10` — **la finestra di raccolta è di 10 anni**: prendiamo
  solo auto immatricolate negli ultimi dieci anni. Annunci più vecchi non sono
  nel dataset, e la loro assenza non significa che il mercato non li abbia
- `paused`, `schedule_*` — configurazione operativa dello scraper, irrilevante
  per l'analisi

---

## 7. Copertura e stato della raccolta

Le 25 marche coprono la gran parte del mercato italiano dell'usato, ma **non è
un censimento**: marche assenti dall'elenco non compaiono affatto.

Al momento dell'estratto una scansione era **in corso**: Ford era a metà, e
Peugeot, BMW, Mercedes-Benz, Volkswagen, Audi e Fiat non erano ancora state
aggiornate in quel giro. Conseguenze pratiche:

- **Ford ha 0 venduti** — non perché non se ne vendano, ma perché la sua prima
  scansione utile non era conclusa
- **Peugeot ha 13 venduti** su 11.601 annunci, per la stessa ragione
- le altre 23 marche hanno numeri coerenti tra loro

Non trarre conclusioni sul confronto tra marche a partire dal conteggio dei
venduti di Ford e Peugeot.

---

## 8. Convenzioni tecniche

- **Tutti i timestamp sono UTC e privi di fuso** (`timestamp without time zone`).
  L'ora locale italiana è UTC+2 in questo periodo. Nessuna colonna porta il fuso:
  va aggiunto in fase di caricamento, non dedotto.
- Le date (`first_registration`) non hanno orario.
- I CSV sono codificati UTF-8, separatore virgola, intestazione in prima riga,
  virgolette secondo lo standard PostgreSQL. I valori vuoti sono campi vuoti,
  non la stringa `NULL`.
- I booleani sono `t` / `f`.
- `schema.sql` contiene il DDL PostgreSQL completo se serve ricostruire le
  tabelle con tipi e vincoli originali.

---

## 9. Spunti che i dati reggono

Cose calcolabili subito, con le cautele di §2:

- **prezzo contro mercato**: `price / price_evaluation_median` per modello, area,
  tipo di venditore — quanto si discosta chi vende in fretta
- **elasticità del ribasso**: incrociare `price_history` con l'esito, per vedere
  se e di quanto i ribassi accorciano la permanenza
- **dinamiche del venditore**: privati contro concessionari su prezzo, tempi,
  chilometraggio; e reputazione del concessionario (`ratings_*`) contro tempi
- **svalutazione**: prezzo contro `first_registration` e `mileage_km` per modello
- **domanda**: `interaction_count` normalizzato sull'età dell'annuncio come
  indicatore anticipatore rispetto alla vendita
- **geografia**: da `latitude`/`longitude` (`province` è troppo scarsa)

Quello che i dati **non** reggono ancora è qualunque affermazione assoluta sui
tempi di vendita, per il motivo di §2.2. Serve accumulare settimane di
osservazione, oppure trattare esplicitamente il troncamento.
