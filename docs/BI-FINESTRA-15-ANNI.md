# La raccolta passa da 10 a 15 anni: cosa cambia nei vostri numeri

Per la sessione che sviluppa AutoSmart-BI. Scritto il **19/08/2026**.

Vi scriviamo **prima** che accada, perché l'effetto principale è che alcune
vostre mediane scenderanno di parecchio in pochi giorni. Non sarà il mercato a
essere cambiato: saremo noi ad aver cambiato cosa guardiamo.

---

## 1. Cosa stiamo facendo

Finora raccoglievamo auto immatricolate negli ultimi **10 anni**, cioè dal
2016. Da stanotte allarghiamo a **15**, quindi dal 2011.

Il numero è misurato, non stimato: abbiamo interrogato AutoScout marca per
marca confrontando i risultati con `year_from` 2016 e 2011.

| | |
|---|---|
| annunci in più | **+43.857** |
| crescita | **+15,3%** |
| completamento | **23/08/2026** |

## 2. Il calendario, perché non arriva tutto insieme

Allargare tutte le marche in una notte avrebbe fatto scoprire 43.857 annunci
da arricchire in una volta: dieci ore e mezza in più su una notte da sei, con
la riclassificazione delle 09:00 che si sarebbe arresa e **il vostro snapshot
saltato**. Procediamo quindi a scaglioni da ~9.000 annunci, uno per notte.

| notte | marche allargate | annunci nuovi |
|---|---|---|
| 19/08 | Fiat, Ford, CUPRA, MG | ~8.900 |
| 20/08 | Audi, Volkswagen, Nissan | ~8.900 |
| 21/08 | BMW, Mercedes-Benz, Opel | ~9.000 |
| 22/08 | MINI, Peugeot, Renault, Alfa Romeo, Citroen, Dacia | ~9.000 |
| 23/08 | Lancia, Land Rover, smart, Jeep, Porsche, Toyota, Kia, Hyundai, Volvo, Skoda | ~8.100 |

**Vi conviene guardare i vostri grafici marca per marca in questi cinque
giorni**: ogni marca si sposta la notte in cui viene allargata, non tutte
insieme. Se una mediana crolla il 21 e le altre no, è BMW o Mercedes, ed è
previsto.

## 3. Quanto scenderanno le mediane

Le auto 2011–2015 sono un altro mercato. Dai dati che già abbiamo:

| anno | prezzo mediano | km medi |
|---|---|---|
| 2011 | 4.000 € | 164.700 |
| 2013 | 5.500 € | 156.800 |
| 2015 | 6.500 € | 136.400 |
| **2016** | **10.500 €** | **147.400** |
| 2019 | 15.890 € | 109.200 |
| 2023 | 21.300 € | 54.500 |

Contro una mediana attuale di circa **17.000 €** sull'intero parco attivo.

L'effetto non è uniforme fra le marche, e la forma conferma che la misura è
reale — le marche con parco circolante vecchio crescono molto di più:

| crescono di più | | crescono di meno | |
|---|---|---|---|
| Lancia | +41,6% | Jeep | +6,2% |
| smart | +35,6% | Skoda | +6,4% |
| MINI | +32,5% | **CUPRA** | **0%** |
| Fiat | +24,2% | **MG** | **0%** |

CUPRA e MG non si muovono di un annuncio perché **non esistevano prima del
2016**. Se nei vostri grafici quelle due restassero ferme mentre le altre si
spostano, è corretto così.

## 4. Cosa vi conviene fare

**Segmentate per anno, o dichiarate la finestra.** Una «mediana di mercato»
calcolata su tutto il database non è più confrontabile con quella della
settimana scorsa. Se pubblicate serie storiche, il 19–23 agosto è una
discontinuità di metodo, non un movimento di prezzo: va marcata, altrimenti fra
sei mesi qualcuno la leggerà come un crollo estivo.

**Il tempo di vendita cambierà distribuzione.** Le auto vecchie e con molti
chilometri restano in vendita più a lungo. Aspettatevi una coda più pesante,
sempre per composizione e non per rallentamento del mercato.

**Le dotazioni arrivano gratis.** Le auto scoperte adesso ricevono `equipment`,
`paint_type` e le nove booleane alla prima lettura, senza bisogno di un secondo
passaggio. La copertura sul parco attivo scenderà temporaneamente — oggi è
all'82% — e risalirà da sola.

## 5. Una cosa che vi riguarda ancora di più: la quarantena di agosto

Modifica separata, decisa oggi.

Finora un annuncio in quarantena diventava una vendita dopo **30 giorni**. Ma
un concessionario che chiude il primo agosto, al trentesimo giorno è ancora in
ferie: dichiarare venduto il suo magazzino avrebbe inventato un mese di vendite
mai avvenute. In agosto la quarantena ha raccolto **5.934 annunci**, quindi non
era un caso di scuola — sarebbe successo il 31 agosto.

Da oggi chi sparisce fra il **15 luglio e il 31 agosto** non si risolve prima
del **15 settembre**. È un pavimento, non una finestra più lunga: chi sparisce
il 25 agosto serve comunque i suoi trenta giorni, che scadono dopo.

**Per voi**: il conteggio delle vendite di agosto **non salirà il 31 agosto**
come sarebbe successo, ma dal **15 settembre** in avanti. Quasi 6.000 vendite
retroattive distribuite sulla seconda metà di settembre, tutte datate al giorno
in cui l'auto è sparita — quindi **agosto si riempirà a posteriori**. Se avete
grafici che si aspettano un mese chiuso, quello non lo è.

---

## Riepilogo

| | |
|---|---|
| finestra | 10 → 15 anni, +43.857 annunci (+15,3%) |
| quando | una marca per volta, dal 19 al 23/08 |
| effetto | mediane in discesa per composizione, non per mercato |
| CUPRA e MG | invariate, non esistevano prima del 2016 |
| dotazioni | arrivano da sole sulle auto nuove |
| quarantena | agosto si risolve dal 15/09, non dal 31/08 |
