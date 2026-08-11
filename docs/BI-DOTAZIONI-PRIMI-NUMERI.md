# Dotazioni: dove siamo e cosa dicono i numeri

Per la sessione che sviluppa AutoSmart-BI. Aggiornato all'**11/08/2026**.

Segue `BI-DOTAZIONI-E-VERNICE.md`, che descrive le colonne. Qui c'è quanto sono
piene, ogni quanto compare ciascuna dotazione, e un avvertimento su come **non**
leggere il legame col prezzo.

Sostituisce la versione del 09/08: i numeri erano calcolati su 4.204 auto, ora
sono su 51.059, e una conclusione era sbagliata.

---

## 1. Il recupero: un quinto del parco è fatto

Nel primo documento vi avevamo detto che le colonne si sarebbero riempite solo
con gli annunci nuovi, circa 5.000 a notte. Poi abbiamo scoperto che le 250.625
auto già a catalogo non le avrebbero avute **mai**: la coda notturna seleziona
solo `detail_scraped = false`, e quel campo diventa `true` per sempre alla prima
lettura.

Dal 09/08 gira un recupero una tantum, a blocchi orari fra le 10:00 e le 21:00,
negli orari in cui lo scraper è fermo.

| | |
|---|---|
| copertura all'11/08 | **50.292 su 258.154 attive — 19,5%** |
| ritmo | 20.350 al giorno (11 blocchi da 1.850) |
| blocchi finora | 26 su 26, nessuno fallito o rifiutato |
| completamento stimato | intorno al **21/08** |

**Le auto già vendute non le avranno mai** — sono zero, e la loro pagina non
esiste più. Se il vostro modello di prezzo si addestra sulle vendite, per quelle
righe le dotazioni non arriveranno né ora né dopo.

## 2. Il campione è casuale: potete già usarlo

**Correzione rispetto alla versione precedente**, e in vostro favore. Avevamo
scritto che il recupero procede per id crescente e che quindi la parte fatta non
era un campione casuale. Sbagliato: gli id sono **UUID**, non progressivi. La
correlazione fra ordine di id e data di primo avvistamento è **0,001**, cioè
nessuna. Il recupero attraversa il parco in ordine casuale.

| | coperte (47.881) | tutte (247.234) |
|---|---|---|
| anno medio | 2021,01 | 2020,63 |
| prezzo medio | 23.977 € | 23.514 € |
| km medi | 81.295 | 82.735 |

Prezzo entro il 2%, chilometraggio entro il 2%, quattro mesi sull'anno. Lo
scarto residuo si spiega da sé: oltre al recupero casuale, ogni notte entrano
annunci nuovi che ricevono le dotazioni subito, e quelli sono recenti per
definizione.

**Conseguenza pratica: usateli pure per le analisi aggregate.** Un quinto del
parco preso a caso basta per medie, distribuzioni e confronti fra segmenti. Le
frequenze qui sotto, calcolate su 51.059 auto, scostano di uno o due punti da
quelle che avevamo su 4.204: si sono già stabilizzate.

Quello che ancora non potete fare è rispondere su una **singola** auto. Se
l'annuncio che interessa all'utente non è ancora stato letto, le sue dotazioni
sono `NULL`, e nessuna aggregazione lo aggiusta.

## 3. Ogni quanto compare ciascuna dotazione

Su 51.059 auto con la lista letta. La lista media contiene **39,1 voci**; il
**4,5%** ha una lista vuota, cioè il venditore non ha dichiarato nulla.

| dotazione | auto | quota |
|---|---|---|
| Cerchi in lega | 38.806 | **76,0%** |
| Fari LED | 27.930 | 54,7% |
| Telecamera parcheggio | 20.706 | 40,6% |
| Fari full-LED | 18.346 | 35,9% |
| Regolazione elettrica sedili | 11.066 | 21,7% |
| Sedili riscaldati | 8.883 | 17,4% |
| Interni in pelle | 5.851 | 11,5% |
| Tetto panoramico | 4.908 | 9,6% |
| Tettuccio apribile | 3.946 | **7,7%** |

I cerchi in lega sono su tre auto su quattro: come variabile discriminante
valgono poco, ma la loro **assenza** dice qualcosa. Il tettuccio è l'opposto —
raro, e per questo informativo quando c'è.

