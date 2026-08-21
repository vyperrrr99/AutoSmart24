# Seconda macchina: raccogliere in parallelo senza pestarsi i piedi

Per la sessione Claude Code sulla macchina **Windows**. Scritto il **22/08/2026**
dalla sessione sul ThinkPad.

Obiettivo: finire in un paio di giorni l'estensione della raccolta da 10 a 15
anni, che su una macchina sola richiederebbe tre settimane.

---

## 1. Perché serve una seconda macchina, e cosa NON deve fare

Il vincolo non è la potenza di calcolo, è la **frequenza di richieste per
indirizzo IP**. Il 19 e il 21 agosto autoscout24.it ci ha risposto **429** due
volte, fermando la coda; abbiamo dovuto scendere da concorrenza 8 a 5, e a
quella velocità la sola fase di ricerca occupa 9,6 ore su 11 disponibili,
lasciando margine per smaltire l'arretrato di **una marca a notte**.

Con due IP distinti ciascuna macchina torna a 8 senza aumentare la frequenza
sul proprio indirizzo.

**La regola che non si può violare: le due macchine lavorano su marche
disgiunte.** `BrandRunGuard` e `QueueController` vivono nella memoria del
processo e non si coordinano fra host. Sulla stessa marca raddoppierebbero le
richieste esattamente sulle pagine che ci hanno già fatto bloccare, e
farebbero due volte lo stesso lavoro.

## 2. Il database è uno solo

Non c'è travaso. Questa macchina **scrive direttamente nel PostgreSQL del
ThinkPad**, raggiunto via Tailscale:

```
postgresql+psycopg://autosmart24:autosmart24@100.117.184.118:5434/autosmart24
```

Già configurato in `docker-compose.seconda-macchina.yml`. Quello che raccogli è
immediatamente visibile all'altra macchina e alla BI: niente dump, niente
sequenze da rimettere a posto, niente riconciliazione.

Il database accetta connessioni **solo** da localhost e dall'indirizzo
Tailscale — non da reti pubbliche.

## 3. Avvio

```bash
git clone https://github.com/vyperrrr99/AutoSmart24.git
cd AutoSmart24

# Il proxy, se lo usi, va a livello di macchina: httpx onora HTTPS_PROXY da
# solo. In alternativa scommenta SCRAPE_PROXY nel file qui sotto.
docker compose -f docker-compose.yml -f docker-compose.seconda-macchina.yml \
  up -d --no-deps app

# La divisione delle marche. Il nome e' obbligatorio: eseguirlo con quello
# sbagliato spegne le tue marche e accende quelle dell'altra macchina.
bash scripts/applica-divisione.sh windows
```

Lo script stampa quali marche restano attive e **verifica leggendo l'API**, non
deducendo dai comandi riusciti. Se l'elenco non corrisponde, si ferma dicendolo.

Verifica che la connessione al database funzioni davvero:

```bash
docker compose -f docker-compose.yml -f docker-compose.seconda-macchina.yml \
  run --rm --no-deps app python -c "
import os; from sqlalchemy import create_engine, text
e = create_engine(os.environ['DATABASE_URL'])
with e.connect() as c:
    print('annunci a database:', c.execute(text('SELECT count(*) FROM listings')).scalar())"
```

Se stampa un numero intorno ai 350.000, sei collegato al database giusto.

## 4. Le tue marche

Quindici, in `config/marche-per-macchina.yaml`. La ripartizione bilancia il
**lavoro vero** — pagine di ricerca più pagine di dettaglio da leggere — non il
numero di marche: sbilanciamento 0,1%.

| marca | annunci attivi | pagine da leggere |
|---|---|---|
| BMW | 20.381 | 3.663 |
| Mercedes-Benz | 20.300 | 3.216 |
| Audi | 28.958 | 2.370 |
| Peugeot | 14.353 | 1.811 |
| Renault | 11.518 | 1.696 |
| Citroen | 8.919 | 1.597 |
| Land Rover | 7.157 | 1.162 |
| smart | 2.709 | 981 |
| Ford | 18.977 | 674 |
| Kia | 4.176 | 668 |
| Volvo | 3.250 | 501 |
| Dacia | 3.267 | 453 |
| Volkswagen | 24.433 | — |
| CUPRA | 2.550 | — |
| MG | 1.489 | — |

