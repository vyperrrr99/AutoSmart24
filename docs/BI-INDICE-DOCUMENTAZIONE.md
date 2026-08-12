# Documentazione per la BI: cosa leggere, e cosa in ciascun documento non è più vero

Aggiornato al **12/08/2026**. Da leggere per primo.

Sette documenti si sono accumulati fra il 31/07 e oggi. Alcuni contengono parti
superate, e due contengono affermazioni che oggi sono **false** — non vaghe,
false. Questo indice dice quali, così nessuno legge un documento vecchio in
isolamento e ci costruisce sopra.

---

## Lo stato di adesso, in una tabella

Se un documento contraddice questa tabella, ha torto il documento.

| | |
|---|---|
| database di riferimento | PostgreSQL locale, `autosmart24-postgres-1` |
| **Supabase** | **abbandonato il 06/08**, non contiene più dati |
| marche raccolte | 26 |
| contenuto | **solo auto usate**: sotto i 1.000 km scartate alla fonte, oggi ne restano 0 |
| annunci attivi | 257.923 (di cui 11.018 copie) |
| venduti | 57.393 · quarantena 4.181 · rimossi 14.761 |
| stati di un annuncio | **quattro**: `active`, `sold`, `quarantine`, `removed` |
| dotazioni e vernice | 12 colonne, copertura **26,7%** e in crescita, completa verso il 21/08 |
| riclassificazione | ogni giorno alle 09:00, con file di esito da controllare |

## I documenti, in ordine di quanto sono attuali

### Attuali, leggeteli

| documento | di cosa parla |
|---|---|
| **`RISPOSTA-ALLA-BI-11-08.md`** | il file di esito `stato/riclassificazione.json`, e cosa è stato consegnato |
| **`BI-DOTAZIONI-E-VERNICE.md`** | le 12 colonne nuove, i due trabocchetti di etichetta, `NULL` ≠ `false` |
| **`BI-DOTAZIONI-PRIMI-NUMERI.md`** | frequenze su 51.000 auto e l'avvertimento sui prezzi |
| **`BACKUP-GESTITO-DALLA-BI.md`** | scritto da voi, resta valido |
| **`RISPOSTA-DALLA-BI-07-08.md`** | scritto da voi, resta valido |

### Ancora utili, ma con parti false

**`AVVIO-SESSIONE-BI.md`** (31/07) — l'avviamento originale. **Le insidie §4.1 e
§4.2 sul tempo di vendita restano il documento più importante che abbiamo
scritto**, e valgono ancora parola per parola: non calcolate il tempo di vendita
da `first_seen_at`, e anche `created_at_source` è distorto verso il basso.

Non è più vero, nello stesso documento:

- **§4.3, «il dataset contiene auto nuove mescolate alle usate»** — falso dal
  31/07. Le auto sotto i 1.000 km sono scartate alla fonte; il vostro
  `is_km_zero` oggi trova zero righe.
- **§4.4, «`status = 'sold'` significa sparito dal sito»** — era vero allora,
  oggi no. Gli stati sono quattro e `sold` significa venduto **dopo** la
  riclassificazione. Vedi `BI-ACCESSO-SUPABASE.md` §3 per la tabella corretta.
- **«25 marche»** — sono 26, aggiunta `smart`.

**`BI-ACCESSO-SUPABASE.md`** (05/08) — **il titolo è ingannevole e metà del
documento è morta.** Tutto ciò che riguarda la connessione a Supabase, la
sincronizzazione delle 09:00 e le migrazioni da applicare là non vale più:
Supabase è stato abbandonato il 06/08 e le sue tabelle svuotate.

Resta invece attuale, ed è la spiegazione migliore che abbiamo:

- **§1.3**, la regola `duplicate_of IS NULL` e perché un annuncio su venti è
  una copia;
- **§3**, la tabella dei quattro stati e i valori di `removal_reason`;
- **§4**, perché esiste la riclassificazione, con i numeri misurati.

Leggete quelle tre sezioni e ignorate il resto.

### Non vi riguarda

`SETUP-SECONDA-MACCHINA.md` e `RISPOSTA-ALLA-BI-06-08.md` — il primo è nostro,
il secondo è superato da quello dell'11/08.

## Le tre regole che nessun documento deve farvi dimenticare

Se doveste leggere una sola cosa, questa.

**1. `status = 'sold'`, e solo dopo la riclassificazione.** Una sparizione non è
una vendita: su un giro reale **1.305 sparizioni su 4.014 non lo erano**, un
terzo. Gli altri tre stati non sono vendite parziali, non contateli.

**2. `duplicate_of IS NULL` su ogni conteggio di inventario e prezzo.** Alcuni
venditori pubblicano lo stesso parco sotto più identità: 11.018 annunci attivi
sono copie. Senza il filtro, un venditore con una politica di prezzo sua vota
nove volte in ogni mediana.

**3. `NULL` non è `false`** sulle dotazioni. Con il 26,7% di copertura, tre auto
su quattro hanno quelle colonne nulle: significa «mai guardata», non «senza
tettuccio». Trattarle come `false` falsa ogni confronto di prezzo, e lo fa in
modo invisibile.

---

## Come ci si scrive adesso

`/home/vperrone/scambio/`, una casella per destinatario, protocollo in
`LEGGIMI.md`. La vostra è `a-bi/`, la nostra `a-scraper/`.

I documenti continuano a stare nei `docs/`, che è la sede giusta per ciò che va
versionato. La casella serve a dire **che** un documento c'è: è la notifica che
mancava, ed è il motivo per cui una vostra richiesta è rimasta ferma quattro
giorni con il lavoro dall'altra parte già pronto.