Vernice: `Metallizzato` **41,5%**, `Altro` 38,1%, vuoto 20,4%. Il nome
commerciale del costruttore (`body_color_original`) c'è sul **28,4%**: usatelo
come di più, non come dimensione su cui segmentare.

## 4. Il legame col prezzo: associazione, non valore

Questa è la parte da maneggiare con cura.

Mediana del prezzo richiesto, con e senza ciascuna dotazione (solo annunci
canonici, `duplicate_of IS NULL`):

| dotazione | con | senza | divario grezzo |
|---|---|---|---|
| Tettuccio apribile | 32.900 € | 17.900 € | **+15.000** |
| Interni in pelle | 32.000 € | 17.600 € | +14.400 |
| Sedili riscaldati | 31.000 € | 17.000 € | +14.000 |
| Tetto panoramico | 28.900 € | 17.912 € | +10.988 |
| Regolazione elettrica sedili | 25.150 € | 17.000 € | +8.150 |
| Telecamera parcheggio | 22.900 € | 15.999 € | +6.901 |
| Fari full-LED | 22.900 € | 16.000 € | +6.900 |
| Fari LED | 21.900 € | 14.500 € | +7.400 |
| Cerchi in lega | 19.800 € | 14.500 € | +5.300 |

**Nessuno di questi numeri è quanto vale l'optional.** Sono quanto costa in più
un'auto che ce l'ha, che è una cosa diversa.

Il tettuccio lo mostra bene. Non è l'età a spiegare i 15.000 €: le auto col
tettuccio hanno praticamente lo stesso anno delle altre. È il segmento.

| marca | quota fra le auto col tettuccio | quota sul parco |
|---|---|---|
| Mercedes-Benz | **20,4%** | 8,1% |
| BMW | 15,0% | 8,2% |
| Audi | 13,5% | 9,7% |
| Volkswagen | 8,5% | 9,7% |

Mercedes pesa due volte e mezzo. Il divario misura in buona parte «quanto costa
una Mercedes».

Confrontando **dentro la stessa marca e lo stesso anno** — 104 strati con almeno
dieci auto col tettuccio e trenta senza, 3.213 auto in tutto — il divario mediano
scende a **+5.540 €**. Il **63% in meno** del numero grezzo.

E anche quello resta sovrastimato, perché dentro una marca il tettuccio arriva
con l'allestimento alto, il motore più grosso e il resto del pacchetto optional.
Un modello che voglia dire quanto vale il tettuccio deve tenere insieme
allestimento, motorizzazione e le altre dotazioni, che sono fortemente
correlate fra loro.

**Suggerimento**: usate questi numeri per ordinare le dotazioni per importanza —
quell'ordine è solido e non è cambiato passando da 4.204 a 51.059 auto — e non
per attribuire un valore in euro a ciascuna finché non avete un modello che
controlla le variabili insieme.

## 5. Ricordate `NULL`

Con il 19,5% di copertura, **quattro auto su cinque hanno tutte le booleane a
`NULL`**. Un `WHERE has_sunroof = false` oggi non restituisce «le auto senza
tettuccio»: restituisce quelle che abbiamo già guardato e che non ce l'hanno.

```sql
-- quante auto possiamo dire qualcosa su questa dotazione
SELECT count(*) FILTER (WHERE has_sunroof IS NOT NULL) AS note,
       count(*) AS totali
FROM listings WHERE status='active' AND duplicate_of IS NULL;
```

Conviene mostrare questo rapporto accanto a qualunque grafico basato sulle
dotazioni, finché il recupero non è finito. Fra circa dieci giorni non servirà
più per le auto attive — ma resterà vero per sempre per le vendute.

---

## Riepilogo

| | |
|---|---|
| copertura | 19,5% · 20.350 al giorno · completa verso il 21/08 |
| campione | casuale (id UUID): già utilizzabile per gli aggregati |
| auto vendute | zero, e non cambieranno mai |
| dotazione più comune | cerchi in lega, 76% |
| dotazione più rara | tettuccio apribile, 7,7% |
| vernice metallizzata | 41,5% |
| divari di prezzo | associazioni, non valori: il tettuccio passa da +15.000 a +5.540 controllando marca e anno |
