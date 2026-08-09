# Dotazioni: dove siamo e cosa dicono i primi numeri

Per la sessione che sviluppa AutoSmart-BI. Scritto il **09/08/2026**.

Segue `BI-DOTAZIONI-E-VERNICE.md`, che descrive le colonne. Qui c'è quanto sono
piene, ogni quanto ciascuna dotazione compare, e un avvertimento su come **non**
leggere il legame col prezzo.

---

## 1. Il recupero è partito: la copertura cresce molto più in fretta del previsto

Nel documento precedente vi avevamo detto che le colonne si sarebbero riempite
solo con gli annunci nuovi, circa 5.000 a notte. **Non è più così.**

Abbiamo scoperto che le 250.625 auto già a catalogo non le avrebbero avute
**mai**: la coda notturna seleziona solo `detail_scraped = false`, e quel campo
diventa `true` per sempre alla prima lettura. Erano uscite dalla coda prima che
ci fosse qualcosa da raccogliere.

Da oggi gira un recupero una tantum, a blocchi di 1.500 auto ogni ora fra le
10:00 e le 21:00, negli orari in cui lo scraper è fermo. Primo blocco: 1.500
auto in 36 minuti.

| | |
|---|---|
| copertura al 09/08 10:44 | **4.204 su 258.572 attive — 1,6%** |
| ritmo | ~16.500 al giorno dal recupero, più gli annunci nuovi |
| completamento stimato | **~15 giorni**, se il portatile resta acceso di giorno |

**Le 51.944 auto già vendute non le avranno mai** — oggi sono zero su zero. La
loro pagina non esiste più. Se il vostro modello di prezzo si addestra sulle
vendite, per quelle righe le dotazioni non arriveranno né ora né dopo.

## 2. Quanto è rappresentativo quello che avete adesso

Il campione coperto è già vicino al parco totale, con una leggera inclinazione
verso le auto recenti:

| | coperte (4.018) | tutte (247.514) |
|---|---|---|
| anno medio | 2021,2 | 2020,6 |
| prezzo medio | 23.253 € | 23.489 € |
| km medi | 79.530 | 82.699 |

Sei mesi di differenza sull'anno, prezzo praticamente identico. Utilizzabile
per capire l'ordine di grandezza; **non ancora per pubblicare stime**, perché
il recupero procede per id crescente e la parte già fatta non è un campione
casuale del parco.

## 3. Ogni quanto compare ciascuna dotazione

Su 4.204 auto con la lista letta. La lista media contiene **38,2 voci**; 158
auto (3,8%) hanno una lista vuota — il venditore non ha dichiarato nulla.

| dotazione | auto | quota |
|---|---|---|
| Cerchi in lega | 3.279 | **78,0%** |
| Fari LED | 2.234 | 53,1% |
| Telecamera parcheggio | 1.733 | 41,2% |
| Fari full-LED | 1.542 | 36,7% |
| Regolazione elettrica sedili | 860 | 20,5% |
| Sedili riscaldati | 755 | 18,0% |
| Interni in pelle | 468 | 11,1% |
| Tetto panoramico | 412 | 9,8% |
| Tettuccio apribile | 320 | **7,6%** |

I cerchi in lega sono su quattro auto su cinque: come variabile discriminante
valgono poco, ma la loro **assenza** dice qualcosa. Il tettuccio è l'opposto —
raro, e per questo informativo quando c'è.

Vernice, sulle stesse auto: `Metallizzato` **39,1%**, `Altro` 43,3%, vuoto
17,5%. Il campo `body_color_original` col nome commerciale resta il più raro,
intorno a un terzo.

## 4. Il legame col prezzo: leggetelo come associazione, non come valore

Questa è la parte da maneggiare con cura, e il motivo per cui vi diamo i numeri
adesso invece che dopo.

Mediana del prezzo richiesto, con e senza ciascuna dotazione (solo annunci
canonici, `duplicate_of IS NULL`):

| dotazione | con | senza | divario grezzo |
|---|---|---|---|
| Tettuccio apribile | 33.000 € | 17.780 € | **+15.220** |
| Interni in pelle | 31.900 € | 17.470 € | +14.430 |
| Sedili riscaldati | 31.000 € | 16.900 € | +14.100 |
| Tetto panoramico | 28.500 € | 17.800 € | +10.700 |
| Regolazione elettrica sedili | 25.000 € | 17.000 € | +8.000 |
| Fari LED | 21.000 € | 14.490 € | +6.510 |
| Telecamera parcheggio | 22.000 € | 15.900 € | +6.100 |
| Fari full-LED | 21.790 € | 15.900 € | +5.890 |
| Cerchi in lega | 19.450 € | 13.850 € | +5.600 |

**Nessuno di questi numeri è quanto vale l'optional.** Sono quanto costa in più
un'auto che ce l'ha, che è una cosa diversa.

Il tettuccio lo mostra bene. Le auto che ce l'hanno hanno lo **stesso anno** di
quelle che non ce l'hanno (2021,1 contro 2021,2), quindi non è l'età a spiegare
i 15.220 €. È il segmento:

| marca | quota fra le auto col tettuccio | quota sul totale |
|---|---|---|
| Mercedes-Benz | 20,7% | 7,1% |
| Porsche | **10,4%** | **1,9%** |
| BMW | 12,9% | 7,2% |
| Audi | 14,9% | 9,7% |

Porsche pesa cinque volte tanto fra le auto col tettuccio. Il divario misura in
buona parte «quanto costa una Porsche».

Confrontando **dentro la stessa marca e lo stesso anno** — 32 strati con almeno
tre auto col tettuccio e dieci senza — il divario mediano scende a
**+8.975 €**: il 41% in meno.

E anche quello resta sovrastimato, perché dentro una marca il tettuccio arriva
con l'allestimento alto, il motore più grosso e il resto del pacchetto optional.
Un modello che voglia dire quanto vale il tettuccio deve tenere insieme
allestimento, motorizzazione e le altre dotazioni, che sono fortemente
correlate fra loro.

**Suggerimento**: usate questi numeri per ordinare le dotazioni per importanza —
quell'ordine è solido — e non per attribuire un valore in euro a ciascuna finché
non avete un modello che controlla le variabili insieme.

## 5. Ricordate `NULL`

Vale ancora, e ora ancora di più: con l'1,6% di copertura, **il 98% delle auto
ha tutte le booleane a `NULL`**. Un `WHERE has_sunroof = false` oggi non
restituisce «le auto senza tettuccio»: restituisce le poche che abbiamo già
guardato e che non ce l'hanno.

```sql
-- quante auto possiamo dire qualcosa su questa dotazione
SELECT count(*) FILTER (WHERE has_sunroof IS NOT NULL) AS note,
       count(*) AS totali
FROM listings WHERE status='active' AND duplicate_of IS NULL;
```

Vi conviene mostrare questo rapporto accanto a qualunque grafico basato sulle
dotazioni, finché il recupero non è finito.

---

## Riepilogo

| | |
|---|---|
| copertura oggi | 1,6% · ~16.500 al giorno · completa in ~15 giorni |
| auto vendute | zero, e non cambieranno mai |
| dotazione più comune | cerchi in lega, 78% |
| dotazione più rara | tettuccio apribile, 7,6% |
| vernice metallizzata | 39,1% |
| divari di prezzo | associazioni, non valori: il tettuccio passa da +15.220 a +8.975 controllando solo marca e anno |
