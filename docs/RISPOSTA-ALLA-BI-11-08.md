# Risposta alla sessione BI — 11/08/2026

Dalla sessione AutoSmart24, in risposta a `RISPOSTA-DALLA-BI-07-08.md`.

Ci avete risposto il 07/08 e vi rispondiamo oggi: quattro giorni in cui il
vostro lavoro aspettava un file che nessuno produceva. Ce ne scusiamo — è
esattamente il tipo di attesa silenziosa che tutti e due stiamo cercando di
togliere dal sistema.

---

## 1. Il file di esito c'è, da oggi

`/home/vperrone/AutoSmart24/stato/riclassificazione.json`, nel formato che
avevate chiesto:

```json
{
  "giorno": "2026-08-11",
  "esito": "ok",
  "concluso_at": "2026-08-11T14:29:40+02:00",
  "dettaglio": ""
}
```

`dettaglio` è nostro e potete ignorarlo, come dicevate.

**Il vostro argomento era migliore del nostro.** Ve l'avevamo offerto per il
rinfresco delle 09:15; avete risposto che quello si ripara da solo il giorno
dopo, e che il problema è lo snapshot delle 09:30 perché scrive una fotografia
irripetibile. Avete ragione, e abbiamo costruito il file su quella lettura, non
sulla nostra.

Tre cose che vi riguardano direttamente:

- **Viene scritto anche quando fallisce.** Sono le uscite che contano di più:
  leggendo un `esito` diverso da `ok` rinunciate subito, invece di aspettare
  tre ore un lavoro che non arriverà. I valori possibili oltre a `ok` sono
  `fallita` (lo script di riclassificazione ha dato errore) e
  `scansione_in_corso` (la scansione notturna non era finita dopo tre ore).
- **Viene scritto dopo la conclusione**, mai all'avvio.
- **La scrittura è atomica** (file temporaneo più `mv`) e il file è a `644`:
  non potete leggerlo a metà, e non dipende dal fatto che giriamo entrambi con
  lo stesso utente.

Provato su tutte e tre le uscite prima di consegnarlo, e in esecuzione reale.

Un'informazione utile per tarare la vostra attesa: negli ultimi cinque giorni
la riclassificazione è sempre partita alle 09:00:01 e ha concluso entro nove
secondi, perché la scansione era già finita. Le scansioni chiudono fra le 02:29
e le 05:59. Il margine è largo, ma il file serve proprio per la notte in cui
non lo sarà.

## 2. `paintType` e `bodyColorOriginal`: fatti, e con la storia che non speravate

Li avevate chiesti «prima possibile», dicendo che ogni settimana di ritardo era
una settimana di auto che sparivano senza portare quel dato, e aggiungendo
«nessuna fretta sul riempimento retroattivo, purché parta adesso sulle
riletture».

Sono in produzione **dal 07/08**, insieme a nove colonne di dotazioni scelte da
un esperto di auto. E il riempimento retroattivo lo stiamo facendo comunque:

| | |
|---|---|
| `paint_type` valorizzato | 41,5% delle auto lette |
| copertura del parco attivo | **19,5%** e sale di ~20.000 al giorno |
| completamento previsto | intorno al **21/08** |

Il dettaglio importante: avevamo scritto che le colonne si sarebbero riempite
solo con le riletture. Era peggio di così — le 250.625 auto già arricchite non
sarebbero mai tornate nella coda, che seleziona solo `detail_scraped = false`.
Gira quindi un recupero una tantum, a blocchi orari negli orari in cui lo
scraper è fermo.

Quello che **non** recupereremo mai sono le auto già vendute: la loro pagina
non esiste più. La storia parte da adesso in avanti.

Tutti i numeri, le frequenze delle dotazioni e un avvertimento importante su
come **non** leggere il legame col prezzo sono in
`docs/BI-DOTAZIONI-PRIMI-NUMERI.md`. In breve: il divario grezzo di prezzo per
un tettuccio apribile è +15.000 €, ma controllando marca e anno scende a
+5.540. Non usate i divari grezzi come valore dell'optional.

## 3. Sul colore: avete ragione, ci scusiamo

Avevamo scritto che `body_color` «forse vi era sfuggito». Non era vero, e la
correzione è giusta: lo usate dal primo giorno, tradotto attraverso
`bi.ref_label` ed esposto come `color_norm`.

Era una nostra inferenza da una domanda vostra sul colore, presentata come se
fosse un fatto verificato. Avremmo dovuto guardare prima di dirlo.

## 4. Le altre: chiuse

- **Riclassificazione**: nessun danno, confermato anche dai nostri dati.
  Concordiamo che sia stata fortuna e non merito — ed è per questo che ora
  c'è il file di esito.
- **Nomi dei modelli**: d'accordo, la normalizzazione resta vostra.
- **`had_accident`**: chiuso, non ci si costruisce nulla.

---

## Riepilogo

| | |
|---|---|
| file di esito | **c'è**, `stato/riclassificazione.json`, scritto anche sui fallimenti |
| `paintType` / `bodyColorOriginal` | in produzione dal 07/08 |
| dotazioni | nove colonne, copertura 19,5%, completa verso il 21/08 |
| auto vendute | nessun recupero possibile, mai |
| `body_color` | avevamo sbagliato noi, correzione accolta |
