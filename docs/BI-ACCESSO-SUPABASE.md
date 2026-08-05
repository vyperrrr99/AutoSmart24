# Accesso al database per l'applicazione BI

Da passare alla sessione che sviluppa AutoSmart-BI. Aggiornato al **05/08/2026**.

Sostituisce la versione del 01/08: cambiano gli stati degli annunci e c'è un
problema di orario da correggere.

---

## 1. Da leggere per primo: due cose urgenti

### 1.1 Lo snapshot delle 04:15 fotografa dati sbagliati

Nel crontab c'è:

```
15 4 * * *  cd /home/vperrone/AutoSmart-BI && ... scripts/snapshot_giornaliero.py
```

A quell'ora succedono due cose che lo rendono inaffidabile:

- **Lo scraper sta ancora lavorando.** Il giro parte alle 22:00 e finisce fra le
  04:00 e le 06:00. Alle 04:15 sono passate circa venti marche su ventisei: lo
  snapshot fotografa un database a metà aggiornamento, con alcune marche di
  stanotte e altre di ieri, e nulla nei dati permette di distinguerle.
- **Le vendite non sono ancora state ripulite.** La riclassificazione (§4) gira
  alle 09:00. Alle 04:15 il campo `sold` contiene ancora tutte le sparizioni,
  comprese quelle che non sono vendite — sul giro del 02/08 erano **1.305 su
  4.014, un terzo**.

**Spostatelo dopo le 09:15.** A quell'ora il giro è finito, le vendite sono
riclassificate e la copia su Supabase è aggiornata.

### 1.2 Le migrazioni vanno applicate anche su Supabase

La sincronizzazione notturna copia **i dati, non lo schema**. Una colonna
aggiunta da una parte e non dall'altra fa fallire la pubblicazione.

È già successo: la migrazione `0008` ha aggiunto `removal_reason` qui e non là.
Ora la sincronizzazione se ne accorge prima e rifiuta di pubblicare con un
messaggio esplicito, invece di fallire su un errore di colonna alle nove del
mattino senza nessuno a guardare.

Se create oggetti nello schema `bi` che dipendono da `public`, ricordate che
ogni mattina `public` viene svuotato e ricaricato: viste materializzate
sopravvivono (vanno solo riaggiornate), vincoli di chiave esterna verso
`public` **no**.

### 1.3 Un annuncio su venti è una copia: filtrate `duplicate_of`

Alcuni venditori pubblicano la stessa auto sotto più identità AutoScout.
Autohero espone un solo catalogo attraverso **nove** id venditore: la stessa
BMW X1 a 63.415 km e 18.999 € compare nove volte, una per id. Dei suoi 12.798
annunci, **9.834 sono copie** — le auto vere sono 2.984.

Sull'intero database sono **14.134 annunci attivi su 261.889 (5,4%)**.

La colonna **`duplicate_of`** punta all'annuncio da contare. `NULL` significa
«questo è quello da contare», quindi una query che ignora la colonna si
comporta come prima che esistesse — ma conta le copie.

```sql
WHERE duplicate_of IS NULL      -- inventario, prezzi, distribuzioni
```

Senza quel filtro, un venditore con una politica di prezzo sua vota nove volte
in ogni mediana. Nel preventivo di una Peugeot 208 fatto a mano, tre copie
Stellantis spostavano la mediana da 13.000 a 12.500 €: **il 4% sul prezzo
consigliato**.

**Sulle vendite non serve fare nulla**: quando le copie spariscono insieme, una
resta `sold` e le altre diventano `removed` con motivo `duplicate_listing`. La
regola `status = 'sold'` continua a bastare.

La deduplicazione avviene **solo** dentro reti registrate a mano in
`config/reti-venditori.yaml`, mai per regola automatica: sette concessionari
indipendenti si chiamano «City Car» in sette province, e fonderli avrebbe
cancellato magazzino reale lasciando un numero plausibile al suo posto.

---

## 2. Connessione

```
Host     aws-0-eu-central-1.pooler.supabase.com
Porta    5432
Database postgres
Utente   postgres.ofbmvgwskvcsyleauyhu
```

Password in `/home/vperrone/AutoSmart24/.env.supabase` sulla macchina dello
scraper, modo 600 ed escluso da git. **Chiedetela all'utente**: il repository
AutoSmart24 è pubblico.

Nella stringa di connessione va **codificata per URL** — contiene un `@`, che
altrimenti verrebbe letto come separatore dell'host.

Usare il **pooler**, non la connessione diretta: `db.<ref>.supabase.co` risolve
solo in IPv6 sul piano gratuito, il pooler risponde anche in IPv4.

PostgreSQL sul server è la **17.6**; lo scraper gira sulla 16.

---

## 3. Gli stati di un annuncio: da quattro, non più da due

Questa è la modifica più importante rispetto alla versione precedente.

| stato | significato | conta come vendita? |
|---|---|---|
| `active` | in vendita adesso | no |
| `sold` | **venduto**, per quanto possiamo saperlo | **sì** |
| `quarantine` | sparito insieme a tutto lo stock del concessionario: probabile chiusura, in attesa | **no, non ancora** |
| `removed` | sparito ma **accertato non venduto** | no, mai |

Stato al 03/08, con un giro ancora in corso: 262.360 attivi, 30.257 venduti,
875 in quarantena, 430 rimossi.

**Per la BI la regola è semplice: `status = 'sold'`.** Gli altri tre non sono
vendite e non vanno contati, nemmeno parzialmente.

