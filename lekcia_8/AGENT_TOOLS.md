# Agent Tools — prehľad

## 1. `search_codebase(query, n_results=5, language=None, obj_type=None)`
**Typ:** Sémantické vyhľadávanie (vector search)

Hľadá v ChromaDB indexe pomocou nomic-embed-text embeddingov. Vracia najrelevantnejšie chunky (1 chunk = 1 SQL procedúra/tabuľka/Python funkcia). Automaticky expanduje výsledky cez dependency graph (`expand_deps=True, dep_depth=2`) — ak nájde procedúru, pritiahne aj jej závislosti. Výsledky formátuje s obsahom (max 800 znakov na chunk).

**Parametre:**
- `query` — čo hľadáme (voľný text, sémanticky)
- `n_results` — počet výsledkov (default 5)
- `language` — filter: `sql`, `python`, `perl`
- `obj_type` — filter: `procedure`, `table`, `view`, `function`, `class`

---

## 2. `get_file_content(filepath, ref='HEAD')`
**Typ:** Git read

Vracia obsah súboru z git histórie. `ref` môže byť `HEAD`, commit SHA, branch, tag. Používa GitPython API s fallbackom na `git show`. Ak súbor neexistuje, vráti chybovú správu.

---

## 3. `get_file_content_direct(filename, start_line=1, max_lines=100)`
**Typ:** Filesystem read (fallback)

Hľadá súbor priamo na filesystéme (`/workspace/repo`) cez `os.walk()` — nevyžaduje git. Podporuje pagináciu (`start_line`, `max_lines`). Ak nájde viac súborov s rovnakým menom, vypíše zoznam a žiada presnejšiu cestu. Fallback keď git nie je inicializovaný.

---

## 4. `get_git_history(filepath, max_commits=10)`
**Typ:** Git história

Git log pre konkrétny súbor. Vracia zoznam commitov: SHA, autor, dátum, commit message, zmenené súbory. Používa GitPython `iter_commits(paths=...)` s fallbackom na `git log --follow` subprocess.

---

## 5. `get_object_deps(object_name, depth=2)`
**Typ:** Dependency graph

Vráti tri veci pre daný objekt:
- **Závislosti** (forward) — čo objekt volá (max 15, do hĺbky `depth`)
- **Dependenti** (reverse) — kto objekt volá (max 10, hĺbka 1)
- **Impact analýza** — koľko objektov je ovplyvnených zmenou

Údaje z `dependency_graph.json` (2577 uzlov).

---

## 6. `search_in_git(pattern, file_pattern=None)`
**Typ:** Fulltext search (git grep)

Presné textové vyhľadávanie cez `git grep -n -i`. Na rozdiel od `search_codebase` (sémantický) toto hľadá presný reťazec. `file_pattern` filtruje súbory (napr. `"*.pl"`). Vracia max 50 výsledkov (súbor, riadok, obsah).

---

## 7. `get_recent_changes(days=7)`
**Typ:** Git aktivita

Zmeny za posledných N dní. Vracia commity so SHA, dátumom, správou a zoznamom zmenených súborov. Max 10 commitov, 5 súborov na commit.

---

## 8. `propose_change(filepath, new_content, reason)`
**Typ:** Code change (single-file, human-in-the-loop)

Navrhne zmenu jedného súboru. Pred návrhom spustí **self-verifikáciu**:
- Python: `ast.parse()` syntax check
- SQL/PL: `sqlglot.parse()` syntax check
- Dependency: overí že EXEC/CALL referencie existujú v grafe

Generuje unified diff, zobrazí ho užívateľovi a čaká na `/approve` alebo `/reject`. Zmena sa nikdy neaplikuje automaticky — vyžaduje explicitné schválenie. Po schválení sa vytvorí `.bak` backup.

---

## 9. `propose_changeset(changes, reason)`
**Typ:** Code change (multi-file, atomic, human-in-the-loop)

Navrhne zmenu viacerých súborov naraz. `changes` je JSON string:
```json
[{"filepath":"...", "new_content":"...", "reason":"..."}]
```

Atomický apply — ak jeden súbor zlyhá, rollback všetkých. Každý súbor prechádza self-verifikáciou. Kombinovaný diff pre všetky súbory.

---

## Stratégia použitia

Model má definovaný fallback reťazec ak nenájde výsledky:
1. `search_codebase` (sémantické) →
2. `search_in_git` (presný text) →
3. `get_object_deps` (závislosti) →
4. `get_file_content_direct` (filesystem)

Tool loop detection: ak model zavolá rovnaký nástroj 3x za sebou, dostane systémový hint na zmenu stratégie.