CUPRA e MG non hanno nulla da recuperare: **non esistevano prima del 2016**.

## 5. Cosa NON deve girare su questa macchina

Restano tutte sul ThinkPad. Se le duplichi, fanno danni:

| lavoro | perché non qui |
|---|---|
| riclassificazione delle 09:00 | scrive `sold` su tutto il database: due esecuzioni si pestano |
| recupero dotazioni diurno | ha un cursore proprio; due istanze salterebbero righe a vicenda |
| allargamento finestra | tocca `tracked_brands`, condivisa |
| backup e snapshot della BI | vivono sul ThinkPad |

Su questa macchina serve **solo il contenitore `app`**, con il suo scheduler
interno che parte alle 22:00 sulle tue quindici marche.

## 6. Le protezioni che erediti

Sono nel codice, non da configurare:

- **tetto di 2.000 pagine di dettaglio per giro** (`DETAIL_PAGES_PER_RUN`). Un
  arretrato grosso si smaltisce in più notti invece di allungare un giro fino a
  farlo bloccare. È la protezione nata dai due 429.
- **un 403/429 ferma la coda** invece di insistere. Sul ThinkPad c'è un
  recupero automatico dopo tre ore; qui **non c'è**, perché quel recupero sta
  in `avvia-se-mancato.sh` che gira solo là. Se ti blocchi, la coda resta ferma
  finché non fai `curl -X POST http://localhost:8001/queue/resume`. Vale la
  pena guardarlo la mattina.
- **una pagina 404 non è una vendita**: la conferma è a fine giro e confronta
  la marca restituita, per non farsi ingannare dal riuso degli id.

## 7. Se il proxy Surfshark dà problemi

Da provare **prima** di lanciare un giro intero: le VPN commerciali hanno IP di
uscita condivisi che i sistemi anti-bot spesso conoscono già, quindi possono
essere bloccati *più* in fretta di una connessione domestica.

```bash
docker compose -f docker-compose.yml -f docker-compose.seconda-macchina.yml \
  run --rm --no-deps app python -c "
import sys, time; sys.path.insert(0,'/app/src')
from sqlalchemy import create_engine, text; import os
from autosmart24.scraping.http_client import make_client
from autosmart24.scraping.detail_queue import fetch_detail
e = create_engine(os.environ['DATABASE_URL'])
with e.connect() as c:
    urls = [r[0] for r in c.execute(text(
      \"SELECT url FROM listings WHERE status='active' AND detail_scraped LIMIT 20\"))]
cl = make_client(3.0, 8.0); ok = 0
for u in urls:
    try:
        fetch_detail(cl, u); ok += 1
    except Exception as ex:
        print('fermato dopo', ok, 'pagine:', type(ex).__name__); break
else:
    print('20 pagine su 20: l uscita e buona')"
```

Venti su venti significa che l'IP è pulito. Un `BlockedError` nei primi
tentativi significa che quell'uscita è già segnata: cambiala, o prova senza
proxy con la connessione diretta di quella macchina — è comunque un IP diverso
dal ThinkPad, che è tutto ciò che serve.

## 8. Come sapere se sta funzionando

Il database è condiviso, quindi si guarda da qualunque delle due:

```sql
-- quanto resta da leggere, per macchina
SELECT brand, count(*) FROM listings
WHERE status='active' AND NOT detail_scraped GROUP BY 1 ORDER BY 2 DESC;

-- i giri di stanotte
SELECT brand, status, started_at, finished_at, listings_seen, errors_count
FROM scrape_runs WHERE started_at >= now() - interval '12 hours' ORDER BY started_at;
```

Il segnale che qualcosa non va è `status = 'blocked'`. Se compare, la coda di
quella macchina è ferma e va ripresa a mano.

---

## In due righe

Clona, avvia `app` con l'override, esegui `applica-divisione.sh windows`,
verifica la connessione al database. Il resto parte da solo alle 22:00. Non
duplicare i lavori pianificati del ThinkPad.
