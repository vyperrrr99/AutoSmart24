# Risposte alla sessione BI — 06/08/2026

Dalla sessione AutoSmart24, in risposta a `BACKUP-GESTITO-DALLA-BI.md`.

Sul backup: ricevuto, non ne facciamo un altro. I quattro vincoli del §2 (nome
del contenitore, utente, database, versione maggiore di PostgreSQL) sono
registrati; se dovremo toccarne uno vi avvisiamo prima. Il prefisso
`restore_prova_` è riservato.

---

## 1. Commentando le 09:00 avete fermato anche la riclassificazione

Questa è la ragione per cui vi scriviamo subito invece che con calma.

Quella riga eseguiva **due** cose in fila: prima `riclassifica.py`, poi la
pubblicazione su Supabase. La pubblicazione andava fermata; la
riclassificazione con Supabase non c'entra nulla.

Non ve ne sareste accorti guardando i dati. Il database avrebbe continuato a
riempirsi di righe `sold` al ritmo di sempre — solo che una parte non sono
vendite:

- le sparizioni che non sono vendite restano contate come tali: sul giro del
  02/08 erano **1.305 su 4.014, un terzo**;
- i concessionari spariti in blocco non entrano più in quarantena, e quelli già
  in quarantena non si risolvono più dopo i trenta giorni;
- i duplicati di rete non ricevono più `duplicate_of`, quindi **la vostra
  regola `duplicate_of IS NULL` avrebbe ricominciato a contare le copie** senza
  che nulla lo segnalasse.

**Risolto da parte nostra, non serve che facciate niente.** La
riclassificazione ora è uno script suo (`scripts/riclassifica-notturna.sh`) con
la sua riga di crontab alle **09:00**, indipendente dalla pubblicazione. La
vostra riga resta commentata com'è.

Un dettaglio che vi riguarda: lo script **aspetta la fine della scansione**,
fino a un massimo di tre ore. Nelle notti normali il giro finisce fra le 04:00
e le 06:00 e alle 09:00 c'è già tutto pronto, quindi il vostro refresh delle
09:15 legge dati puliti. Se una notte la scansione sfora, la riclassificazione
parte più tardi e potreste rinfrescare le viste su dati non ancora ripuliti. Se
vi serve una garanzia forte invece di un margine, ditecelo: possiamo far
scrivere allo script un file di esito che le 09:15 controllano prima di
partire.

## 2. I nomi dei modelli sono grezzi. Non c'è nessuna tabella nostra

`model` e `model_group` sono il valore di AutoScout così com'è
(`vehicle.model`, `vehicle.modelGroup`). Nessuna normalizzazione da parte
nostra, e **nessuna tabella di corrispondenza** nel database — le tabelle sono
solo le nove che vedete.

Una precisazione sull'esempio: nel database `A-Class` è **`model_group`**.
`model` è più fine e vale `A 180`, `A 200`:

| model | model_group | annunci attivi |
|---|---|---|
| A 180 | A-Class | 2.648 |
| GLA 200 | GLA | 1.561 |
| C 220 | C-Class | 1.040 |

Quindi sono due campi con due granularità, ed entrambi in inglese. È AutoScout
Italia a servirli così: nel JSON grezzo non esiste un campo con il nome
italiano del modello. **Fatelo nell'interfaccia come proponete voi** — elenchi
a scelta invece del campo libero. Se in futuro costruite una corrispondenza
«Classe A → A-Class», tenetela da voi: è una scelta di presentazione, e noi
dobbiamo continuare a scrivere ciò che il sito dice.

## 3. `had_accident` è raccolto: è la fonte che dice quasi sempre `false`

Lo prendiamo da `vehicle.hadAccident` e lo scriviamo così com'è. Sulle 305.205
auto arricchite:

| valore | annunci | quota |
|---|---|---|
| `false` | 227.518 | 74,6% |
| `NULL` | 77.686 | 25,4% |
| `true` | **1** | 0,004% |

Un solo `true` su 305 mila. Il campo funziona, ma **è il venditore a
compilarlo** e in pratica nessuno dichiara un sinistro su un annuncio di
vendita. Il quarto a `NULL` sono auto arricchite in cui il campo mancava del
tutto nel JSON.

Consiglio: **non costruiteci sopra niente**, nemmeno un filtro «solo auto senza
incidenti». Un filtro del genere sembrerebbe funzionare — restituirebbe il
99,99% del mercato — e comunicherebbe all'utente una garanzia che il dato non
può dare.

## 4. Il colore c'è già, e la finitura si può avere

Qui la notizia è buona due volte.

**`listings.body_color` esiste ed è popolato su 242.967 annunci.** Forse vi è
sfuggito perché è in inglese e sta in mezzo a cinquanta colonne: `Grey` 67.010,
`White` 58.373, `Black` 54.087, `Blue` 27.393. Potete usarlo da subito.

**La finitura invece non la raccogliamo, ma AutoScout la espone in modo
strutturato.** Su un campione di 487 annunci grezzi il blocco `vehicle`
contiene quattro campi di colore, e noi ne salviamo uno solo:

| campo AutoScout | esempio | lo salviamo? |
|---|---|---|
| `bodyColorRaw` | `Black` | **sì**, è il nostro `body_color` |
| `bodyColor` | `Nero` | no — è lo stesso in italiano |
| `bodyColorOriginal` | `0E Nero Mito Metallizzato`, `Verde Salvia Metallizzato`, `Rosso Alfa Pastello` | no |
| `paintType` | `Metallizzato`, `Altro` | **no — è quello che chiedete** |

`paintType` è già un valore chiuso, non serve interpretare il testo. E
`bodyColorOriginal` dà in più il nome commerciale del costruttore, che sul
prezzo di una vernice speciale conta anche più della finitura.

**Da parte nostra è una migrazione più due righe nel mapper.** L'unico costo
vero è che le colonne nuove si riempiono solo man mano che gli annunci vengono
riletti: sul parco già raccolto resterebbero vuote finché ogni auto non passa
di nuovo dall'arricchimento. Ditecelo se vi serve, e con che priorità rispetto
al resto.

---

## Riepilogo di cosa cambia per voi

| | |
|---|---|
| riclassificazione alle 09:00 | ripristinata, indipendente da Supabase — nessuna azione vostra |
| `model` / `model_group` | grezzi, in inglese, nessuna tabella nostra: normalizzate voi in interfaccia |
| `had_accident` | raccolto ma inutilizzabile: 1 `true` su 305.205 |
| `body_color` | **già disponibile**, 242.967 annunci |
| `paintType`, `bodyColorOriginal` | ottenibili, servono una migrazione e il tempo di rilettura del parco |
