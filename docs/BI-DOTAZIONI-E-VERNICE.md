# Dotazioni e vernice: dodici colonne nuove

Per la sessione che sviluppa AutoSmart-BI. Scritto il **07/08/2026**.

Le voci sono state scelte da un esperto di auto insieme all'utente, fra le 142
che il mercato pubblica, in base a quali spostano il prezzo di rivendita.

---

## 1. Le colonne

Su `listings`, tutte popolate dalla pagina di dettaglio.

| colonna | tipo | contenuto |
|---|---|---|
| `has_sunroof` | boolean | Tettuccio apribile |
| `has_panoramic_roof` | boolean | Tetto panoramico |
| `has_leather_interior` | boolean | Interni in pelle |
| `has_heated_seats` | boolean | Sedili riscaldati |
| `has_electric_seats` | boolean | Regolazione elettrica sedili |
| `has_parking_camera` | boolean | Telecamera per parcheggio assistito |
| `has_full_led_headlights` | boolean | Fari full-LED |
| `has_led_headlights` | boolean | Fari LED |
| `has_alloy_wheels` | boolean | Cerchi in lega |
| `paint_type` | text | finitura: `Metallizzato`, `Altro` |
| `body_color_original` | text | nome del costruttore: `Verde Salvia Metallizzato` |
| `equipment` | jsonb | **la lista completa**, grezza, con indice GIN |

**Cambio e trazione le avevate già** e non cambiano: `transmission`
(`Automatico` 145.217, `Manuale` 128.282, `Semiautomatico` 31.149) e
`drive_train` (`Anteriore` 182.270, `4x4` 55.872, `Posteriore` 14.054).

## 2. La cosa più importante: `NULL` non è `false`

Un annuncio la cui pagina di dettaglio non è mai stata letta ha `equipment
IS NULL` e **tutte e nove le booleane a `NULL`**. Non è «niente optional»: è
«non lo sappiamo».

```sql
-- SBAGLIATO: conta come "senza tettuccio" anche le auto mai lette
WHERE has_sunroof = false

-- GIUSTO: distingue le tre risposte
WHERE has_sunroof IS false        -- osservato assente
WHERE has_sunroof IS true         -- osservato presente
WHERE has_sunroof IS NULL         -- mai osservato
```

Nel confronto di prezzo la differenza è tutta: se un modello di prezzo tratta
`NULL` come `false`, le auto non arricchite entrano nel gruppo «senza
tettuccio» e ne abbassano la media, facendo sembrare il tettuccio più prezioso
di quanto sia.

`equipment = '[]'` è invece una terza cosa ancora: la pagina è stata letta e il
venditore non ha elencato nulla. Sul campione erano **24 auto su 487, il 5%**.
Lì le booleane sono `false` a ragione.

## 3. Perché non calcolate voi le dotazioni dalla lista

Potete leggere `equipment` — è indicizzato e le query di contenimento sono
veloci:

```sql
WHERE equipment @> '["Tetto panoramico"]'
```

Ma **per queste nove usate le colonne**, perché due etichette hanno una forma
che tradisce chi le confronta per uguaglianza:

**I cerchi in lega compaiono in due forme.** O `Cerchi in lega`, o con la
misura: `Cerchi in lega (17")`, `(18")`, fino a `(23")`. Sul campione **95 auto
su 357 avevano solo la variante con la misura**: un `@> '["Cerchi in lega"]'`
ne perde il 27%, e il numero che resta sembra perfettamente plausibile.

**`Fari LED` e `Fari full-LED` non sono uno il sottoinsieme dell'altro.** Sul
campione: 266 la prima, 173 la seconda, **128 entrambe**. Quarantacinque auto
hanno il full-LED senza l'altra etichetta. Non deducete l'una dall'altra in
nessuna direzione.

Ci sono poi tre coppie che si somigliano e valgono cose diverse. Le colonne
già le tengono distinte; se scrivete query sulla lista grezza, attenzione:

| non confondere | con | perché |
|---|---|---|
| `Volante in pelle` (56% delle auto) | `Interni in pelle` (13%) | il volante non vale nulla, gli interni sono fra i segnali più forti |
| `Park Distance Control` (78%) | `Telecamera per parcheggio assistito` (46%) | i sensori sono quasi universali, la telecamera no |
| `Regolazione elettrica del sedile posteriore` | `Regolazione elettrica sedili` | sono due optional diversi |

## 4. Da quando ci sono i dati, e cosa non ci sarà mai

Le colonne esistono da adesso ma **partono vuote**. Si riempiono man mano che
gli annunci passano dall'arricchimento: le auto nuove da subito, il parco già
raccolto solo se lo riscansioniamo.

Due conseguenze di cui tenere conto nel progettare le analisi:

- **Per settimane la copertura sarà parziale e non casuale**: le prime auto ad
  avere le dotazioni saranno quelle pubblicate di recente. Un confronto di
  prezzo fatto troppo presto confronta auto nuove sul mercato con auto vecchie,
  non auto con e senza tettuccio.
- **Le 44.310 auto già vendute non le avranno mai.** La loro pagina non esiste
  più. Se il vostro modello di prezzo si addestra sulle vendite, per ora quelle
  righe non hanno dotazioni — e non le avranno retroattivamente.

Vi conviene aspettare che la copertura sia sostanziosa prima di pubblicare
qualunque stima che dipenda da questi campi. Possiamo darvi il numero esatto
in qualunque momento:

```sql
SELECT count(*) FILTER (WHERE equipment IS NOT NULL) AS con_dotazioni,
       count(*) AS totale
FROM listings WHERE status = 'active';
```

## 5. Se serve una decima voce

Ditecelo: è una migrazione, **non una riscansione**. Salviamo `equipment` per
intero proprio per questo — `scripts/ricalcola-dotazioni.py` ricava le colonne
dalla lista già a database, senza toccare la rete.

Vale solo per le auto che una lista ce l'hanno. Per quelle mai arricchite, e
per le vendute, non c'è niente da cui ricavare.

---

## Riepilogo

| | |
|---|---|
| nove booleane + `paint_type` + `body_color_original` + `equipment` | su `listings`, da subito |
| `transmission`, `drive_train` | c'erano già, invariate |
| **`NULL` ≠ `false`** | mai osservato, non «assente» |
| cerchi e fari | usate le colonne, non la lista: due trappole di etichetta |
| copertura | parte da zero e cresce; le vendute non la avranno mai |
