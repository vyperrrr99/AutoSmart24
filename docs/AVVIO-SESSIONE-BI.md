# Avvio della sessione per l'app di BI

Documento di consegna per aprire una sessione Claude Code nuova sul progetto
di business intelligence per l'usato, con i dati raccolti da AutoSmart24.

Aggiornato al **31/07/2026**.

---

## 1. Cosa incollare all'inizio della sessione

Aprire Claude Code **nella cartella del progetto BI** (non in `AutoSmart24`) e
incollare questo:

> Progettiamo e sviluppiamo un'applicazione di business intelligence sul mercato
> italiano dell'auto usata. I dati arrivano da uno scraper che ho già in
> produzione, AutoSmart24, che raccoglie gli annunci di autoscout24.it.
>
> Prima di qualunque cosa leggi `/home/vperrone/AutoSmart24/docs/AVVIO-SESSIONE-BI.md`:
> contiene come accedere ai dati, lo stato della raccolta e — soprattutto — quattro
> insidie metodologiche che invalidano le analisi più ovvie se ignorate.
>
> Poi leggi `/home/vperrone/AutoSmart24/export-bi/README.md`, che documenta lo
> schema tabella per tabella.
>
> Ho già un prototipo di interfaccia fatto con Claude Design e delle specifiche
> parziali, che trovi in <PERCORSO DA COMPLETARE>.
>
> Usa la skill superpowers:brainstorming per arrivare a una spec, prima di
> scrivere codice.

**Da completare prima di incollare:** il percorso del prototipo e delle
specifiche parziali.

---

## 2. Come accedere ai dati

### PostgreSQL, in diretta

Il database gira in Docker sulla ThinkPad ed è raggiungibile da qualunque
processo sulla stessa macchina:

```
host     localhost
porta    5434
database autosmart24
utente   autosmart24
password autosmart24
```

```
postgresql://autosmart24:autosmart24@localhost:5434/autosmart24
```

Da riga di comando, senza client installato:

```bash
sudo docker exec -i autosmart24-postgres-1 psql -U autosmart24 -d autosmart24 -c "SELECT count(*) FROM listings;"
```

Sono le credenziali di sviluppo già presenti in `docker-compose.yml`, quindi
scriverle qui non aggiunge esposizione. Vanno però cambiate se la macchina
venisse mai resa raggiungibile da fuori: la porta è in ascolto su `0.0.0.0`.

**Leggere, non scrivere.** Lo scraper è l'unico proprietario di queste tabelle.
Una BI che scrive sulle stesse righe entrerebbe in conflitto con le scansioni
notturne. Se servono tabelle proprie, crearle in uno **schema separato**.

### Estratto in CSV, per lavorare offline

`/home/vperrone/AutoSmart24/export-bi/` — campione stratificato del 29/07
mattina: 24.957 annunci, di cui **tutti** i venduti di allora, più `README.md`
con lo schema commentato e `schema.sql` col DDL completo.

Più piccolo del database e sufficiente per progettare. Da rigenerare quando
servono dati aggiornati: al momento dell'estratto i venduti erano 6.961, oggi
sono **17.574**.

### API di sola lettura

`http://localhost:8001` espone lo stato della raccolta (`/brands`, `/queue`,
`/brands/<slug>/runs`). Serve a sapere **quanto è aggiornato** un dato, non a
leggere gli annunci.

---

## 3. Stato della raccolta

| | |
|---|---|
| annunci | 311.132 |
| attivi | 293.558 |
| venduti | 17.574 |
| non ancora arricchiti | 6.463 |
| marche | 25 |
| raccolta iniziata | **24/07/2026** |
| finestra temporale | ultimi **10 anni** di immatricolazione |

Le 25 marche coprono gran parte del mercato italiano, ma non sono un
censimento: le marche fuori elenco non compaiono affatto.

Tutte e 25 le marche sono state aggiornate integralmente il **30/07**, in un
giro unico di 9h34m concluso senza errori. Audi, che per cinque tentativi era
caduta su un id riassegnato da AutoScout e la cui rilevazione vendite non era
mai girata, è stata recuperata: 1.791 venduti, di cui 980 dichiarati in quel
giro. **Tutte le marche sono ora confrontabili fra loro.**

