# Insurance Agent - PostgreSQL (LangFlow)

## Popis

AI Agent pre prácu s PostgreSQL databázou poistných zmlúv vytvorený v LangFlow.
Agent pracuje s databázou, používa SQL nástroj a odpovedá na dotazy cez LLM (OpenAI GPT-4o-mini).

## Funkcie agenta
- **Databázové dotazy**: Agent vykonáva SQL SELECT dotazy na PostgreSQL databázu
- **Práca s dátumami**: Má vstavaný nástroj pre aktuálny dátum a čas
- **Slovenský jazyk**: Agent komunikuje v slovenčine
- **Bezpečnosť**: Konfigurovaný iba pre čítanie (SELECT), nie pre zápis

## Architektúra workflow

```
Chat Input → Agent (GPT-4o-mini) → Chat Output
                  ↑
            SQL Database Tool
            (PostgreSQL - odin)
```

**Komponenty:**
1. **Chat Input** - vstup od používateľa
2. **Agent** - AI agent s LLM a nástrojmi
3. **SQL Database** - nástroj pre vykonávanie SQL dotazov
4. **Chat Output** - výstup odpovede

## Databázová štruktúra

Databáza `odin` obsahuje tieto hlavné tabuľky:

### klient
| Stĺpec | Typ | Popis |
|---------|-----|-------|
| id | integer | Primárny kľúč |
| meno | varchar | Krstné meno |
| priezvisko | varchar | Priezvisko |
| email | varchar | Email |
| telefon | varchar | Telefón |
| mobil | varchar | Mobil |
| mesto | varchar | Mesto |
| rc_ico | varchar | Rodné číslo / IČO |
| bd_is_active | integer | 1 = aktívny |

### poistna_zmluva
| Stĺpec | Typ | Popis |
|---------|-----|-------|
| id | integer | Primárny kľúč |
| cislo_pz | varchar | Číslo poistnej zmluvy |
| stav_id | integer | FK na enum_stav_zmluvy |
| poistnik_id | integer | FK na klient.id |
| produkt_id | integer | FK na enum_produkt |
| datum_zaciatku | timestamp | Začiatok zmluvy |
| datum_dojednania | timestamp | Dátum dojednania |
| datum_storna | timestamp | Dátum storna |
| rocne_poistne | numeric | Ročné poistné |
| poistovna_id | integer | FK na enum_poistovna |
| bd_is_active | integer | 1 = aktívny |

### poistna_udalost
| Stĺpec | Typ | Popis |
|---------|-----|-------|
| id | integer | Primárny kľúč |
| poistnik | varchar | Meno poistníka |
| cislo | varchar | Číslo udalosti |
| stav | varchar | Stav udalosti |
| pz | varchar | Číslo poistnej zmluvy |
| datum_hlasenia | date | Dátum nahlásenia |
| datum_vzniku | date | Dátum vzniku |
| skoda_cena_odhad | numeric | Odhadovaná cena škody |
| skoda_cena_vyplatene | numeric | Vyplatená suma |
| bd_is_active | integer | 1 = aktívny |

### Enum tabuľky (pre JOIN-y)
- `enum_stav_zmluvy` - stavy poistných zmlúv
- `enum_produkt` - produkty poistenia
- `enum_poistovna` - poisťovne
- `enum_typ_poistenia` - typy poistenia
- `enum_stav_pu` - stavy poistných udalostí

## Konfigurácia

### PostgreSQL pripojenie
```
Host: host.docker.internal (z Docker kontajnera)
Port: 5432
Database: odin
Username: postgres
Password: postgres
```

**Connection String (pre LangFlow v Dockeri):**
```
postgresql://postgres:postgres@host.docker.internal:5432/odin
```

### OpenAI API
```
Model: gpt-4o-mini
Temperature: 0.1
```

## Inštalácia a spustenie

### 1. Spustenie LangFlow Docker kontajnera
```bash
docker run -d --name langflow -p 7860:7860 langflowai/langflow:latest
```

### 2. Overenie PostgreSQL databázy
```bash
docker ps | grep postgres
```
Databáza beží na porte 5432 a je dostupná cez `host.docker.internal` z Docker kontajnerov.

### 3. Import workflow do LangFlow
1. Otvorte `http://localhost:7860/`
2. Kliknite na **"New Flow"** → **"Import"**
3. Vyberte súbor `Insurance Agent - PostgreSQL.json`
4. Workflow sa automaticky naimportuje

### 4. Konfigurácia (po importe)
- V **Agent** komponente skontrolujte OpenAI API Key
- V **SQL Database** komponente skontrolujte Database URL

## Použitie agenta

### Príklady otázok:

**Základné dotazy:**
```
Koľko máme celkovo poistných zmlúv?
Zobraz mi prvých 10 klientov
Koľko máme aktívnych klientov?
```

**Dotazy s dátumom:**
```
Koľko poistných udalostí bolo tento mesiac?
Zmluvy dojednané za posledných 30 dní
Poistné udalosti za rok 2024
```

**Komplexné dotazy:**
```
Zobraz klientov s aktívnymi zmluvami (bd_is_active = 1)
Aká je priemerná výška ročného poistného?
Top 5 klientov podľa počtu zmlúv
Nájdi všetky nevybavené poistné udalosti
Celková suma vyplatených škôd
```

**Špecifické vyhľadávanie:**
```
Nájdi zmluvy klienta s priezviskom "Novák"
Zobraz všetky udalosti pre zmluvu číslo XYZ
Ktorí klienti majú viac ako 2 zmluvy?
```

## Bezpečnostné opatrenia

1. **Iba čítanie**: Agent vykonáva iba SELECT dotazy
2. **Potvrdenie zmien**: Pri požiadavke na zmenu sa spýta na potvrdenie
3. **Obmedzenie iterácií**: Max 15 pokusov na vyriešenie úlohy
4. **Nízka teplota**: 0.1 pre konzistentné a presné SQL dotazy

## Troubleshooting

### Agent sa nepripojí k databáze
- Overte, že PostgreSQL beží: `docker ps | grep postgres`
- Skontrolujte, že port 5432 je namapovaný: `0.0.0.0:5432->5432/tcp`
- V SQL komponente musí byť `host.docker.internal` (nie `localhost`)

### OpenAI API chyba
- Overte platnosť API kľúča na https://platform.openai.com/
- Skontrolujte kredit na OpenAI účte

### Agent nerozumie otázke
- Upresnite otázku s konkrétnymi názvami tabuliek
- Agent vie zistiť štruktúru tabuľky cez `information_schema.columns`

## Technické detaily

- **LangFlow**: latest (v Docker)
- **PostgreSQL**: 13+ (v Docker, port 5432)
- **LLM**: OpenAI GPT-4o-mini
- **Max iterations**: 15
- **Temperature**: 0.1
- **Chat history**: 100 správ
