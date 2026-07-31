# Accesso al database per l'applicazione BI

Da passare alla sessione che sviluppa AutoSmart-BI. Aggiornato al **01/08/2026**.

---

## 1. Cosa è cambiato rispetto alla spec del 31/07

La spec prevedeva di leggere il PostgreSQL locale dello scraper su
`localhost:5434`. **Non è più così.** I dati vivono ora anche su **Supabase**, e
l'applicazione legge da lì.

Cambiano tre cose:

- **La connessione**: Supabase, non `localhost`.
- **Dove vive lo schema `bi`**: su Supabase, accanto ai dati, non sulla macchina
  dello scraper. Le migrazioni Alembic vanno puntate lì.
- **La freschezza**: i dati sono una copia rigenerata ogni mattina, non una
  vista in tempo reale. Vedi §4.

Tutto il resto della spec resta valido, incluso il principio che
`public` è di proprietà dello scraper e l'applicazione non ci scrive mai.

---

## 2. Connessione

```
Host     aws-0-eu-central-1.pooler.supabase.com
Porta    5432
Database postgres
Utente   postgres.ofbmvgwskvcsyleauyhu
```

La password è nel file `/home/vperrone/AutoSmart24/.env.supabase` sulla macchina
dello scraper, modo 600 ed escluso da git. **Chiedila all'utente**: non va
committata, e il repository AutoSmart24 è pubblico.

Nella stringa di connessione la password va **codificata per URL** — contiene un
carattere `@`, che altrimenti verrebbe letto come separatore dell'host.

Usare il **pooler**, non la connessione diretta: `db.<ref>.supabase.co` risolve
solo in IPv6 sul piano gratuito, mentre il pooler risponde anche in IPv4.

PostgreSQL sul server è la **17.6**; lo scraper gira sulla 16.

---

## 3. Cosa c'è, e cosa no

Cinque tabelle, in `public`:

| tabella | righe (01/08) | contenuto |
|---|---|---|
| `listings` | 286.300 | l'annuncio: auto, prezzo, venditore, stato |
| `price_history` | 304.184 | ogni variazione di prezzo osservata |
| `dealers` | 7.884 | i concessionari |
| `brand_catalog` | 290 | tutte le marche esistenti su AutoScout |
| `tracked_brands` | 26 | le marche raccolte, con la loro configurazione |

**Non sono state copiate** `scrape_events` e `scrape_runs`: sono la telemetria
dello scraper, non servono all'analisi e peserebbero altri 9 MB su un piano da
500. Se serve sapere quanto è aggiornato un dato, si ricava da
`listings.last_seen_at`.

Stato al 01/08: **263.916 attivi, 22.384 usciti**. Occupazione attuale 252 MB
sui 500 del piano gratuito, con una crescita di circa 1 MB al giorno: restano
**quattro o cinque mesi** prima di dover passare al piano a pagamento.

---

## 4. Il dato è una copia notturna, non una vista in tempo reale

Lo scraper gira ogni sera alle 22:00 e finisce verso le 8:00. Alle **09:00** un
lavoro pianificato ricopia tutto su Supabase.

Due conseguenze da tenere presenti nel progetto:

- **Fra le 09:00 e le 09:00 del giorno dopo i dati non cambiano.** Le viste
  materializzate possono essere aggiornate una volta al giorno e non hanno
  bisogno di logiche di invalidazione.
- **La copia è un `TRUNCATE` più ricarica in un'unica transazione.** Chi legge
  vede la copia di ieri finché quella nuova non è completa, mai una tabella a
  metà. Ma qualunque oggetto che dipenda da queste tabelle deve sopravvivere a
  un `TRUNCATE`: viste materializzate sì (vanno solo riaggiornate dopo), vincoli
  di chiave esterna verso `public` **no** — non crearne dallo schema `bi`.

Se la sincronizzazione fallisce, l'applicazione mostra i dati del giorno prima
invece di rompersi. Il log è in `sync-supabase.log` sulla macchina dello scraper.

---

## 5. Due decisioni dello scraper che cambiano la spec

### 5.1 Le auto non usate non arrivano più: `is_km_zero` è morto

La spec definiva `is_km_zero` come `mileage_km < 1000`, escludendole dalla curva
chilometri↔prezzo ma mantenendole con un filtro dedicato.

**Lo scraper ora le scarta alla fonte**, con esattamente la stessa soglia, e le
esistenti sono state rimosse dal database: 34.703 il 31/07, altre 1.429
all'allineamento della soglia. `is_km_zero` troverà sempre zero righe.

Toglietelo, o lasciatelo come rete di sicurezza, ma non progettateci sopra
analisi: quel segmento non esiste più nei dati.

Motivo della decisione: erano 11.041 auto mai immatricolate (49.543 € di media)
e 20.895 km 0 immatricolate da nove mesi (33.557 €), contro 23.408 € e 81.217 km
delle usate vere. Tenute dentro spingevano ogni statistica di prezzo verso
l'alto, **in modo diverso da marca a marca** perché le premium ne hanno di più —
quindi l'errore non si annullava nei confronti.

Il database contiene ora solo auto usate: **81.615 km e 23.285 €** di media.

### 5.2 I numeri della spec sono superati

```
                  spec BI (29/07)     ora (01/08)
annunci               305.427           286.300
attivi                293.926           263.916
usciti                 11.501            22.384
non arricchiti         13.954             6.435
marche                     25                26   (aggiunta smart)
```

Gli usciti sono **raddoppiati**: sono passati tre giri completi. Qualunque quota
calcolata sui valori del 29/07 va rifatta.

---

## 6. Cose da sapere sui dati, non ripetute qui

Le insidie metodologiche — il tempo di vendita che non si calcola da
`first_seen_at`, il troncamento a destra, `sold` che significa «sparito dal
sito», il riuso degli id — sono documentate in
`/home/vperrone/AutoSmart24/docs/AVVIO-SESSIONE-BI.md`, §4 e §5. Restano tutte
valide.

Lo schema tabella per tabella è in
`/home/vperrone/AutoSmart24/export-bi/README.md`.

---

## 7. Una richiesta

Lo schema `bi`, il ruolo di sola lettura e le migrazioni Alembic vivono ora su
Supabase, dove **anche lo scraper scrive** ogni mattina con un `TRUNCATE` su
`public`.

Se create oggetti che dipendono da `public` — viste, viste materializzate,
funzioni — scriveteli in modo che sopravvivano a quella ricarica, e ditecelo:
sono l'unica cosa che potrebbe far fallire la sincronizzazione notturna, e il
fallimento si vedrebbe solo il mattino dopo.