---

## 4. Le quattro insidie

Sono la parte di questo documento che conta di più. Ognuna produce un numero
che sembra plausibile ed è sbagliato.

### 4.1 Il tempo di vendita non si calcola da `first_seen_at`

`first_seen_at` è quando **lo scraper** ha visto l'annuncio la prima volta, e
raccoglie dal 24/07/2026. Un'auto pubblicata a maggio e venduta ieri risulta
"vista la prima volta" pochi giorni fa.

```
mediana sold_at − first_seen_at        →  1,8 giorni   ← non significa nulla
mediana sold_at − created_at_source    → 19,9 giorni   ← questo è il dato
```

Usare **`created_at_source`**, la data di pubblicazione dichiarata da
AutoScout. È popolata sul 100% degli annunci arricchiti.

### 4.2 Anche quel dato è distorto verso il basso

La finestra di osservazione è di pochi giorni. Si vedono solo le auto vendute
**dentro** quella finestra, quindi le vendite lente mancano sistematicamente: è
troncamento a destra.

Una mediana calcolata sui soli venduti **sottostima il tempo di vendita reale**,
e l'errore si riduce con le settimane. Per stime corrette servono metodi che
trattano i dati troncati — Kaplan-Meier sull'insieme completo, dove gli annunci
ancora attivi entrano come osservazioni censurate invece di essere esclusi.

Se l'app deve mostrare un tempo medio di vendita già ora, va accompagnato dalla
finestra di osservazione, mai presentato come valore assoluto.

### 4.3 Il dataset contiene auto nuove mescolate alle usate

Circa **11.000 annunci attivi** (3,8%) non hanno `first_registration`. Non è un
dato mancante: sono auto **mai immatricolate**, quindi quella data non esiste.
Misurato il 30/07:

| | annunci | prezzo medio |
|---|---|---|
| km non dichiarati | 7.339 | 51.181 € |
| 0-100 km (nuova o km 0) | 3.698 | 46.133 € |
| oltre 5.000 km | **1** | — |

Contro il resto del database: **24.221 € di media e 75.315 km**.

Costano circa il doppio. Qualunque analisi di prezzo che non le separi sbaglia
verso l'alto, e in modo **non uniforme fra le marche**, perché le premium ne
hanno di più — quindi l'errore non si annulla nei confronti.

Il filtro è `first_registration IS NULL`, che qui coincide quasi perfettamente
con "nuova o km 0": un solo annuncio su 11.043 sfugge alla regola.

I ~3.700 con chilometraggio dichiarato a zero sono **km 0** — auto immatricolate
dal concessionario e rivendute. Non sono rumore: sono un segmento di mercato con
una sua dinamica di prezzo, che vale la pena analizzare separatamente invece di
scartare.

### 4.4 `status = 'sold'` significa "sparito dal sito"

L'annuncio non è più raggiungibile ed è stato confermato rimosso da due
verifiche indipendenti. Nella grande maggioranza dei casi è una vendita, ma un
ritiro del venditore o una scadenza finiscono nella stessa categoria, e nessun
segnale li distingue.

**Storia della qualità di questo campo.** Fino al 28/07 un difetto leggeva un
errore transitorio del sito come rimozione: sono stati individuati e riportati
ad `active` **268 record**, e la causa è stata corretta il 29/07. Il fix ha poi
retto due giri completi — 13h34m su 24 marche e 9h34m su tutte e 25 — con zero
falsi positivi in entrambi. I dati attuali sono successivi alla correzione e
verificati aprendo le pagine.

Il criterio che identifica un falso storico, se dovesse mai riapparire: un
annuncio dichiarato venduto **meno di un'ora** dopo essere stato visto vivo.
Oggi ce ne sono zero.

---

## 5. Altre avvertenze, in ordine di quanto fanno male