La colonna `removal_reason` dice perché un annuncio non è una vendita:

| valore | cosa significa |
|---|---|
| `twin_on_sale` | lo stesso concessionario ha ancora in vendita un'auto identica per marca, modello, anno, alimentazione, trazione e chilometraggio. Se si fosse venduta sarebbero spariti entrambi gli annunci |
| `republished` | l'annuncio è ricomparso sotto un id nuovo, con riferimento interno del concessionario **e** impronta dell'auto concordi |
| `dealer_closure` | tutto lo stock del venditore è sparito in una notte — almeno 5 auto e oltre il 50%. In quarantena |
| `quarantine_expired` | era in quarantena, è rimasto invisibile 30 giorni: **è una vendita**, ma provata dall'assenza e non osservata |
| `duplicate_listing` | copia di un'auto pubblicata più volte dalla stessa rete di venditori: la vendita è contata sull'annuncio canonico |

`quarantine_expired` è l'unico che compare su righe `sold`. Se vi serve
distinguere le vendite osservate da quelle dedotte, è quel campo.

---

## 4. Perché esiste la riclassificazione

Un annuncio che sparisce dai risultati di ricerca è **l'unica prova** che
questo progetto ha di una vendita. È una prova debole, e tre cose la imitano.

Misurato su una settimana di dati veri: **5.366 sparizioni su 26.536 — una su
cinque — avevano un'auto identica ancora in vendita dallo stesso
concessionario**, e una singola notte ha mostrato 50 concessionari perdere il
100% dello stock in una volta.

Il tempo di vendita è la metrica su cui costruite l'applicazione. Ognuna di
queste lasciata come vendita non è rumore attorno a un valore vero: è un evento
inventato che tira la mediana verso il basso.

Due delle tre regole **osservano**, una **deduce**, e sono trattate
diversamente. Il gemello ancora in vendita e la ripubblicazione sono fatti
verificabili aprendo un URL. La sparizione in blocco è un'inferenza, e può
sbagliare nella direzione opposta: un concessionario disordinato può lasciare
online auto già vendute e ripulire il magazzino una volta al mese, nel qual
caso quella sparizione **è** un blocco di vendite vere registrate in ritardo.
Per questo aspetta trenta giorni invece di decidere.

**`sold_at` sopravvive alla quarantena** e registra quando l'auto è sparita, non
quando l'abbiamo accettato. Risolvere con la data di conferma aggiungerebbe un
mese al tempo di vendita di ogni auto passata di lì.

---

## 5. Un annuncio può tornare in vendita

Un annuncio dato per venduto che ricompare torna `active`, `sold_at` viene
azzerato e `removal_reason` cancellato.

**Il conteggio delle vendite può quindi diminuire fra due giorni.** Non è un
errore: è una correzione. Ogni ritorno lascia un evento in `scrape_events` con
quanti giorni era rimasto invisibile — utile se un numero pubblicato la
settimana prima non torna più.

Se la BI conserva serie storiche, tenetene conto: un valore calcolato ieri può
legittimamente non essere riproducibile oggi.

---

## 6. Cosa c'è nel database

Cinque tabelle in `public`, di proprietà dello scraper. L'applicazione non ci
scrive mai.

| tabella | contenuto |
|---|---|
| `listings` | l'annuncio: auto, prezzo, venditore, stato |
| `price_history` | ogni variazione di prezzo osservata |
| `dealers` | i concessionari |
| `brand_catalog` | tutte le marche esistenti su AutoScout |
| `tracked_brands` | le 26 marche raccolte, con la loro configurazione |

Non copiate `scrape_events` e `scrape_runs`: sono la telemetria dello scraper.
Se serve sapere quanto è aggiornato un dato, si ricava da `last_seen_at`.

**Solo auto usate.** Dal 31/07 lo scraper scarta alla fonte tutto ciò che ha
`mileage_km` sotto i 1.000 o nullo — auto nuove e km 0 — con la stessa
definizione del vostro `is_km_zero`, che quindi troverà sempre zero righe.
Toglietelo o lasciatelo come rete, ma non progettateci sopra analisi. Il
database contiene ora **81.615 km e 23.285 €** di media.

Occupazione su Supabase: 252 MB dei 500 del piano gratuito, con circa 1 MB al
giorno di crescita. Restano quattro o cinque mesi.

---

## 7. Ritmo della giornata

```
22:00   lo scraper parte, 26 marche in fila
04-06   il giro finisce
09:00   riclassificazione delle vendite, poi copia su Supabase
09:03   i dati del giorno sono pronti
```

Fra le 09:03 e le 09:00 del giorno dopo **i dati non cambiano**. Le viste
materializzate possono essere aggiornate una volta al giorno senza logiche di
invalidazione.

La copia è un `TRUNCATE` più ricarica in un'unica transazione: chi legge vede
quella di ieri finché la nuova non è completa, mai una tabella a metà. Se la
riclassificazione fallisce, **non si pubblica nulla** — meglio i dati di ieri
che quelli di oggi con dentro vendite inventate.

---

## 8. Il resto, dove sta

Le insidie metodologiche che restano tutte valide — il tempo di vendita che non
si calcola da `first_seen_at`, il troncamento a destra, il riuso degli id — sono
in `/home/vperrone/AutoSmart24/docs/AVVIO-SESSIONE-BI.md`, §4 e §5.

Lo schema tabella per tabella è in
`/home/vperrone/AutoSmart24/export-bi/README.md`.
