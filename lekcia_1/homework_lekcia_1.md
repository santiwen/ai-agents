PS C:\projects\AI\ai-agents-homeworks> python .\homework_lekcia_1.py

🤖 DEMO: LLM API s Function Calling
============================================================
Používateľská otázka: Koľko je 16 krát 16?
============================================================

1️⃣ Volám LLM API s otázkou...

2️ LLM sa rozhodol použiť nástroj(e):

   Nástroj: calculator
   Argumenty: {'operation': 'multiply', 'a': 16, 'b': 16}
   Výsledok: 256

3️⃣ Volám LLM znovu s výsledkami nástrojov...

4️⃣ Finálna odpoveď LLM:

   16 krát 16 je 256.

============================================================


============================================================
Používateľská otázka: Aké je počasie v Bratislave?
============================================================

1️⃣ Volám LLM API s otázkou...

2️ LLM sa rozhodol použiť nástroj(e):

   Nástroj: get_current_weather
   Argumenty: {'location': 'Bratislava'}
   Výsledok: 22°C, Oblačno

3️⃣ Volám LLM znovu s výsledkami nástrojov...

4️⃣ Finálna odpoveď LLM:

   V Bratislave je aktuálne 22 °C a oblačno.

============================================================


============================================================
Používateľská otázka: Vypočítaj 150 deleno 3 a potom mi povedz počasie v Prahe
============================================================

1️⃣ Volám LLM API s otázkou...

2️ LLM sa rozhodol použiť nástroj(e):

   Nástroj: calculator
   Argumenty: {'operation': 'divide', 'a': 150, 'b': 3}
   Výsledok: 50.0

   Nástroj: get_current_weather
   Argumenty: {'location': 'Praha'}
   Výsledok: Počasie pre toto miesto nie je dostupné

3️⃣ Volám LLM znovu s výsledkami nástrojov...

4️⃣ Finálna odpoveď LLM:

   150 deleno 3 je 50.

Pokiaľ ide o počasie v Prahe, žiaľ, nemám k dispozícii aktuálne informácie. Môžeš si skontrolovať najnovšiu predpoveď počasia na webových stránkach alebo aplikáciách venovaných meteorológii.

   Výsledok: Počasie pre toto miesto nie je dostupné

3️⃣ Volám LLM znovu s výsledkami nástrojov...

4️⃣ Finálna odpoveď LLM:

   150 deleno 3 je 50.

Pokiaľ ide o počasie v Prahe, žiaľ, nemám k dispozícii aktuálne informácie. Môžeš si skontrolovať najnovšiu predpoveď počasia na webových stránkach alebo aplikáciách venovaných meteorológii.


4️⃣ Finálna odpoveď LLM:

   150 deleno 3 je 50.

Pokiaľ ide o počasie v Prahe, žiaľ, nemám k dispozícii aktuálne informácie. Môžeš si skontrolovať najnovšiu predpoveď počasia na webových stránkach alebo aplikáciách venovaných meteorológii.


   150 deleno 3 je 50.

Pokiaľ ide o počasie v Prahe, žiaľ, nemám k dispozícii aktuálne informácie. Môžeš si skontrolovať najnovšiu predpoveď počasia na webových stránkach alebo aplikáciách venovaných meteorológii.

Pokiaľ ide o počasie v Prahe, žiaľ, nemám k dispozícii aktuálne informácie. Môžeš si skontrolovať najnovšiu predpoveď počasia na webových stránkach alebo aplikáciách venovaných meteorológii.

============================================================
h venovaných meteorológii.

============================================================

============================================================



============================================================
Používateľská otázka: Kto bol prvý človek na Mesiaci?
============================================================

1️⃣ Volám LLM API s otázkou...

2️⃣ LLM odpovedal bez použitia nástroja:

   Prvým človekom na Mesiaci bol astronaut Neil Armstrong. Zosadil sa na Mesiaci 20. júla 1969 počas misie Apollo 11. Po ňom nasledoval astronaut Buzz Aldrin. Armstrongov výrok pri pristátí na Mesiaci bol: "To je malý krok pre človeka, ale veľký skok pre ľudstvo."

============================================================