- **Gli id non sono identità stabili.** AutoScout riassegna l'id di un annuncio
  ritirato a un'auto diversa, a volte di un'altra marca. Non trattare
  `listings.id` come chiave permanente di un veicolo fisico in analisi che
  attraversano il tempo.
- **Gli annunci non arricchiti hanno solo i campi della lista di ricerca**
  (`detail_scraped = false`, 6.463 righe). Niente `created_at_source`, quindi
  vanno esclusi da ogni analisi temporale.
- **`province` è popolata solo al 27,9%.** Per la geografia usare
  `latitude`/`longitude`, oppure ricavare la provincia dal CAP.
- **`interaction_count` cresce nel tempo**: va normalizzato sull'età
  dell'annuncio prima di confrontare annunci diversi.
- **`price_history` parte dal 24/07.** Un'auto pubblicata a marzo e già
  ribassata tre volte prima di allora ci risulta senza ribassi.
- **Tutti i timestamp sono UTC e senza fuso.** L'ora locale italiana è UTC+2 in
  questo periodo. Il fuso va aggiunto in fase di caricamento, non dedotto.

---

## 6. Il campo più utile, e la sua decodifica

`price_evaluation_median` è la stima di AutoScout del prezzo mediano di mercato
per auto comparabili: già normalizzata per modello, anno e chilometri.

`price_evaluation_category` è un intero 0-6 che AutoScout non documenta.
Correlandolo col rapporto fra prezzo richiesto e mediana di mercato risulta una
scala ordinale perfettamente monotona:

```
categoria   n annunci   mediana di price / price_evaluation_median
    0          5.011                0,780      ← molto sotto mercato
    3         86.678                1,023      ← in linea
    6          3.008                1,371      ← molto sopra
```

Il rapporto `price / price_evaluation_median` è calcolabile direttamente ed è
più fine della categoria, che ne è la discretizzazione.

---

## 7. Analisi che i dati reggono già

Con le cautele del §4:

- **prezzo contro mercato** per modello, area, tipo di venditore
- **elasticità del ribasso**: `price_history` incrociato con l'esito
- **privati contro concessionari** su prezzo, tempi, chilometraggio; e
  reputazione del concessionario (`dealers.ratings_*`) contro tempi
- **svalutazione** rispetto a `first_registration` e `mileage_km`
- **domanda**: `interaction_count` normalizzato come indicatore anticipatore
- **geografia** dalle coordinate

Quel che i dati **non** reggono ancora è qualunque affermazione assoluta sui
tempi di vendita. Serve accumulare settimane, oppure trattare esplicitamente il
troncamento.

---

## 8. Come lavora bene questo progetto

Il repo AutoSmart24 segue il ciclo **brainstorming → spec → piano → esecuzione
con subagenti e revisione**. Ha pagato: gli ultimi due lavori hanno intercettato
prima del rilascio un difetto di interazione fra componenti che corrompeva i
dati, e cinque difetti passati solo perché i test non potevano fallire.

Due abitudini che vale la pena portarsi dietro:

- **Un test che non può fallire non è copertura.** Rompere di proposito il
  codice e pretendere che il test lo denunci.
- **Verificare sui dati veri, non solo sulla suite.** Il difetto dei falsi
  venduti non si vedeva leggendo il codice: si è visto aprendo le pagine degli
  annunci.

Le spec già scritte stanno in `/home/vperrone/AutoSmart24/docs/superpowers/specs/`.

---

## 9. Riferimenti

| cosa | dove |
|---|---|
| schema commentato e CSV | `/home/vperrone/AutoSmart24/export-bi/` |
| DDL PostgreSQL | `/home/vperrone/AutoSmart24/export-bi/schema.sql` |
| spec del fix vendite | `docs/superpowers/specs/2026-07-28-sold-detection-design.md` |
| il difetto del riuso id | `docs/superpowers/specs/2026-07-28-listing-id-reuse-known-issue.md` |
| spec della resilienza | `docs/superpowers/specs/2026-07-29-scraper-resilience-design.md` |
| repository | https://github.com/vyperrrr99/AutoSmart24 |
